"""Repositorio Notion para el modelo de polling.

No hace falta buscar filas por id de envio: cada corrida consulta
directamente las filas pendientes (Estado vacio/"nuevo" o Email Enviado sin
marcar) y el propio `page_id` que trae esa consulta sirve como clave de
idempotencia. "Calendar Event ID" evita crear el evento dos veces si el
envio de email fallo en una corrida anterior y la fila se reintenta.
"""

from __future__ import annotations

from typing import Any

from notion_client import Client

from .schemas import (
    PROP_CALENDAR_EVENT_ID,
    PROP_EMAIL_ENVIADO,
    PROP_ESTADO,
    ESTADO_CONFIRMADO,
    ESTADO_NUEVO,
)


class NotionRepository:
    def __init__(self, token: str, data_source_id: str) -> None:
        self._client = Client(auth=token)
        self._data_source_id = data_source_id

    def find_pending_pages(self) -> list[dict[str, Any]]:
        response = self._client.data_sources.query(
            data_source_id=self._data_source_id,
            filter={
                "or": [
                    {"property": PROP_ESTADO, "select": {"is_empty": True}},
                    {"property": PROP_ESTADO, "select": {"equals": ESTADO_NUEVO}},
                    {"property": PROP_EMAIL_ENVIADO, "checkbox": {"equals": False}},
                ]
            },
        )
        return response.get("results", [])

    def set_calendar_event_id(self, page_id: str, event_id: str) -> None:
        self._client.pages.update(
            page_id=page_id,
            properties={PROP_CALENDAR_EVENT_ID: {"rich_text": [{"text": {"content": event_id}}]}},
        )

    def mark_confirmed(self, page_id: str) -> None:
        self._client.pages.update(
            page_id=page_id,
            properties={
                PROP_EMAIL_ENVIADO: {"checkbox": True},
                PROP_ESTADO: {"select": {"name": ESTADO_CONFIRMADO}},
            },
        )
