"""Closes yesterday's personal spend and sends it over Telegram. On the
month's last day, also sends a month-total summary.

    python scripts/nightly_expense_report.py            # read, close, send
    python scripts/nightly_expense_report.py --dry-run  # read and print only; send nothing

Runs nightly from .github/workflows/expense_report.yml, shortly after
midnight (TIMEZONE, default America/Costa_Rica). Reads BN statement debits,
Wink sends, Wink card transactions, Wink bill payments and BAC transactions
(lib/payment_mail.py) for the day that just ended, excludes Wink sends already recorded as collaborator payouts in
payment_events (read-only -- see lib/expenses.py), and messages the total
to a Telegram bot (lib/telegram.py) -- not WhatsApp: Meta's Cloud API needs
a Business Account, and CallMeBot (the free workaround) closed registration.

Stateless by design: each run re-reads exactly the emails it needs for the
window it's closing (IMAP SINCE + a date-range filter) rather than tracking
a processed-message table, so there's nothing to replay or dedupe across
runs. On a normal night that window is just yesterday; on the month's last
day it widens to the whole month (so the month-end message's total is
computed from the same read, not a second mailbox pass or a stored running
total).

Prints totals only, never merchant/recipient names: its output lands in
public CI logs.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from lib import telegram
from lib.db import connect
from lib.expenses import close, format_message, format_month_total
from lib.payment_mail import (
    GmailReader,
    parse_bac_transaction,
    parse_bn_statement_debits,
    parse_wink_bill_payment,
    parse_wink_send,
    parse_wink_transaction,
    pdf_text,
)

REQUIRED_ENV = [
    "GMAIL_ADDRESS",
    "GMAIL_APP_PASSWORD",
    "DATABASE_URL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]


def _business_wink_refs(database_url: str) -> set[str]:
    """external_ref (Message-ID) of every Wink send already tracked as a
    collaborator payout -- read-only, never writes payment_events."""
    with connect(database_url) as conn:
        rows = conn.execute(
            "SELECT external_ref FROM payment_events WHERE source = 'wink_send'"
        ).fetchall()
    return {r[0] for r in rows}


def main() -> int:
    dry_run = "--dry-run" in sys.argv[1:]
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        print(f"Missing environment variable(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    tz = ZoneInfo(os.environ.get("TIMEZONE", "America/Costa_Rica"))
    override_date = os.environ.get("OVERRIDE_DATE")
    # OVERRIDE_DATE re-closes a past day (and its month-end, if it's the
    # month's last day) -- e.g. to resend a corrected total after a parser
    # fix. Unset on every normal nightly run, where `yesterday` is today - 1.
    yesterday = date.fromisoformat(override_date) if override_date else (
        datetime.now(tz).date() - timedelta(days=1)
    )
    month_start = yesterday.replace(day=1)
    is_month_end = (yesterday + timedelta(days=1)).day == 1

    business_refs = _business_wink_refs(os.environ["DATABASE_URL"])
    # Optional: IBANs of the owner's own accounts at other banks, confirmed
    # by hand (a Wink send to a bare IBAN has no name for is_owner to check
    # -- see lib.expenses.close). Comma-separated, e.g.
    # "CR00000000000000000001,CR00000000000000000002".
    own_ibans = frozenset(
        iban.strip() for iban in os.environ.get("OWN_IBAN_ACCOUNTS", "").split(",") if iban.strip()
    )

    # On a normal night, read exactly yesterday. On the month's last day,
    # widen the read to the whole month so the month-end total comes from
    # this same pass -- no second mailbox read, no stored running total.
    range_start = month_start if is_month_end else yesterday

    reader = GmailReader(os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"])
    movements = []
    owner = ""
    stats = {"emails": 0, "debits": 0, "sends": 0, "bac": 0, "wink_txn": 0, "wink_bill": 0}
    try:
        # BN's statement email isn't sent the day of the transaction -- it's
        # monthly, on BN's own schedule -- so "SINCE range_start" would almost
        # always miss it even though it lists the debits in range. Search
        # back far enough to always catch the latest statement, then keep
        # only the lines that actually fall in [range_start, yesterday].
        bn_since = range_start - timedelta(days=35)
        for item in reader.items(bn_since, skip=set(), wanted=[GmailReader.DEFAULT_WANTED[0]]):
            stats["emails"] += 1
            for pdf in item.pdfs or []:
                stmt_owner, all_debits = parse_bn_statement_debits(pdf_text(pdf))
                owner = owner or stmt_owner
                debits = [d for d in all_debits if range_start <= d.occurred_on <= yesterday]
                stats["debits"] += len(debits)
                movements += debits

        # Wink and BAC notify immediately, same day as the transaction.
        same_day_wanted = [
            GmailReader.DEFAULT_WANTED[1],
            GmailReader.EXPENSE_WANTED[2],
            GmailReader.EXPENSE_WANTED[3],
            GmailReader.EXPENSE_WANTED[4],
        ]
        for item in reader.items(range_start, skip=set(), wanted=same_day_wanted):
            stats["emails"] += 1
            if item.kind == "wink_send":
                send = parse_wink_send(item.text or "", item.message_id)
                if send and range_start <= send.occurred_on <= yesterday:
                    stats["sends"] += 1
                    movements.append(send)
            elif item.kind == "wink_transaction":
                wink_txn = parse_wink_transaction(item.text or "", item.message_id)
                if wink_txn and range_start <= wink_txn.occurred_on <= yesterday:
                    stats["wink_txn"] += 1
                    movements.append(wink_txn)
            elif item.kind == "wink_bill_payment":
                bill = parse_wink_bill_payment(item.text or "", item.message_id)
                if bill and range_start <= bill.occurred_on <= yesterday:
                    stats["wink_bill"] += 1
                    movements.append(bill)
            elif item.kind == "bac_transaction":
                txn = parse_bac_transaction(item.html or "", item.message_id)
                if txn and range_start <= txn.occurred_on <= yesterday:
                    stats["bac"] += 1
                    movements.append(txn)
    finally:
        reader.close()

    daily_report = close(
        [m for m in movements if m.occurred_on == yesterday], business_refs, owner, own_ibans
    )
    message = format_message(daily_report)
    print(
        f"{yesterday}: {stats} -> total {daily_report.total} ({len(daily_report.days)} day(s), "
        f"{daily_report.excluded_business_count} business, "
        f"{daily_report.excluded_self_transfer_count} self-transfer movement(s) excluded)"
    )

    month_message = None
    if is_month_end:
        month_report = close(movements, business_refs, owner, own_ibans)
        month_message = format_month_total(month_report, month_start)
        print(f"{month_start:%Y-%m} (month end): total {month_report.total}")

    if dry_run:
        print(message)
        if month_message:
            print(month_message)
        return 0

    telegram.send(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"], message)
    if month_message:
        telegram.send(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"], month_message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
