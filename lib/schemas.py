"""Parsing y validacion de una fila (pagina) de la base de Notion.

Nombres de propiedad confirmados via la API contra la base real
"Solicitudes ACV" (data source id consultado el 2026-07-25):

    Nombre (title), Tipo (select: "Asesoría"/"Clases"), Correo (email),
    "Fecha " (date, con un espacio al final -- asi esta en Notion hoy),
    Estado (select: "nuevo"/"confirmado"), Calendar Event ID (rich_text),
    Email Enviado (checkbox), Creado (created_time).

Si renombras la columna "Fecha " en Notion para sacarle el espacio, hay que
actualizar PROP_FECHA aca abajo.
"""

from __future__ import annotations

import unicodedata
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Union

from dateutil import parser as dateutil_parser
from dateutil.parser import ParserError

PROP_NOMBRE = "Nombre"
PROP_TIPO = "Tipo"
PROP_CORREO = "Correo"
PROP_FECHA = "Fecha "
PROP_ESTADO = "Estado"
PROP_CALENDAR_EVENT_ID = "Calendar Event ID"
PROP_EMAIL_ENVIADO = "Email Enviado"

ESTADO_NUEVO = "nuevo"
ESTADO_CONFIRMADO = "confirmado"


class RowError(ValueError):
    """La fila de Notion no se pudo interpretar (campos faltantes o invalidos)."""


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


def _parse_date(value: str) -> date:
    try:
        return dateutil_parser.parse(value).date()
    except (ParserError, ValueError, OverflowError) as exc:
        raise RowError(f"Fecha invalida: {value!r}") from exc


def _parse_datetime(value: str) -> datetime:
    try:
        return dateutil_parser.parse(value)
    except (ParserError, ValueError, OverflowError) as exc:
        raise RowError(f"Fecha/hora invalida: {value!r}") from exc


@dataclass
class AsesoriaSubmission:
    page_id: str
    nombre: str
    correo: str
    fecha: date
    calendar_event_id: str | None
    email_enviado: bool


@dataclass
class ClasesSubmission:
    page_id: str
    nombre: str
    correo: str
    fecha_hora: datetime
    calendar_event_id: str | None
    email_enviado: bool


Submission = Union[AsesoriaSubmission, ClasesSubmission]


def submission_from_page(page: dict[str, Any]) -> Submission:
    page_id = page["id"]
    properties = page.get("properties", {})

    nombre = _get_title(properties, PROP_NOMBRE)
    correo = _get_email(properties, PROP_CORREO)
    tipo = _get_select_name(properties, PROP_TIPO)
    fecha_raw = _get_date_start(properties, PROP_FECHA)
    calendar_event_id = _get_rich_text(properties, PROP_CALENDAR_EVENT_ID)
    email_enviado = _get_checkbox(properties, PROP_EMAIL_ENVIADO)

    if not nombre or not correo or not tipo or not fecha_raw:
        raise RowError(
            "Faltan campos requeridos (nombre, correo, tipo y/o fecha) en la fila"
        )

    tipo_norm = _normalize(tipo)

    if "asesor" in tipo_norm:
        return AsesoriaSubmission(
            page_id=page_id,
            nombre=nombre,
            correo=correo,
            fecha=_parse_date(fecha_raw),
            calendar_event_id=calendar_event_id,
            email_enviado=email_enviado,
        )

    if "clase" in tipo_norm:
        return ClasesSubmission(
            page_id=page_id,
            nombre=nombre,
            correo=correo,
            fecha_hora=_parse_datetime(fecha_raw),
            calendar_event_id=calendar_event_id,
            email_enviado=email_enviado,
        )

    raise RowError(f"Tipo de solicitud desconocido: {tipo!r}")
