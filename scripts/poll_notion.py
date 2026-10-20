"""Polling entry point: queries pending rows in Notion and, for each one,
creates the Calendar event (if applicable) and sends the confirmation email.

Meant to run via cron (GitHub Actions or otherwise). Each row is processed
in isolation: if one fails, it's logged and the run continues with the rest
— a row with invalid data shouldn't stop everything else.
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from lib.calendar_client import CalendarRepository
from lib.config import Config, ConfigError, get_config
from lib.email_client import send_confirmation_email, send_invoice_email
from lib.ics import build_ics
from lib.invoicing import build_invoice_pdf, format_invoice_number
from lib.notion_client import NotionRepository
from lib.schemas import AdvisorySubmission, ClassSubmission, RowError, submission_from_page

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("poll_notion")


def process_page(config: Config, notion: NotionRepository, page: dict) -> None:
    submission = submission_from_page(page)
    page_id = submission.page_id

    needs_event = isinstance(submission, ClassSubmission) or (
        isinstance(submission, AdvisorySubmission) and config.create_advisory_calendar_event
    )
    if needs_event and not submission.calendar_event_id:
        calendar = CalendarRepository(config.google_credentials_info(), config.google_calendar_id)
        event = calendar.create_event_for_submission(
            submission, config.timezone, config.class_duration_minutes
        )
        notion.set_calendar_event_id(page_id, event["id"])
        logger.info("Calendar event created (page_id=%s)", page_id)

    if not submission.email_sent:
        ics_bytes = build_ics(submission, config.timezone, config.class_duration_minutes)
        send_confirmation_email(config, submission, ics_bytes)
        logger.info("Confirmation email sent (page_id=%s)", page_id)

    notion.mark_confirmed(page_id)
    logger.info("Row marked as confirmed (page_id=%s)", page_id)

    if submission.price is not None and not submission.invoice_sent:
        invoice_number = format_invoice_number(notion.get_last_invoice_number() + 1)
        pdf_bytes = build_invoice_pdf(config, submission, invoice_number, date.today())
        send_invoice_email(config, submission, pdf_bytes, invoice_number)
        notion.mark_invoiced(page_id, invoice_number)
        logger.info(
            "Invoice %s generated and sent (page_id=%s)", invoice_number, page_id
        )


def run() -> None:
    config = get_config()
    for name, value in (
        ("NOTION_TOKEN", config.notion_token),
        ("NOTION_DATA_SOURCE_ID", config.notion_data_source_id),
    ):
        if not value:
            raise ConfigError(f"Missing environment variable {name}")
    notion = NotionRepository(config.notion_token, config.notion_data_source_id)

    pending = notion.find_pending_pages()
    logger.info("Pending rows found: %d", len(pending))

    processed = 0
    failed = 0
    for page in pending:
        try:
            process_page(config, notion, page)
            processed += 1
        except RowError as exc:
            failed += 1
            logger.warning("Invalid row (page_id=%s): %s", page.get("id"), exc)
        except Exception:
            failed += 1
            logger.exception("Failed processing row (page_id=%s)", page.get("id"))

    logger.info("Run finished: %d processed, %d failed", processed, failed)


if __name__ == "__main__":
    run()
