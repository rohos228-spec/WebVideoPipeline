"""Генерация, хранение и проверка одноразовых кодов (OTP).

Коды 6-значные, действуют 10 минут, хранятся в базе в виде SHA-256 хеша.
Защита от перебора: максимум 5 попыток.
Защита от спама (рейт-лимит): не чаще 1 кода в 60 секунд.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EmailVerification
from app.services.studio_users import normalize_email

#: Соль для хеширования кодов в базе (защита от радужных таблиц)
_SALT = "studio_otp_v1_salt_928f"

#: Время жизни кода (минуты)
CODE_TTL_MINUTES = 10

#: Задержка перед повторной отправкой (секунды)
RESEND_INTERVAL_SECONDS = 60

#: Максимальное число попыток ввода
MAX_ATTEMPTS = 5


class VerificationError(RuntimeError):
    """Ошибка валидации кода (неверный, истёк, превышены попытки)."""


class RateLimitError(RuntimeError):
    """Слишком частый запрос кода."""


def generate_otp() -> str:
    """Генерация 6-значного цифрового кода."""
    # 100000 - 999999
    num = secrets.randbelow(900000) + 100000
    return str(num)


def hash_otp(code: str) -> str:
    """Получение SHA-256 хеша проверочного кода."""
    clean = code.replace("-", "").replace(" ", "").strip()
    return hashlib.sha256(f"{_SALT}:{clean}".encode()).hexdigest()


async def issue_code(
    session: AsyncSession,
    email: str,
    purpose: str = "register",
) -> str:
    """Выпустить новый проверочный код. Проверяет рейт-лимит (раз в 60 с)."""
    addr = normalize_email(email)
    now = datetime.now(UTC).replace(tzinfo=None)

    # 1. Проверяем рейт-лимит по (email, purpose)
    recent = await session.execute(
        select(EmailVerification)
        .where(
            EmailVerification.email == addr,
            EmailVerification.purpose == purpose,
        )
        .order_by(EmailVerification.created_at.desc())
        .limit(1)
    )
    last = recent.scalar_one_or_none()
    if last is not None:
        elapsed = (now - last.created_at).total_seconds()
        if elapsed < RESEND_INTERVAL_SECONDS:
            left = int(RESEND_INTERVAL_SECONDS - elapsed)
            raise RateLimitError(f"Повторный код можно запросить через {left} с")

    # 2. Удаляем старые коды для этого email и purpose
    await session.execute(
        delete(EmailVerification).where(
            EmailVerification.email == addr,
            EmailVerification.purpose == purpose,
        )
    )

    # 3. Генерируем новый код
    code = generate_otp()
    record = EmailVerification(
        id=str(uuid.uuid4()),
        email=addr,
        code_hash=hash_otp(code),
        purpose=purpose,
        attempts=0,
        expires_at=now + timedelta(minutes=CODE_TTL_MINUTES),
        created_at=now,
    )
    session.add(record)
    await session.flush()
    return code


async def verify_code(
    session: AsyncSession,
    email: str,
    code: str,
    purpose: str = "register",
) -> None:
    """Проверить код. Если верен — сгорает. Если нет — считает попытки."""
    addr = normalize_email(email)
    clean_code = code.replace("-", "").replace(" ", "").strip()
    if not clean_code or len(clean_code) < 4:
        raise VerificationError("Введите код подтверждения")

    now = datetime.now(UTC).replace(tzinfo=None)
    result = await session.execute(
        select(EmailVerification)
        .where(
            EmailVerification.email == addr,
            EmailVerification.purpose == purpose,
            EmailVerification.expires_at > now,
        )
        .order_by(EmailVerification.created_at.desc())
        .limit(1)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise VerificationError("Код не найден или истёк срок его действия. Запросите новый код.")

    if record.attempts >= MAX_ATTEMPTS:
        await session.execute(delete(EmailVerification).where(EmailVerification.id == record.id))
        await session.commit()
        raise VerificationError("Превышено количество попыток. Запросите новый код.")

    candidate_hash = hash_otp(clean_code)
    if not hmac.compare_digest(record.code_hash, candidate_hash):
        record.attempts += 1
        await session.commit()
        left = MAX_ATTEMPTS - record.attempts
        if left > 0:
            raise VerificationError(f"Неверный код. Осталось попыток: {left}")
        raise VerificationError("Неверный код. Попытки исчерпаны, запросите новый код.")

    # Успех: удаляем использованный код
    await session.execute(delete(EmailVerification).where(EmailVerification.id == record.id))
    await session.flush()
