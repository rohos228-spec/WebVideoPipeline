"""Предохранители старта: приложение отказывается подниматься в опасной позе.

Три отказа, и каждый закрывает состояние, в котором система выглядит рабочей и
при этом небезопасна. Именно поэтому они на старте, а не в проверке при
запросе: упавший старт видно сразу, а 500 на одной ручке из тридцати можно не
заметить неделю.

Тесты здесь короткие и скучные, но без них предохранитель — это код, который
никто никогда не исполнял. Первая же правка условия («а давайте предупреждение
вместо отказа») пройдёт незамеченной.
"""

from __future__ import annotations

import pytest

from app.settings import settings
from tests import accounts_harness as ah


def _create_app():
    from app.web.api import create_app

    return create_app()


async def _startup(app) -> None:
    """Провести приложение через lifespan.

    Проверки живут в `_lifespan`, а не в `create_app`: до подъёма приложение
    ещё ничего не знает про базу. Обычный запрос через `ASGITransport` lifespan
    НЕ выполняет — тест на нём был бы зелёным всегда и не исполнял бы ни одной
    проверяемой строки. Поэтому событие подаётся напрямую.
    """
    received = [{"type": "lifespan.startup"}]
    sent: list[dict] = []

    async def receive():
        return received.pop(0) if received else {"type": "lifespan.shutdown"}

    async def send(message):
        sent.append(message)

    await app({"type": "lifespan"}, receive, send)

    for message in sent:
        if message.get("type") == "lifespan.startup.failed":
            raise RuntimeError(message.get("message", "старт не удался"))


async def test_accounts_on_sqlite_refuse_to_start(monkeypatch) -> None:
    """Учётные записи на SQLite — это арендаторы там, где RLS не существует.

    Не «пока не переехали», а «политик в движке нет физически».
    `require_isolation` поймал бы это на первом запросе, но лучше не подняться.
    """
    monkeypatch.setattr(settings, "studio_session_secret", ah.SECRET)
    monkeypatch.setattr(settings, "database_url", "")  # → SQLite

    with pytest.raises(RuntimeError, match="row-level security"):
        await _startup(_create_app())


async def test_open_port_without_accounts_refuses_to_start(monkeypatch) -> None:
    """`WEB_HOST=0.0.0.0` без учёток — открытый `/api/fleet` для всей сети.

    До 2026-08-24 порт закрывался парой WEB_AUTH_USER/WEB_AUTH_PASSWORD —
    паролем открытым текстом в окружении. Пара удалена вместе с этим способом
    защиты, и молча остаться с открытым запуском команд на машинах парка
    нельзя.
    """
    monkeypatch.setattr(settings, "studio_session_secret", "")
    monkeypatch.setattr(settings, "web_host", "0.0.0.0")

    with pytest.raises(RuntimeError, match="открыт всей сети"):
        await _startup(_create_app())


async def test_loopback_without_accounts_is_fine(monkeypatch) -> None:
    """Обратная сторона: режим владельца на своей машине ломать незачем.

    Это состояние сегодняшней живой установки, и предохранитель не имеет права
    её уронить.
    """
    monkeypatch.setattr(settings, "studio_session_secret", "")
    monkeypatch.setattr(settings, "web_host", "127.0.0.1")
    await _startup(_create_app())


async def test_short_secret_refuses_to_start(monkeypatch) -> None:
    """Ключ короче 32 байт для HS256 — это подделываемый токен админа.

    Раньше здесь стояло предупреждение, и по делу: секрет был общим с
    биллингом, менять его в одиночку было нельзя. Общего секрета больше нет —
    значит и повода терпеть слабый ключ тоже.
    """
    monkeypatch.setattr(settings, "studio_session_secret", "коротко")
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://app:app@127.0.0.1/vp")

    with pytest.raises(RuntimeError, match="32 байт"):
        await _startup(_create_app())


async def test_secret_length_is_measured_in_bytes_not_characters(monkeypatch) -> None:
    """Кириллица в UTF-8 — два байта на символ.

    Строка из 20 русских букв это 40 байт: по символам она «короткая», по
    байтам — законная. Проверка обязана считать байты, иначе она отвергает
    годные секреты и принимает негодные ASCII-строки той же длины.
    """
    twenty_cyrillic = "секретсекретсекретсе"
    assert len(twenty_cyrillic) == 20
    assert len(twenty_cyrillic.encode("utf-8")) == 40

    monkeypatch.setattr(settings, "studio_session_secret", twenty_cyrillic)
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://app:app@127.0.0.1/vp")

    # До проверки длины дело дойдёт, до базы — нет: старт упадёт позже, на
    # подключении. Нам важно, что упал он НЕ на длине секрета.
    with pytest.raises(Exception) as exc:  # noqa: PT011
        await _startup(_create_app())
    assert "32 байт" not in str(exc.value)
