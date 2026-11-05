"""Reads money movements from the owner's Gmail (IMAP, read-only).

Three senders are read:

- Banco Nacional's monthly statement (bncontacto@bncr.fi.cr, subject
  "Estado de cuenta ...", PDF attached): every credit line names who paid,
  e.g. ``20-09-26 ANA CATALINA RODRIGU/TRANSFERENCIA SINPE 7,000.00+``;
  every debit line (``500.00-``) is the owner's own spend -- a self-transfer
  between their own accounts names them the same way a credit names a payer
  (e.g. ``CUBILLO VILLALOBOS A/RECARGA DE DINERO``), a merchant charge has
  no payer to split out. The statement itself is monthly, on BN's own
  schedule -- but BN also sends a same-day "Voucher Digital" (same sender,
  plain receipt body) the moment a card purchase is authorized, with the
  merchant, an English-abbreviated date/time, masked card, NRO. AUT, REF
  and a CRC total using a comma decimal separator (``1900,00``), parsed by
  parse_bn_voucher.
- Wink's "Notificación de envío" (transacciones@holawink.com): one email per
  transfer the owner sends, with recipient name, amount and date. Wink also
  sends a "Notificación de transacción" (same sender) for card purchases --
  Comercio/Monto de la compra/Fecha y hora, parsed by parse_wink_transaction
  -- and a "Pago de servicios públicos" (same sender) for Wink's own bill-pay
  feature -- Servicio/Monto debitado/Fecha del pago, parsed by
  parse_wink_bill_payment.
- BAC's "Notificación de transacción" (notificacionbac@baccredomatic.cr,
  HTML): one email per card transaction (Comercio/Fecha/Monto/Tipo de
  Transacción table), sent the moment it's authorized.

scripts/sync_payments.py reads BN credits + Wink sends for the client-payment
business (lib/payments.py). scripts/nightly_expense_report.py reads BN
debits + Wink sends + BAC transactions for the owner's personal spend
closing (lib/expenses.py) -- a separate, read-only concern over the same
mailbox; it never writes payment_events.

Parsing is pure (text in, records out) so it's tested without a mailbox.
"""

from __future__ import annotations

import email
import imaplib
import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from email.header import decode_header, make_header
from html import unescape
from typing import Iterator

BN_SENDER = "bncontacto@bncr.fi.cr"
WINK_SENDER = "transacciones@holawink.com"
BAC_SENDER = "notificacionbac@baccredomatic.cr"


@dataclass
class Movement:
    source: str  # 'bn_statement' | 'bn_statement_debit' | 'bn_voucher' | 'wink_send' | 'wink_transaction' | 'wink_bill_payment' | 'bac_transaction'
    occurred_on: date
    amount: Decimal
    counterparty: str
    description: str
    external_ref: str


# --- BN statement ---------------------------------------------------------

# FECHA NÚMERO [DD-MM-YY] DESCRIPCIÓN MONTO(+/-) SALDO
_BN_LINE = re.compile(
    r"^(?P<posted>\d{2}-\d{2})\s+(?P<number>\d+)\s+(?:(?P<when>\d{2}-\d{2}-\d{2})\s+)?"
    r"(?P<desc>.+?)\s+(?P<amount>[\d,]+\.\d{2})(?P<sign>[+-])\s+(?P<balance>-?[\d,]+\.\d{2}-?)$"
)
_BN_STATEMENT_DATE = re.compile(r"Fecha [ée]ste estado:\s*(\d{2})/(\d{2})/(\d{4})")
_BN_ACCOUNT = re.compile(r"N[úu]mero de cuenta:\s*([\d-]+)")
_BN_OWNER = re.compile(r"^Nombre:\s*(.+)$", re.M)


def _amount(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


@dataclass
class Statement:
    account: str
    owner: str
    credits: list[Movement]
    unparsed_credit_lines: int


def _bn_occurred_on(match: re.Match, stmt_month: int, stmt_year: int) -> date:
    if match["when"]:
        d, mth, yy = (int(x) for x in match["when"].split("-"))
        return date(2000 + yy, mth, d)
    d, mth = (int(x) for x in match["posted"].split("-"))
    # A statement spans ~a month; a later month than the statement's own
    # belongs to the previous year (December lines in January).
    return date(stmt_year - (mth > stmt_month), mth, d)


def _bn_statement_header(text: str) -> tuple[int, int, str, str]:
    m = _BN_STATEMENT_DATE.search(text)
    if not m:
        raise ValueError("BN statement without 'Fecha éste estado'")
    stmt_month, stmt_year = int(m.group(2)), int(m.group(3))
    account = (_BN_ACCOUNT.search(text) or [None, "?"])[1]
    owner = (_BN_OWNER.search(text) or [None, ""])[1].strip()
    return stmt_month, stmt_year, account, owner


def parse_bn_statement(text: str) -> Statement:
    """Credits (``+`` lines) of one BN statement's extracted text."""
    stmt_month, stmt_year, account, owner = _bn_statement_header(text)

    credits, unparsed = [], 0
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        match = _BN_LINE.match(line)
        if not match:
            if re.search(r"\d\.\d{2}\+", line):
                unparsed += 1
            continue
        if match["sign"] != "+":
            continue
        occurred = _bn_occurred_on(match, stmt_month, stmt_year)
        desc = match["desc"].strip()
        payer, _, concept = desc.partition("/")
        credits.append(
            Movement(
                source="bn_statement",
                occurred_on=occurred,
                amount=_amount(match["amount"]),
                counterparty=payer.strip(),
                description=concept.strip(),
                external_ref=f"{account}:{match['number']}:{occurred.isoformat()}:{match['amount']}",
            )
        )
    return Statement(account, owner, credits, unparsed)


def parse_bn_statement_debits(text: str) -> tuple[str, list[Movement]]:
    """Debits (``-`` lines) of one BN statement's extracted text -- the
    owner's own card/transfer spend, plus their own transfers between
    accounts (same ``NAME/CONCEPT`` shape as a credit's payer/concept, e.g.
    ``CUBILLO VILLALOBOS A/RECARGA DE DINERO``). A line without ``/`` has no
    payer to split out -- the whole description is the merchant/concept,
    e.g. ``R INSTITUCIONAL TEC CA CARTAGO CRI``.

    Returns (owner, debits): the caller is expected to drop debits whose
    counterparty is the owner themselves (lib.payments.is_owner) before
    treating them as personal spend -- a self-transfer moved money, it
    didn't spend it. See lib.expenses.close."""
    stmt_month, stmt_year, account, owner = _bn_statement_header(text)

    debits = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        match = _BN_LINE.match(line)
        if not match or match["sign"] != "-":
            continue
        occurred = _bn_occurred_on(match, stmt_month, stmt_year)
        desc = match["desc"].strip()
        payer, sep, concept = desc.partition("/")
        counterparty, description = (payer.strip(), concept.strip()) if sep else ("", desc)
        debits.append(
            Movement(
                source="bn_statement_debit",
                occurred_on=occurred,
                amount=_amount(match["amount"]),
                counterparty=counterparty,
                description=description,
                external_ref=f"{account}:{match['number']}:{occurred.isoformat()}:{match['amount']}:D",
            )
        )
    return owner, debits


# --- BN "Voucher Digital" (same-day purchase receipt) ----------------------

# The merchant name sits on its own line, immediately followed by a line with
# an English-abbreviated date -- both anchored to line boundaries (re.M) so a
# stray "." from the preceding narrative sentence can't be swallowed into the
# merchant capture the way an unanchored, whitespace-collapsed match would.
_BN_VOUCHER_WHEN = re.compile(
    r"^(?P<merchant>[A-Z0-9ÁÉÍÓÚÑ.,'/&-]+(?: [A-Z0-9ÁÉÍÓÚÑ.,'/&-]+)*)$\n"
    r"^(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(?P<day>\d{1,2}),\s*(?P<year>\d{4})"
    r"\s*-\s*(?P<time>\d{1,2}:\d{2}\s*[ap]\.m\.)$",
    re.M,
)
_BN_VOUCHER_AUT = re.compile(r"NRO\.\s*AUT:?\s*(?P<auth>\d+)")
_BN_VOUCHER_REF = re.compile(r"\bREF:?\s*(?P<ref>\d+)")
_BN_VOUCHER_TOTAL = re.compile(r"TOTAL:?\s*CRC\s*(?P<amount>[\d.,]+)")


def _voucher_lines(text: str) -> str:
    """Normalizes a voucher body to one logical line per row, whether it
    arrived as plain text (real newlines already) or HTML (tags stripped,
    block/break tags turned into newlines first so the merchant and date
    rows stay on separate lines)."""
    marked = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</tr>|</li>|</td>", "\n", text)
    marked = re.sub(r"(?s)<[^>]+>", " ", marked)
    marked = unescape(marked)
    lines = (re.sub(r"[ \t]+", " ", ln).strip() for ln in marked.splitlines())
    return "\n".join(ln for ln in lines if ln)


def _voucher_amount(text: str) -> Decimal:
    """BN's voucher total uses a comma decimal separator (``1900,00``), the
    opposite of the statement's dot-decimal amounts -- strip any dot
    (thousands) then swap the comma for a dot."""
    return Decimal(text.replace(".", "").replace(",", "."))


def parse_bn_voucher(text: str, message_id: str) -> Movement | None:
    """One BN "Voucher Digital" email (bncontacto@bncr.fi.cr, same sender as
    the monthly statement but sent the moment a card purchase is
    authorized) -> a Movement. None when a required field is missing."""
    norm = _voucher_lines(text)
    when = _BN_VOUCHER_WHEN.search(norm)
    auth = _BN_VOUCHER_AUT.search(norm)
    total = _BN_VOUCHER_TOTAL.search(norm)
    if not (when and auth and total) or when["month"] not in _BAC_MONTHS:
        return None
    occurred = date(int(when["year"]), _BAC_MONTHS[when["month"]], int(when["day"]))
    ref = _BN_VOUCHER_REF.search(norm)
    description = f"Compra con BN (REF {ref['ref']})" if ref else "Compra con BN"
    return Movement(
        source="bn_voucher",
        occurred_on=occurred,
        amount=_voucher_amount(total["amount"]),
        counterparty=when["merchant"].strip(),
        description=description,
        external_ref=message_id,
    )


def pdf_text(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader  # only the sync job needs it

    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf_bytes)).pages)


# --- Wink "Notificación de envío" ------------------------------------------

_WINK_FIELD = r"\*?{label}:?\*?:?\s*(.+)"


def _wink_field(text: str, label: str) -> str | None:
    m = re.search(_WINK_FIELD.format(label=label), text)
    return m.group(1).strip() if m else None


def parse_wink_send(text: str, message_id: str) -> Movement | None:
    """None when it isn't a completed transfer notification.

    Two layouts: SINPE Móvil (recipient name + phone) and SINPE to an IBAN
    account (account number only, no name -- stored as "Cuenta <IBAN>", so
    it can only be assigned by hand)."""
    name = _wink_field(text, "Nombre del destinatario")
    account = _wink_field(text, "Cuenta de destino")
    if not name and account and account.upper().startswith("CR"):
        name = f"Cuenta {account}"
    amount = _wink_field(text, "Monto transferido")
    when = _wink_field(text, "Fecha de la transferencia") or _wink_field(
        text, "Fecha y hora de la transferencia"
    )
    if not (name and amount and when):
        return None
    amount_match = re.search(r"[\d,]+\.\d{2}", amount)
    date_match = re.search(r"(\d{2})/(\d{2})/(\d{4})", when)
    if not (amount_match and date_match):
        return None
    d, m, y = (int(x) for x in date_match.groups())
    phone = _wink_field(text, "Número de destino")
    return Movement(
        source="wink_send",
        occurred_on=date(y, m, d),
        amount=_amount(amount_match.group(0)),
        counterparty=name.replace("_", " ").strip(),
        description=f"SINPE Móvil a {phone}" if phone else "Transferencia SINPE a cuenta",
        external_ref=message_id,
    )


# --- Wink "Notificación de transacción" (card purchase) --------------------


def parse_wink_transaction(text: str, message_id: str) -> Movement | None:
    """One Wink "Notificación de transacción" email (card purchase, plain
    text) -> a Movement, or None when a required field is missing."""
    comercio = _wink_field(text, "Comercio")
    monto = _wink_field(text, "Monto de la compra")
    when = _wink_field(text, "Fecha y hora")
    if not (comercio and monto and when):
        return None
    amount_match = re.search(r"[\d,]+\.\d{2}", monto)
    date_match = re.search(r"(\d{2})/(\d{2})/(\d{4})", when)
    if not (amount_match and date_match):
        return None
    d, m, y = (int(x) for x in date_match.groups())
    card = _wink_field(text, "Tarjeta utilizada")
    return Movement(
        source="wink_transaction",
        occurred_on=date(y, m, d),
        amount=_amount(amount_match.group(0)),
        counterparty=comercio.strip(),
        description=f"Compra con tarjeta Wink ({card})" if card else "Compra con tarjeta Wink",
        external_ref=message_id,
    )


# --- Wink "Pago de servicios públicos" (bill pay) ---------------------------


def parse_wink_bill_payment(text: str, message_id: str) -> Movement | None:
    """One Wink "Pago de servicios públicos" email (Wink's own bill-pay
    feature, plain text) -> a Movement, or None when a required field is
    missing. Unlike parse_wink_send/parse_wink_transaction, the money moves
    straight from the linked bank account (Cuenta de origen) to the service
    provider -- there's no recipient name to match, so the service name
    (e.g. "Liberty Postpago & Servicios") is the counterparty."""
    servicio = _wink_field(text, "Servicio")
    monto = _wink_field(text, "Monto debitado")
    when = _wink_field(text, "Fecha del pago")
    if not (servicio and monto and when):
        return None
    amount_match = re.search(r"[\d,]+\.\d{2}", monto)
    date_match = re.search(r"(\d{2})/(\d{2})/(\d{4})", when)
    if not (amount_match and date_match):
        return None
    d, m, y = (int(x) for x in date_match.groups())
    comprobante = _wink_field(text, "Número de comprobante")
    return Movement(
        source="wink_bill_payment",
        occurred_on=date(y, m, d),
        amount=_amount(amount_match.group(0)),
        counterparty=unescape(servicio.strip()),
        description=f"Pago de servicios (comprobante {comprobante})" if comprobante else "Pago de servicios",
        external_ref=message_id,
    )


# --- BAC "Notificación de transacción" --------------------------------------

_BAC_LABELS = [
    "Comercio",
    "Ciudad y país",
    "Fecha",
    "MASTER",
    "VISA",
    "Autorización",
    "Referencia",
    "Tipo de Transacción",
    "Monto",
]
_BAC_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
# BAC's own reversal/refund labels aren't confirmed from a real sample yet --
# this is a best-effort guard so a refund doesn't inflate the expense total;
# verify against a real reversal email and extend this set if it's named
# differently.
_BAC_REFUND_TYPES = {"ANULACION", "REVERSO", "DEVOLUCION"}


def _strip_html(markup: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", markup)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _bac_field(text: str, label: str) -> str | None:
    stop = "|".join(re.escape(lbl) + ":" for lbl in _BAC_LABELS if lbl != label)
    m = re.search(rf"{re.escape(label)}:\s*(.+?)(?=(?:{stop})|$)", text)
    return m.group(1).strip() if m else None


def parse_bac_transaction(html: str, message_id: str) -> Movement | None:
    """One BAC "Notificación de transacción" email (HTML body) -> a Movement.

    None when the amount isn't in CRC (no cross-currency total yet) or the
    transaction type looks like a reversal -- see ``_BAC_REFUND_TYPES``."""
    text = _strip_html(html)
    comercio = _bac_field(text, "Comercio")
    fecha = _bac_field(text, "Fecha")
    tipo = (_bac_field(text, "Tipo de Transacción") or "").strip().upper()
    monto = _bac_field(text, "Monto")
    if not (comercio and fecha and monto) or tipo in _BAC_REFUND_TYPES:
        return None
    money = re.match(r"([A-Z]{3})\s*([\d,]+\.\d{2})", monto)
    when = re.match(r"([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{4}),\s*(\d{2}):(\d{2})", fecha)
    if not money or money.group(1) != "CRC" or not when or when.group(1) not in _BAC_MONTHS:
        return None
    occurred = date(int(when.group(3)), _BAC_MONTHS[when.group(1)], int(when.group(2)))
    return Movement(
        source="bac_transaction",
        occurred_on=occurred,
        amount=_amount(money.group(2)),
        counterparty=comercio,
        description=tipo.title() or "Compra",
        external_ref=message_id,
    )


# --- Gmail (IMAP) ------------------------------------------------------------


def _text_body(msg: email.message.Message) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/plain" and not part.get_filename():
            raw = part.get_payload(decode=True) or b""
            return raw.decode(part.get_content_charset() or "utf-8", "replace")
    return ""


def _html_body(msg: email.message.Message) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/html" and not part.get_filename():
            raw = part.get_payload(decode=True) or b""
            return raw.decode(part.get_content_charset() or "utf-8", "replace")
    return ""


@dataclass
class MailItem:
    message_id: str
    kind: str  # 'bn_statement' | 'wink_send' | 'bac_transaction'
    subject: str
    text: str | None = None          # Wink body
    html: str | None = None          # BAC body
    pdfs: list[bytes] | None = None  # BN statement PDFs


class GmailReader:
    """Read-only IMAP access to the owner's whole mailbox ("All Mail")."""

    def __init__(self, address: str, app_password: str) -> None:
        self._imap = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=120)
        self._imap.login(address, app_password)
        _, folders = self._imap.list()
        all_mail = next(
            re.search(rb'"([^"]+)"$', f).group(1).decode() for f in folders if b"\\All" in f
        )
        self._imap.select(f'"{all_mail}"', readonly=True)

    def close(self) -> None:
        try:
            self._imap.logout()
        except Exception:
            pass

    def _search(self, since: date, query: str) -> list[bytes]:
        since_imap = since.strftime("%d-%b-%Y")
        _, data = self._imap.search(None, "SINCE", since_imap, "X-GM-RAW", f'"{query}"')
        return data[0].split()

    DEFAULT_WANTED = [
        ("bn_statement", f"from:{BN_SENDER} subject:estado", "estado de cuenta"),
        ("wink_send", f"from:{WINK_SENDER}", "notificación de envío"),
    ]
    EXPENSE_WANTED = DEFAULT_WANTED + [
        ("bac_transaction", f"from:{BAC_SENDER}", "notificación de transacción"),
        ("wink_transaction", f"from:{WINK_SENDER}", "notificación de transacción"),
        ("wink_bill_payment", f"from:{WINK_SENDER}", "pago de servicios"),
        ("bn_voucher", f"from:{BN_SENDER} subject:voucher", "voucher digital"),
    ]

    def items(
        self, since: date, skip: set[str], wanted: list[tuple[str, str, str]] | None = None
    ) -> Iterator[MailItem]:
        """Mail items since `since`, skipping Message-IDs in `skip`.

        `wanted` is a list of (kind, IMAP query, lowercase subject substring)
        tuples; defaults to BN statements + Wink sends (``DEFAULT_WANTED``).
        scripts/nightly_expense_report.py passes ``EXPENSE_WANTED`` to also
        read BAC transactions. ASCII-only queries: IMAP search rejects
        accents, so subjects are filtered here instead."""
        for kind, query, subject_has in wanted or self.DEFAULT_WANTED:
            for uid in self._search(since, query):
                _, data = self._imap.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT)])")
                headers = email.message_from_bytes(data[0][1])
                message_id = (headers["Message-ID"] or f"uid-{uid.decode()}").strip()
                subject = str(make_header(decode_header(headers["Subject"] or "")))
                if message_id in skip or subject_has not in subject.lower():
                    continue
                _, data = self._imap.fetch(uid, "(BODY.PEEK[])")
                msg = email.message_from_bytes(data[0][1])
                if kind in ("wink_send", "wink_transaction", "wink_bill_payment"):
                    yield MailItem(message_id, kind, subject, text=_text_body(msg))
                elif kind == "bac_transaction":
                    yield MailItem(message_id, kind, subject, html=_html_body(msg))
                elif kind == "bn_voucher":
                    yield MailItem(message_id, kind, subject, text=_text_body(msg) or _html_body(msg))
                else:
                    pdfs = [
                        p.get_payload(decode=True)
                        for p in msg.walk()
                        if (p.get_filename() or "").lower().endswith(".pdf")
                        and "glosario" not in (p.get_filename() or "").lower()
                    ]
                    yield MailItem(message_id, kind, subject, pdfs=pdfs)
