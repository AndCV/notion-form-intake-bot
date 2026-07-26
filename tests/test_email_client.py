from datetime import date, datetime
from email import message_from_string
from email.header import decode_header

from lib.email_client import send_confirmation_email
from lib.schemas import AsesoriaSubmission, ClasesSubmission


def _asesoria_submission():
    return AsesoriaSubmission(
        page_id="page-1",
        nombre="Ana",
        correo="ana@example.com",
        fecha=date(2026, 8, 1),
        calendar_event_id=None,
        email_enviado=False,
    )


def _clases_submission():
    return ClasesSubmission(
        page_id="page-2",
        nombre="Beto",
        correo="beto@example.com",
        fecha_hora=datetime(2026, 8, 2, 15, 30, 0),
        calendar_event_id=None,
        email_enviado=False,
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
    raise AssertionError("No se encontro parte text/html en el mensaje")


def _send_and_parse(mocker, submission):
    fake_smtp = mocker.Mock()
    fake_smtp.__enter__ = mocker.Mock(return_value=fake_smtp)
    fake_smtp.__exit__ = mocker.Mock(return_value=False)
    mocker.patch("lib.email_client.smtplib.SMTP", return_value=fake_smtp)

    send_confirmation_email("andres@gmail.com", "app-password", submission, b"ICS")

    fake_smtp.starttls.assert_called_once()
    fake_smtp.login.assert_called_once_with("andres@gmail.com", "app-password")
    from_addr, to_addrs, message_str = fake_smtp.sendmail.call_args[0]
    assert from_addr == "andres@gmail.com"
    assert to_addrs == [submission.correo]
    return message_from_string(message_str)


def test_asesoria_email_mentions_deadline_and_signature(mocker):
    message = _send_and_parse(mocker, _asesoria_submission())

    assert _decode_subject(message) == "Confirmación de tu solicitud de asesoría — ACV"
    body = _decoded_html_body(message)
    assert "01/08/2026" in body
    assert "Andrés Cubillo" in body
    assert "Equipo ACV" not in body


def test_clases_email_mentions_datetime(mocker):
    message = _send_and_parse(mocker, _clases_submission())

    assert _decode_subject(message) == "Confirmación de tu clase — ACV"
    body = _decoded_html_body(message)
    assert "02/08/2026" in body
    assert "15:30" in body


def test_ics_attachment_is_included(mocker):
    message = _send_and_parse(mocker, _asesoria_submission())

    attachments = [
        part for part in message.walk() if part.get_filename() == "evento.ics"
    ]
    assert len(attachments) == 1
    assert attachments[0].get_payload(decode=True) == b"ICS"
