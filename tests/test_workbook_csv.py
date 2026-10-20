from datetime import date
from decimal import Decimal

import pytest

from lib.workbook_csv import WorkbookFormatError, parse_workbook_csv

# Synthetic data only: the real workbook holds client names and revenue and
# never enters the repo.
FIXTURE = """﻿I Cuatrimestre,,Enero,,,,,,,
I Cuatrimestre,Fecha,Nombre,Trabajo,Precio,Ganancia,,,,
I Cuatrimestre,2026-01-05,Cliente A,Clases,10000,10000,,,,
I Cuatrimestre,2026-01-09,Cliente B ,Trabajo,25000,5000,Debe 5000,,,
I Cuatrimestre,,,Total,35000,15000,,,,
II Cuatrimestre,,Mayo,,,,,,,
II Cuatrimestre,Fecha,Nombre,Trabajo,Precio,Ganancia,,,,
II Cuatrimestre,2026-03-26,Cliente C,Estadística,15000,15000,,,,
II Cuatrimestre,2026-05-31,Cliente D,Pago final,8500,0,,,,
II Cuatrimestre,,,Total,23500,15000,,,,
III Cuatrimestre,,Octubre,,,,,,,
III Cuatrimestre,Fecha,Nombre,Trabajo,Precio,Ganancia,,,,
III Cuatrimestre,2025-10-02,Cliente E,Trabajo,12000,9000,,,,
Resumen de Ganancias,Resumen de Ganancias 2026,,,,,,,,
Resumen de Ganancias,Mes,Cuatrimestre,Servicios,Clases / tutorías,Trabajos y otros,Precio facturado,Ganancia neta,Margen,
Resumen de Ganancias,Enero,I,2,1,1,35000,15000,0.42857142857142855,7500
"""


def test_parses_month_blocks_rows_and_totals_ignoring_summary_sheets():
    """protects: every service row lands in its month block with its amounts;
    fails_when: summary-sheet rows are read as services or a Total row is lost;
    no_claim: says nothing about the real workbook's layout beyond this fixture."""
    blocks = parse_workbook_csv(FIXTURE, 2026)

    assert [(b.month_label, b.month, len(b.rows)) for b in blocks] == [
        ("Enero", 1, 2),
        ("Mayo", 5, 2),
        ("Octubre", 10, 1),
    ]
    assert blocks[0].total == (Decimal(35000), Decimal(15000))
    assert blocks[0].sums() == blocks[0].total
    assert blocks[2].total is None


def test_keeps_raw_text_note_and_derives_collaborator_pay():
    """protects: client text is stored as typed and pay = Precio - Ganancia;
    fails_when: names get trimmed (export must reproduce them) or pay is wrong;
    no_claim: no database insert is exercised."""
    row = parse_workbook_csv(FIXTURE, 2026)[0].rows[1]

    assert row.client_name == "Cliente B "
    assert row.note == "Debe 5000"
    assert row.collaborator_pay == Decimal(20000)


def test_zero_profit_row_means_full_price_went_to_the_collaborator():
    """protects: a 0-profit row imports with pay equal to its price;
    fails_when: 0 profit is rejected or pay is computed as 0;
    no_claim: none beyond protects."""
    row = parse_workbook_csv(FIXTURE, 2026)[1].rows[1]

    assert row.profit == 0
    assert row.collaborator_pay == row.price


def test_dates_outside_their_block_move_into_it_and_keep_the_original():
    """protects: typo'd year/month dates are moved into their month block;
    fails_when: a row is filed under the wrong month or the original is lost;
    no_claim: does not check the real workbook's 3 expected fixes."""
    blocks = parse_workbook_csv(FIXTURE, 2026)

    may_fix = blocks[1].rows[0]
    assert may_fix.service_date == date(2026, 5, 26)
    assert may_fix.original_date == date(2026, 3, 26)
    oct_fix = blocks[2].rows[0]
    assert oct_fix.service_date == date(2026, 10, 2)
    assert oct_fix.original_date == date(2025, 10, 2)
    assert blocks[0].rows[0].original_date is None


@pytest.mark.parametrize(
    "bad_row",
    [
        "I Cuatrimestre,2026-01-05,X,Clases,abc,100,,,,",
        "I Cuatrimestre,2026-01-05,X,Clases,100,200,,,,",
        "I Cuatrimestre,2026-02-31,X,Clases,100,100,,,,",
    ],
)
def test_rejects_malformed_rows(bad_row):
    """protects: bad numbers, profit above price and impossible dates stop the import;
    fails_when: a malformed row is silently imported;
    no_claim: none beyond protects."""
    text = "I Cuatrimestre,,Enero,,,,,,,\n" + bad_row + "\n"

    with pytest.raises(WorkbookFormatError):
        parse_workbook_csv(text, 2026)
