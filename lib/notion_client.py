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
    PROP_INVOICE_NUMBER,
    PROP_INVOICE_SENT,
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

    def get_last_invoice_number(self) -> int:
        """Highest already-assigned invoice number, or 0 if none yet.

        Invoice numbers are zero-padded to a fixed width, so sorting the
        property as text gives the same order as sorting it numerically.
        This reads the existing database as the single source of truth
        instead of keeping a separate counter, so a number is only ever
        "used" once an invoice is actually generated -- rows that never get
        invoiced don't create gaps.
        """
        response = self._client.data_sources.query(
            data_source_id=self._data_source_id,
            filter={"property": PROP_INVOICE_NUMBER, "rich_text": {"is_not_empty": True}},
            sorts=[{"property": PROP_INVOICE_NUMBER, "direction": "descending"}],
            page_size=1,
        )
        results = response.get("results", [])
        if not results:
            return 0
        rich_text = results[0]["properties"][PROP_INVOICE_NUMBER]["rich_text"]
        return int(rich_text[0]["plain_text"])

    def mark_invoiced(self, page_id: str, invoice_number: str) -> None:
        self._client.pages.update(
            page_id=page_id,
            properties={
                PROP_INVOICE_NUMBER: {"rich_text": [{"text": {"content": invoice_number}}]},
                PROP_INVOICE_SENT: {"checkbox": True},
            },
        )
