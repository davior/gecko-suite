"""Account emails over SMTP. Ported from gecko-notes' mailer, settings from `app.config`.

Best-effort: a failure is logged, never raised, so a flaky mail server cannot fail a
sign-up. With SMTP unset nothing is sent and the link or code is logged instead, which
keeps every flow usable in development.
"""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Optional

import markdown as _markdown
from fastapi import BackgroundTasks
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings

logger = logging.getLogger(__name__)

APP_NAME = "Gecko Suite"
_env = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
                   autoescape=select_autoescape(["html"]))


def base_url() -> str:
    return settings.app_base_url.rstrip("/")


def _render(template: str, context: dict[str, Any]) -> tuple[str, str]:
    ctx = {"app_name": APP_NAME, "base_url": base_url(), **context}
    text = _env.get_template(template).render(**ctx)
    html = _env.get_template("layout.html").render(
        content=_markdown.markdown(text, extensions=["extra"]), **ctx)
    return text, html


def _send(to: str, subject: str, text: str, html: str) -> None:
    message = MIMEMultipart("alternative")
    message["Subject"], message["From"], message["To"] = subject, settings.smtp_from, to
    message.attach(MIMEText(text, "plain", "utf-8"))
    message.attach(MIMEText(html, "html", "utf-8"))
    try:
        if settings.smtp_ssl:
            server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout)
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout)
        with server:
            server.ehlo()
            if settings.smtp_starttls and not settings.smtp_ssl:
                server.starttls()
                server.ehlo()
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.sendmail(settings.smtp_from, [to], message.as_string())
        logger.info("Sent %r to %s", subject, to)
    except Exception:  # noqa: BLE001 - best-effort, as in Notes
        logger.exception("Could not send %r to %s", subject, to)


def send(to: str, template: str, subject: str, context: dict[str, Any],
         background: Optional[BackgroundTasks] = None) -> None:
    if not settings.email_enabled:
        hint = context.get("verify_url") or context.get("reset_url") or context.get("code")
        logger.warning("Email off: would send %r to %s. Actionable value: %s", subject, to, hint)
        return
    text, html = _render(template, context)
    if background is not None:
        background.add_task(_send, to, subject, text, html)
    else:
        _send(to, subject, text, html)
