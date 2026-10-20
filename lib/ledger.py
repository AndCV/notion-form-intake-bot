"""Earnings summary and CSV export, computed from the services table.

Rebuilds what the workbook's "Resumen de Ganancias" and "Balance y
Resultados" sheets computed with formulas, and writes a CSV in the same
layout as the workbook's export (Ganancias_<year>_completo.csv), so it opens
in Excel exactly like before.

Pure functions over plain dicts (rows from lib.db.list_services), so they're
tested without a database.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Iterable

MONTH_NAMES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Setiembre", "Octubre", "Noviembre", "Diciembre",
]  # fmt: skip
CUATRIMESTRES = ("I", "II", "III")

# Same rule as the workbook's Resumen sheet, which counted the Trabajo column
# with COUNTIF "*clas*" / "*tutor*" / "*admis*" (case-insensitive).
_CLASS_RE = re.compile(r"clas|tutor|admis", re.IGNORECASE)
_WIDTH = 10  # columns per CSV row, like the workbook export


def cuatrimestre(month: int) -> str:
    return CUATRIMESTRES[(month - 1) // 4]


def sheet_name(month: int) -> str:
    return f"{cuatrimestre(month)} Cuatrimestre"


def is_class(job: str) -> bool:
    return bool(_CLASS_RE.search(job or ""))


def client_key(name: str) -> str:
    """Groups spellings of the same client: case, accents and spaces."""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().split())


def ratio(numerator: Decimal, denominator: Decimal) -> float | None:
    """None when the denominator is 0 (shown as "—")."""
    return float(numerator) / float(denominator) if denominator else None


@dataclass
class Period:
    label: str
    cuatrimestre: str
    count: int = 0
    classes: int = 0
    price: Decimal = Decimal(0)
    cost: Decimal = Decimal(0)
    profit: Decimal = Decimal(0)
    months: int = 0
    number: int | None = None  # month number, for month periods

    @property
    def others(self) -> int:
        return self.count - self.classes

    @property
    def margin(self) -> float | None:
        return ratio(self.profit, self.price)

    @property
    def avg_profit(self) -> float | None:
        return ratio(self.profit, Decimal(self.count))

    def add(self, row: dict[str, Any]) -> None:
        self.count += 1
        self.classes += is_class(row["job"])
        self.price += Decimal(row["price"])
        self.cost += Decimal(row["collaborator_pay"])
        self.profit += Decimal(row["profit"])


@dataclass
class Client:
    name: str
    count: int = 0
    price: Decimal = Decimal(0)
    profit: Decimal = Decimal(0)


@dataclass
class Summary:
    year: int
    months: list[Period]  # only months that have services, in order
    cuatrimestres: list[Period]
    total: Period
    clients: list[Client] = field(default_factory=list)

    def month_growth(self) -> list[float | None]:
        """Profit change vs the previous listed month; None for the first."""
        out: list[float | None] = []
        for i, m in enumerate(self.months):
            prev = self.months[i - 1].profit if i else None
            out.append(None if prev is None else ratio(m.profit - prev, prev))
        return out


def summarize(rows: Iterable[dict[str, Any]], year: int) -> Summary:
    by_month: dict[int, Period] = {}
    by_cuatri = {c: Period(f"{c} Cuatrimestre", c) for c in CUATRIMESTRES}
    total = Period("TOTAL", " + ".join(CUATRIMESTRES))
    clients: dict[str, Client] = {}

    for row in rows:
        month = row["service_date"].month
        period = by_month.setdefault(
            month, Period(MONTH_NAMES[month - 1], cuatrimestre(month), number=month)
        )
        period.add(row)
        by_cuatri[cuatrimestre(month)].add(row)
        total.add(row)
        client = clients.setdefault(client_key(row["client_name"]), Client(row["client_name"].strip()))
        client.count += 1
        client.price += Decimal(row["price"])
        client.profit += Decimal(row["profit"])

    months = [by_month[m] for m in sorted(by_month)]
    for m in sorted(by_month):
        by_cuatri[cuatrimestre(m)].months += 1
    total.months = len(months)
    used = [p for p in by_cuatri.values() if p.months]
    total.cuatrimestre = " + ".join(p.cuatrimestre for p in used) or "—"
    recurring = sorted(
        (c for c in clients.values() if c.count >= 2), key=lambda c: (-c.profit, c.name)
    )
    return Summary(year, months, used, total, recurring)


# --- CSV export -----------------------------------------------------------------


def _num(value: Decimal | float | int | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return repr(value)
    value = Decimal(value)
    return str(int(value)) if value == value.to_integral_value() else str(value)


def _row(sheet: str, *cells: Any) -> list[str]:
    out = [sheet] + ["" if c is None else str(c) for c in cells]
    return out + [""] * (_WIDTH - len(out))


def export_csv(rows: list[dict[str, Any]], year: int) -> str:
    """CSV in the workbook export's layout: one month block per month with
    services (header, rows by date, Total), then the two summary sheets.

    Rows are ordered by date (the workbook kept typing order) and every month
    gets a Total row, the open month included.
    """
    out: list[list[str]] = []
    by_month: dict[int, list[dict[str, Any]]] = {}
    for row in sorted(rows, key=lambda r: (r["service_date"], r["id"])):
        by_month.setdefault(row["service_date"].month, []).append(row)

    for month, items in sorted(by_month.items()):
        sheet = sheet_name(month)
        out.append(_row(sheet, "", MONTH_NAMES[month - 1]))
        out.append(_row(sheet, "Fecha", "Nombre", "Trabajo", "Precio", "Ganancia"))
        for r in items:
            # Column 7 held free-text notes in the workbook; on app rows
            # `notes` is the invoice line instead, so it stays out.
            note = r["notes"] if r.get("source") == "import" else None
            out.append(_row(sheet, r["service_date"].isoformat(), r["client_name"], r["job"],
                            _num(r["price"]), _num(r["profit"]), note))  # fmt: skip
        price = sum((Decimal(r["price"]) for r in items), Decimal(0))
        profit = sum((Decimal(r["profit"]) for r in items), Decimal(0))
        out.append(_row(sheet, "", "", "Total", _num(price), _num(profit)))

    summary = summarize(rows, year)
    out += _resumen_rows(summary)
    out += _balance_rows(summary)

    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(out)
    # BOM so Excel opens it as UTF-8 (accents, ₡).
    return "﻿" + buffer.getvalue()


def _resumen_rows(s: Summary) -> list[list[str]]:
    sh = "Resumen de Ganancias"
    rows = [
        _row(sh, f"Resumen de Ganancias {s.year}"),
        _row(sh, "Consolidado del año a la fecha — montos en colones (₡)"),
        _row(sh, "RESUMEN MENSUAL"),
        _row(sh, "Mes", "Cuatrimestre", "Servicios", "Clases / tutorías", "Trabajos y otros",
             "Precio facturado", "Ganancia neta", "Margen", "Ganancia prom. por servicio"),
    ]  # fmt: skip
    for p in s.months + [s.total]:
        rows.append(_row(sh, p.label, p.cuatrimestre, p.count, p.classes, p.others,
                         _num(p.price), _num(p.profit), _num(p.margin), _num(p.avg_profit)))  # fmt: skip

    if s.months:
        best = max(s.months, key=lambda p: p.profit)
        worst = min(s.months, key=lambda p: p.profit)
        busiest = max(s.months, key=lambda p: p.count)
        most_classes = max(s.months, key=lambda p: p.classes)
        best_margin = max(s.months, key=lambda p: p.margin or 0)
        rows += [
            _row(sh, "MÉTRICAS CLAVE"),
            _row(sh, "Indicador", "", "Valor", "", "Detalle"),
            _row(sh, "Servicios realizados", "", s.total.count),
            _row(sh, "Total facturado", "", _num(s.total.price)),
            _row(sh, "Ganancia neta total", "", _num(s.total.profit)),
            _row(sh, "Costo / pago a terceros", "", _num(s.total.cost)),
            _row(sh, "Margen de ganancia global", "", _num(s.total.margin)),
            _row(sh, "Ganancia promedio por servicio", "", _num(s.total.avg_profit)),
            _row(sh, "Ganancia promedio mensual", "", _num(ratio(s.total.profit, Decimal(len(s.months))))),
            _row(sh, "Mes con más servicios", "", busiest.label, "", f"{busiest.count} servicios"),
            _row(sh, "Mes con mayor ganancia", "", best.label, "", _num(best.profit)),
            _row(sh, "Mes con menor ganancia", "", worst.label, "", _num(worst.profit)),
            _row(sh, "Mes con más clases / tutorías", "", most_classes.label, "", f"{most_classes.classes} clases"),
            _row(sh, "Mes con mejor margen", "", best_margin.label, "", _num(best_margin.margin)),
        ]  # fmt: skip
        for c in s.cuatrimestres:
            rows.append(_row(sh, f"Ganancia {c.label}", "", _num(c.profit), "",
                             _num(ratio(c.profit, s.total.profit))))  # fmt: skip
        rows.append(_row(sh, "Peso de clases / tutorías sobre servicios", "",
                         _num(ratio(Decimal(s.total.classes), Decimal(s.total.count)))))  # fmt: skip

    rows += [
        _row(sh, "CLIENTES RECURRENTES (ordenados por ganancia)"),
        _row(sh, "Cliente", "Agrupado como", "Servicios", "Facturado", "Ganancia", "% de la ganancia total"),
    ]
    for c in s.clients:
        rows.append(_row(sh, c.name, client_key(c.name), c.count, _num(c.price), _num(c.profit),
                         _num(ratio(c.profit, s.total.profit))))  # fmt: skip
    return rows


def _balance_rows(s: Summary) -> list[list[str]]:
    sh = "Balance y Resultados"
    rows = [
        _row(sh, f"Balance y Análisis de Resultados {s.year}"),
        _row(sh, "Servicios académicos — montos en colones (₡). Fuente: servicios registrados."),
        _row(sh, "1. CUADRO DE BALANCE"),
        _row(sh, "Mes", "Cuatrimestre", "Servicios", "Ingresos facturados (₡)",
             "Costos / pagos a terceros (₡)", "Ganancia neta (₡)", "Margen neto", "Var. % ganancia"),
    ]  # fmt: skip
    for p, growth in zip(s.months, s.month_growth()):
        rows.append(_row(sh, p.label, p.cuatrimestre, p.count, _num(p.price), _num(p.cost),
                         _num(p.profit), _num(p.margin), _num(growth)))  # fmt: skip
    t = s.total
    rows.append(_row(sh, "TOTAL AÑO A LA FECHA", t.cuatrimestre, t.count, _num(t.price), _num(t.cost),
                     _num(t.profit), _num(t.margin), "—"))  # fmt: skip

    rows += [
        _row(sh, "2. ANÁLISIS DE RESULTADOS"),
        _row(sh, "Indicador", "", "", "Valor", "Detalle"),
        _row(sh, "Ingresos facturados", "", "", _num(t.price), "Total cobrado a clientes en el año a la fecha"),
        _row(sh, "Costos / pagos a terceros", "", "", _num(t.cost), "Pagos a colaboradores"),
        _row(sh, "Ganancia neta", "", "", _num(t.profit), "Ingresos menos pagos a terceros"),
        _row(sh, "Margen neto global", "", "", _num(t.margin), "Ganancia neta / ingresos facturados"),
        _row(sh, "Servicios realizados", "", "", t.count, "Cantidad de trabajos, clases y tutorías registrados"),
        _row(sh, "Ingreso promedio por servicio", "", "", _num(ratio(t.price, Decimal(t.count))), "Precio medio de cada servicio"),
        _row(sh, "Ganancia promedio por servicio", "", "", _num(t.avg_profit), "Lo que realmente queda por cada servicio"),
        _row(sh, "Comparativo por cuatrimestre"),
        _row(sh, "Periodo", "Meses", "Servicios", "Ingresos (₡)", "Costos (₡)", "Ganancia (₡)", "Margen", "% de la ganancia"),
    ]  # fmt: skip
    for c in s.cuatrimestres + [t]:
        rows.append(_row(sh, c.label, c.months, c.count, _num(c.price), _num(c.cost), _num(c.profit),
                         _num(c.margin), _num(ratio(c.profit, t.profit))))  # fmt: skip
    return rows


def year_range(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year + 1, 1, 1)
