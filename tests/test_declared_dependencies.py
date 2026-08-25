"""Всё, что приложение импортирует, объявлено в `pyproject.toml`.

Разрыв между «установлено у меня» и «объявлено в проекте» — это ошибка, которую
не видит ни один прогон: у разработчика пакет стоит, суита зелёная, mypy
доволен. Замечают её там, где окружение собирается с нуля, — в образе или в CI,
и обычно не сразу, потому что импорт может быть ленивым.

Именно так и вышло с `argon2-cffi` 2026-08-25: пакет поставили руками в `.venv`,
в `pyproject.toml` не внесли. Приложение в контейнере поднялось (`passwords`
импортируется лениво, только на входе), `/api/health` отвечал, фронт отдавался —
и всё это при неработающем входе. Нашлось не тестом, а попыткой завести админа
внутри контейнера.

Тест грубый намеренно: он не разбирает граф импортов, а проверяет соответствие
для короткого списка пакетов, без которых приложение не работает. Полный анализ
зависимостей — отдельный инструмент; здесь нужен дешёвый предохранитель против
конкретной ошибки «поставил, но не записал».
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _declared() -> set[str]:
    """Имена пакетов из основных зависимостей, без версий и экстра."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    out: set[str] = set()
    for raw in data["project"]["dependencies"]:
        # "httpx[socks]>=0.27,<1" → "httpx"
        name = raw.split(";")[0].split("[")[0]
        for sep in (">=", "<=", "==", "~=", ">", "<", "!="):
            name = name.split(sep)[0]
        out.add(name.strip().lower().replace("_", "-"))
    return out


def _optional(group: str) -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    out: set[str] = set()
    for raw in data["project"]["optional-dependencies"].get(group, []):
        name = raw.split(";")[0].split("[")[0]
        for sep in (">=", "<=", "==", "~=", ">", "<", "!="):
            name = name.split(sep)[0]
        out.add(name.strip().lower().replace("_", "-"))
    return out


#: Модуль → пакет, который его даёт. Список ручной и короткий: сюда попадает
#: то, без чего приложение не работает, а имя модуля не совпадает с именем
#: пакета (или совпадает, но забыть его особенно дорого).
CORE = {
    "jwt": "pyjwt",
    "argon2": "argon2-cffi",
    "fastapi": "fastapi",
    "sqlalchemy": "sqlalchemy",
    "alembic": "alembic",
    "pydantic": "pydantic",
    "pydantic_settings": "pydantic-settings",
    "uvicorn": "uvicorn",
    "loguru": "loguru",
    "httpx": "httpx",
    "jinja2": "jinja2",
    "openpyxl": "openpyxl",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "tiktoken": "tiktoken",
}


@pytest.mark.parametrize(("module", "package"), sorted(CORE.items()))
def test_core_import_is_declared(module: str, package: str) -> None:
    """Пакет объявлен в основных зависимостях, а не только установлен."""
    assert package in _declared(), (
        f"модуль {module!r} приходит из пакета {package!r}, а его нет в "
        f"[project.dependencies]. Установленный, но не объявленный пакет "
        f"работает у вас и отсутствует в образе и в CI."
    )


@pytest.mark.parametrize(("module", "package"), sorted(CORE.items()))
def test_core_import_actually_works(module: str, package: str) -> None:
    """И он действительно импортируется в этом окружении.

    Обратная половина: объявить пакет и не поставить — тоже разрыв, просто с
    другой стороны. Вместе две проверки означают «объявлено ровно то, что
    используется».
    """
    pytest.importorskip(module, reason=f"{package} не установлен — pip install -e '.[dev]'")


def test_postgres_driver_is_an_extra_not_a_core_dependency() -> None:
    """`asyncpg` — экстра, и это осознанно.

    Режим владельца работает на SQLite, и тянуть драйвер Postgres в базовую
    установку значит требовать компилятор там, где база — файл. В образ он
    попадает через `pip install ".[postgres,s3]"` (см. Dockerfile).
    """
    declared = _declared()
    assert "asyncpg" not in declared
    assert "asyncpg" in _optional("postgres")


def test_dev_group_has_what_the_gate_runs() -> None:
    """Гейт зовёт ruff, mypy, pytest и собирает покрытие.

    Инструмент, которого нет в `[dev]`, — это шаг гейта, падающий на чистой
    машине с «command not found», причём падающий первым же прогоном в CI.
    """
    dev = _optional("dev")
    for tool in ("ruff", "mypy", "pytest", "pytest-cov", "pytest-asyncio"):
        assert tool in dev, f"{tool} гоняется гейтом, но не объявлен в [dev]"
