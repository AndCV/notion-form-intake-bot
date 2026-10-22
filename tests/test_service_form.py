from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from lib.service_form import parse_service_form

TZ = "America/Costa_Rica"


def _fields(**overrides):
    base = dict(
        service_date="2026-10-20",
        client_name="Cliente A",
        job="Cálculo",
        kind="clase",
        scheduled_at="2026-10-21T15:30",
        deadline="",
        price="15000",
        notes="",
    )
    base.update(overrides)
    return base


def _parse(fields=None, emails=("a@example.com",), collaborators=(7,)):
    return parse_service_form(fields or _fields(), list(emails), TZ, set(collaborators))


def test_valid_class_is_parsed_in_the_business_timezone():
    """protects: a class keeps its local wall-clock time as an aware datetime;
    fails_when: the time is stored naive or shifted to UTC wall time;
    no_claim: no database or calendar call."""
    result = _parse(emails=["A@Example.com ", "b@example.com", "a@example.com", ""])

    assert result.errors == []
    v = result.value
    assert v.scheduled_at == datetime(2026, 10, 21, 15, 30, tzinfo=ZoneInfo(TZ))
    assert v.deadline is None
    assert v.emails == ["a@example.com", "b@example.com"]
    assert v.price == Decimal(15000) and v.collaborator_id is None and v.profit == 15000


def test_valid_work_with_collaborator_derives_profit():
    """protects: profit = price - collaborator pay for a hired collaborator;
    fails_when: pay is ignored or the deadline is lost;
    no_claim: none beyond protects."""
    fields = _fields(kind="trabajo", scheduled_at="", deadline="2026-10-30", price="25000",
                     hire="1", collaborator_id="7", collaborator_pay="15000")
    v = _parse(fields).value

    assert v.deadline == date(2026, 10, 30) and v.scheduled_at is None
    assert (v.collaborator_id, v.collaborator_pay, v.profit) == (7, Decimal(15000), Decimal(10000))


def test_unchecked_hire_ignores_collaborator_fields():
    """protects: collaborator inputs left over in the form don't count unless "hire" is on;
    fails_when: a hidden pay value lowers the profit;
    no_claim: none beyond protects."""
    v = _parse(_fields(collaborator_id="7", collaborator_pay="5000")).value

    assert v.collaborator_id is None and v.collaborator_pay == 0


@pytest.mark.parametrize(
    "fields, emails, message",
    [
        (_fields(), [], "al menos un correo"),
        (_fields(), ["", "  "], "al menos un correo"),
        (_fields(), ["not-an-email"], "Correo inválido"),
        (_fields(), [f"c{i}@example.com" for i in range(6)], "Máximo 5"),
        (_fields(price="0"), ["a@example.com"], "precio"),
        (_fields(price="15000.50"), ["a@example.com"], "precio"),
        (_fields(hire="1", collaborator_id="7", collaborator_pay="20000"), ["a@example.com"], "mayor que el precio"),
        (_fields(scheduled_at=""), ["a@example.com"], "fecha y hora"),
        (_fields(kind="trabajo", scheduled_at=""), ["a@example.com"], "fecha de entrega"),
        (_fields(hire="1", collaborator_pay="100"), ["a@example.com"], "elige a quién"),
        (_fields(hire="1", collaborator_id="99", collaborator_pay="100"), ["a@example.com"], "desactivado"),
        (_fields(kind="otro"), ["a@example.com"], "tipo"),
        (_fields(client_name=" "), ["a@example.com"], "cliente"),
    ],
)
def test_rejections(fields, emails, message):
    """protects: each invalid form is refused with a Spanish message naming the problem;
    fails_when: an invalid service would be saved and emailed;
    no_claim: the HTTP 400 status is covered in tests/test_app.py."""
    result = _parse(fields, emails)

    assert result.value is None
    assert any(message in e for e in result.errors), result.errors


def test_several_client_names_are_cleaned_and_joined():
    """protects: a service for several people -- every name is kept in order,
    blanks and repeats dropped, and they're joined for the invoice greeting;
    fails_when: a name is lost, repeated, or joined differently;
    no_claim: none beyond protects."""
    result = parse_service_form(
        _fields(client_name=""), ["a@example.com"], TZ, set(),
        names=[" Ana  Mora ", "", "Luis", "ana mora", "Marta"],
    )  # fmt: skip

    assert result.errors == []
    assert result.value.client_names == ["Ana Mora", "Luis", "Marta"]
    assert result.value.client_name == "Ana Mora, Luis y Marta"
    assert parse_service_form(_fields(), ["a@example.com"], TZ, set(), names=["", " "]).errors == [
        "Falta el nombre del cliente."
    ]
    assert _parse().value.client_names == ["Cliente A"]
