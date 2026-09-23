"""Outgoing email. Services build an EmailMessage and hand it to an EmailSender.

Only the console backend exists for now; a real provider is chosen in Phase 14
(Alerts + Notifications) and plugs in behind the same EmailSender interface.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger("vyterlix.email")


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    def send(self, message: EmailMessage) -> None: ...


class ConsoleEmailSender:
    """Writes the email to the log. Blocked in prod by Settings validation."""

    def send(self, message: EmailMessage) -> None:
        logger.info(
            "email.console",
            extra={
                "ctx": {
                    "from": get_settings().email_from,
                    "to": message.to,
                    "subject": message.subject,
                    "body": message.body,
                }
            },
        )


def get_email_sender() -> EmailSender:
    """FastAPI dependency. Tests override this with an in-memory outbox."""
    return ConsoleEmailSender()
