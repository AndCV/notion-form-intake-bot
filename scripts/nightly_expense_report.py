"""Closes yesterday's personal spend and sends it over Telegram.

    python scripts/nightly_expense_report.py            # read, close, send
    python scripts/nightly_expense_report.py --dry-run  # read and print only; send nothing

Runs nightly from .github/workflows/expense_report.yml, shortly after
midnight (TIMEZONE, default America/Costa_Rica). Reads BN statement debits,
Wink sends and BAC transactions (lib/payment_mail.py) for the day that just
ended, excludes Wink sends already recorded as collaborator payouts in
payment_events (read-only -- see lib/expenses.py), and messages the total
to a Telegram bot (lib/telegram.py) -- not WhatsApp: Meta's Cloud API needs
a Business Account, and CallMeBot (the free workaround) closed registration.

Stateless by design: each run re-reads exactly yesterday's emails (IMAP
SINCE + a same-day filter) rather than tracking a processed-message table,
so there's nothing to replay or dedupe across runs -- a closing is naturally
scoped to one day.

Prints totals only, never merchant/recipient names: its output lands in
public CI logs.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from lib import telegram
from lib.db import connect
from lib.expenses import close, format_message
from lib.payment_mail import (
    GmailReader,
    parse_bac_transaction,
    parse_bn_statement_debits,
    parse_wink_send,
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
    yesterday = datetime.now(tz).date() - timedelta(days=1)

    business_refs = _business_wink_refs(os.environ["DATABASE_URL"])

    reader = GmailReader(os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"])
    movements = []
    stats = {"emails": 0, "debits": 0, "sends": 0, "bac": 0}
    try:
        # BN's statement email isn't sent the day of the transaction -- it's
        # monthly, on BN's own schedule -- so "SINCE yesterday" would almost
        # always miss it even though it lists yesterday's debit. Search back
        # far enough to always catch the latest statement, then keep only
        # the lines that actually fall on yesterday.
        bn_since = yesterday - timedelta(days=35)
        for item in reader.items(bn_since, skip=set(), wanted=[GmailReader.DEFAULT_WANTED[0]]):
            stats["emails"] += 1
            for pdf in item.pdfs or []:
                debits = [
                    d for d in parse_bn_statement_debits(pdf_text(pdf)) if d.occurred_on == yesterday
                ]
                stats["debits"] += len(debits)
                movements += debits

        # Wink and BAC notify immediately, same day as the transaction.
        same_day_wanted = [GmailReader.DEFAULT_WANTED[1], GmailReader.EXPENSE_WANTED[2]]
        for item in reader.items(yesterday, skip=set(), wanted=same_day_wanted):
            stats["emails"] += 1
            if item.kind == "wink_send":
                send = parse_wink_send(item.text or "", item.message_id)
                if send and send.occurred_on == yesterday:
                    stats["sends"] += 1
                    movements.append(send)
            elif item.kind == "bac_transaction":
                txn = parse_bac_transaction(item.html or "", item.message_id)
                if txn and txn.occurred_on == yesterday:
                    stats["bac"] += 1
                    movements.append(txn)
    finally:
        reader.close()

    report = close(movements, business_refs)
    message = format_message(report)
    print(
        f"{yesterday}: {stats} -> total {report.total} ({len(report.days)} day(s), "
        f"{report.excluded_business_count} business movement(s) excluded)"
    )

    if dry_run:
        print(message)
        return 0

    telegram.send(
        os.environ["TELEGRAM_BOT_TOKEN"],
        os.environ["TELEGRAM_CHAT_ID"],
        message,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
