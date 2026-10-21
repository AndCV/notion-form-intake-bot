"""Reads new payment emails from Gmail and matches them to services.

    python scripts/sync_payments.py            # read, store, match
    python scripts/sync_payments.py --dry-run  # read and parse only; print totals, write nothing

Runs hourly from .github/workflows/payments.yml. Reads only Banco Nacional
statements and Wink transfer notifications (see lib/payment_mail.py), in
read-only mode, sent on or after PAYMENTS_SINCE (default: the go-live day).
Each email is parsed once (mail_processed); unmatched movements are
re-evaluated on every run.

Prints totals only, never names: its output lands in public CI logs.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from lib import payments
from lib.db import connect
from lib.payment_mail import GmailReader, parse_bn_statement, parse_wink_send, pdf_text

DEFAULT_SINCE = "2026-10-20"  # web app go-live: earlier services were settled in the workbook


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    since = date.fromisoformat(os.environ.get("PAYMENTS_SINCE") or DEFAULT_SINCE)
    for name in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"):
        if not os.environ.get(name):
            print(f"Missing environment variable {name}", file=sys.stderr)
            return 1

    reader = GmailReader(os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"])
    stats = {"emails": 0, "statements": 0, "credits": 0, "sends": 0, "new": 0, "unparsed": 0}
    try:
        with connect(autocommit=True) as conn:
            done = (
                set()
                if args.dry_run
                else {r[0] for r in conn.execute("SELECT message_id FROM mail_processed").fetchall()}
            )
            for item in reader.items(since, done):
                stats["emails"] += 1
                movements, owner = [], ""
                if item.kind == "bn_statement":
                    for pdf in item.pdfs or []:
                        statement = parse_bn_statement(pdf_text(pdf))
                        owner = owner or statement.owner
                        stats["statements"] += 1
                        stats["credits"] += len(statement.credits)
                        stats["unparsed"] += statement.unparsed_credit_lines
                        movements += statement.credits
                else:
                    send = parse_wink_send(item.text or "", item.message_id)
                    if send:
                        stats["sends"] += 1
                        movements.append(send)
                if args.dry_run:
                    continue
                with conn.transaction():
                    for movement in movements:
                        stats["new"] += payments.ingest(conn, movement, owner) is not None
                    conn.execute(
                        "INSERT INTO mail_processed (message_id, kind) VALUES (%s, %s) "
                        "ON CONFLICT DO NOTHING",
                        (item.message_id, item.kind),
                    )
            if args.dry_run:
                print(f"dry run since {since}: {stats}")
                return 0
            result = payments.rematch(conn)
            summary = f"{stats} -> {result}"
            conn.execute("INSERT INTO sync_runs (summary) VALUES (%s)", (summary,))
            print(f"since {since}: {summary}")
    finally:
        reader.close()
    if stats["unparsed"]:
        print(f"WARNING: {stats['unparsed']} statement credit lines couldn't be read", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
