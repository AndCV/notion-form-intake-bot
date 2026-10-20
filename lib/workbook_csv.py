"""Reads the earnings workbook's CSV export (Ganancias_<year>_completo.csv).

The export flattens every sheet into one CSV whose first column is the sheet
name. Only the cuatrimestre sheets hold services; each is a run of month
blocks laid out as:

    <sheet>,,<Month>,,,          month header
    <sheet>,Fecha,Nombre,Trabajo,Precio,Ganancia
    <sheet>,<YYYY-MM-DD>,<name>,<job>,<price>,<profit>[,<free-text note>]
    ...
    <sheet>,,,Total,<sum price>,<sum profit>     (missing on the open month)

The summary sheets ("Resumen de Ganancias", "Balance y Resultados") are
derived data and are ignored here.

The file holds real client names and revenue: it is never committed (the
repo is public). Tests use synthetic fixtures.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "setiembre": 9,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


class WorkbookFormatError(ValueError):
    pass


@dataclass
class ServiceRow:
    service_date: date
    client_name: str
    job: str
    price: Decimal
    profit: Decimal
    note: str | None
    # Set when the row's own date disagreed with the month block it sits in
    # and was moved into that block (a typo in the workbook).
    original_date: date | None = None

    @property
    def collaborator_pay(self) -> Decimal:
        return self.price - self.profit


@dataclass
class MonthBlock:
    sheet: str
    month: int
    month_label: str
    rows: list[ServiceRow] = field(default_factory=list)
    # (price, profit) from the block's Total row, or None for the open month.
    total: tuple[Decimal, Decimal] | None = None

    def sums(self) -> tuple[Decimal, Decimal]:
        return (
            sum((r.price for r in self.rows), Decimal(0)),
            sum((r.profit for r in self.rows), Decimal(0)),
        )


def _amount(raw: str, where: str) -> Decimal:
    try:
        return Decimal(raw.strip())
    except Exception as exc:
        raise WorkbookFormatError(f"{where}: not a number: {raw!r}") from exc


def parse_workbook_csv(text: str, year: int) -> list[MonthBlock]:
    """Parses the cuatrimestre sheets into month blocks, in file order.

    A row whose date falls outside its block's year/month is moved into the
    block (same day) and keeps the original in `original_date`.
    """
    blocks: list[MonthBlock] = []
    current: MonthBlock | None = None

    for line_no, cells in enumerate(csv.reader(io.StringIO(text.lstrip("﻿"))), start=1):
        cells = cells + [""] * (7 - len(cells))
        sheet, c_date, c_name, c_job, c_price, c_profit, c_note = (c for c in cells[:7])
        if "Cuatrimestre" not in sheet:
            continue
        where = f"line {line_no}"

        if not c_date and c_name and not c_job:
            month = MONTHS.get(c_name.strip().lower())
            if month is None:
                raise WorkbookFormatError(f"{where}: unknown month header {c_name!r}")
            current = MonthBlock(sheet=sheet.strip(), month=month, month_label=c_name.strip())
            blocks.append(current)
            continue

        if c_date == "Fecha" or not any(cells[1:]):
            continue

        if current is None:
            raise WorkbookFormatError(f"{where}: row before any month header")

        if c_job.strip() == "Total" and not c_date:
            current.total = (_amount(c_price, where), _amount(c_profit, where))
            continue

        try:
            row_date = date.fromisoformat(c_date.strip())
        except ValueError as exc:
            raise WorkbookFormatError(f"{where}: bad date {c_date!r}") from exc

        original = None
        if (row_date.year, row_date.month) != (year, current.month):
            original = row_date
            try:
                row_date = row_date.replace(year=year, month=current.month)
            except ValueError as exc:
                raise WorkbookFormatError(
                    f"{where}: {c_date} can't be moved into {current.month_label} {year}"
                ) from exc

        price = _amount(c_price, where)
        profit = _amount(c_profit, where)
        if price <= 0 or profit < 0 or profit > price:
            raise WorkbookFormatError(f"{where}: invalid price/profit {c_price}/{c_profit}")

        current.rows.append(
            ServiceRow(
                service_date=row_date,
                client_name=c_name,
                job=c_job,
                price=price,
                profit=profit,
                note=c_note.strip() or None,
                original_date=original,
            )
        )

    if not blocks:
        raise WorkbookFormatError("no cuatrimestre month blocks found")
    return blocks
