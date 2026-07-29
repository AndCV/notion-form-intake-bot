"""Parsing and validation of a Notion database row (page).

Property names below match a real deployment's Notion database. Values stay
in Spanish because they mirror the real Notion schema and real form data:

    Nombre (title), Tipo (select: "Asesoría"/"Clases"), Correo (email),
    "Fecha " (date, with a trailing space -- that's how it is in Notion
    today), Estado (select: "nuevo"/"confirmado"), Calendar Event ID
    (rich_text), Email Enviado (checkbox), Creado (created_time).

If you rename the "Fecha " column in Notion to drop the trailing space,
update PROP_DATE below.
"""

from __future__ import annotations

import unicodedata
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Union

from dateutil import parser as dateutil_parser
from dateutil.parser import ParserError

PROP_NAME = "Nombre"
PROP_TYPE = "Tipo"
PROP_EMAIL = "Correo"
PROP_DATE = "Fecha "
PROP_STATUS = "Estado"
PROP_CALENDAR_EVENT_ID = "Calendar Event ID"
PROP_EMAIL_SENT = "Email Enviado"
PROP_PRICE = "Precio"
PROP_DETAIL = "Detalle"
PROP_INVOICE_NUMBER = "Número de Factura"
PROP_INVOICE_SENT = "Factura Enviada"

STATUS_NEW = "nuevo"
STATUS_CONFIRMED = "confirmado"


class RowError(ValueError):
    """The Notion row couldn't be parsed (missing or invalid fields)."""


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _get_title(properties: dict[str, Any], name: str) -> str | None:
    items = properties.get(name, {}).get("title", [])
    text = "".join(item.get("plain_text", "") for item in items).strip()
    return text or None


def _get_rich_text(properties: dict[str, Any], name: str) -> str | None:
    items = properties.get(name, {}).get("rich_text", [])
    text = "".join(item.get("plain_text", "") for item in items).strip()
    return text or None


def _get_select_name(properties: dict[str, Any], name: str) -> str | None:
    select = properties.get(name, {}).get("select")
    return select.get("name") if select else None


def _get_email(properties: dict[str, Any], name: str) -> str | None:
    return properties.get(name, {}).get("email")


def _get_checkbox(properties: dict[str, Any], name: str) -> bool:
    return bool(properties.get(name, {}).get("checkbox"))


def _get_date_start(properties: dict[str, Any], name: str) -> str | None:
    date_value = properties.get(name, {}).get("date")
    return date_value.get("start") if date_value else None


def _get_number(properties: dict[str, Any], name: str) -> float | None:
    return properties.get(name, {}).get("number")


def _parse_date(value: str) -> date:
    try:
        return dateutil_parser.parse(value).date()
    except (ParserError, ValueError, OverflowError) as exc:
        raise RowError(f"Invalid date: {value!r}") from exc


def _parse_datetime(value: str) -> datetime:
    try:
        return dateutil_parser.parse(value)
    except (ParserError, ValueError, OverflowError) as exc:
        raise RowError(f"Invalid date/time: {value!r}") from exc


@dataclass
class AdvisorySubmission:
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


Submission = Union[AdvisorySubmission, ClassSubmission]


def submission_from_page(page: dict[str, Any]) -> Submission:
    page_id = page["id"]
    properties = page.get("properties", {})

    name = _get_title(properties, PROP_NAME)
    email = _get_email(properties, PROP_EMAIL)
    type_ = _get_select_name(properties, PROP_TYPE)
    date_raw = _get_date_start(properties, PROP_DATE)
    calendar_event_id = _get_rich_text(properties, PROP_CALENDAR_EVENT_ID)
    email_sent = _get_checkbox(properties, PROP_EMAIL_SENT)
    price = _get_number(properties, PROP_PRICE)
    detail = _get_rich_text(properties, PROP_DETAIL)
    invoice_number = _get_rich_text(properties, PROP_INVOICE_NUMBER)
    invoice_sent = _get_checkbox(properties, PROP_INVOICE_SENT)

    if not name or not email or not type_ or not date_raw:
        raise RowError("Missing required fields (name, email, type and/or date) in the row")

    type_norm = _normalize(type_)

    if "asesor" in type_norm:
        return AdvisorySubmission(
            page_id=page_id,
            name=name,
            email=email,
            deadline=_parse_date(date_raw),
            calendar_event_id=calendar_event_id,
            email_sent=email_sent,
            price=price,
            detail=detail,
            invoice_number=invoice_number,
            invoice_sent=invoice_sent,
        )

    if "clase" in type_norm:
        return ClassSubmission(
            page_id=page_id,
            name=name,
            email=email,
            scheduled_at=_parse_datetime(date_raw),
            calendar_event_id=calendar_event_id,
            email_sent=email_sent,
            price=price,
            detail=detail,
            invoice_number=invoice_number,
            invoice_sent=invoice_sent,
        )

    raise RowError(f"Unknown request type: {type_!r}")
