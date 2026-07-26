"""Envio del correo de confirmacion via Gmail (SMTP + App Password), con el
.ics adjunto.

Se manda desde una cuenta de Gmail personal en vez de un proveedor
transaccional (Resend) porque no hay un dominio propio verificado: Gmail no
exige dominio para mandar a cualquier destinatario, solo una App Password de
la cuenta remitente (ver README, seccion "Gmail (envio de correo)").

El texto de los correos esta aislado en las funciones _asesoria_email y
_clases_email de mas abajo para que sea facil de editar sin tocar el resto
de la logica de envio.
"""

from __future__ import annotations

import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .schemas import AsesoriaSubmission, Submission


def _asesoria_email(submission: AsesoriaSubmission) -> tuple[str, str]:
    fecha_texto = submission.fecha.strftime("%d/%m/%Y")
    subject = "Confirmación de tu solicitud de asesoría — ACV"
    html = f"""
        <p>Hola {submission.nombre},</p>
        <p>Recibimos tu solicitud de asesoría. Tu fecha límite registrada es el
        <strong>{fecha_texto}</strong>.</p>
        <p>Te vamos a contactar antes de esa fecha para coordinar los detalles.
        Adjuntamos un evento de referencia para que lo agregues a tu
        calendario.</p>
        <p>Gracias por confiar en ACV.</p>
        <p>— Andrés Cubillo</p>
    """
    return subject, html


def _clases_email(submission: Submission) -> tuple[str, str]:
    fecha_texto = submission.fecha_hora.strftime("%d/%m/%Y a las %H:%M")
    subject = "Confirmación de tu clase — ACV"
    html = f"""
        <p>Hola {submission.nombre},</p>
        <p>Tu clase quedó agendada para el <strong>{fecha_texto}</strong>.</p>
        <p>Adjuntamos el evento para que lo agregues a tu calendario. Cualquier
        cambio de horario avísanos con anticipación.</p>
        <p>Nos vemos pronto.</p>
        <p>— Andrés Cubillo</p>
    """
    return subject, html


def _build_message(gmail_address: str, submission: Submission, ics_bytes: bytes) -> MIMEMultipart:
    if isinstance(submission, AsesoriaSubmission):
        subject, html = _asesoria_email(submission)
    else:
        subject, html = _clases_email(submission)

    message = MIMEMultipart("mixed")
    message["Subject"] = subject
    message["From"] = gmail_address
    message["To"] = submission.correo
    message.attach(MIMEText(html, "html"))

    attachment = MIMEBase("text", "calendar")
    attachment.set_payload(ics_bytes)
    encoders.encode_base64(attachment)
    attachment.add_header("Content-Disposition", "attachment", filename="evento.ics")
    message.attach(attachment)

    return message


def send_confirmation_email(
    gmail_address: str, gmail_app_password: str, submission: Submission, ics_bytes: bytes
) -> None:
    message = _build_message(gmail_address, submission, ics_bytes)

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(gmail_address, gmail_app_password)
        server.sendmail(gmail_address, [submission.correo], message.as_string())
