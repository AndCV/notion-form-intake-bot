"""Sends the confirmation email via Gmail (SMTP + App Password), with the
.ics attached.

Sent from a personal Gmail account instead of a transactional provider
(e.g. Resend) because there's no verified custom domain: Gmail doesn't
require one to send to any recipient, only an App Password for the sending
account (see README, "Gmail (sending email)" section).

The email copy itself is isolated in _advisory_email and _class_email below
so it's easy to edit without touching the rest of the send logic. The copy
stays in Spanish on purpose: it's real content sent to real Spanish-speaking
clients, not code.
"""

from __future__ import annotations

import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .schemas import AdvisorySubmission, Submission


def _advisory_email(submission: AdvisorySubmission, business_name: str) -> tuple[str, str]:
    date_text = submission.deadline.strftime("%d/%m/%Y")
    subject = f"Confirmación de tu solicitud de asesoría — {business_name}"
    html = f"""
        <p>Hola {submission.name},</p>
        <p>Recibimos tu solicitud de asesoría. Tu fecha límite registrada es el
        <strong>{date_text}</strong>.</p>
        <p>Te vamos a contactar antes de esa fecha para coordinar los detalles.
        Adjuntamos un evento de referencia para que lo agregues a tu
        calendario.</p>
        <p>Gracias por confiar en {business_name}.</p>
        <p>— Andrés Cubillo</p>
    """
    return subject, html


def _class_email(submission: Submission, business_name: str) -> tuple[str, str]:
    date_text = submission.scheduled_at.strftime("%d/%m/%Y a las %H:%M")
    subject = f"Confirmación de tu clase — {business_name}"
    html = f"""
        <p>Hola {submission.name},</p>
        <p>Tu clase quedó agendada para el <strong>{date_text}</strong>.</p>
        <p>Adjuntamos el evento para que lo agregues a tu calendario. Cualquier
        cambio de horario avísanos con anticipación.</p>
        <p>Nos vemos pronto.</p>
        <p>— Andrés Cubillo</p>
    """
    return subject, html


def _build_message(
    gmail_address: str, submission: Submission, ics_bytes: bytes, business_name: str
) -> MIMEMultipart:
    if isinstance(submission, AdvisorySubmission):
        subject, html = _advisory_email(submission, business_name)
    else:
        subject, html = _class_email(submission, business_name)

    message = MIMEMultipart("mixed")
    message["Subject"] = subject
    message["From"] = gmail_address
    message["To"] = submission.email
    message.attach(MIMEText(html, "html"))

    attachment = MIMEBase("text", "calendar")
    attachment.set_payload(ics_bytes)
    encoders.encode_base64(attachment)
    attachment.add_header("Content-Disposition", "attachment", filename="evento.ics")
    message.attach(attachment)

    return message


def send_confirmation_email(
    gmail_address: str,
    gmail_app_password: str,
    submission: Submission,
    ics_bytes: bytes,
    business_name: str,
) -> None:
    message = _build_message(gmail_address, submission, ics_bytes, business_name)

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(gmail_address, gmail_app_password)
        server.sendmail(gmail_address, [submission.email], message.as_string())


def send_invoice_email(
    gmail_address: str,
    gmail_app_password: str,
    submission: Submission,
    pdf_bytes: bytes,
    invoice_number: str,
) -> None:
    message = MIMEMultipart("mixed")
    message["Subject"] = f"Factura {invoice_number}"
    message["From"] = gmail_address
    message["To"] = submission.email
    message.attach(
        MIMEText(f"<p>Hola {submission.name},</p><p>Adjuntamos tu factura.</p>", "html")
    )

    attachment = MIMEBase("application", "pdf")
    attachment.set_payload(pdf_bytes)
    encoders.encode_base64(attachment)
    attachment.add_header(
        "Content-Disposition", "attachment", filename=f"factura-{invoice_number}.pdf"
    )
    message.attach(attachment)

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(gmail_address, gmail_app_password)
        server.sendmail(gmail_address, [submission.email], message.as_string())
