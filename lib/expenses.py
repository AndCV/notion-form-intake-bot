"""Nightly personal-spend closing: groups money movements by date and formats
the Telegram summary text.

Independent of lib/payments.py (the client-payment business, matched against
services and collaborators): this is the owner's own spend across BN debits,
Wink sends and BAC transactions (lib/payment_mail.py). A Wink send already
recorded as a collaborator payout in payment_events is excluded -- that's
business spend, not personal, and scripts/sync_payments.py already tracks it.

Pure functions over plain Movement records, so they're tested without a
mailbox or a database.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .payment_mail import Movement

SOURCE_LABELS = {
    "bn_statement_debit": "BN",
    "wink_send": "Wink",
    "bac_transaction": "BAC",
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


def close(movements: list[Movement], business_wink_refs: set[str]) -> ExpenseReport:
    """Groups `movements` (BN debits, Wink sends, BAC transactions) by date,
    excluding Wink sends whose external_ref (the email Message-ID) is in
    `business_wink_refs` -- those are collaborator payouts already tracked
    by the client-payment business, not personal spend."""
    personal, excluded = [], 0
    for m in movements:
        if m.source == "wink_send" and m.external_ref in business_wink_refs:
            excluded += 1
            continue
        personal.append(m)

    by_day: dict[date, list[Movement]] = defaultdict(list)
    for m in personal:
        by_day[m.occurred_on].append(m)

    days = [
        DayTotal(occurred_on=day, total=sum((m.amount for m in ms), Decimal(0)), movements=ms)
        for day, ms in sorted(by_day.items())
    ]
    total = sum((d.total for d in days), Decimal(0))
    return ExpenseReport(days=days, total=total, excluded_business_count=excluded)


def _where(m: Movement) -> str:
    """Merchant/recipient text for one line of the message: BAC and Wink
    carry it in counterparty (comercio / destinatario); a BN debit has no
    payer to split out (see parse_bn_statement_debits), so its merchant
    text is in description instead."""
    return m.counterparty or m.description or "(sin detalle)"


def format_message(report: ExpenseReport, currency_symbol: str = "₡") -> str:
    """Plain-text summary (Telegram legacy Markdown: *bold*): one line per
    movement (where it went + which card) under each day, then the day and
    grand totals -- still short enough for a single chat message at normal
    daily volumes."""
    if not report.days:
        return "Cierre de gastos: sin movimientos."
    lines = ["*Cierre de gastos*"]
    for day in report.days:
        lines.append(f"*{day.occurred_on:%d/%m}*")
        for m in day.movements:
            source = SOURCE_LABELS.get(m.source, m.source)
            lines.append(f"  • {_where(m)} ({source}): {currency_symbol}{m.amount:,.2f}")
        lines.append(f"  Subtotal: {currency_symbol}{day.total:,.2f}")
    lines.append(f"*Total: {currency_symbol}{report.total:,.2f}*")
    return "\n".join(lines)
