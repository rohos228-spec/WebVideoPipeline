"""Запись живых ответов моделей — сырьё для голденсетов.

Голденсет нельзя выдумать. Смысл его ровно в том, что модель отвечает не так,
как мы себе представляем: лишний ключ, `null` вместо строки, число строкой,
markdown-забор вокруг JSON, обрезанный хвост. Написанный из головы «пример
ответа» проверяет наши фантазии, а не конвейер.

**Чего в проекте не было.** `llm_calls` пишет метаданные — модель, токены,
стоимость, вердикт, — но НЕ текст ответа. Двести двадцать четыре записи за все
прогоны, и ни одного сохранённого ответа: восстановить, на чём именно
споткнулся конвейер месяц назад, нечем. Единственное исключение — `llm_rejects/`
в каталоге проекта, куда падают отвергнутые ответы; то есть история есть ровно
у брака, а у нормальной работы её нет.

**Что делает этот модуль.** По флагу окружения кладёт рядом с каждым вызовом
пару «запрос → сырой ответ» в отдельный каталог. Оттуда случай переносится в
`evals/golden/` командой `scripts/goldens.py add`, и дальше он живёт как
детерминированный тест: реальный текст модели прогоняется через контракт без
единого обращения к сети.

**Почему по флагу, а не всегда.** Ответы моделей — это содержимое роликов
заказчика и десятки килобайт на вызов. Писать их всегда значит удваивать диск
под каждым проектом ради данных, которые нужны раз в месяц. Флаг ставится
осознанно, на прогон, из которого собирают корпус.

**Секретов в записи нет по построению.** Пишутся промт и ответ, но не
заголовки и не ключи: ключ живёт в клиенте (`gpt_api`), сюда не попадает даже
случайно, потому что сюда его никто не передаёт.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

#: Куда писать. Пусто — не писать вовсе; это состояние по умолчанию.
ENV_DIR = "LLM_RECORD_DIR"

#: Верхняя граница на файл. Ответ длиннее — обрезается с явной пометкой:
#: голденсет из мегабайтного ответа никто не станет читать, а место он займёт.
MAX_CHARS = 400_000

_SAFE = re.compile(r"[^a-zA-Z0-9_.-]+")


def record_dir() -> Path | None:
    """Каталог записи или `None`, если запись выключена."""
    raw = (os.environ.get(ENV_DIR) or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def enabled() -> bool:
    return record_dir() is not None


def _slug(value: str, limit: int = 48) -> str:
    cleaned = _SAFE.sub("-", (value or "").strip()).strip("-")
    return (cleaned or "no-name")[:limit]


def record(
    *,
    contract: str,
    reply: str,
    prompt: str = "",
    model: str = "",
    node_key: str = "",
    verdict: str = "",
    error: str = "",
    extra: dict[str, Any] | None = None,
) -> Path | None:
    """Сохранить один обмен. Возвращает путь или `None`, если запись выключена.

    Функция обязана быть безобидной: её зовут из середины шага конвейера, и
    отказ файловой системы не должен ронять генерацию ролика. Любая ошибка
    записи глотается — потеря случая для корпуса дешевле потерянного прогона.
    """
    root = record_dir()
    if root is None:
        return None

    body = reply or ""
    truncated = len(body) > MAX_CHARS
    if truncated:
        body = body[:MAX_CHARS] + "\n… [обрезано рекордером]"

    payload = {
        "contract": contract,
        "model": model,
        "node_key": node_key,
        "verdict": verdict or ("error" if error else "ok"),
        "error": error,
        "truncated": truncated,
        "prompt": prompt or "",
        "reply": body,
        "extra": extra or {},
    }

    try:
        root.mkdir(parents=True, exist_ok=True)
        # Имя несёт контракт, время и хеш ответа. Хеш — чтобы повтор того же
        # ответа не плодил файлы: корпус собирают прогоном, а прогон повторяет
        # один и тот же вызов при ретраях.
        digest = hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()[:10]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = root / f"{_slug(contract)}__{stamp}__{digest}.json"
        if path.exists():
            return path
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001
        # См. докстроку: рекордер не имеет права быть причиной падения шага.
        return None
