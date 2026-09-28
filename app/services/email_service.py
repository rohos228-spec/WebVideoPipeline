"""Сервис отправки сервисных email-сообщений (коды подтверждения, сброс паролей).

Поддерживает стандартный SMTP (Яндекс, Mail.ru, корпоративный SMTP).
При отсутствии настроек SMTP работает в безопасном режиме разработки (печать в лог).
"""

from __future__ import annotations

import asyncio
import email.utils
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.settings import settings

logger = logging.getLogger(__name__)


def _build_html_email(title: str, text: str, code: str, note: str) -> str:
    """Генерация красивого HTML-письма в тёмно-нейтральной стилистике студии."""
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
</head>
<body style="margin: 0; padding: 0; background-color: #0f1015; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #f4f4f5;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #0f1015; padding: 40px 20px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" style="max-width: 480px; background-color: #181920; border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 16px; padding: 32px; box-shadow: 0 10px 30px rgba(0,0,0,0.5);">
          <tr>
            <td align="center" style="padding-bottom: 24px;">
              <span style="font-size: 20px; font-weight: 700; color: #ffffff; letter-spacing: -0.5px;">Видеостудия</span>
            </td>
          </tr>
          <tr>
            <td>
              <h1 style="margin: 0 0 12px 0; font-size: 18px; font-weight: 600; color: #f4f4f5; text-align: center;">{title}</h1>
              <p style="margin: 0 0 24px 0; font-size: 14px; line-height: 1.5; color: #a1a1aa; text-align: center;">{text}</p>
            </td>
          </tr>
          <tr>
            <td align="center" style="padding: 12px 0 28px 0;">
              <div style="display: inline-block; background-color: #272730; border: 1px solid rgba(255, 255, 255, 0.15); border-radius: 10px; padding: 14px 28px; font-size: 32px; font-weight: 700; letter-spacing: 6px; font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, Courier, monospace; color: #38bdf8;">
                {code}
              </div>
            </td>
          </tr>
          <tr>
            <td>
              <p style="margin: 0; font-size: 12px; line-height: 1.5; color: #71717a; text-align: center;">
                Срок действия кода: <strong>10 минут</strong>.<br>{note}
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def _send_smtp_sync(
    *,
    to_email: str,
    subject: str,
    text_content: str,
    html_content: str,
) -> bool:
    """Синхронная отправка письма через SMTP (вызывается в отдельном потоке)."""
    host = settings.smtp_host.strip()
    port = int(settings.smtp_port)
    user = settings.smtp_user.strip()
    password = settings.smtp_password
    sender = settings.smtp_from.strip() or f"Видеостудия <{user}>"
    use_ssl = bool(settings.smtp_ssl) or port == 465

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid()

    msg.attach(MIMEText(text_content, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=15) as server:
                if user and password:
                    server.login(user, password)
                server.sendmail(user or sender, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.ehlo()
                try:
                    server.starttls()
                    server.ehlo()
                except Exception:
                    pass
                if user and password:
                    server.login(user, password)
                server.sendmail(user or sender, [to_email], msg.as_string())
        logger.info("Email успешно отправлен на %s (тема: %s)", to_email, subject)
        return True
    except Exception as exc:
        logger.error("Ошибка отправки email на %s через %s:%s: %s", to_email, host, port, exc)
        return False


async def send_verification_code(email_addr: str, code: str, purpose: str = "register") -> bool:
    """Отправить 6-значный проверочный код.

    Если SMTP настроен — уходит реальное письмо.
    Если нет — код выводится в лог сервера (режим разработки).
    """
    if purpose == "reset_password":
        subject = f"Код сброса пароля: {code}"
        title = "Сброс пароля"
        text = "Вы запросили восстановление доступа к Видеостудии. Введите код на странице смены пароля:"
        note = "Если вы не запрашивали сброс пароля, проигнорируйте это письмо."
    else:
        subject = f"Код подтверждения регистрации: {code}"
        title = "Подтверждение регистрации"
        text = "Добро пожаловать в Видеостудию! Введите этот код для завершения регистрации:"
        note = "Если вы не регистрировались в студии, проигнорируйте это письмо."

    plain = f"{title}\n\n{text}\n\nКод: {code}\n\n{note}\nСрок действия: 10 минут."
    html = _build_html_email(title, text, code, note)

    if not settings.smtp_configured:
        # Режим разработки: печатаем код в лог с заметным разделителем
        logger.warning(
            "\n"
            "======================================================================\n"
            "  [EMAIL DEV MODE] SMTP не настроен. Одноразовый код для %s:\n"
            "  Назначение: %s\n"
            "  Код подтверждения: >>> %s <<<\n"
            "======================================================================",
            email_addr,
            purpose,
            code,
        )
        return True

    return await asyncio.to_thread(
        _send_smtp_sync,
        to_email=email_addr,
        subject=subject,
        text_content=plain,
        html_content=html,
    )
