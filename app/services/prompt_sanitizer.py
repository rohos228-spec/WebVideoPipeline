"""Очистка и санитайзер промптов от рассуждений, отказов и болтовни ИИ.

Защищает от ситуаций, когда модель возвращает отказ (refusal) или дописывает
свои комментарии/рассуждения в тело промпта (например: «— I не могу выполнить
эту генерацию. Запрос описывает вооружённого преступника...»).
"""

from __future__ import annotations

import re

# Паттерны полного отказа или начала объяснения отказа
_REFUSAL_START_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)^(?:к\s+сожалению,?\s+)?(?:я\s+)?не\s+могу\s+(?:выполнить|сгенерировать|создать|нарисовать|обработать)"
    ),
    re.compile(
        r"(?i)^(?:i\s+cannot|i\s+am\s+unable\s+to|sorry,?\s+i\s+cannot)\s+(?:fulfill|generate|create|complete|draw|process)"
    ),
    re.compile(
        r"(?i)^(?:извините|sorry|к\s+сожалению|unfortunately)[,\s]+(?:но\s+)?(?:я\s+не\s+могу|i\s+cannot)"
    ),
    re.compile(r"(?i)^(?:как\s+искусственный\s+интеллект|как\s+языковая\s+модель|as\s+an?\s+ai)"),
    re.compile(r"(?i)^(?:этот\s+запрос|данный\s+запрос|this\s+request)\s+(?:нарушает|противоречит|violates)"),
    re.compile(r"(?i)^(?:отказ|refusal|запрос\s+отклон[её]н)\b"),
)

# Маркеры хвоста-рассуждения или прилипшего отказа (после дефиса/тире или переноса строки)
_CHATTER_TAIL_RE = re.compile(
    r"(?is)\s*(?:—|–|--|-)\s*"
    r"(?:(?:i|я)\s+не\s+могу\s+выполнить|"
    r"i\s+cannot\s+(?:fulfill|generate|create)|"
    r"не\s+могу\s+выполнить\s+эту\s+генерацию|"
    r"запрос\s+описывает|"
    r"данный\s+запрос\s+описывает|"
    r"это\s+по\s+сути\s+создание|"
    r"к\s+сожалению,?\s+(?:я\s+)?не\s+могу|"
    r"к\s+сожалению,?\s+i\s+cannot|"
    r"извините,?\s+(?:но\s+)?(?:я\s+)?не\s+могу|"
    r"as\s+an\s+ai|"
    r"как\s+искусственный\s+интеллект|"
    r"нарушает\s+(?:наши\s+)?(?:правила|политик)|"
    r"слишком\s+близко\s+к\s+контенту).*$"
)

# Хвостовые блоки мета-комментариев / примечаний
_BLOCK_TAIL_RE = re.compile(
    r"(?is)\n+\s*"
    r"(?:примечание|заметка|note|рассуждение|вывод|пояснение|комментарий|дисклеймер|disclaimer|reasoning|explanation)\s*"
    r"[:—–-].*$"
)

# Вводная болтовня в начале строки («Вот промпт:», «Sure, here is the prompt:» и т.п.)
_INTRO_CHATTER_RE = re.compile(
    r"(?i)^(?:"
    r"вот\s+(?:обновл[её]нный\s+|ваш\s+|новый\s+)?промп?т|"
    r"здесь\s+(?:обновл[её]нный\s+|новый\s+)?промп?т|"
    r"конечно[!,.]\s*(?:вот\s+промп?т)?[,:]?|"
    r"sure[!,.]\s*(?:here\s+is\s+(?:the\s+)?(?:new\s+|updated\s+|sanitized\s+)?prompt)?[,:]?|"
    r"certainly[!,.]\s*(?:here\s+is\s+(?:the\s+)?prompt)?[,:]?|"
    r"here\s+is\s+(?:the\s+)?(?:new\s+|updated\s+|sanitized\s+)?prompt|"
    r"prompt\s*:"
    r")[\s:—–-]*"
)


def is_ai_refusal(text: str) -> bool:
    """Проверяет, является ли текст (или его начало) отказом модели."""
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    return any(p.search(cleaned) for p in _REFUSAL_START_PATTERNS)


def strip_ai_chatter(text: str) -> str:
    """Удаляет вводные фразы и прилипшие хвосты рассуждений/отказов."""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""

    # Срезаем вводные («Вот промпт:», «Sure, here is...»)
    cleaned = _INTRO_CHATTER_RE.sub("", cleaned).strip()

    # Срезаем блоки примечаний в конце («Примечание: ...», «Вывод: ...»)
    cleaned = _BLOCK_TAIL_RE.sub("", cleaned).strip()

    # Срезаем прилипший через тире отказ («... фон. — I не могу выполнить...»)
    cleaned = _CHATTER_TAIL_RE.sub("", cleaned).strip()

    # Убираем повисшие тире и запятые на конце после обрезки
    cleaned = re.sub(r"[\s—–,-]+$", "", cleaned).strip()

    return cleaned


def clean_prompt_from_ai_chatter(text: str) -> tuple[str, bool]:
    """Санирует текст промпта.

    Возвращает (очищенный_промпт, был_ли_отказ).
    Если текст целиком являлся отказом или после очистки остался пустым —
    возвращает ("", True).
    """
    raw = (text or "").strip()
    if not raw:
        return "", True

    if is_ai_refusal(raw):
        return "", True

    cleaned = strip_ai_chatter(raw)

    if not cleaned or is_ai_refusal(cleaned):
        return "", True

    return cleaned, False
