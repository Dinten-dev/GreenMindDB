import logging

import resend

from app.config import settings

logger = logging.getLogger(__name__)


class EmailService:
    """Handles transactional email deliveries (like verification and alerts)."""

    @staticmethod
    def send_verification_email(to_email: str, token: str):
        if not settings.resend_api_key:
            # Fallback for local development if the API key is not set
            logger.info(f"!!! DEV-MODE: Verification Token for {to_email}: {token} !!!")
            return

        try:
            resend.api_key = settings.resend_api_key

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

            resend.Emails.send(
                {
                    "from": settings.email_from,
                    "to": to_email,
                    "subject": "Bestätige deine GreenMind Anmeldung",
                    "html": html_content,
                }
            )

            logger.info(f"Verification email successfully dispatched via Resend to {to_email}")

        except Exception as e:
            logger.error(f"Failed to send Resend email to {to_email}: {e}")


def send_notification_email(subject: str, body: str):
    """Send an internal notification email (e.g. contact form submissions)."""
    recipient = settings.contact_form_to
    if not recipient:
        logger.warning("CONTACT_FORM_TO not configured. Skipping notification email.")
        return

    if not settings.resend_api_key:
        logger.info(f"!!! DEV-MODE: Notification email skipped. Subject: {subject} !!!")
        return

    try:
        resend.api_key = settings.resend_api_key
        resend.Emails.send(
            {
                "from": settings.email_from,
                "to": recipient,
                "subject": subject,
                "text": body,
            }
        )
        logger.info(f"Notification email sent: {subject}")
    except Exception as e:
        logger.error(f"Failed to send notification email: {e}")
