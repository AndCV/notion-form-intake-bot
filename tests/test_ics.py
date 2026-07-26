from datetime import date, datetime

from icalendar import Calendar

from lib.ics import build_ics
from lib.schemas import AdvisorySubmission, ClassSubmission


def test_build_ics_advisory_all_day_event():
    submission = AdvisorySubmission(
        page_id="page-1",
        name="Ana",
        email="ana@example.com",
        deadline=date(2026, 8, 1),
        calendar_event_id=None,
        email_sent=False,
    )

    ics_bytes = build_ics(submission, timezone="UTC", class_duration_minutes=60)
    calendar = Calendar.from_ical(ics_bytes)
    events = list(calendar.walk("VEVENT"))

    assert len(events) == 1
    event = events[0]
    assert "page-1" in str(event["UID"])
    assert event["DTSTART"].dt == date(2026, 8, 1)
    assert event["DTEND"].dt == date(2026, 8, 2)


def test_build_ics_class_timed_event():
    submission = ClassSubmission(
        page_id="page-2",
        name="Beto",
        email="beto@example.com",
        scheduled_at=datetime(2026, 8, 2, 15, 30, 0),
        calendar_event_id=None,
        email_sent=False,
    )

    ics_bytes = build_ics(submission, timezone="America/Costa_Rica", class_duration_minutes=45)
    calendar = Calendar.from_ical(ics_bytes)
    event = calendar.walk("VEVENT")[0]

    start = event["DTSTART"].dt
    end = event["DTEND"].dt
    assert start.hour == 15 and start.minute == 30
    assert (end - start).total_seconds() == 45 * 60
    assert start.tzinfo is not None
