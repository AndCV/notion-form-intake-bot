"""Builds the invoice PDF: renders templates/invoice.html with Jinja2, then
converts it to PDF with WeasyPrint.

Kept free of any Notion/email dependency so it's easy to test and to run
standalone (see scripts/generate_sample_invoice.py). The invoice number
itself is computed by the caller (see NotionRepository.get_last_invoice_number)
and just passed in here already formatted.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

from .config import Config
from .schemas import Submission

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=True)


def format_invoice_number(number: int) -> str:
    return f"{number:07d}"


def format_currency(value: float, symbol: str) -> str:
    return f"{symbol}{value:,.0f}".replace(",", "")


def render_invoice_html(
    config: Config, submission: Submission, invoice_number: str, issue_date: date
) -> str:
    price = submission.price or 0
    formatted_price = format_currency(price, config.invoice_currency_symbol)

    template = _env.get_template("invoice.html")
    return template.render(
        issuer_name=config.invoice_issuer_name,
        tagline=config.invoice_tagline,
        invoice_number=invoice_number,
        issue_date=issue_date.strftime("%d / %m / %Y"),
        client_name=submission.name,
        item_description=submission.detail or "",
        item_price=formatted_price,
        item_total=formatted_price,
        payment_method=config.invoice_payment_method,
        phone=config.invoice_phone,
        email=config.invoice_email,
        address=config.invoice_address,
        website=config.invoice_website,
    )


def render_invoice_pdf(html: str) -> bytes:
    return HTML(string=html).write_pdf()


def build_invoice_pdf(
    config: Config, submission: Submission, invoice_number: str, issue_date: date
) -> bytes:
    html = render_invoice_html(config, submission, invoice_number, issue_date)
    return render_invoice_pdf(html)
