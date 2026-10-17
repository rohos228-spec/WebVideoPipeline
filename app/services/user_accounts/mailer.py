"""Outgoing mail. Backends: console (default: log only, nothing leaves the machine), memory (tests), smtp."""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

from app import settings_auth

log = logging.getLogger("via.mail")
OUTBOX: list[dict] = []  # memory backend


def send_mail(to: str, subject: str, body: str, html: str | None = None, **_: object) -> bool:
    """Returns True only when really handed to an SMTP server."""
    backend = settings_auth.mail_backend()
    if backend == "memory":
        OUTBOX.append({"to": to, "subject": subject, "body": body, "html": html})
        return False
    if backend != "smtp" or not os.environ.get("SMTP_HOST"):
        log.warning("[VIA dev-mail] to=%s | %s\n%s", to, subject, body)
        print(f"[VIA dev-mail] to={to} | {subject}\n{body}", flush=True)
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings_auth.mail_from(), to, subject
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    host, port = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))
    user, pwd = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASSWORD", "")
    try:
        if os.environ.get("SMTP_SSL", "").lower() in {"1", "true"} or port == 465:
            srv: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context())
        else:
            srv = smtplib.SMTP(host, port, timeout=15)
            if os.environ.get("SMTP_STARTTLS", "1").lower() not in {"0", "false"}:
                srv.starttls(context=ssl.create_default_context())
        with srv:
            if user:
                srv.login(user, pwd)
            srv.send_message(msg)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("SMTP send failed to %s: %s", to, e)
        return False
