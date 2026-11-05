"""Nightly personal-spend closing: groups money movements by date and formats
the Telegram summary text.

Independent of lib/payments.py (the client-payment business, matched against
services and collaborators): this is the owner's own spend across BN debits,
Wink sends and BAC transactions (lib/payment_mail.py). Two kinds of
non-spend are excluded, not just totaled separately: a Wink send already
recorded as a collaborator payout in payment_events (business spend,
already tracked by scripts/sync_payments.py), and a BN debit or Wink send
naming the owner themselves (lib.payments.is_owner) -- the owner moving
money between their own accounts, not something they actually spent.

Pure functions over plain Movement records, so they're tested without a
mailbox or a database.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .ledger import MONTH_NAMES
from .payment_mail import Movement
from .payments import is_owner

SOURCE_LABELS = {
    "bn_statement_debit": "BN",
    "bn_voucher": "BN",
    "wink_send": "Wink",
    "wink_transaction": "Wink",
    "wink_bill_payment": "Wink",
    "bac_transaction": "BAC",
    "apple_receipt": "Apple",
}


@dataclass
class DayTotal:
    occurred_on: date
    total: Decimal
    movements: list[Movement]


@dataclass
class ExpenseReport:
    days: list[DayTotal]
    total: Decimal
    excluded_business_count: int
    excluded_self_transfer_count: int = 0
    # Non-CRC movements (currently only apple_receipt, in USD): kept as line
    # items under their day like any other movement, but summed here instead
    # of into `total` -- see close()'s docstring.
    foreign_totals: dict[str, Decimal] = field(default_factory=dict)


def close(
    movements: list[Movement],
    business_wink_refs: set[str],
    owner: str = "",
    own_ibans: frozenset[str] = frozenset(),
) -> ExpenseReport:
    """Groups `movements` (BN debits, Wink sends, BAC transactions) by date.
    Excludes three kinds of non-spend, not just totals them separately:
    Wink sends whose external_ref (the email Message-ID) is in
    `business_wink_refs` (collaborator payouts, already tracked by the
    client-payment business); any BN debit or Wink send whose counterparty
    is `owner` themselves (is_owner(counterparty, owner)) -- the owner
    moving money between their own accounts, not spend, whether that shows
    up as a BN debit (a self-transfer line, same NAME/CONCEPT shape as a
    credit's payer) or a Wink send naming the owner as the recipient; and a
    Wink send to an IBAN with no name ("Cuenta <IBAN>", see
    parse_wink_send) whose IBAN is in `own_ibans` -- that case has no name
    to check against is_owner, so it has to be confirmed by hand once and
    listed (OWN_IBAN_ACCOUNTS in .env.example) rather than inferred.

    A BN public-utility bill payment ("PAGO CNFL 27561844", "PAGO AYA ...")
    has that same NAME/CONCEPT shape -- BN lists the account holder as payer
    for a bill-pay debit just like it does for a self-transfer -- but it's
    real spend, so it's kept even when the counterparty matches the owner:
    only a self-transfer's description is excluded, never one starting with
    "PAGO " (case-insensitive).

    `owner` is the BN statement holder's name from
    parse_bn_statement_debits; pass "" to skip the name check (e.g. no BN
    statement was read this run).

    A non-CRC movement (currently only apple_receipt, billed in USD) stays
    in its day's `movements` list like any other line item, but its amount
    is kept out of `total`/`DayTotal.total` and summed separately into
    `foreign_totals` instead -- there's no live exchange rate here, so
    mixing currencies into one number would misstate the total rather than
    just omit a conversion."""
    personal, excluded_business, excluded_self = [], 0, 0
    for m in movements:
        if m.source == "wink_send" and m.external_ref in business_wink_refs:
            excluded_business += 1
            continue
        is_bill_payment = m.source == "bn_statement_debit" and m.description.upper().startswith("PAGO ")
        if (
            m.source in ("bn_statement_debit", "wink_send")
            and owner
            and is_owner(m.counterparty, owner)
            and not is_bill_payment
        ):
            excluded_self += 1
            continue
        if m.source == "wink_send" and m.counterparty.removeprefix("Cuenta ").strip() in own_ibans:
            excluded_self += 1
            continue
        personal.append(m)

    by_day: dict[date, list[Movement]] = defaultdict(list)
    for m in personal:
        by_day[m.occurred_on].append(m)

    days = [
        DayTotal(
            occurred_on=day,
            total=sum((m.amount for m in ms if m.currency == "CRC"), Decimal(0)),
            movements=ms,
        )
        for day, ms in sorted(by_day.items())
    ]
    total = sum((d.total for d in days), Decimal(0))
    foreign_totals: dict[str, Decimal] = defaultdict(Decimal)
    for m in personal:
        if m.currency != "CRC":
            foreign_totals[m.currency] += m.amount
    return ExpenseReport(
        days=days,
        total=total,
        excluded_business_count=excluded_business,
        excluded_self_transfer_count=excluded_self,
        foreign_totals=dict(foreign_totals),
    )


def _where(m: Movement) -> str:
    """Merchant/recipient text for one line of the message: BAC, Wink and a
    BN self-looking debit carry it in counterparty; a plain merchant debit
    (see parse_bn_statement_debits) has no payer to split out, so its
    merchant text is in description instead."""
    return m.counterparty or m.description or "(sin detalle)"


def _amount_text(m: Movement, currency_symbol: str) -> str:
    """CRC movements use `currency_symbol`; anything else (currently only
    apple_receipt, USD) is shown in its own currency code instead of being
    converted -- see close()'s docstring."""
    if m.currency == "CRC":
        return f"{currency_symbol}{m.amount:,.2f}"
    return f"{m.currency} {m.amount:,.2f}"


def format_message(report: ExpenseReport, currency_symbol: str = "₡") -> str:
    """Plain-text summary (Telegram legacy Markdown: *bold*): one line per
    movement (where it went + which card) under each day, then the day and
    grand totals -- still short enough for a single chat message at normal
    daily volumes. A day's Subtotal and the grand Total are CRC only; a
    non-CRC movement still gets its own line, and any foreign-currency
    totals get a trailing section of their own (see close()'s docstring)."""
    if not report.days:
        return "Cierre de gastos: sin movimientos."
    lines = ["*Cierre de gastos*"]
    for day in report.days:
        lines.append(f"*{day.occurred_on:%d/%m}*")
        for m in day.movements:
            source = SOURCE_LABELS.get(m.source, m.source)
            lines.append(f"  • {_where(m)} ({source}): {_amount_text(m, currency_symbol)}")
        lines.append(f"  Subtotal: {currency_symbol}{day.total:,.2f}")
    lines.append(f"*Total: {currency_symbol}{report.total:,.2f}*")
    for currency, amount in sorted(report.foreign_totals.items()):
        lines.append(f"*Total {currency} (no incluido arriba): {amount:,.2f}*")
    return "\n".join(lines)


def format_month_total(report: ExpenseReport, month_start: date, currency_symbol: str = "₡") -> str:
    """Short monthly summary (Telegram legacy Markdown: *bold*): total spend
    for `month_start`'s month plus a subtotal per card. `report` must cover
    the whole month (scripts/nightly_expense_report.py sends this once, on
    the last day's nightly run, alongside that day's regular message). The
    per-card subtotals and the month total are CRC only; a non-CRC source
    (currently only apple_receipt) gets its own per-currency line instead of
    being mixed in, same reasoning as close()'s docstring."""
    month_name = MONTH_NAMES[month_start.month - 1]
    header = f"*Cierre de {month_name} {month_start.year}*"
    if not report.days:
        return f"{header}: sin movimientos."
    by_label: dict[str, Decimal] = defaultdict(Decimal)
    by_label_foreign: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for day in report.days:
        for m in day.movements:
            label = SOURCE_LABELS.get(m.source, m.source)
            if m.currency == "CRC":
                by_label[label] += m.amount
            else:
                by_label_foreign[(label, m.currency)] += m.amount
    lines = [header]
    for label, amount in sorted(by_label.items()):
        lines.append(f"  {label}: {currency_symbol}{amount:,.2f}")
    for (label, currency), amount in sorted(by_label_foreign.items()):
        lines.append(f"  {label} ({currency}): {amount:,.2f}")
    lines.append(f"*Total del mes: {currency_symbol}{report.total:,.2f}*")
    return "\n".join(lines)
