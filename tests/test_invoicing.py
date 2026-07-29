from datetime import date
from types import SimpleNamespace

from lib.invoicing import (
    build_invoice_pdf,
    format_currency,
    format_invoice_number,
    render_invoice_html,
)
from lib.schemas import AdvisorySubmission


def _config(**overrides):
    base = dict(
        invoice_issuer_name="Test Issuer",
        invoice_tagline="Test Tagline",
        invoice_phone="555-1234",
        invoice_email="issuer@example.com",
        invoice_address="Somewhere",
        invoice_website="https://example.com",
        invoice_payment_method="Bank transfer",
        invoice_currency_symbol="$",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _submission(**overrides):
    base = dict(
        page_id="page-1",
        name="Ana Lopez",
        email="ana@example.com",
        deadline=date(2026, 8, 1),
        calendar_event_id=None,
        email_sent=True,
        price=15000,
        detail="Consulting session",
    )
    base.update(overrides)
    return AdvisorySubmission(**base)


def test_format_invoice_number_pads_to_seven_digits():
    assert format_invoice_number(1) == "0000001"
    assert format_invoice_number(1234567) == "1234567"


def test_format_currency_has_no_thousands_separator():
    assert format_currency(15000, "$") == "$15000"


def test_render_invoice_html_includes_key_fields():
    html = render_invoice_html(_config(), _submission(), "0000001", date(2026, 7, 29))

    assert "Test Issuer" in html
    assert "Ana Lopez" in html
    assert "Consulting session" in html
    assert "$15000" in html
    assert "0000001" in html
    assert "29 / 07 / 2026" in html


def test_build_invoice_pdf_produces_valid_pdf_bytes():
    pdf_bytes = build_invoice_pdf(_config(), _submission(), "0000001", date(2026, 7, 29))

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000
