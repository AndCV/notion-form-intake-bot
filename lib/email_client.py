"""Sends the confirmation email via Zoho Mail (SMTP over SSL + App
Password), with the .ics attached.

Sent from a personal Zoho Mail account instead of a transactional provider
(e.g. Resend) because there's no verified custom domain: Zoho doesn't
require one to send to any recipient, only an App Password for the sending
account (see README, "Zoho Mail (sending email)" section).

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

from .config import Config
from .schemas import AdvisorySubmission, Submission, recipients

# Same palette as the invoice letterhead (see templates/invoice.html), so the
# email signature matches the PDF branding.
_NAVY = "#0F226F"
_TEAL = "#3CCACF"
_GRAY = "#6A6F86"

# Personal (@zohomail.com) and free-plan accounts use smtp.zoho.com; paid
# organization accounts on a custom domain use smtppro.zoho.com instead.
_SMTP_HOST = "smtp.zoho.com"
_SMTP_PORT = 465


def _wrap_email_html(content: str) -> str:
    """Wraps the email body in an explicit white card.

    Gmail's dark theme repaints any color it judges "close to black" (like
    our navy) to a lighter shade for contrast, and light backgrounds
    (#EEF0F6 pills) to a darker one -- silently breaking the brand palette.
    An explicit bgcolor (attribute, not just CSS) on every colored cell,
    plus the color-scheme meta tags, tells Gmail to leave it alone.
    """
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
</head>
<body style="margin:0;padding:0;background-color:#ffffff;" bgcolor="#ffffff">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background-color:#ffffff;" bgcolor="#ffffff">
    <tr>
        <td align="center" style="padding:24px 12px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                   style="max-width:560px;background-color:#ffffff;" bgcolor="#ffffff">
                <tr>
                    <td style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1a1a1a;">
                        {content}
                    </td>
                </tr>
            </table>
        </td>
    </tr>
</table>
</body>
</html>"""


def _signature_html(config: Config) -> str:
    tagline_row = (
        f'<p style="margin:3px 0 0 0;font-size:12px;color:{_GRAY};background-color:#ffffff;">'
        f"{config.invoice_tagline}</p>"
        if config.invoice_tagline
        else ""
    )
    website_display = config.invoice_website.removeprefix("https://").removeprefix("http://")
    badge_links = (
        (config.invoice_phone, f"tel:{config.invoice_phone}"),
        (config.invoice_email, f"mailto:{config.invoice_email}"),
        (website_display, config.invoice_website),
    )
    # Gmail's dark theme repaints any "dark text on white" or "light bg" pair
    # it judges unstyled -- even past an explicit bgcolor on a parent <td>.
    # The mitigation that actually holds is pairing color: with a
    # background-color: in the SAME style attribute as the text itself, on
    # every element down to the leaf (span/a), not just the containing cell.
    badges = "".join(
        f"""
        <td style="background-color:#EEF0F6;border-radius:14px;padding:5px 12px;
                   font-size:11px;white-space:nowrap;" bgcolor="#EEF0F6">
            <a href="{href}" style="color:#1a1a1a;background-color:#EEF0F6;text-decoration:none;">
                <span style="color:{_TEAL};background-color:#EEF0F6;">&#9679;</span> {display}
            </a>
        </td>
        <td style="width:8px;"></td>
        """
        for display, href in badge_links
        if display
    )
    return f"""
        <table role="presentation" cellpadding="0" cellspacing="0"
               style="margin-top:24px;border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;background-color:#ffffff;" bgcolor="#ffffff">
            <tr>
                <td style="background-color:{_NAVY};width:5px;" bgcolor="{_NAVY}"></td>
                <td style="background-color:{_TEAL};width:3px;" bgcolor="{_TEAL}"></td>
                <td style="padding:8px 0 10px 16px;background-color:#ffffff;" bgcolor="#ffffff">
                    <p style="margin:0;font-size:16px;font-weight:bold;color:{_NAVY};background-color:#ffffff;">{config.invoice_issuer_name}</p>
                    {tagline_row}
                    <table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:8px;border-collapse:separate;">
                        <tr>{badges}</tr>
                    </table>
                </td>
            </tr>
        </table>
    """


def _advisory_email(submission: AdvisorySubmission, config: Config) -> tuple[str, str]:
    date_text = submission.deadline.strftime("%d/%m/%Y")
    subject = f"Confirmación de tu solicitud de asesoría — {config.business_name}"
    html = _wrap_email_html(f"""
        <p>Hola {submission.name},</p>
        <p>Recibimos tu solicitud de asesoría. Tu fecha límite registrada es el
        <strong>{date_text}</strong>.</p>
        <p>Te vamos a contactar antes de esa fecha para coordinar los detalles.
        Adjuntamos un evento de referencia para que lo agregues a tu
        calendario.</p>
        <p>Gracias por confiar en {config.business_name}.</p>
        {_signature_html(config)}
    """)
    return subject, html


def _class_email(submission: Submission, config: Config) -> tuple[str, str]:
    date_text = submission.scheduled_at.strftime("%d/%m/%Y a las %H:%M")
    subject = f"Confirmación de tu clase — {config.business_name}"
    html = _wrap_email_html(f"""
        <p>Hola {submission.name},</p>
        <p>Tu clase quedó agendada para el <strong>{date_text}</strong>.</p>
        <p>Adjuntamos el evento para que lo agregues a tu calendario. Cualquier
        cambio de horario avísanos con anticipación.</p>
        <p>Nos vemos pronto.</p>
        {_signature_html(config)}
    """)
    return subject, html


def _build_message(submission: Submission, ics_bytes: bytes, config: Config) -> MIMEMultipart:
    if isinstance(submission, AdvisorySubmission):
        subject, html = _advisory_email(submission, config)
    else:
        subject, html = _class_email(submission, config)

    message = MIMEMultipart("mixed")
    message["Subject"] = subject
    message["From"] = config.zoho_address
    message["To"] = ", ".join(recipients(submission))
    message.attach(MIMEText(html, "html"))

    attachment = MIMEBase("text", "calendar")
    attachment.set_payload(ics_bytes)
    encoders.encode_base64(attachment)
    attachment.add_header("Content-Disposition", "attachment", filename="evento.ics")
    message.attach(attachment)

    return message


def _send(config: Config, to: list[str], message: MIMEMultipart) -> dict[str, tuple[int, bytes]]:
    """Sends the message; returns the recipients the server refused.

    smtplib raises SMTPRecipientsRefused only when *every* recipient is
    refused; a partial refusal comes back as this dict instead.
    """
    with smtplib.SMTP_SSL(_SMTP_HOST, _SMTP_PORT) as server:
        server.login(config.zoho_address, config.zoho_app_password)
        return server.sendmail(config.zoho_address, to, message.as_string())


def send_confirmation_email(
    config: Config, submission: Submission, ics_bytes: bytes
) -> dict[str, tuple[int, bytes]]:
    return _send(config, recipients(submission), _build_message(submission, ics_bytes, config))


def send_invoice_email(
    config: Config, submission: Submission, pdf_bytes: bytes, invoice_number: str
) -> dict[str, tuple[int, bytes]]:
    message = MIMEMultipart("mixed")
    message["Subject"] = f"Factura {invoice_number}"
    message["From"] = config.zoho_address
    message["To"] = ", ".join(recipients(submission))
    message.attach(
        MIMEText(
            _wrap_email_html(
                f"<p>Hola {submission.name},</p><p>Adjuntamos tu factura.</p>"
                f"{_signature_html(config)}"
            ),
            "html",
        )
    )

    attachment = MIMEBase("application", "pdf")
    attachment.set_payload(pdf_bytes)
    encoders.encode_base64(attachment)
    attachment.add_header(
        "Content-Disposition", "attachment", filename=f"factura-{invoice_number}.pdf"
    )
    message.attach(attachment)

    return _send(config, recipients(submission), message)
