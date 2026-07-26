from datetime import date, datetime

from icalendar import Calendar

from lib.ics import build_ics
from lib.schemas import AsesoriaSubmission, ClasesSubmission


def test_build_ics_asesoria_all_day_event():
    submission = AsesoriaSubmission(
        page_id="page-1",
        nombre="Ana",
        correo="ana@example.com",
        fecha=date(2026, 8, 1),
        calendar_event_id=None,
        email_enviado=False,
    )

    ics_bytes = build_ics(submission, timezone="UTC", class_duration_minutes=60)
    calendar = Calendar.from_ical(ics_bytes)
    events = list(calendar.walk("VEVENT"))

    assert len(events) == 1
    event = events[0]
    assert "page-1" in str(event["UID"])
    assert event["DTSTART"].dt == date(2026, 8, 1)
    assert event["DTEND"].dt == date(2026, 8, 2)


def test_build_ics_clases_timed_event():
    submission = ClasesSubmission(
        page_id="page-2",
        nombre="Beto",
        correo="beto@example.com",
        fecha_hora=datetime(2026, 8, 2, 15, 30, 0),
        calendar_event_id=None,
        email_enviado=False,
    )

    ics_bytes = build_ics(submission, timezone="America/Costa_Rica", class_duration_minutes=45)
    calendar = Calendar.from_ical(ics_bytes)
    event = calendar.walk("VEVENT")[0]

    start = event["DTSTART"].dt
    end = event["DTEND"].dt
    assert start.hour == 15 and start.minute == 30
    assert (end - start).total_seconds() == 45 * 60
    assert start.tzinfo is not None
