"""The shape the calendar, .ics, email and invoice code consume.

A service from the web app (a row of the `services` table, see lib/db.py)
is adapted here into one of two kinds:

    AdvisorySubmission -- "trabajo": an all-day event on its deadline
    ClassSubmission    -- "clase": a timed event at its scheduled time
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Union
from zoneinfo import ZoneInfo


@dataclass
class AdvisorySubmission:
    # Stable id used as the .ics UID ("service-<id>").
    page_id: str
    name: str
    email: str
    deadline: date
    calendar_event_id: str | None
    email_sent: bool
    price: float | None = None
    detail: str | None = None
    invoice_number: str | None = None
    invoice_sent: bool = False
    # Every client recipient; `email` is the first of them.
    emails: list[str] = field(default_factory=list)


@dataclass
class ClassSubmission:
    page_id: str
    name: str
    email: str
    scheduled_at: datetime
    calendar_event_id: str | None
    email_sent: bool
    price: float | None = None
    detail: str | None = None
    invoice_number: str | None = None
    invoice_sent: bool = False
    emails: list[str] = field(default_factory=list)


Submission = Union[AdvisorySubmission, ClassSubmission]


def recipients(submission: Submission) -> list[str]:
    return submission.emails or [submission.email]


def submission_from_service(service: dict[str, Any], timezone: str) -> Submission:
    """Adapts a service row (lib/db.get_service) to a Submission."""
    emails = list(service["emails"])
    common = dict(
        page_id=f"service-{service['id']}",
        name=service["client_name"],
        email=emails[0],
        emails=emails,
        calendar_event_id=service["calendar_event_id"],
        email_sent=service["confirmation_sent_at"] is not None,
        price=float(service["price"]),
        detail=service["notes"] or service["job"],
        invoice_number=service["invoice_number"],
        invoice_sent=service["invoice_sent_at"] is not None,
    )
    if service["kind"] == "clase":
        # Stored as an instant; shown and scheduled in the business's zone.
        local = service["scheduled_at"].astimezone(ZoneInfo(timezone))
        return ClassSubmission(scheduled_at=local, **common)
    return AdvisorySubmission(deadline=service["deadline"], **common)
