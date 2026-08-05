from datetime import date, datetime
from email import message_from_string
from email.header import decode_header
from types import SimpleNamespace

from lib.email_client import send_confirmation_email
from lib.schemas import AdvisorySubmission, ClassSubmission

BUSINESS_NAME = "Test Co"


def _config(**overrides):
    base = dict(
        gmail_address="andres@gmail.com",
        gmail_app_password="app-password",
        business_name=BUSINESS_NAME,
        invoice_issuer_name="Andrés Cubillo",
        invoice_tagline="Test Tagline",
        invoice_phone="555-1234",
        invoice_email="andres@gmail.com",
        invoice_website="example.com",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _advisory_submission():
    return AdvisorySubmission(
        page_id="page-1",
        name="Ana",
        email="ana@example.com",
        deadline=date(2026, 8, 1),
        calendar_event_id=None,
        email_sent=False,
    )


def _class_submission():
    return ClassSubmission(
        page_id="page-2",
        name="Beto",
        email="beto@example.com",
        scheduled_at=datetime(2026, 8, 2, 15, 30, 0),
        calendar_event_id=None,
        email_sent=False,
    )


def _decode_subject(message) -> str:
    parts = decode_header(message["Subject"])
    return "".join(
        chunk.decode(encoding or "utf-8") if isinstance(chunk, bytes) else chunk
        for chunk, encoding in parts
    )


def _decoded_html_body(message) -> str:
    for part in message.walk():
        if part.get_content_type() == "text/html":
            return part.get_payload(decode=True).decode("utf-8")
    raise AssertionError("No text/html part found in the message")


def _send_and_parse(mocker, submission):
    fake_smtp = mocker.Mock()
    fake_smtp.__enter__ = mocker.Mock(return_value=fake_smtp)
    fake_smtp.__exit__ = mocker.Mock(return_value=False)
    mocker.patch("lib.email_client.smtplib.SMTP", return_value=fake_smtp)

    send_confirmation_email(_config(), submission, b"ICS")

    fake_smtp.starttls.assert_called_once()
    fake_smtp.login.assert_called_once_with("andres@gmail.com", "app-password")
    from_addr, to_addrs, message_str = fake_smtp.sendmail.call_args[0]
    assert from_addr == "andres@gmail.com"
    assert to_addrs == [submission.email]
    return message_from_string(message_str)


def test_advisory_email_mentions_deadline_business_name_and_signature(mocker):
    message = _send_and_parse(mocker, _advisory_submission())

    assert _decode_subject(message) == f"Confirmación de tu solicitud de asesoría — {BUSINESS_NAME}"
    body = _decoded_html_body(message)
    assert "01/08/2026" in body
    assert BUSINESS_NAME in body
    assert "Andrés Cubillo" in body


def test_class_email_mentions_datetime_and_business_name(mocker):
    message = _send_and_parse(mocker, _class_submission())

    assert _decode_subject(message) == f"Confirmación de tu clase — {BUSINESS_NAME}"
    body = _decoded_html_body(message)
    assert "02/08/2026" in body
    assert "15:30" in body


def test_ics_attachment_is_included(mocker):
    message = _send_and_parse(mocker, _advisory_submission())

    attachments = [part for part in message.walk() if part.get_filename() == "evento.ics"]
    assert len(attachments) == 1
    assert attachments[0].get_payload(decode=True) == b"ICS"
