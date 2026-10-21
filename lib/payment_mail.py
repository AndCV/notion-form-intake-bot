"""Reads money movements from the owner's Gmail (IMAP, read-only).

Only two senders are read:

- Banco Nacional's monthly statement (bncontacto@bncr.fi.cr, subject
  "Estado de cuenta ...", PDF attached): every credit line names who paid,
  e.g. ``20-09-26 ANA CATALINA RODRIGU/TRANSFERENCIA SINPE 7,000.00+``.
  BN sends no per-transfer email, so client payments show up once a month.
- Wink's "Notificación de envío" (transacciones@holawink.com): one email per
  transfer the owner sends, with recipient name, amount and date.

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
from typing import Iterator

BN_SENDER = "bncontacto@bncr.fi.cr"
WINK_SENDER = "transacciones@holawink.com"


@dataclass
class Movement:
    source: str  # 'bn_statement' | 'wink_send'
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


def parse_bn_statement(text: str) -> Statement:
    """Credits (``+`` lines) of one BN statement's extracted text."""
    m = _BN_STATEMENT_DATE.search(text)
    if not m:
        raise ValueError("BN statement without 'Fecha éste estado'")
    stmt_month, stmt_year = int(m.group(2)), int(m.group(3))
    account = (_BN_ACCOUNT.search(text) or [None, "?"])[1]
    owner = (_BN_OWNER.search(text) or [None, ""])[1].strip()

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
        if match["when"]:
            d, mth, yy = (int(x) for x in match["when"].split("-"))
            occurred = date(2000 + yy, mth, d)
        else:
            d, mth = (int(x) for x in match["posted"].split("-"))
            # A statement spans ~a month; a later month than the statement's
            # own belongs to the previous year (December lines in January).
            occurred = date(stmt_year - (mth > stmt_month), mth, d)
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


# --- Gmail (IMAP) ------------------------------------------------------------


def _text_body(msg: email.message.Message) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/plain" and not part.get_filename():
            raw = part.get_payload(decode=True) or b""
            return raw.decode(part.get_content_charset() or "utf-8", "replace")
    return ""


@dataclass
class MailItem:
    message_id: str
    kind: str  # 'bn_statement' | 'wink_send'
    subject: str
    text: str | None = None          # Wink body
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

    def items(self, since: date, skip: set[str]) -> Iterator[MailItem]:
        """BN statements and Wink transfer notifications since `since`,
        skipping Message-IDs in `skip`. ASCII-only queries: IMAP search
        rejects accents, so subjects are filtered here instead."""
        wanted = [
            ("bn_statement", f"from:{BN_SENDER} subject:estado", "estado de cuenta"),
            ("wink_send", f"from:{WINK_SENDER}", "notificación de envío"),
        ]
        for kind, query, subject_has in wanted:
            for uid in self._search(since, query):
                _, data = self._imap.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT)])")
                headers = email.message_from_bytes(data[0][1])
                message_id = (headers["Message-ID"] or f"uid-{uid.decode()}").strip()
                subject = str(make_header(decode_header(headers["Subject"] or "")))
                if message_id in skip or subject_has not in subject.lower():
                    continue
                _, data = self._imap.fetch(uid, "(BODY.PEEK[])")
                msg = email.message_from_bytes(data[0][1])
                if kind == "wink_send":
                    yield MailItem(message_id, kind, subject, text=_text_body(msg))
                else:
                    pdfs = [
                        p.get_payload(decode=True)
                        for p in msg.walk()
                        if (p.get_filename() or "").lower().endswith(".pdf")
                        and "glosario" not in (p.get_filename() or "").lower()
                    ]
                    yield MailItem(message_id, kind, subject, pdfs=pdfs)
