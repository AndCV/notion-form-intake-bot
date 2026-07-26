"""Notion repository for the polling model.

No need to look up rows by a submission id: every run queries directly for
pending rows (Status empty/"nuevo" or Email Sent unchecked), and the
`page_id` that query already returns works as the idempotency key.
"Calendar Event ID" prevents creating the event twice if the email send
failed in a previous run and the row gets retried.
"""

from __future__ import annotations

from typing import Any

from notion_client import Client

from .schemas import (
    PROP_CALENDAR_EVENT_ID,
    PROP_EMAIL_SENT,
    PROP_STATUS,
    STATUS_CONFIRMED,
    STATUS_NEW,
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
                    {"property": PROP_STATUS, "select": {"is_empty": True}},
                    {"property": PROP_STATUS, "select": {"equals": STATUS_NEW}},
                    {"property": PROP_EMAIL_SENT, "checkbox": {"equals": False}},
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
                PROP_EMAIL_SENT: {"checkbox": True},
                PROP_STATUS: {"select": {"name": STATUS_CONFIRMED}},
            },
        )
