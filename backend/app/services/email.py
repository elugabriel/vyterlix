# ruff: noqa: E501
"""Outgoing email. Services build an EmailMessage and hand it to an EmailSender.

The console backend (dev and test only) writes the email to the log. The SMTP backend sends it
through any provider that offers SMTP, chosen by settings, behind the same EmailSender interface.
"""

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
from email.utils import formataddr, parseaddr
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


class EmailDeliveryError(Exception):
    """The provider did not take the email. Nothing was sent."""


class SmtpEmailSender:
    """Sends through an SMTP server (STARTTLS by default). A new connection for each email: emails
    here are few, and it keeps a dropped connection from ever affecting the next one."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        *,
        use_tls: bool = True,
        timeout: float = 20.0,
        sender: str = "",
        smtp_class=smtplib.SMTP,
    ) -> None:
        self._host, self._port, self._username, self._password = host, port, username, password
        self._use_tls, self._timeout, self._from, self._smtp = use_tls, timeout, sender, smtp_class

    def send(self, message: EmailMessage) -> None:
        mime = MimeMessage()
        name, address = parseaddr(self._from)
        mime["From"] = formataddr((name, address)) if address else self._from
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.body)
        try:
            with self._smtp(self._host, self._port, timeout=self._timeout) as server:
                if self._use_tls:
                    server.starttls(context=ssl.create_default_context())
                if self._username:
                    server.login(self._username, self._password or "")
                server.send_message(mime)
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailDeliveryError(f"{type(exc).__name__}") from exc


def get_email_sender() -> EmailSender:
    """FastAPI dependency. Tests override this with an in-memory outbox."""
    settings = get_settings()
    if settings.email_backend == "smtp":
        return SmtpEmailSender(
            settings.smtp_host, settings.smtp_port, settings.smtp_username,
            None if settings.smtp_password is None else settings.smtp_password.get_secret_value(),
            use_tls=settings.smtp_use_tls, timeout=settings.smtp_timeout_seconds, sender=settings.email_from,
        )  # fmt: skip
    return ConsoleEmailSender()
