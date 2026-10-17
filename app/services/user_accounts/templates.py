"""Email templates (RU). Each returns (subject, text, html). No provider names in user-visible text."""
from __future__ import annotations

from html import escape

_BRAND = "VIA"
_WRAP = (
    '<div style="font-family:Arial,sans-serif;background:#0b1416;color:#e6f2f1;padding:32px">'
    '<div style="max-width:520px;margin:auto;background:#10201f;border-radius:12px;padding:28px">'
    '<h2 style="color:#3fd1c0;margin-top:0">{title}</h2>{content}'
    '<p style="color:#7f9a98;font-size:12px;margin-top:28px">Если вы не запрашивали это письмо, просто проигнорируйте его.</p>'
    "</div></div>"
)


def _btn(url: str, label: str) -> str:
    u = escape(url, quote=True)
    return (f'<p><a href="{u}" style="display:inline-block;background:#3fd1c0;color:#04201d;padding:12px 22px;'
            f'border-radius:8px;text-decoration:none;font-weight:bold">{escape(label)}</a></p>'
            f'<p style="font-size:12px;color:#7f9a98">Или откройте ссылку: {u}</p>')


def verify_email(name: str, link: str, code: str) -> tuple[str, str, str]:
    subject = f"{_BRAND} — подтвердите почту"
    text = (f"Здравствуйте, {name}!\n\nПодтвердите почту, чтобы пользоваться {_BRAND}:\n{link}\n\n"
            f"Или введите код: {code}\nСсылка и код действуют 24 часа.")
    html = _WRAP.format(title="Подтвердите почту", content=(
        f"<p>Здравствуйте, {escape(name)}!</p><p>Подтвердите почту, чтобы пользоваться {_BRAND}.</p>"
        + _btn(link, "Подтвердить почту") + f"<p>Код подтверждения: <b>{escape(code)}</b> (24 часа)</p>"))
    return subject, text, html


def reset_password(name: str, link: str) -> tuple[str, str, str]:
    subject = f"{_BRAND} — восстановление пароля"
    text = f"Здравствуйте, {name}!\n\nСсылка для смены пароля (действует 1 час):\n{link}"
    html = _WRAP.format(title="Восстановление пароля", content=(
        f"<p>Здравствуйте, {escape(name)}!</p><p>Чтобы задать новый пароль, нажмите кнопку. Ссылка действует 1 час.</p>"
        + _btn(link, "Сменить пароль")))
    return subject, text, html


def team_invite(owner_name: str, link: str) -> tuple[str, str, str]:
    subject = f"{_BRAND} — приглашение в команду"
    text = f"{owner_name} добавил вас в свою команду {_BRAND}.\nОткрыть студию: {link}"
    html = _WRAP.format(title="Приглашение в команду", content=(
        f"<p>{escape(owner_name)} добавил вас в свою команду {_BRAND}.</p>" + _btn(link, "Открыть студию")))
    return subject, text, html
