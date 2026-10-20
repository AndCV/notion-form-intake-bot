"""Runs a web app service's outgoing steps: Calendar event, confirmation
email (.ics attached), invoice email and -- when a collaborator was hired --
their payment receipt.

Idempotent: each step runs only if
its marker is still empty and stamps it the moment it succeeds, so a retry
only redoes what's missing. A failing step doesn't stop the others; the
errors land in services.last_error for the detail page.

Known gap (accepted, see issue #1): if the process dies after an email went
out but before its marker was stamped, a retry sends that one email again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import psycopg

from . import db
from .config import Config
from .schemas import Submission, submission_from_service


class ServiceBusy(RuntimeError):
    """Another request is already processing this service."""


@dataclass
class Senders:
    """The outside world, injectable so tests never hit Google or Zoho."""

    create_calendar_event: Callable[[Config, Submission], dict[str, Any]]
    send_confirmation_email: Callable[[Config, Submission, bytes], dict]
    send_invoice_email: Callable[[Config, Submission, bytes, str], dict]
    build_ics: Callable[[Submission, str, int], bytes]
    build_invoice_pdf: Callable[[Config, Submission, str, Any], bytes]
    # (config, to_name, to_email, details, pdf, receipt_number)
    send_receipt_email: Callable[..., dict]
    # (config, collaborator_name, description, amount, receipt_number, issue_date)
    build_receipt_pdf: Callable[..., bytes]


def default_senders() -> Senders:
    # Imported here so importing this module stays cheap (WeasyPrint and the
    # Google client load only when something is actually sent).
    from .calendar_client import CalendarRepository
    from .email_client import send_confirmation_email, send_invoice_email, send_receipt_email
    from .ics import build_ics
    from .invoicing import build_invoice_pdf, build_receipt_pdf

    def create_calendar_event(config: Config, submission: Submission) -> dict[str, Any]:
        calendar = CalendarRepository(config.google_credentials_info(), config.google_calendar_id)
        return calendar.create_event_for_submission(
            submission, config.timezone, config.class_duration_minutes
        )

    return Senders(
        create_calendar_event=create_calendar_event,
        send_confirmation_email=send_confirmation_email,
        send_invoice_email=send_invoice_email,
        build_ics=build_ics,
        build_invoice_pdf=build_invoice_pdf,
        send_receipt_email=send_receipt_email,
        build_receipt_pdf=build_receipt_pdf,
    )


def _receipt_details(service: dict[str, Any], config: Config) -> list[tuple[str, str]]:
    symbol = config.invoice_currency_symbol
    amount = f"{symbol}{int(service['collaborator_pay']):,}".replace(",", ".")
    if service["kind"] == "clase":
        local = service["scheduled_at"].astimezone(ZoneInfo(config.timezone))
        when = ("Clase", local.strftime("%d/%m/%Y %H:%M"))
    else:
        when = ("Entrega", service["deadline"].strftime("%d/%m/%Y"))
    return [
        ("Cliente", service["client_name"]),
        ("Trabajo", service["job"]),
        when,
        ("Monto", amount),
    ]


def _refused_note(label: str, refused: dict) -> str | None:
    if not refused:
        return None
    return f"{label}: Zoho rechazó {', '.join(sorted(refused))}"


def process_service(
    conn: psycopg.Connection, config: Config, service_id: int, senders: Senders
) -> list[str]:
    """Runs the pending steps; returns the error/warning lines (empty = all ok).

    Raises ServiceBusy if another request holds the lease.
    """
    if not db.acquire_lease(conn, service_id):
        raise ServiceBusy(service_id)
    problems: list[str] = []
    try:
        service = db.get_service(conn, service_id)
        if service is None:
            return problems
        submission = submission_from_service(service, config.timezone)
        now = lambda: datetime.now(dt_timezone.utc)  # noqa: E731

        if not service["calendar_event_id"]:
            try:
                event = senders.create_calendar_event(config, submission)
                db.mark_step(conn, service_id, "calendar_event_id", event["id"])
            except Exception as exc:
                problems.append(f"Calendario: {exc}")

        if not service["confirmation_sent_at"]:
            try:
                ics = senders.build_ics(submission, config.timezone, config.class_duration_minutes)
                refused = senders.send_confirmation_email(config, submission, ics)
                db.mark_step(conn, service_id, "confirmation_sent_at", now())
                if note := _refused_note("Confirmación", refused):
                    problems.append(note)
            except Exception as exc:
                problems.append(f"Confirmación: {exc}")

        if not service["invoice_sent_at"] and service["invoice_number"]:
            try:
                issue_date = service["created_at"].astimezone(ZoneInfo(config.timezone)).date()
                pdf = senders.build_invoice_pdf(
                    config, submission, service["invoice_number"], issue_date
                )
                refused = senders.send_invoice_email(
                    config, submission, pdf, service["invoice_number"]
                )
                db.mark_step(conn, service_id, "invoice_sent_at", now())
                if note := _refused_note("Factura", refused):
                    problems.append(note)
            except Exception as exc:
                problems.append(f"Factura: {exc}")

        if (
            service["collaborator_id"]
            and service["receipt_number"]
            and not service["receipt_sent_at"]
        ):
            try:
                details = _receipt_details(service, config)
                description = " · ".join(f"{label}: {value}" for label, value in details[:3])
                issue_date = datetime.now(ZoneInfo(config.timezone)).date()
                pdf = senders.build_receipt_pdf(
                    config,
                    service["collaborator_name"],
                    description,
                    float(service["collaborator_pay"]),
                    service["receipt_number"],
                    issue_date,
                )
                refused = senders.send_receipt_email(
                    config,
                    service["collaborator_name"],
                    service["collaborator_email"],
                    details,
                    pdf,
                    service["receipt_number"],
                )
                if refused:
                    raise RuntimeError(f"Zoho rechazó {', '.join(refused)}")
                db.mark_receipt_sent(
                    conn, service_id, service["collaborator_name"], service["collaborator_email"]
                )
            except Exception as exc:
                problems.append(f"Recibo: {exc}")

        db.set_last_error(conn, service_id, "\n".join(problems) or None)
        return problems
    finally:
        db.release_lease(conn, service_id)
