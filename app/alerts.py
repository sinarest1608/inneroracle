"""Optional email to the site owner (festival verse results and job failures).

Off unless ALERT_SMTP_USER and ALERT_SMTP_PASSWORD are set; then one message per
call over STARTTLS with certificate verification on. Callers must never put a
verse, its reference or a quote in the subject or body. Failures are logged by
exception type only and never raised: an email problem must not break a send.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import settings

log = logging.getLogger("inneroracle.alerts")

TIMEOUT = 20  # seconds


def configured() -> bool:
    return bool(settings.alert_smtp_user and settings.alert_smtp_password)


def send(subject: str, body: str) -> bool:
    if not configured():
        log.info("alert (email not configured): %s", subject)
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.alert_smtp_user
    msg["To"] = settings.alert_email_to or settings.alert_smtp_user
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings.alert_smtp_host, settings.alert_smtp_port,
                          timeout=TIMEOUT) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(settings.alert_smtp_user, settings.alert_smtp_password)
            smtp.send_message(msg)
    except Exception as e:  # noqa: BLE001 — SMTP errors can echo credentials
        log.error("alert email failed: %s (subject: %s)", type(e).__name__, subject)
        return False
    log.info("alert email sent: %s", subject)
    return True


async def notify(subject: str, body: str) -> bool:
    return await asyncio.to_thread(send, subject, body)
