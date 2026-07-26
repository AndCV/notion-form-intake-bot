"""Creacion de eventos en Google Calendar via service account.

No se agregan "attendees" a los eventos: las service accounts sin
Domain-Wide Delegation of Authority no pueden invitar asistentes en
calendarios ajenos/compartidos (la API devuelve error). La confirmacion al
cliente/alumno va por email con el .ics adjunto (ver lib/email_client.py y
lib/ics.py); el calendario es solo para tu propia agenda (y su sync a Apple
Calendar).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

from .schemas import AsesoriaSubmission, ClasesSubmission, Submission

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
        if isinstance(submission, AsesoriaSubmission):
            body = build_all_day_event_body(submission)
        else:
            body = build_timed_event_body(submission, timezone, class_duration_minutes)
        return self._service.events().insert(calendarId=self._calendar_id, body=body).execute()


def build_all_day_event_body(submission: AsesoriaSubmission) -> dict[str, Any]:
    end = submission.fecha + timedelta(days=1)
    return {
        "summary": f"Entrega asesoria - {submission.nombre}",
        "description": f"Fecha limite de entrega para {submission.nombre} ({submission.correo}).",
        "start": {"date": submission.fecha.isoformat()},
        "end": {"date": end.isoformat()},
    }


def build_timed_event_body(
    submission: ClasesSubmission, timezone: str, duration_minutes: int
) -> dict[str, Any]:
    start = submission.fecha_hora
    end = start + timedelta(minutes=duration_minutes)
    return {
        "summary": f"Clase - {submission.nombre}",
        "description": f"Sesion agendada con {submission.nombre} ({submission.correo}).",
        "start": {"dateTime": start.isoformat(), "timeZone": timezone},
        "end": {"dateTime": end.isoformat(), "timeZone": timezone},
    }
