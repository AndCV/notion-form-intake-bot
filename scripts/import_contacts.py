"""Loads client emails into client_contacts, so the service form fills in a
client's email from their name.

    python scripts/import_contacts.py "~/Downloads/.../Solicitudes ACV ..._all.csv"           # report only
    python scripts/import_contacts.py "~/Downloads/.../Solicitudes ACV ..._all.csv" --apply   # store

Sources: the Notion form export (columns Nombre, Correo, Creado) and the
emails already saved on services. For a client that appears more than once
the most recent request wins, and an entry newer than the export (a service
created in the app) is never overwritten. Safe to run again.

Prints counts only, never names or emails: the repo is public.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from lib.db import connect, remember_contact
from lib.ledger import client_key

_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split()
    )
}
_CREATED_RE = re.compile(r"(\d{1,2}) de (\w+) de (\d{4})(?: (\d{1,2}):(\d{2}))?")
_EMAIL_RE = re.compile(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+")


def parse_created(text: str, timezone: str) -> datetime | None:
    """'26 de julio de 2026 18:57' -> aware datetime; None if unreadable."""
    m = _CREATED_RE.search(text.lower())
    if not m or m.group(2) not in _MONTHS:
        return None
    day, _, year, hour, minute = m.groups()
    return datetime(
        int(year), _MONTHS[m.group(2)], int(day), int(hour or 0), int(minute or 0),
        tzinfo=ZoneInfo(timezone),
    )


def notion_contacts(path: Path, timezone: str) -> list[tuple[str, list[str], datetime | None]]:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    out = []
    for row in rows:
        name = (row.get("Nombre") or "").strip()
        emails = [e.lower() for e in _EMAIL_RE.findall(row.get("Correo") or "")]
        if client_key(name) and emails:
            out.append((name, emails, parse_created(row.get("Creado") or "", timezone)))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    load_dotenv()

    timezone = os.environ.get("TIMEZONE", "America/Costa_Rica")
    contacts = notion_contacts(args.csv.expanduser(), timezone)
    clients = {client_key(name) for name, _, _ in contacts}
    undated = sum(1 for _, _, at in contacts if at is None)
    print(f"Notion: {len(contacts)} requests with email, {len(clients)} clients, {undated} undated")
    if not args.apply:
        print("Report only; rerun with --apply to store them.")
        return 0

    epoch = datetime(2000, 1, 1, tzinfo=ZoneInfo(timezone))
    with connect() as conn:
        # Oldest first, so the latest request for each client ends up stored.
        for name, emails, at in sorted(contacts, key=lambda c: c[2] or epoch):
            remember_contact(conn, name, emails, at or epoch)
        services = conn.execute(
            """
            SELECT s.client_name, s.created_at, array_agg(e.email ORDER BY e.email)
            FROM services s JOIN service_emails e ON e.service_id = s.id
            WHERE s.deleted_at IS NULL
            GROUP BY s.id ORDER BY s.created_at
            """
        ).fetchall()
        for name, created_at, emails in services:
            remember_contact(conn, name, list(emails), created_at)
        total = conn.execute("SELECT count(*) FROM client_contacts").fetchone()[0]
    print(f"Services with email: {len(services)}. Contacts stored: {total}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
