"""Punto de entrada del polling: consulta filas pendientes en Notion y para
cada una crea el evento de Calendar (si corresponde) y envia el correo de
confirmacion.

Pensado para correr via cron (GitHub Actions u otro). Cada fila se procesa de
forma aislada: si una falla, se loggea y se sigue con las demas — una fila
con datos invalidos no debe frenar el resto de la corrida.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from lib.calendar_client import CalendarRepository
from lib.config import Config, get_config
from lib.email_client import send_confirmation_email
from lib.ics import build_ics
from lib.notion_client import NotionRepository
from lib.schemas import AsesoriaSubmission, ClasesSubmission, RowError, submission_from_page

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("poll_notion")


def process_page(config: Config, notion: NotionRepository, page: dict) -> None:
    submission = submission_from_page(page)
    page_id = submission.page_id

    needs_event = isinstance(submission, ClasesSubmission) or (
        isinstance(submission, AsesoriaSubmission) and config.create_asesoria_calendar_event
    )
    if needs_event and not submission.calendar_event_id:
        calendar = CalendarRepository(config.google_credentials_info(), config.google_calendar_id)
        event = calendar.create_event_for_submission(
            submission, config.timezone, config.class_duration_minutes
        )
        notion.set_calendar_event_id(page_id, event["id"])
        logger.info("Evento de calendario creado (page_id=%s)", page_id)

    if not submission.email_enviado:
        ics_bytes = build_ics(submission, config.timezone, config.class_duration_minutes)
        send_confirmation_email(
            config.gmail_address, config.gmail_app_password, submission, ics_bytes
        )
        logger.info("Email de confirmacion enviado (page_id=%s)", page_id)

    notion.mark_confirmed(page_id)
    logger.info("Fila marcada como confirmada (page_id=%s)", page_id)


def run() -> None:
    config = get_config()
    notion = NotionRepository(config.notion_token, config.notion_data_source_id)

    pending = notion.find_pending_pages()
    logger.info("Filas pendientes encontradas: %d", len(pending))

    processed = 0
    failed = 0
    for page in pending:
        try:
            process_page(config, notion, page)
            processed += 1
        except RowError as exc:
            failed += 1
            logger.warning("Fila invalida (page_id=%s): %s", page.get("id"), exc)
        except Exception:
            failed += 1
            logger.exception("Fallo procesando fila (page_id=%s)", page.get("id"))

    logger.info("Corrida terminada: %d procesadas, %d con error", processed, failed)


if __name__ == "__main__":
    run()
