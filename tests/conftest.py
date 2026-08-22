"""Общие фикстуры тестов.

W1-fix п.7: центральный harness-гейт (auto_advance) включён по умолчанию,
чтобы CI ловил регрессии данных. Тест, которому нужен gate OFF (например,
механика продвижения без on-disk артефактов), помечается
``@pytest.mark.no_harness_gate`` — см. ``pyproject.toml`` pytest markers.

Каждый тест получает свой ``settings.data_dir`` / ``sqlite_path`` /
``logs_dir`` в tmp — иначе slug'и вроде ``vo-test``/``store`` делят
/workspace/data и сыплют соседей (FileExistsError, stale voiceover.txt).

``logs_dir`` добавлен 2026-08-22: до этого писатели журналов держали
``Path("logs/errors.log")`` константой от CWD, и прогон дописывал реальные
``logs/status.log`` и ``logs/errors.log`` репозитория. То же было с
``data/library/old`` — ``local_library`` считал корни на импорте, мимо
подмены ``data_dir`` (накопилось 136 каталогов от чужих прогонов).
"""

from __future__ import annotations

import shutil
import tempfile
from functools import lru_cache
from pathlib import Path

import pytest
from sqlalchemy.pool import NullPool

from app.settings import settings

_ORIG_DATA_DIR = Path(settings.data_dir)
_ORIG_SQLITE_PATH = Path(settings.sqlite_path)
_ORIG_LOGS_DIR = Path(settings.logs_dir)


@lru_cache(maxsize=1)
def _schema_template() -> Path:
    """Один раз построить пустую БД со схемой и дальше копировать файл.

    `create_all` на каждый тест — это ~30 CREATE TABLE и заметно больше
    двух тысяч раз за прогон: суита из полутора минут превращалась в
    четверть часа. Копия готового файла стоит миллисекунды.
    """
    from sqlalchemy import create_engine

    from app.models import Base

    path = Path(tempfile.mkdtemp(prefix="vp-schema-")) / "template.db"
    engine = create_engine(f"sqlite:///{path}")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    return path


def _isolate_db_engine(monkeypatch, db_path: Path) -> None:
    """Увести движок БД на временный файл.

    `app/db.py` создаёт `engine`/`SessionLocal` на импорте, по пути из
    настроек. Подмена `settings.sqlite_path` его уже не трогает — поэтому
    всё, что ходит через общий `session_scope` (work_lease, llm_ledger,
    media_ledger), писало в БОЕВУЮ `data/state.db` репозитория. Живой
    прогон получал в свои деньги строки из тестов, а lease на `step:split`
    с TTL в час ронял следующий прогон суиты. Схему поднимаем синхронным
    движком: фикстура синхронная, а таблицы нужны сразу.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.db as app_db

    db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_schema_template(), db_path)

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setattr(app_db, "SessionLocal", async_sessionmaker(engine, expire_on_commit=False))


@pytest.fixture(autouse=True)
def _isolate_settings_paths(tmp_path_factory, monkeypatch, request):
    # W1-fix п.7: гейт включён по умолчанию (False). Тест может явно
    # отключить через маркер no_harness_gate.
    if "no_harness_gate" in request.keywords:
        monkeypatch.setattr(settings, "harness_gate_disabled", True)
    else:
        monkeypatch.setattr(settings, "harness_gate_disabled", False)
    # Stub-вердикты проверок без API-ключа: в проде fail-closed (RuntimeError),
    # тестам stub-путь нужен явно.
    monkeypatch.setattr(settings, "allow_stub_checks", True)
    root = tmp_path_factory.mktemp("vp-isol")
    monkeypatch.setattr(settings, "data_dir", root)
    monkeypatch.setattr(settings, "sqlite_path", root / "state.db")
    monkeypatch.setattr(settings, "logs_dir", root / "logs")
    _isolate_db_engine(monkeypatch, root / "state.db")
    yield
    # На случай прямой записи settings.data_dir = ... в обход monkeypatch.
    settings.data_dir = _ORIG_DATA_DIR
    settings.sqlite_path = _ORIG_SQLITE_PATH
    settings.logs_dir = _ORIG_LOGS_DIR
