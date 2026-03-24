"""Business logic services for the GreenMind backend.

Services encapsulate business logic and database operations,
keeping routers thin and focused on HTTP concerns.

Usage:
    from app.services.email_service import EmailService
"""

from app.services.email_service import EmailService, send_notification_email

__all__ = ["EmailService", "send_notification_email"]
