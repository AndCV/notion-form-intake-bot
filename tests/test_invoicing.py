from datetime import date
from types import SimpleNamespace

from lib.invoicing import (
    build_invoice_pdf,
    format_currency,
    format_invoice_number,
    render_invoice_html,
    render_receipt_html,
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


def test_invoice_keeps_its_title_labels_terms_and_payment_method():
    """protects: making the template shared didn't change the client invoice;
    fails_when: the invoice loses FACTURA, its labels, terms or payment method;
    no_claim: visual layout is checked by eye with generate_sample_invoice.py."""
    html = render_invoice_html(_config(), _submission(), "0000001", date(2026, 7, 29))

    assert "<h1>FACTURA</h1>" in html
    assert "FACTURAR A:" in html and "Factura N.&ordm;:" in html
    assert "Bank transfer" in html and "Ministerio de Hacienda" in html
    assert "RECIBO" not in html


def test_receipt_names_the_collaborator_and_only_their_amount():
    """protects: the collaborator receipt is titled, addressed and priced correctly;
    fails_when: it reads as an invoice, shows a bank line, or the wrong amount;
    no_claim: none beyond protects."""
    html = render_receipt_html(
        _config(), "Colaborador Uno", "Cliente: X · Trabajo: Y", 15000, "0000007", date(2026, 10, 20)
    )

    assert "<h1>RECIBO DE PAGO</h1>" in html
    assert "PAGADO A:" in html and "Recibo N.&ordm;:" in html and "0000007" in html
    assert "Colaborador Uno" in html and "$15000" in html
    assert "MÉTODO DE PAGO" not in html and "M&Eacute;TODO DE PAGO" not in html
    assert "<h1>FACTURA</h1>" not in html


def test_invoice_and_receipt_leave_out_the_tagline():
    """protects: the owner's request to drop the tagline from the invoice
    (and the receipt, which shares its letterhead);
    fails_when: INVOICE_TAGLINE shows up on either document;
    no_claim: the email signature, which still uses it."""
    invoice = render_invoice_html(_config(), _submission(), "0000001", date(2026, 7, 29))
    receipt = render_receipt_html(_config(), "Eduardo", "Trabajo", 5000, "0000001", date(2026, 7, 29))

    assert "Test Tagline" not in invoice and "Test Tagline" not in receipt
    assert "Test Issuer" in invoice and "Test Issuer" in receipt
