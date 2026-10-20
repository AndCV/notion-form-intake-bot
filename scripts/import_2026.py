"""Imports the 2026 earnings workbook (its CSV export) into Postgres.

    python scripts/import_2026.py ~/Downloads/Ganancias_2026_completo.csv           # report only
    python scripts/import_2026.py ~/Downloads/Ganancias_2026_completo.csv --apply   # insert

Without --apply it only prints a report: rows per month, each month's sums
against its Total row, and the dates it moved into their month block.
--apply inserts everything in one transaction, and refuses when:
  - any month's sums differ from its Total row,
  - the number of moved dates isn't EXPECTED_DATE_FIXES (the owner reviewed
    exactly those; anything else is a new typo to look at first),
  - 2026 imported rows already exist (so a second run can't duplicate them).

Imported rows get source='import', no emails, no kind, no collaborator
(the workbook never recorded who), and collaborator_pay = Precio - Ganancia.

The report prints aggregates only, never client names: the repo is public
and its output tends to get pasted around.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from lib.db import connect
from lib.workbook_csv import MonthBlock, WorkbookFormatError, parse_workbook_csv

YEAR = 2026
# Confirmed by the owner on 2026-10-20 (issue #1): two August rows typed
# with 2025 and one May row typed with March.
EXPECTED_DATE_FIXES = 3


def _report(blocks: list[MonthBlock]) -> tuple[bool, int, int]:
    """Prints the report; returns (totals_ok, row_count, date_fixes)."""
    totals_ok = True
    rows = fixes = 0
    print(f"{'Mes':<11} {'Filas':>5} {'Precio':>10} {'Ganancia':>10}  Total del CSV")
    for block in blocks:
        price, profit = block.sums()
        if block.total is None:
            check = "sin fila Total (mes abierto)"
        elif block.total == (price, profit):
            check = "coincide"
        else:
            totals_ok = False
            check = f"NO COINCIDE ({block.total[0]:.0f} / {block.total[1]:.0f})"
        print(f"{block.month_label:<11} {len(block.rows):>5} {price:>10.0f} {profit:>10.0f}  {check}")
        rows += len(block.rows)
        for row in block.rows:
            if row.original_date:
                fixes += 1
                print(f"  fecha corregida: {row.original_date} -> {row.service_date}")
    print(f"Total filas: {rows} | fechas corregidas: {fixes} (esperadas {EXPECTED_DATE_FIXES})")
    return totals_ok, rows, fixes


def _insert(blocks: list[MonthBlock]) -> int:
    with connect() as conn:
        existing = conn.execute(
            "SELECT count(*) FROM services WHERE source = 'import' "
            "AND service_date >= %s AND service_date < %s",
            (f"{YEAR}-01-01", f"{YEAR + 1}-01-01"),
        ).fetchone()[0]
        if existing:
            raise SystemExit(f"Ya hay {existing} filas importadas de {YEAR}; no se insertó nada.")

        inserted = 0
        with conn.cursor() as cur:
            for block in blocks:
                for row in block.rows:
                    cur.execute(
                        "INSERT INTO services (service_date, client_name, job, price, "
                        "collaborator_pay, notes, source) VALUES (%s, %s, %s, %s, %s, %s, 'import')",
                        (
                            row.service_date,
                            row.client_name,
                            row.job,
                            row.price,
                            row.collaborator_pay,
                            row.note,
                        ),
                    )
                    inserted += 1
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    try:
        blocks = parse_workbook_csv(args.csv_path.read_text(encoding="utf-8"), YEAR)
    except WorkbookFormatError as exc:
        print(f"CSV inválido: {exc}", file=sys.stderr)
        return 1

    totals_ok, _, fixes = _report(blocks)
    if not args.apply:
        return 0 if totals_ok else 1

    if not totals_ok:
        print("Algún mes no coincide con su fila Total; no se insertó nada.", file=sys.stderr)
        return 1
    if fixes != EXPECTED_DATE_FIXES:
        print(
            f"Se corrigieron {fixes} fechas y se esperaban {EXPECTED_DATE_FIXES}; "
            "revisa el CSV. No se insertó nada.",
            file=sys.stderr,
        )
        return 1

    print(f"Insertadas {_insert(blocks)} filas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
