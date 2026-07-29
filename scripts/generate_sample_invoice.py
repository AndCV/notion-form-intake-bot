"""Generates one sample invoice PDF locally, with made-up data, so the
design can be reviewed quickly without touching Notion, Calendar or Gmail.

Only reads the INVOICE_* environment variables (the ones that affect the
letterhead) -- none of the other required config (NOTION_TOKEN, service
account, etc.) is needed to run this.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from lib.invoicing import build_invoice_pdf
from lib.schemas import AdvisorySubmission


def _sample_config() -> SimpleNamespace:
    return SimpleNamespace(
        invoice_issuer_name=os.environ.get("INVOICE_ISSUER_NAME", "Your Name"),
        invoice_tagline=os.environ.get("INVOICE_TAGLINE", ""),
        invoice_phone=os.environ.get("INVOICE_PHONE", ""),
        invoice_email=os.environ.get("INVOICE_EMAIL", ""),
        invoice_address=os.environ.get("INVOICE_ADDRESS", ""),
        invoice_website=os.environ.get("INVOICE_WEBSITE", ""),
        invoice_payment_method=os.environ.get("INVOICE_PAYMENT_METHOD", "Bank transfer"),
        invoice_currency_symbol=os.environ.get("INVOICE_CURRENCY_SYMBOL", "$"),
    )


def _sample_submission() -> AdvisorySubmission:
    return AdvisorySubmission(
        page_id="sample",
        name="Dayanna Amador",
        email="cliente@example.com",
        deadline=date.today(),
        calendar_event_id=None,
        email_sent=True,
        price=15000,
        detail="Examen de contabilidad",
        invoice_number=None,
        invoice_sent=False,
    )


def main() -> None:
    config = _sample_config()
    submission = _sample_submission()

    pdf_bytes = build_invoice_pdf(config, submission, "0000001", date.today())

    output_path = Path(__file__).resolve().parent.parent / "sample_invoice.pdf"
    output_path.write_bytes(pdf_bytes)
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
