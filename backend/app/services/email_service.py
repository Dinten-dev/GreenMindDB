import logging
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

import resend

from app.config import settings

logger = logging.getLogger(__name__)


def dispatch_email(to_email: str, subject: str, *, text: str = "", html: str = "") -> bool:
    """True means provider accepted dispatch, never proof of inbox delivery."""
    if settings.environment.lower() in {"stage", "staging"} and (
        not settings.staging_email_to or to_email.casefold() != settings.staging_email_to.casefold()
    ):
        return False
    try:
        if settings.email_transport == "smtp":
            if not settings.smtp_username or not settings.smtp_password_file:
                return False
            password = Path(settings.smtp_password_file).read_text().rstrip("\n")
            if not password:
                return False
            message = EmailMessage()
            message["From"] = settings.email_from
            message["To"] = to_email
            message["Subject"] = subject
            message.set_content(
                text or "Bitte öffne diese Nachricht in einem HTML-fähigen Mailprogramm."
            )
            if html:
                message.add_alternative(html, subtype="html")
            with smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=10,
                context=ssl.create_default_context(),
            ) as smtp:
                smtp.login(settings.smtp_username, password)
                return not smtp.send_message(message)
        if not settings.resend_api_key:
            return False
        resend.api_key = settings.resend_api_key
        payload = {"from": settings.email_from, "to": to_email, "subject": subject}
        payload["html" if html else "text"] = html or text
        result = resend.Emails.send(payload)
        return bool(result and result.get("id"))
    except Exception:
        logger.error("Mail provider did not accept dispatch")
        return False


class EmailService:
    """Handles transactional email deliveries (like verification and alerts)."""

    @staticmethod
    def send_invitation_email(to_email: str, token: str) -> bool:
        # Fragments never reach HTTP access logs or Referer headers.
        link = f"{settings.frontend_url.rstrip('/')}/signup#invitation_token={token}"
        return dispatch_email(
            to_email,
            "Deine GreenMind Einladung",
            text=f"Dein Zugang zu GreenMind: {link}\nDie Einladung gilt 48 Stunden und kann einmal eingelöst werden.",
        )

    @staticmethod
    def send_verification_email(to_email: str, token: str):
        # The URL pointing to the Next.js frontend verify page
        verification_link = f"{settings.frontend_url}/verify?token={token}"

        html_content = f"""
        <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #1d1d1f;">Willkommen bei GreenMind</h2>
            <p style="color: #515154; font-size: 16px; line-height: 1.5;">
                Vielen Dank für deine Registrierung auf der Plattform.
            </p>
            <p style="color: #515154; font-size: 16px; line-height: 1.5;">
                Bitte verifiziere deine E-Mail-Adresse, um unsere Edge Gateways provisionieren zu können:
            </p>
            <div style="margin: 30px 0;">
                <a href="{verification_link}" style="background-color: #0071e3; color: white; padding: 12px 24px; text-decoration: none; border-radius: 8px; font-weight: 600;">Account aktivieren</a>
            </div>
            <p style="color: #86868b; font-size: 12px;">Wenn du diesen Account nicht angefordert hast, kannst du diese E-Mail ignorieren.</p>
        </div>
        """
        return dispatch_email(to_email, "Bestätige deine GreenMind Anmeldung", html=html_content)


def send_notification_email(subject: str, body: str):
    """Send an internal notification email (e.g. contact form submissions)."""
    recipient = settings.contact_form_to
    if not recipient:
        logger.warning("CONTACT_FORM_TO not configured. Skipping notification email.")
        return

    return dispatch_email(recipient, subject, text=body)
