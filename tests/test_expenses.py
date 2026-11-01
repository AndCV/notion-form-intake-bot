"""Grouping and message formatting of the nightly expense closing. Synthetic
data only."""

from datetime import date
from decimal import Decimal

from lib.expenses import close, format_message, format_month_total
from lib.payment_mail import Movement


def _movement(
    source, occurred_on, amount, external_ref="<x@x>", counterparty="SOME MERCHANT", description=""
) -> Movement:
    return Movement(
        source=source,
        occurred_on=occurred_on,
        amount=Decimal(amount),
        counterparty=counterparty,
        description=description,
        external_ref=external_ref,
    )


def test_close_groups_by_date_and_totals():
    """protects: movements across sources sum correctly per day and overall;
    fails_when: a day's total is wrong, days are out of order, or a movement is dropped;
    no_claim: none beyond protects."""
    movements = [
        _movement("bn_statement_debit", date(2026, 10, 20), "500.00"),
        _movement("bac_transaction", date(2026, 10, 20), "8500.00"),
        _movement("wink_send", date(2026, 10, 19), "15000.00", external_ref="<w1@x>"),
    ]
    report = close(movements, business_wink_refs=set())

    assert [(d.occurred_on, d.total, len(d.movements)) for d in report.days] == [
        (date(2026, 10, 19), Decimal("15000.00"), 1),
        (date(2026, 10, 20), Decimal("9000.00"), 2),
    ]
    assert report.total == Decimal("24000.00")
    assert report.excluded_business_count == 0


def test_close_excludes_business_wink_sends():
    """protects: a Wink send already tracked as a collaborator payout (payment_events)
      doesn't double-count as personal spend;
    fails_when: it's included in the total, or a personal Wink send is wrongly excluded;
    no_claim: none beyond protects."""
    business = _movement("wink_send", date(2026, 10, 20), "40000.00", external_ref="<business@x>")
    personal = _movement("wink_send", date(2026, 10, 20), "5000.00", external_ref="<personal@x>")

    report = close([business, personal], business_wink_refs={"<business@x>"})

    assert report.total == Decimal("5000.00")
    assert report.excluded_business_count == 1
    assert len(report.days) == 1 and len(report.days[0].movements) == 1


def test_close_excludes_self_transfers_on_bn_and_wink():
    """protects: a BN debit or a Wink send naming the owner themselves
      (is_owner(counterparty, owner)) isn't counted as spend, while a genuine merchant
      debit (no payer to split), a differently-named debit, and a Wink send to someone
      else still are;
    fails_when: a self-transfer on either source inflates the total, or a real
      debit/send gets wrongly excluded;
    no_claim: is_owner's truncated-name matching is lib.payments', not re-tested here."""
    bn_self_transfer = _movement(
        "bn_statement_debit", date(2026, 10, 20), "50000.00", counterparty="CUBILLO VILLALOBOS A"
    )
    wink_self_transfer = _movement(
        "wink_send", date(2026, 10, 20), "221000.00", external_ref="<w3@x>", counterparty="ANDRES DAVID CUBILLO"
    )
    merchant = _movement(
        "bn_statement_debit", date(2026, 10, 20), "3000.00", counterparty="", description="SODA LA DEPORTIVA"
    )
    someone_else = _movement(
        "bn_statement_debit", date(2026, 10, 20), "1000.00", counterparty="PEREZ SOLANO MARIA"
    )
    wink_to_someone_else = _movement(
        "wink_send", date(2026, 10, 20), "2000.00", external_ref="<w4@x>", counterparty="LEONEL CUBILLO RAMIREZ"
    )

    report = close(
        [bn_self_transfer, wink_self_transfer, merchant, someone_else, wink_to_someone_else],
        business_wink_refs=set(),
        owner="ANDRES DAVID CUBILLO VILLALOBOS",
    )

    assert report.total == Decimal("6000.00")
    assert report.excluded_self_transfer_count == 2
    assert len(report.days[0].movements) == 3

    # owner="" (no BN statement read this run) skips the check entirely.
    no_owner_report = close([bn_self_transfer], business_wink_refs=set())
    assert no_owner_report.total == Decimal("50000.00")
    assert no_owner_report.excluded_self_transfer_count == 0


def test_format_message_empty_and_populated():
    """protects: each movement shows its merchant and card, days get a subtotal, the
      message ends with the grand total, and a BN debit (no counterparty) falls back to
      its description text;
    fails_when: a merchant/card line is missing or mislabeled, a subtotal is wrong, or a
      BN debit with no counterparty renders blank instead of its description."""
    empty = close([], business_wink_refs=set())
    assert format_message(empty) == "Cierre de gastos: sin movimientos."

    report = close(
        [
            _movement("bac_transaction", date(2026, 10, 20), "8500.00", counterparty="LIBRERIA UNIVERSAL"),
            _movement(
                "bn_statement_debit",
                date(2026, 10, 21),
                "1500.00",
                counterparty="",
                description="SUPERMERCADO XYZ",
            ),
        ],
        business_wink_refs=set(),
    )
    message = format_message(report)
    assert message.startswith("*Cierre de gastos*")
    assert "*20/10*" in message and "*21/10*" in message
    assert "  • LIBRERIA UNIVERSAL (BAC): ₡8,500.00" in message
    assert "  Subtotal: ₡8,500.00" in message
    assert "  • SUPERMERCADO XYZ (BN): ₡1,500.00" in message
    assert "  Subtotal: ₡1,500.00" in message
    assert message.endswith("*Total: ₡10,000.00*")


def test_format_month_total_breaks_down_by_card():
    """protects: the month-end message names the Spanish month, sums per card
      (BN/Wink/BAC) and ends with the grand total;
    fails_when: a card subtotal is wrong, the month name/year is off, or an
      empty month crashes formatting instead of saying so."""
    empty = close([], business_wink_refs=set())
    assert format_month_total(empty, date(2026, 10, 1)) == "*Cierre de Octubre 2026*: sin movimientos."

    report = close(
        [
            _movement("bac_transaction", date(2026, 10, 5), "13449.99"),
            _movement("wink_send", date(2026, 10, 12), "5000.00", external_ref="<w2@x>"),
            _movement("bn_statement_debit", date(2026, 10, 31), "8250.50"),
        ],
        business_wink_refs=set(),
    )
    message = format_month_total(report, date(2026, 10, 1))
    assert message.startswith("*Cierre de Octubre 2026*")
    assert "  BAC: ₡13,449.99" in message
    assert "  BN: ₡8,250.50" in message
    assert "  Wink: ₡5,000.00" in message
    assert message.endswith("*Total del mes: ₡26,700.49*")
