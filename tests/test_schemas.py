from datetime import date, datetime, timezone
from decimal import Decimal

from lib.schemas import AdvisorySubmission, ClassSubmission, recipients, submission_from_service


def _service(**overrides):
    base = dict(
        id=7, client_name="Cliente A", job="Cálculo", kind="trabajo",
        scheduled_at=None, deadline=date(2026, 10, 30), price=Decimal("15000.00"),
        notes=None, emails=["a@example.com", "b@example.com"], calendar_event_id=None,
        confirmation_sent_at=None, invoice_number="0000073", invoice_sent_at=None,
    )  # fmt: skip
    base.update(overrides)
    return base


def test_work_becomes_an_all_day_submission_with_every_email():
    """protects: a "trabajo" maps to the all-day kind, with all recipients and the
    job as invoice line when there's no detail;
    fails_when: the kind, recipients or invoice line are wrong;
    no_claim: none beyond protects."""
    sub = submission_from_service(_service(), "America/Costa_Rica")

    assert isinstance(sub, AdvisorySubmission)
    assert sub.page_id == "service-7" and sub.deadline == date(2026, 10, 30)
    assert recipients(sub) == ["a@example.com", "b@example.com"] and sub.email == "a@example.com"
    assert sub.detail == "Cálculo" and sub.price == 15000.0 and sub.invoice_number == "0000073"
    assert not sub.email_sent and not sub.invoice_sent


def test_class_is_shown_in_the_business_timezone():
    """protects: a class stored as a UTC instant comes back at local wall-clock time;
    fails_when: the calendar/email would show UTC (6 hours off in Costa Rica);
    no_claim: none beyond protects."""
    utc = datetime(2026, 10, 21, 21, 30, tzinfo=timezone.utc)
    sub = submission_from_service(
        _service(kind="clase", scheduled_at=utc, deadline=None, notes="Tutoría"), "America/Costa_Rica"
    )

    assert isinstance(sub, ClassSubmission)
    assert sub.scheduled_at.strftime("%Y-%m-%d %H:%M") == "2026-10-21 15:30"
    assert sub.detail == "Tutoría"


def test_recipients_falls_back_to_the_single_email():
    """protects: a submission built with one email still has a recipient list;
    fails_when: it returns an empty list;
    no_claim: none beyond protects."""
    sub = AdvisorySubmission(page_id="x", name="A", email="a@example.com", deadline=date(2026, 1, 1),
                             calendar_event_id=None, email_sent=False)  # fmt: skip

    assert recipients(sub) == ["a@example.com"]
