"""Parsing of the two payment emails and the matching rules. Synthetic data
only: the real statements and notifications hold third parties' names."""

from datetime import date
from decimal import Decimal

import pytest

from lib.payment_mail import Movement, parse_bn_statement, parse_wink_send
from lib.payments import decide_client, decide_collaborator, is_owner, name_matches, recipient_matches

STATEMENT = """Datos del cliente
Nombre: ANDRES DAVID CUBILLO VILLALOBOS
Número de cuenta:
200-01-153-082164-8
Fecha último estado: 18/09/2026
Fecha éste estado: 16/10/2026
FECHA NÚMERO DESCRIPCIÓN MONTO SALDO DIARIO
21-09 601118 18-09-26 R INSTITUCIONAL TEC CA CARTAGO CRI 500.00- 2,575.29
21-09 94988933 19-09-26 PEREZ SOLANO MARIA/LO QUE DEB A 6075918 10,000.00+ 11,175.29
21-09 56899564 20-09-26 ANA CATALINA RODRIGU/TRANSFERENCIA SINPE 7,000.00+ 30,175.29
21-09 97881221 CUBILLO VILLALOBOS A/RECARGA DE DINERO 55,000.00+ 75,175.29
"""

WINK_SINPE = """Notificación de transferencia
¡Hola Andres!
*Cuenta de origen:* Cuenta de ahorros en colones
*Número de destino:* 88887777
*Nombre del destinatario:* ANGEL_EDUARDO_FALLAS
*Monto transferido:* 15,000.00 colones
*Fecha de la transferencia:* 18/10/2026
"""

WINK_IBAN = """Notificación de transferencia
*Cuenta de destino:* CR92000000000000000000
*Monto transferido:* 250,000.00 colones
*Fecha y hora de la transferencia:* 15/10/2026, 08:41 PM (hora de Costa Rica)
"""


def test_bn_statement_reads_only_credits_with_payer_and_concept():
    """protects: every "+" line becomes a credit with its payer name, amount and date;
    fails_when: debits leak in, a credit is lost, or the payer is split wrong;
    no_claim: verified once against two real statements (totals equal the header)."""
    st = parse_bn_statement(STATEMENT)

    assert st.owner == "ANDRES DAVID CUBILLO VILLALOBOS" and st.account == "200-01-153-082164-8"
    assert [(c.counterparty, c.description, c.amount, c.occurred_on) for c in st.credits] == [
        ("PEREZ SOLANO MARIA", "LO QUE DEB A 6075918", Decimal(10000), date(2026, 9, 19)),
        ("ANA CATALINA RODRIGU", "TRANSFERENCIA SINPE", Decimal(7000), date(2026, 9, 20)),
        ("CUBILLO VILLALOBOS A", "RECARGA DE DINERO", Decimal(55000), date(2026, 9, 21)),
    ]
    assert len({c.external_ref for c in st.credits}) == 3 and st.unparsed_credit_lines == 0


def test_december_lines_in_a_january_statement_belong_to_last_year():
    """protects: a posting date without its own year is dated within the statement span;
    fails_when: a December line in a January statement lands in next December;
    no_claim: none beyond protects."""
    text = STATEMENT.replace("16/10/2026", "16/01/2027").replace(
        "21-09 97881221", "28-12 97881221"
    )
    assert parse_bn_statement(text).credits[-1].occurred_on == date(2026, 12, 28)


def test_wink_sinpe_and_iban_sends():
    """protects: both Wink send layouts are read; the IBAN one keeps the account as name;
    fails_when: either layout is dropped or the amount/date is wrong;
    no_claim: other Wink emails (deposits, card) are filtered by subject upstream."""
    sinpe = parse_wink_send(WINK_SINPE, "<a@x>")
    iban = parse_wink_send(WINK_IBAN, "<b@x>")

    assert (sinpe.counterparty, sinpe.amount, sinpe.occurred_on) == ("ANGEL EDUARDO FALLAS", Decimal(15000), date(2026, 10, 18))
    assert sinpe.description == "SINPE Móvil a 88887777" and sinpe.external_ref == "<a@x>"
    assert (iban.counterparty, iban.amount, iban.occurred_on) == ("Cuenta CR92000000000000000000", Decimal(250000), date(2026, 10, 15))
    assert parse_wink_send("Notificación de depósito\n*Monto depositado:* 1.00", "<c@x>") is None


def test_names():
    """protects: name rules -- owner detection, truncated payer names, Wink recipient prefix;
    fails_when: a client payment is taken as the owner's or a stranger matches;
    no_claim: none beyond protects."""
    assert is_owner("CUBILLO VILLALOBOS A", "ANDRES DAVID CUBILLO VILLALOBOS")
    assert is_owner("ANDRES DAVID CUBILLO VIL", "ANDRES DAVID CUBILLO VILLALOBOS")
    assert not is_owner("CUBILLO RAMIREZ LEONEL", "ANDRES DAVID CUBILLO VILLALOBOS")
    assert name_matches("ANA CATALINA RODRIGU", "Catalina", {})
    assert name_matches("ANA CATALINA RODRIGU", "Catalina Rodríguez", {})
    assert not name_matches("PEREZ SOLANO MARIA", "Catalina", {})
    assert name_matches("PEREZ SOLANO MARIA", "Jimena", {"PEREZ SOLANO MARIA": "jimena"})
    assert recipient_matches("ANGEL EDUARDO FALLAS", "Angel Eduardo Fallas Valverde")
    assert not recipient_matches("ANGEL EDUARDO MORA", "Angel Eduardo Fallas Valverde")
    assert not recipient_matches("ANGEL", "Angel Eduardo Fallas Valverde")


def _credit(payer, amount, on=date(2026, 10, 25)):
    return Movement("bn_statement", on, Decimal(amount), payer, "TRANSFERENCIA SINPE", "r")


def _svc(id, client, price, on=date(2026, 10, 20)):
    return dict(id=id, service_date=on, client_name=client, price=Decimal(price))


PENDING = [_svc(1, "Catalina", 7000), _svc(2, "Jimena", 7000), _svc(3, "Jimena", 12000, date(2026, 10, 22))]


def test_client_is_paid_only_when_name_and_amount_both_match():
    """protects: the owner's rule -- auto-paid needs the statement name AND the stored price;
    fails_when: an amount-only or name-only coincidence marks a service paid;
    no_claim: none beyond protects."""
    assert decide_client(_credit("ANA CATALINA RODRIGU", 7000), "", PENDING, {}).service_ids == [1]
    amount_only = decide_client(_credit("PEREZ SOLANO MARIA", 7000), "", PENDING, {})
    assert (amount_only.status, amount_only.service_ids) == ("pending", [])
    assert "Catalina, Jimena" in amount_only.note and "nombre no" in amount_only.note
    name_only = decide_client(_credit("ANA CATALINA RODRIGU", 9000), "", PENDING, {})
    assert name_only.status == "pending" and "monto no" in name_only.note


def test_one_transfer_for_several_services_of_the_same_client():
    """protects: a payment equal to the sum of a client's oldest pending services pays them all;
    fails_when: it stays pending or pays another client's service;
    no_claim: none beyond protects."""
    d = decide_client(_credit("JIMENA ARAYA MORA", 19000), "", PENDING, {})

    assert (d.status, d.service_ids) == ("matched", [2, 3])


def test_alias_and_owner_and_window():
    """protects: a remembered payer pays for its client; own transfers and payments far
    from the service date are not matched;
    fails_when: any of these is auto-marked;
    no_claim: none beyond protects."""
    alias = {"PEREZ SOLANO MARIA": "jimena"}
    assert decide_client(_credit("PEREZ SOLANO MARIA", 12000), "", PENDING, alias).service_ids == [3]
    own = decide_client(_credit("CUBILLO VILLALOBOS A", 7000), "ANDRES DAVID CUBILLO VILLALOBOS", PENDING, {})
    assert own.status == "ignored"
    late = decide_client(_credit("ANA CATALINA RODRIGU", 7000, date(2027, 2, 1)), "", PENDING, {})
    assert late.status == "ignored"


COLLABS = [dict(id=1, name="Eduardo", payout_name="Angel Eduardo Fallas Valverde"), dict(id=2, name="Otro", payout_name=None)]
TO_PAY = [
    dict(id=10, service_date=date(2026, 10, 1), collaborator_id=1, collaborator_pay=Decimal(9000)),
    dict(id=11, service_date=date(2026, 10, 3), collaborator_id=1, collaborator_pay=Decimal(6000)),
    dict(id=12, service_date=date(2026, 10, 4), collaborator_id=1, collaborator_pay=Decimal(15000)),
]


def _send(name, amount):
    return Movement("wink_send", date(2026, 10, 20), Decimal(amount), name, "", "m")


def test_collaborator_payouts():
    """protects: a Wink send to the collaborator pays the matching service, or the oldest
    ones summing to it; other recipients are ignored;
    fails_when: the wrong service is paid or a stranger's transfer counts;
    no_claim: none beyond protects."""
    assert decide_collaborator(_send("ANGEL EDUARDO FALLAS", 15000), COLLABS, TO_PAY).service_ids == [12]
    assert decide_collaborator(_send("ANGEL EDUARDO FALLAS", 9000), COLLABS, TO_PAY).service_ids == [10]
    summed = decide_collaborator(_send("ANGEL EDUARDO FALLAS", 30000), COLLABS, TO_PAY)
    assert (summed.status, summed.service_ids, summed.collaborator_id) == ("matched", [10, 11, 12], 1)
    odd = decide_collaborator(_send("ANGEL EDUARDO FALLAS", 7000), COLLABS, TO_PAY)
    assert odd.status == "pending" and odd.collaborator_id == 1
    assert decide_collaborator(_send("ANDRES DAVID CUBILLO", 2500), COLLABS, TO_PAY).status == "ignored"


def test_any_of_several_names_can_pay_a_shared_service():
    """protects: a service for "Ana y Luis" is paid when the statement shows
    either person with the stored price;
    fails_when: only the first name is recognized;
    no_claim: none beyond protects."""
    shared = dict(_svc(9, "Ana Mora y Luis Quesada", 20000), client_names=["Ana Mora", "Luis Quesada"])

    assert decide_client(_credit("LUIS QUESADA SOTO", 20000), "", [shared], {}).service_ids == [9]
    assert decide_client(_credit("ANA MORA VEGA", 20000), "", [shared], {}).service_ids == [9]
    assert decide_client(_credit("PEDRO SOTO", 20000), "", [shared], {}).status == "pending"
