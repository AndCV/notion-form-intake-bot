"""Generacion del adjunto .ics para el correo de confirmacion.

Se construye a partir de los datos propios de la submission, no de la
respuesta de Google Calendar: asi el email sigue teniendo un evento valido
adjunto aunque la creacion del evento en Calendar haya fallado (o se reintente
por separado).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event

from .schemas import AsesoriaSubmission, ClasesSubmission, Submission


def build_ics(submission: Submission, timezone: str, class_duration_minutes: int) -> bytes:
    calendar = Calendar()
    calendar.add("prodid", "-//ACV//Formulario de captacion//ES")
    calendar.add("version", "2.0")

    event = Event()
    event.add("uid", f"{submission.page_id}@acv-formulario")
    event.add("dtstamp", datetime.now(dt_timezone.utc))

    if isinstance(submission, AsesoriaSubmission):
        event.add("summary", f"Entrega asesoria - {submission.nombre}")
        event.add("dtstart", submission.fecha)
        event.add("dtend", submission.fecha + timedelta(days=1))
    elif isinstance(submission, ClasesSubmission):
        start = submission.fecha_hora
        if start.tzinfo is None:
            start = start.replace(tzinfo=ZoneInfo(timezone))
        event.add("summary", f"Clase - {submission.nombre}")
        event.add("dtstart", start)
        event.add("dtend", start + timedelta(minutes=class_duration_minutes))

    calendar.add_component(event)
    return calendar.to_ical()
