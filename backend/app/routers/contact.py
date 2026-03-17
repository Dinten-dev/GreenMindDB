"""Contact & Early Access form endpoints with email delivery."""

import smtplib
import logging
from datetime import datetime, timezone
from email.message import EmailMessage

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["contact"])

limiter = Limiter(key_func=get_remote_address)


# ── Schemas ────────────────────────────────────────

class ContactRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    company: str = Field(default="", max_length=200)
    message: str = Field(..., min_length=1, max_length=5000)
    # Honeypot: must be empty
    website: str = Field(default="", max_length=0)


class EarlyAccessRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    company: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    country: str = Field(..., min_length=1, max_length=100)
    message: str = Field(default="", max_length=5000)
    # Honeypot: must be empty
    website: str = Field(default="", max_length=0)


# ── Helpers ────────────────────────────────────────

def _send_email(subject: str, body: str) -> None:
    """Send a plain-text email via SMTP."""
    if not settings.smtp_user or not settings.smtp_password:
        logger.warning("SMTP credentials not configured – email not sent.")
        logger.info("Would have sent email:\nSubject: %s\n%s", subject, body)
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
        logger.info("Email sent: %s", subject)
    except Exception as exc:
        logger.error("Failed to send email: %s", exc)
        raise HTTPException(status_code=502, detail="Email delivery failed. Please try again later.")


# ── Endpoints ──────────────────────────────────────

@router.post("/contact")
@limiter.limit("5/minute")
async def submit_contact(request: Request, payload: ContactRequest):
    """Handle Contact form submission."""
    # Honeypot check
    if payload.website:
        return {"status": "ok"}

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    body = (
        f"New Website Contact Request\n"
        f"{'=' * 40}\n\n"
        f"Name:    {payload.name}\n"
        f"Email:   {payload.email}\n"
        f"Company: {payload.company or '—'}\n\n"
        f"Message:\n{payload.message}\n\n"
        f"Submitted at: {timestamp}\n"
    )

    _send_email("New GreenMind Website Contact Request", body)
    return {"status": "ok"}


@router.post("/early-access")
@limiter.limit("5/minute")
async def submit_early_access(request: Request, payload: EarlyAccessRequest):
    """Handle Early Access form submission."""
    # Honeypot check
    if payload.website:
        return {"status": "ok"}

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    body = (
        f"New Early Access Request\n"
        f"{'=' * 40}\n\n"
        f"Name:    {payload.name}\n"
        f"Company: {payload.company}\n"
        f"Email:   {payload.email}\n"
        f"Country: {payload.country}\n\n"
        f"Message:\n{payload.message or '—'}\n\n"
        f"Submitted at: {timestamp}\n"
    )

    _send_email("New GreenMind Early Access Request", body)
    return {"status": "ok"}
