"""input_hash: привязка результата единицы работы к её входу (этап 2).

Единица работы (NodeRun / батч / кадр-генерация) хранит
``input_hash = sha256(канонический JSON от компонентов)``:

- нормализованный вход единицы — explicit allowlist полей, собирает
  call-site (НЕ «всё, что видно»: списки кадров отсортированы по uuid,
  файлы-референсы — по содержимому, не по пути/mtime);
- отпечаток типа результата: контракт этапа 5 (имя + content-hash его
  JSON-схемы) либо provider+endpoint для медиа-единиц;
- версия промпта — sha256 эффективного текста (`get_effective_text` +
  вшитые хинты call-site), НЕ git-ревизия (prompts/* вне git). Тот же
  хэш = ``prompt_version_hash`` учёта этапа 3 — один источник;
- модель + параметры генерации.

Совпадение hash при успешном прошлом результате = cache hit; несовпадение
= чекпоинт протух, единица выполняется заново. Формат значения —
``"ih1:<sha256 hex>"``: смена формата (префикса) автоматически
инвалидирует все старые чекпоинты.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

# Версия формата. Менять ТОЛЬКО при несовместимой смене правил сборки —
# все существующие чекпоинты станут mismatch (одноразовый прогрев кэша).
HASH_VERSION = "ih1"


# ── нормализация ──────────────────────────────────────────────────────────


def normalize_text(text: str) -> str:
    """Семантически-нейтральная нормализация текста для хэширования.

    Промпт-файлы правятся вне git (CRLF/BOM от редакторов Windows) —
    перевод строки не должен менять input_hash.
    """
    return text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


def canonical_json(value: Any) -> str:
    """Детерминированная сериализация: sort_keys + плотные разделители.

    Порядок ключей dict не влияет на результат. Порядок СПИСКОВ —
    значимый: call-site обязан отсортировать коллекции без семантического
    порядка сам (кадры — по uuid), см. allowlist-правило спеки.
    """
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    )


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


# ── компоненты ────────────────────────────────────────────────────────────


def prompt_version_hash(effective_text: str, *, hints: Iterable[str] = ()) -> str:
    """sha256 промпта «как уйдёт в модель»: эффективный текст + вшитые хинты.

    ``effective_text`` — результат `gpt_text_builder.get_effective_text`;
    ``hints`` — вшитые в код хвосты (`_*_DB_HINT`, футеры батчей),
    передаются call-site'ом. Этот же хэш пишется в учёт этапа 3
    (`prompt_version_hash` в llm_calls) — сравнение версий не расходится.
    """
    parts = [normalize_text(effective_text)]
    parts.extend(normalize_text(h) for h in hints)
    return _sha256("\x00".join(parts))


def step_prompt_hash(project: Any, step_code: str, *, hints: Iterable[str] = (), **ctx: Any) -> str:
    """Удобная обёртка: промпт шага проекта → prompt_version_hash."""
    from app.services.gpt_text_builder import get_effective_text

    return prompt_version_hash(
        get_effective_text(project, step_code, **ctx), hints=hints
    )


def contract_fingerprint(contract_name: str) -> str:
    """Отпечаток типа результата LLM-единицы: имя + content-hash схемы.

    Версия схемы = хэш её JSON-представления: правка контракта этапа 5
    меняет отпечаток без ручного ведения номеров версий.
    """
    from app.contracts import get_contract

    contract = get_contract(contract_name)
    return f"{contract.name}:{_sha256(canonical_json(contract.json_schema()))}"


def media_fingerprint(provider: str, endpoint: str = "") -> str:
    """Отпечаток медиа-единицы (генерация img/video/audio) — вместо контракта."""
    return f"media:{provider}:{endpoint}"


def file_content_hash(path: str | Path) -> str:
    """Идентичность файла-референса — по содержимому, не по пути/mtime.

    (hero-реф выбирается «новейший по mtime» — touch не должен менять вход.)
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── сборка ────────────────────────────────────────────────────────────────


def compute_input_hash(
    *,
    unit_input: Any,
    fingerprint: str,
    prompt_hash: str | None = None,
    model: str | None = None,
    params: Mapping[str, Any] | None = None,
) -> str:
    """input_hash единицы работы из нормализованных компонентов.

    ``unit_input`` — allowlist-подмножество входа (JSON-сериализуемое,
    коллекции отсортированы call-site'ом); ``fingerprint`` —
    `contract_fingerprint(...)` либо `media_fingerprint(...)`;
    ``params`` — параметры генерации (температура/размер/aspect/refs…).
    """
    envelope = {
        "v": HASH_VERSION,
        "input": unit_input,
        "type": fingerprint,
        "prompt": prompt_hash,
        "model": model,
        "params": dict(params) if params else None,
    }
    return f"{HASH_VERSION}:{_sha256(canonical_json(envelope))}"


def hashes_match(stored: Any, current: str) -> bool:
    """Сверка сохранённого hash с текущим; legacy-чекпоинт без hash = mismatch."""
    return isinstance(stored, str) and bool(stored) and stored == current
