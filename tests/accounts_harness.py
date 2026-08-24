"""Общая оснастка для тестов, которым нужен вошедший пользователь.

До 2026-08-24 тесты собирали токен руками — `jwt.encode({...}, SECRET)` в
каждом файле, где нужен был арендатор. Это работало, пока личность была чужой
и токен состоял из трёх полей. Теперь у токена есть роль и поколение, а слой
личности ходит в базу за фактом «учётка жива»: собранный вручную токен
проходил бы подпись и падал на проверке отзыва, причём в каждом файле
по-своему.

Поэтому один вход в систему на все тесты — вот здесь.

**Почему подменяется `app.db.SessionLocal`.** Слой личности стоит ДО роутера,
и зависимостей FastAPI на этом уровне не существует: `app.dependency_overrides`
до него не достаёт. Свою сессию он берёт из `app.db.SessionLocal` в момент
вызова, а не на импорте, — именно затем, чтобы её можно было подменить.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.services.studio_auth import ROLE_ADMIN, ROLE_MEMBER

#: Секрет для тестов. Не короче 32 байт: приложение отказывается стартовать со
#: слабым ключом, и тест на слабом ключе проверял бы не то, что в проде.
SECRET = "тестовый-секрет-подписи-длиною-заведомо-больше-тридцати-двух-байт"

#: Пароль для заведённых в тесте учёток. Проходит `passwords.assert_strong`.
PASSWORD = "gT7k-mQ2p-Xw9d-Rn4s"


@dataclass(frozen=True)
class Account:
    """Заведённый пользователь и его готовый токен."""

    user_id: str
    email: str
    role: str
    token: str

    @property
    def auth(self) -> dict[str, str]:
        """Заголовок для `AsyncClient`."""
        return {"Authorization": f"Bearer {self.token}"}


def configure(monkeypatch, *, allow_sqlite: bool = True) -> None:
    """Режим учётных записей поверх SQLite.

    SQLite здесь законен ровно потому, что проверяется механика личности, а не
    изоляция: политик в этом движке нет, и `ALLOW_UNISOLATED_TENANTS`
    существует для таких случаев. Саму изоляцию проверяет
    `tests/test_rls_postgres.py` на живом Postgres — иначе проверять нечего.
    """
    from app.settings import settings

    monkeypatch.setattr(settings, "studio_session_secret", SECRET)
    monkeypatch.setattr(settings, "studio_brand", "")
    monkeypatch.setattr(settings, "tenant_start_credits", 0.0)
    if allow_sqlite:
        monkeypatch.setattr(settings, "allow_unisolated_tenants", True)


async def make_engine(path):
    """Движок и фабрика сессий на файле в tmp, схема создана."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def bind_identity_session(monkeypatch, factory) -> None:
    """Показать слою личности ту же базу, что и роутерам.

    Без этого middleware пойдёт в глобальный `SessionLocal` — то есть в
    `data/state.db` репозитория, — не найдёт там тестового пользователя и
    честно откажет в доступе. Ошибка при этом выглядела бы как «токен не
    принят», и искать её пришлось бы в разборе токена, где всё правильно.
    """
    import app.db as db_mod

    monkeypatch.setattr(db_mod, "SessionLocal", factory)


async def make_account(
    factory,
    *,
    email: str = "member@studio.local",
    role: str = ROLE_MEMBER,
    password: str = PASSWORD,
) -> Account:
    """Завести учётку в базе и выписать ей настоящий токен."""
    from app.services import studio_users
    from app.services.studio_auth import identity_of, issue_token

    async with factory() as session:
        user = await studio_users.create_user(session, email=email, password=password, role=role)
        await session.commit()
        identity = identity_of(user)

    return Account(
        user_id=identity.user_id,
        email=identity.email,
        role=identity.role,
        token=issue_token(identity),
    )


async def make_admin(factory, *, email: str = "admin@studio.local") -> Account:
    """Админ: у него нет баланса, потому что у него нет кассы."""
    return await make_account(factory, email=email, role=ROLE_ADMIN)
