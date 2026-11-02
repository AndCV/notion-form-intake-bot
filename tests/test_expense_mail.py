"""Parsing of BN statement debits and BAC transaction emails. Synthetic data
only: the real statements and notifications hold the owner's own spend
details (merchant, amount) that don't need to live in the repo."""

from datetime import date
from decimal import Decimal

from lib.payment_mail import (
    parse_bac_transaction,
    parse_bn_statement_debits,
    parse_wink_transaction,
)

STATEMENT_WITH_DEBITS = """Datos del cliente
Nombre: ANDRES DAVID CUBILLO VILLALOBOS
Número de cuenta:
200-01-153-082164-8
Fecha último estado: 18/09/2026
Fecha éste estado: 16/10/2026
FECHA NÚMERO DESCRIPCIÓN MONTO SALDO DIARIO
21-09 601118 18-09-26 R INSTITUCIONAL TEC CA CARTAGO CRI 500.00- 2,575.29
21-09 94988933 19-09-26 PEREZ SOLANO MARIA/LO QUE DEB A 6075918 10,000.00+ 11,175.29
21-09 71220099 20-09-26 SUPERMERCADO XYZ SAN JOSE CRI 8,250.50- 20,175.29
21-09 88112233 21-09-26 CUBILLO VILLALOBOS A/RECARGA DE DINERO 5,000.00- 15,175.29
"""

BAC_HTML = """<table>
<tr><td>Comercio:</td><td><b>LIBRERIA UNIVERSAL</b></td></tr>
<tr><td>Ciudad y pa&iacute;s:</td><td>San Jos&eacute;, Costa Rica</td></tr>
<tr><td>Fecha:</td><td><b>Oct 15, 2026, 14:05</b></td></tr>
<tr><td>MASTER:</td><td>************1234</td></tr>
<tr><td>Autorizaci&oacute;n:</td><td>009988</td></tr>
<tr><td>Referencia:</td><td>112233445566</td></tr>
<tr><td>Tipo de Transacci&oacute;n:</td><td>COMPRA</td></tr>
<tr><td>Monto:</td><td><b>CRC 8,500.00</b></td></tr>
</table>"""


def test_bn_statement_debits_are_the_owners_own_spend():
    """protects: every "-" line becomes a debit with amount/date/description, credits ignored,
      the statement owner is returned, and a self-transfer line (NAME/CONCEPT) splits its
      payer into counterparty the same way a credit does;
    fails_when: a credit leaks in, a debit is lost, the owner is wrong, or a self-transfer's
      name lands in description instead of counterparty (so it can't be filtered later);
    no_claim: verified only against synthetic lines matching the real statement's layout."""
    owner, debits = parse_bn_statement_debits(STATEMENT_WITH_DEBITS)

    assert owner == "ANDRES DAVID CUBILLO VILLALOBOS"
    assert [(d.counterparty, d.description, d.amount, d.occurred_on) for d in debits] == [
        ("", "R INSTITUCIONAL TEC CA CARTAGO CRI", Decimal(500), date(2026, 9, 18)),
        ("", "SUPERMERCADO XYZ SAN JOSE CRI", Decimal("8250.50"), date(2026, 9, 20)),
        ("CUBILLO VILLALOBOS A", "RECARGA DE DINERO", Decimal(5000), date(2026, 9, 21)),
    ]
    assert all(d.source == "bn_statement_debit" for d in debits)
    assert len({d.external_ref for d in debits}) == 3


WINK_TRANSACTION = """Notificación de transacción

¡Hola Winker!

Se registró una transacción con tu tarjeta. Estos son los detalles de la transacción:

Tarjeta utilizada: Terminada en 9900
Monto de la compra: 5,000.00
Comercio: RESTAURANTE DEMO SAN JOSE CR
Fecha y hora: 15/10/2026, 01:30 PM (hora de Costa Rica)
"""


def test_wink_transaction_reads_merchant_amount_and_date():
    """protects: a Wink card-purchase email's Comercio/Monto de la compra/Fecha y hora
      fields become a Movement, separately from parse_wink_send's transfer emails;
    fails_when: the merchant, amount or date is missing or misparsed;
    no_claim: verified once against a real Wink card-transaction email's field layout
      (values here are synthetic)."""
    m = parse_wink_transaction(WINK_TRANSACTION, "<winktxn1@x>")

    assert m.counterparty == "RESTAURANTE DEMO SAN JOSE CR"
    assert m.amount == Decimal("5000.00")
    assert m.occurred_on == date(2026, 10, 15)
    assert m.source == "wink_transaction" and m.external_ref == "<winktxn1@x>"


def test_wink_transaction_missing_fields_returns_none():
    """protects: a Wink "Notificación de envío" (transfer) or unrelated email doesn't
      become a phantom card-purchase Movement;
    fails_when: a required field (Comercio/Monto de la compra/Fecha y hora) is missing
      but a Movement is built anyway."""
    assert parse_wink_transaction("Hola Winker, gracias por usar Wink.", "<winktxn2@x>") is None


def test_bac_transaction_reads_merchant_amount_and_date():
    """protects: the HTML table's Comercio/Fecha/Monto fields become a Movement in CRC;
    fails_when: a non-CRC amount is counted, or a reversal type isn't filtered out;
    no_claim: verified once against a real BAC email's field layout (values are synthetic)."""
    m = parse_bac_transaction(BAC_HTML, "<bac1@x>")

    assert m.counterparty == "LIBRERIA UNIVERSAL"
    assert m.amount == Decimal("8500.00")
    assert m.occurred_on == date(2026, 10, 15)
    assert m.description == "Compra"
    assert m.source == "bac_transaction" and m.external_ref == "<bac1@x>"


def test_bac_transaction_skips_non_crc_and_reversals():
    """protects: a USD charge or a reversal doesn't silently inflate the CRC expense total;
    fails_when: either slips through as a counted Movement;
    no_claim: 'ANULACION' is a guess at BAC's reversal wording, not confirmed from a real sample."""
    usd = BAC_HTML.replace("CRC 8,500.00", "USD 16.00")
    reversal = BAC_HTML.replace("COMPRA", "ANULACION")

    assert parse_bac_transaction(usd, "<bac2@x>") is None
    assert parse_bac_transaction(reversal, "<bac3@x>") is None


def test_bac_transaction_missing_fields_returns_none():
    """protects: a malformed or unrelated HTML email doesn't become a phantom Movement;
    fails_when: a required field (Comercio/Fecha/Monto) is missing but a Movement is built anyway."""
    assert parse_bac_transaction("<p>Hola, gracias por su visita.</p>", "<bac4@x>") is None
