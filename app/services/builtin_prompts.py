"""Промты, встроенные в код, и их посев в базу.

Некоторые шаги обязаны работать и без библиотеки на диске: на сервере
каталог `prompts/` смонтирован только для чтения и неполон, а шаг разбора
состава (`cast_extract`) появился позже библиотеки — файла для него там нет.

Поэтому текст живёт в коде, а на старте один раз кладётся в базу на
системный уровень. Дальше это обычный промт: виден в редакторе, правится,
переопределяется на уровне арендатора. Повторный посев ничего не трогает —
затирать правку человека встроенным текстом нельзя.
"""

from __future__ import annotations

from typing import Any

from loguru import logger


def builtin_prompts() -> dict[tuple[str, str], str]:
    """(код шага, имя) → текст. Функция, а не константа: импорт ленивый."""
    from app.services.cast_extract import DEFAULT_PROMPT as cast_default

    return {("cast", "default"): cast_default}


async def seed_builtin_prompts(session: Any) -> int:
    """Положить встроенные промты в базу, если их там ещё нет. Возвращает число."""
    from app.services import prompt_store
    from app.services.prompt_store import PromptScope

    system = PromptScope()
    seeded = 0
    for (step_code, name), text in builtin_prompts().items():
        if prompt_store.resolve(step_code, name, system) is not None:
            continue
        await prompt_store.save(session, step_code, name, text, scope=system)
        seeded += 1
        logger.info("промт-библиотека: встроенный промт {}/{} посеян в базу", step_code, name)
    return seeded
