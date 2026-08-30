"""Режим героя решается один раз, до плана, и ложится в проект.

**Что было.** `hero_mode` по умолчанию `auto`, и в этом режиме вопрос «нужен
ли ролику сквозной персонаж» решала модель — трижды и независимо: в промте
плана (раздел «hero_needed — как решать»), в промте закадрового текста
(«РЕЖИМ ОПРЕДЕЛИ САМ») и потом ещё раз в шаге персонажей через разбор
состава. Ответ плана в `project.hero_mode` не возвращался, поэтому каждый
следующий шаг гадал заново и мог разойтись с предыдущим.

**Что стало.** Перед планом один короткий вызов: тема → `hero` | `no_hero` +
причина. Результат пишется в `project.hero_mode` и в `meta["hero_decision"]`
(с источником и причиной — чтобы было видно, кто решил). Дальше все промты
получают готовое указание, а разделы «реши сам» из них код вырезает
(`chatgpt_xlsx`). Человек может переопределить режим через PATCH проекта или
инструмент оркестратора — тогда сюда мы уже не заходим: решается только
`auto`.

**Почему отдельный вызов, а не разбор плана.** План и так просят проставить
`hero_needed`, но тогда указание приезжает в промт плана постфактум, и модель
внутри плана решает сама — ровно то, от чего уходим. Вызов дешёвый: одна
тема, две строки ответа.

**Почему разбор текста, а не structured output.** Схему enforce'ят только
релеи из `GPT_STRUCTURED_RELAYS`; остальные принимают `response_format` молча
и игнорируют (см. `cast_extract`). Просим две строки и разбираем регулярками.

**Запасной вариант.** Модель не ответила или ответ не разобрался — ставим
`no_hero` и помечаем `source="fallback"`. Герой без сюжетной нужды только
добавляет брака (одно лицо во всех кадрах), а тематический ролик по теме с
героем всё равно смотрится; к тому же решение видно в meta и правится рукой.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Protocol

from loguru import logger

HERO = "hero"
NO_HERO = "no_hero"
AUTO = "auto"

#: Что ставим, если модель не помогла. См. docstring модуля.
FALLBACK_MODE = NO_HERO

#: Промт по умолчанию. Живёт в коде, а не только файлом: папки `prompts/` на
#: сервере может не быть вовсе. Файл (`prompts/01a_hero_decision/default.md`
#: или запись в базе), если он есть, побеждает.
DEFAULT_PROMPT = """\
Ты решаешь, нужен ли короткому вертикальному ролику (60–75 секунд) сквозной
персонаж — один и тот же герой во всех кадрах.

Тема ролика: {topic}

`hero` — если у темы есть индивидуальный герой, за которым зритель следит:
конкретный человек (названный или узнаваемый по роли), животное, персонаж.
Он появится больше чем в одном кадре, и с ним что-то происходит.

`no_hero` — если ролик про явление, процесс, место, список фактов, устройство
чего-либо; люди в кадре могут быть, но они взаимозаменяемы.

Не выбирай `hero` «чтобы было живее». Сквозной герой — это обязательство
держать одинаковое лицо во всех кадрах; без сюжетной нужды он только
добавляет брака. Сомневаешься — `no_hero`.

Ответь РОВНО в таком формате, две строки, без вступления и пояснений:

РЕЖИМ: hero или no_hero
ПОЧЕМУ: одна строка
"""

_MODE_LINE = re.compile(r"^\s*(?:режим|mode)\s*[:—-]\s*(no_hero|hero)\b", re.IGNORECASE | re.MULTILINE)
_REASON_LINE = re.compile(
    r"^\s*(?:почему|причина|reason|why)\s*[:—-]\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE
)
_NEEDED_FLAG = re.compile(r"hero_needed\s*[=:]\s*(true|false)", re.IGNORECASE)
_BARE_MODE = re.compile(r"\b(no_hero|hero)\b", re.IGNORECASE)


class _AsksFresh(Protocol):
    async def ask_fresh(self, text: str, *, project_id: int | None = None) -> str: ...


def build_prompt(topic: str, template: str | None = None) -> str:
    """Собрать запрос. `template` — содержимое файла промта, если он заведён.

    `.format` по шаблону пользователя опасен: любая фигурная скобка в тексте
    уронит шаг на KeyError. Меняем только известную метку; если её в шаблоне
    нет — тема дописывается в конец, иначе модель получила бы одни инструкции.
    """
    src = (template or DEFAULT_PROMPT).strip()
    clean_topic = (topic or "").strip() or "— не задана —"
    body = src.replace("{topic}", clean_topic)
    if "{topic}" not in src:
        body += f"\n\nТема ролика: {clean_topic}"
    return body


def parse_reply(reply: str) -> tuple[str | None, str]:
    """Ответ модели → (режим | None, причина).

    Разбор снисходительный: модель ставит звёздочки, пишет `hero_needed=true`
    вместо `РЕЖИМ: hero`, дописывает «Вот мой ответ». Сначала строка «РЕЖИМ»,
    потом флаг `hero_needed`, потом первое голое слово — и только если всё
    мимо, None. `no_hero` проверяется раньше `hero`, иначе оно бы в нём
    утонуло.
    """
    # Обёртки markdown мешают и режиму, и причине: «**Почему:** …».
    text = (reply or "").replace("`", "").replace("**", "").replace("__", "")
    mode: str | None = None

    m = _MODE_LINE.search(text)
    if m:
        mode = m.group(1).lower()
    else:
        f = _NEEDED_FLAG.search(text)
        if f:
            mode = HERO if f.group(1).lower() == "true" else NO_HERO
        else:
            b = _BARE_MODE.search(text)
            if b:
                mode = b.group(1).lower()

    r = _REASON_LINE.search(text)
    reason = (r.group(1).strip().strip("«»\"'") if r else "").strip()
    return mode, reason


async def decide(project: Any, gpt: _AsksFresh, *, template: str | None = None) -> tuple[str, str, str]:
    """Спросить модель. Возвращает (режим, причина, источник).

    Источник — `model` или `fallback`. Наружу ошибок не пускаем: план должен
    состояться и с запасным режимом.
    """
    topic = (getattr(project, "topic", "") or "").strip()
    prompt = build_prompt(topic, template)
    try:
        reply = await gpt.ask_fresh(prompt, project_id=getattr(project, "id", None))
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] hero_decision: модель не ответила ({}) — ставлю {}", project.id, e, FALLBACK_MODE
        )
        return FALLBACK_MODE, f"модель не ответила: {e}", "fallback"

    mode, reason = parse_reply(reply)
    if mode not in (HERO, NO_HERO):
        logger.warning(
            "[#{}] hero_decision: ответ не разобран ({!r}) — ставлю {}",
            project.id,
            (reply or "")[:120],
            FALLBACK_MODE,
        )
        return FALLBACK_MODE, "ответ модели не разобран", "fallback"
    return mode, reason, "model"


async def ensure_hero_mode(project: Any, gpt: _AsksFresh, *, template: str | None = None) -> str:
    """Гарантировать, что `project.hero_mode` ∈ {hero, no_hero}.

    Явно выставленный человеком режим не трогаем. `auto` (и пусто) — решаем и
    записываем; след решения в `meta["hero_decision"]`. Ничего не коммитим:
    это дело вызывающего шага.
    """
    current = (getattr(project, "hero_mode", None) or AUTO).strip()
    if current in (HERO, NO_HERO):
        return current

    mode, reason, source = await decide(project, gpt, template=template)
    project.hero_mode = mode
    meta = dict(project.meta or {}) if isinstance(getattr(project, "meta", None), dict) else {}
    meta["hero_decision"] = {
        "mode": mode,
        "reason": reason,
        "source": source,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    project.meta = meta
    logger.info("[#{}] hero_decision: {} ({}) — {}", project.id, mode, source, reason or "без причины")
    return mode
