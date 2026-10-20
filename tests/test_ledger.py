import csv
import io
import os
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from lib.ledger import client_key, export_csv, is_class, summarize
from lib.workbook_csv import parse_workbook_csv


def _row(i, d, client, job, price, pay=0, notes=None, source="app"):
    price, pay = Decimal(price), Decimal(pay)
    return dict(id=i, service_date=d, client_name=client, job=job, price=price,
                collaborator_pay=pay, profit=price - pay, notes=notes, source=source)  # fmt: skip


ROWS = [
    _row(1, date(2026, 1, 5), "Cliente A", "Clases", 10000),
    _row(2, date(2026, 1, 9), "cliente a ", "Trabajo", 25000, 20000, notes="Debe 5000", source="import"),
    _row(3, date(2026, 5, 2), "Cliente B", "Tutoría Java", 8000),
    _row(4, date(2026, 5, 1), "Clientè B", "Clases de admisión", 15000, 5000, notes="Detalle factura"),
    _row(5, date(2026, 10, 3), "Cliente C", "Programación", 12000),
]


def test_class_rule_matches_the_workbook_countif():
    """protects: "Clases / tutorías" uses the workbook's *clas*/*tutor*/*admis* rule;
    fails_when: a class is counted as work or twice;
    no_claim: verified against the real workbook in test_matches_real_workbook_resumen."""
    assert [is_class(j) for j in ("Clases", "CLase", "Tutoria", "Admisión", "Clases de admisión")] == [True] * 5
    assert not is_class("Programación") and not is_class("TFG")


def test_summary_by_month_cuatrimestre_and_total():
    """protects: per-period counts, sums, cost, margin and average profit;
    fails_when: a row lands in the wrong month/cuatrimestre or a sum is off;
    no_claim: none beyond protects."""
    s = summarize(ROWS, 2026)

    assert [(p.label, p.number, p.count, p.classes, int(p.price), int(p.profit)) for p in s.months] == [
        ("Enero", 1, 2, 1, 35000, 15000),
        ("Mayo", 5, 2, 2, 23000, 18000),
        ("Octubre", 10, 1, 0, 12000, 12000),
    ]
    assert [(c.label, c.months, int(c.profit)) for c in s.cuatrimestres] == [
        ("I Cuatrimestre", 1, 15000), ("II Cuatrimestre", 1, 18000), ("III Cuatrimestre", 1, 12000),
    ]  # fmt: skip
    t = s.total
    assert (t.count, int(t.price), int(t.cost), int(t.profit)) == (5, 70000, 25000, 45000)
    assert t.margin == pytest.approx(45000 / 70000) and t.avg_profit == 9000
    assert s.month_growth() == [None, pytest.approx(3000 / 15000), pytest.approx(-6000 / 18000)]


def test_zero_denominators_give_none_not_errors():
    """protects: edge rule 9 -- margin/growth with a 0 denominator show "—";
    fails_when: ZeroDivisionError or a made-up number;
    no_claim: none beyond protects."""
    rows = [_row(1, date(2026, 1, 1), "A", "Clases", 100, 100), _row(2, date(2026, 2, 1), "A", "Clases", 100)]
    s = summarize(rows, 2026)

    assert s.months[0].margin == 0.0 and s.month_growth()[1] is None
    assert summarize([], 2026).total.margin is None


def test_clients_grouped_ignoring_case_accents_and_spaces():
    """protects: recurring clients merge "Cliente A"/"cliente a "/"Clientè B"/"Cliente B";
    fails_when: spellings split, or one-off clients are listed;
    no_claim: different spellings (extra letters) still split -- documented in the page."""
    s = summarize(ROWS, 2026)

    assert client_key(" Clientè  B ") == "cliente b"
    assert [(c.count, int(c.profit)) for c in s.clients] == [(2, 18000), (2, 15000)]


def test_export_round_trips_through_the_workbook_reader():
    """protects: criterion 14 layout -- the export reads back as the same month blocks,
    rows and Total rows, with notes only on imported rows;
    fails_when: a row, block or total is lost or the layout drifts;
    no_claim: the summary sections' wording is not compared to the workbook."""
    text = export_csv(ROWS, 2026)

    assert text.startswith("﻿")
    blocks = parse_workbook_csv(text, 2026)
    assert [(b.sheet, b.month_label, len(b.rows), b.total) for b in blocks] == [
        ("I Cuatrimestre", "Enero", 2, (Decimal(35000), Decimal(15000))),
        ("II Cuatrimestre", "Mayo", 2, (Decimal(23000), Decimal(18000))),
        ("III Cuatrimestre", "Octubre", 1, (Decimal(12000), Decimal(12000))),
    ]
    assert [r.service_date.day for r in blocks[1].rows] == [1, 2]  # ordered by date
    assert blocks[0].rows[1].note == "Debe 5000" and blocks[1].rows[0].note is None
    lines = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    assert {len(line) for line in lines} == {10}
    assert any(line[:2] == ["Resumen de Ganancias", "TOTAL"] for line in lines)
    assert any(line[:2] == ["Balance y Resultados", "TOTAL AÑO A LA FECHA"] for line in lines)


# --- against the real workbook (local only) -------------------------------------
# The workbook holds real client data and is never committed. Point
# LEDGER_CSV at its export to run these:
#   LEDGER_CSV=~/Downloads/Ganancias_2026_completo.csv pytest tests/test_ledger.py

REAL = os.environ.get("LEDGER_CSV")
real = pytest.mark.skipif(not REAL, reason="LEDGER_CSV not set (real workbook stays local)")


def _real_rows():
    text = Path(REAL).expanduser().read_text(encoding="utf-8")
    blocks = parse_workbook_csv(text, 2026)
    rows = [
        _row(i, r.service_date, r.client_name, r.job, r.price, r.collaborator_pay, r.note, "import")
        for i, r in enumerate(r for b in blocks for r in b.rows)
    ]
    return text, blocks, rows


@real
def test_matches_real_workbook_resumen():
    """protects: criterion 13 -- Jan-Aug and TOTAL equal the workbook's Resumen sheet
    (services, classes, works, billed, profit, margin);
    fails_when: any month or the total differs from the Excel formulas;
    no_claim: Sep/Oct had no Resumen rows in the workbook to compare against."""
    text, _, rows = _real_rows()
    s = summarize([r for r in rows if r["service_date"].month <= 8], 2026)
    excel = {
        line[1]: line
        for line in csv.reader(io.StringIO(text.lstrip("﻿")))
        if line[0] == "Resumen de Ganancias" and len(line) > 9 and line[3].isdigit()
    }

    for p in s.months + [s.total]:
        e = excel[p.label]
        assert (p.count, p.classes, p.others, int(p.price), int(p.profit)) == tuple(int(x) for x in e[3:8]), p.label
        assert p.margin == pytest.approx(float(e[8])), p.label


@real
def test_real_export_keeps_every_row_and_month_total():
    """protects: criterion 14 -- exporting the imported data gives back every row
    (as a multiset per month) and the workbook's Total rows;
    fails_when: a row or a month total is lost or changed;
    no_claim: row order differs on purpose (by date, not typing order)."""
    _, source_blocks, rows = _real_rows()
    exported = {b.month: b for b in parse_workbook_csv(export_csv(rows, 2026), 2026)}

    def key(r):
        return (r.service_date, r.client_name, r.job, r.price, r.profit, r.note)

    for b in source_blocks:
        assert Counter(map(key, exported[b.month].rows)) == Counter(map(key, b.rows)), b.month_label
        if b.total is not None:
            assert exported[b.month].total == b.total, b.month_label
