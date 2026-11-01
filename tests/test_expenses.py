"""Grouping and message formatting of the nightly expense closing. Synthetic
data only."""

from datetime import date
from decimal import Decimal

from lib.expenses import close, format_message
from lib.payment_mail import Movement


def _movement(source, occurred_on, amount, external_ref="<x@x>") -> Movement:
    return Movement(
        source=source,
        occurred_on=occurred_on,
        amount=Decimal(amount),
        counterparty="SOME MERCHANT",
        description="",
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


def test_format_message_empty_and_populated():
    """protects: the message is non-empty and readable with no movements and with several days;
    fails_when: an empty report crashes formatting, or the total/day lines are malformed."""
    empty = close([], business_wink_refs=set())
    assert format_message(empty) == "Cierre de gastos: sin movimientos."

    report = close(
        [
            _movement("bac_transaction", date(2026, 10, 20), "8500.00"),
            _movement("bn_statement_debit", date(2026, 10, 21), "1500.00"),
        ],
        business_wink_refs=set(),
    )
    message = format_message(report)
    assert message.startswith("*Cierre de gastos*")
    assert "20/10: ₡8,500.00 (1 mov.)" in message
    assert "21/10: ₡1,500.00 (1 mov.)" in message
    assert message.endswith("*Total: ₡10,000.00*")
