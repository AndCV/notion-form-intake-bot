"""Creates Google Calendar events via a service account.

No "attendees" are added to the events: service accounts without
Domain-Wide Delegation of Authority can't invite attendees on
other people's/shared calendars (the API errors out). The client's
confirmation goes out by email with the .ics attached instead (see
lib/email_client.py and lib/ics.py); the calendar is just for the owner's
own agenda (and its sync to Apple Calendar).

Event titles/descriptions stay in Spanish on purpose: this is real content
for a Spanish-speaking solo business, not code.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

from .schemas import AdvisorySubmission, ClassSubmission, Submission

_SCOPES = ["https://www.googleapis.com/auth/calendar"]


class CalendarRepository:
    def __init__(self, credentials_info: dict[str, Any], calendar_id: str) -> None:
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info, scopes=_SCOPES
        )
        self._service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        self._calendar_id = calendar_id

    def create_event_for_submission(
        self, submission: Submission, timezone: str, class_duration_minutes: int
    ) -> dict[str, Any]:
        if isinstance(submission, AdvisorySubmission):
            body = build_all_day_event_body(submission)
        else:
            body = build_timed_event_body(submission, timezone, class_duration_minutes)
        return self._service.events().insert(calendarId=self._calendar_id, body=body).execute()


def build_all_day_event_body(submission: AdvisorySubmission) -> dict[str, Any]:
    end = submission.deadline + timedelta(days=1)
    return {
        "summary": f"Entrega asesoria - {submission.name}",
        "description": f"Fecha limite de entrega para {submission.name} ({submission.email}).",
        "start": {"date": submission.deadline.isoformat()},
        "end": {"date": end.isoformat()},
    }


def build_timed_event_body(
    submission: ClassSubmission, timezone: str, duration_minutes: int
) -> dict[str, Any]:
    start = submission.scheduled_at
    end = start + timedelta(minutes=duration_minutes)
    return {
        "summary": f"Clase - {submission.name}",
        "description": f"Sesion agendada con {submission.name} ({submission.email}).",
        "start": {"dateTime": start.isoformat(), "timeZone": timezone},
        "end": {"dateTime": end.isoformat(), "timeZone": timezone},
    }
