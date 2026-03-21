"""Email service – encapsulates SMTP logic for contact and notification emails.

Extracted from the contact router to follow the service layer pattern.
This makes email sending testable, reusable, and keeps routers thin.
"""

import smtplib
from email.message import EmailMessage

from fastapi import HTTPException

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


def send_notification_email(subject: str, body: str) -> None:
    """Send a plain-text email via SMTP.

    Args:
        subject: Email subject line.
        body: Plain-text email body.

    Raises:
        HTTPException: If email delivery fails (502).
    """
    if not settings.smtp_user or not settings.smtp_password:
        logger.warning(
            "SMTP credentials not configured – email not sent",
            extra={"subject": subject},
        )
        logger.info("Would have sent email:\nSubject: %s\n%s", subject, body)
        return

    if not settings.email_receiver:
        logger.warning("No email receiver configured – skipping email")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user
    msg["To"] = settings.email_receiver
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        logger.info("Email sent successfully", extra={"subject": subject})
    except Exception as exc:
        logger.error("Failed to send email: %s", exc, extra={"subject": subject})
        raise HTTPException(
            status_code=502,
            detail="Email delivery failed. Please try again later.",
        ) from exc
