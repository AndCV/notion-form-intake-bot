"""Applies db/schema.sql to DATABASE_URL and optionally seeds data.

    python scripts/migrate.py
    python scripts/migrate.py --seed-collaborator "Name" name@example.com
    python scripts/migrate.py --seed-invoice-counter 123

Safe to run on every deploy: the schema is all IF NOT EXISTS, and the
receipt counter is only created if missing.

--seed-invoice-counter belongs to the Notion cutover (issue #1, edge rule 1):
run it only after the Notion poller is disabled, with the last invoice number
Notion handed out. It refuses to run twice, so the two systems can never hand
out the same number.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from lib.db import connect

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--seed-collaborator", nargs=2, metavar=("NAME", "EMAIL"))
    parser.add_argument("--seed-invoice-counter", type=int, metavar="LAST_NUMBER")
    args = parser.parse_args()

    with connect() as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO counters (name, last_value) VALUES ('receipt', 0) ON CONFLICT DO NOTHING"
        )
        print("Schema applied.")

        if args.seed_collaborator:
            name, email = args.seed_collaborator
            row = conn.execute(
                "INSERT INTO collaborators (name, email) VALUES (%s, %s) "
                "ON CONFLICT (email) DO NOTHING RETURNING id",
                (name, email.strip().lower()),
            ).fetchone()
            print(f"Collaborator {'added' if row else 'already present'}: {name}")

        if args.seed_invoice_counter is not None:
            if args.seed_invoice_counter < 0:
                print("Invoice counter must be >= 0.", file=sys.stderr)
                return 1
            row = conn.execute(
                "INSERT INTO counters (name, last_value) VALUES ('invoice', %s) "
                "ON CONFLICT DO NOTHING RETURNING last_value",
                (args.seed_invoice_counter,),
            ).fetchone()
            if row is None:
                conn.rollback()
                print("Invoice counter already seeded; nothing changed.", file=sys.stderr)
                return 1
            print(f"Invoice counter seeded at {row[0]}; next invoice is {row[0] + 1}.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
