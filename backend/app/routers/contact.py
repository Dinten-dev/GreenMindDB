"""Contact & Early Access form endpoints with email delivery."""

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.schemas.contact import ContactRequest, EarlyAccessRequest
from app.services.email_service import send_notification_email

router = APIRouter(tags=["contact"])

limiter = Limiter(key_func=get_remote_address)


# ── Endpoints ──────────────────────────────────────


@router.post("/contact")
@limiter.limit("5/minute")
async def submit_contact(request: Request, payload: ContactRequest):
    """Handle Contact form submission."""
    # Honeypot check
    if payload.website:
        return {"status": "ok"}

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    body = (
        f"New Website Contact Request\n"
        f"{'=' * 40}\n\n"
        f"Name:    {payload.name}\n"
        f"Email:   {payload.email}\n"
        f"Company: {payload.company or '—'}\n\n"
        f"Message:\n{payload.message}\n\n"
        f"Submitted at: {timestamp}\n"
    )

    send_notification_email("New GreenMind Website Contact Request", body)
    return {"status": "ok"}


@router.post("/early-access")
@limiter.limit("5/minute")
async def submit_early_access(request: Request, payload: EarlyAccessRequest):
    """Handle Early Access form submission."""
    # Honeypot check
    if payload.website:
        return {"status": "ok"}

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

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

    send_notification_email("New GreenMind Early Access Request", body)
    return {"status": "ok"}
