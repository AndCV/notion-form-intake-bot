"""Builds the .ics attachment for the confirmation email.

Built from the submission's own data, not from Google Calendar's response:
that way the email still gets a valid event attached even if the Calendar
event creation failed (or gets retried separately).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event

from .schemas import AdvisorySubmission, ClassSubmission, Submission


def build_ics(submission: Submission, timezone: str, class_duration_minutes: int) -> bytes:
    calendar = Calendar()
    calendar.add("prodid", "-//notion-form-intake-bot//EN")
    calendar.add("version", "2.0")

    event = Event()
    event.add("uid", f"{submission.page_id}@notion-form-intake-bot")
    event.add("dtstamp", datetime.now(dt_timezone.utc))

    if isinstance(submission, AdvisorySubmission):
        event.add("summary", f"Entrega asesoria - {submission.name}")
        event.add("dtstart", submission.deadline)
        event.add("dtend", submission.deadline + timedelta(days=1))
    elif isinstance(submission, ClassSubmission):
        start = submission.scheduled_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=ZoneInfo(timezone))
        event.add("summary", f"Clase - {submission.name}")
        event.add("dtstart", start)
        event.add("dtend", start + timedelta(minutes=class_duration_minutes))

    calendar.add_component(event)
    return calendar.to_ical()
