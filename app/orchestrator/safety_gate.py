from __future__ import annotations

import re
from dataclasses import dataclass

_DELETE = (
    r"(?:удали|удалите|удалить|удаляй|удаляйте|удалять|снеси|снесите|снести|сотри|сотрите|стереть|"
    r"стирай|очисти|очистите|очистить|вычисти|вычистить|уничтожь|уничтожьте|уничтожить|выкинь|выкинуть|"
    r"выбрось|выбросить|грохни|грохнуть|подчисти|подчистить)(?![а-я])"
)
_ALL = r"(?:все|всех|всем|всеми|всю|вся|каждый|каждую|каждого|каждом|любые|целиком|полностью|подчистую)"
_TARGET = r"(?:кадр|сцен|проект|шот|фрейм|ролик|видео|картинк|медиа|клип)[а-я]*"
_MASS_DELETE_RE = re.compile(
    r"(?<![а-я])"
    + _DELETE
    + r"\s+(?:[а-я]+\s+){0,2}?"
    + _ALL
    + r"\s+(?:[а-я]+\s+){0,1}?"
    + _TARGET
    + r"|(?<![а-я])"
    + _ALL
    + r"\s+(?:[а-я]+\s+){0,1}?"
    + _TARGET
    + r"\s+(?:[а-я0-9]+\s+){0,3}?"
    + _DELETE
    + r"|(?<![а-я])"
    + _DELETE
    + r"\s+(?:[а-я0-9]+[\s,]+){0,4}?(?:во|в|из|на|по)\s+"
    + _ALL
    + r"\s+проект[а-я]*"
    + r"|(?<![а-я])"
    + _DELETE
    + r"\s+(?:[а-я]+\s+){0,1}?(?:проекты|сцены|кадры)(?:\s+целиком|\s+полностью)?\s*(?:[.!,]|$)"
)
_SELF_APPROVE_RE = re.compile(
    r"(?<![а-я])(?:подтверди|подтверждай|одобри|одобряй|согласуй|согласовывай|аппрув[а-я]*|апрув[а-я]*|"
    r"нажми\s+(?:за\s+меня\s+)?[«\"']?(?:подтверд|одобр)[а-я]*)\s+(?:[«\"'а-я»]+\s+){0,3}?"
    r"(?:сам(?:а|о|остоятельно)?|за\s+(?:меня|нас|человека|оператора|пользовател[а-я]*)|"
    r"вместо\s+(?:меня|нас|человека|оператора)|автоматически|без\s+меня)(?![а-я])"
    r"|(?<!я\s)(?<![а-я])(?:сам|сама|самостоятельно|автоматически)\s+(?:[а-я]+\s+){0,2}?"
    r"(?:подтверди|подтверждай|одобри|одобряй|согласуй|аппрув[а-я]*|апрув[а-я]*)(?![а-я])"
    r"|(?<![а-я])(?:одобр|подтвержд|соглас)[а-я]*\s+(?:[а-я]+\s+){0,2}?за\s+(?:человека|оператора)(?![а-я])"
)
_CHECKS_OFF_RE = re.compile(
    r"(?<![а-я])(?:отключ|выключ|убер|убра|сними|снять|отмен|обойд|обход|игнор|пропуст|деактив)[а-я]*\s+"
    r"(?:(?:все|всё|любые|эти|наши|мои)\s+)?(?:проверк|валидац|валидатор|критик|стоп-?кран|kill)[а-я]*"
)
_PAST_QUESTION_RE = re.compile(
    r"^(?:а\s+)?(?:почему|зачем|кто|когда|откуда|как\s+так)(?![а-я])(?!\s+бы(?![а-я]))"
)
_NODE_CHECK_RE = re.compile(r"агент|checkmode|checkfix|автоисправ|\bqc\b|нод[аыуеой]")

_BYPASS_TEXT = (
    "Отключать или обходить подтверждения, лимиты расходов, проверки и аварийный стоп из чата "
    "нельзя: это решает человек в настройках студии."
)
_SELF_APPROVE_TEXT = (
    "Подтверждать шаги за человека я не могу: платное и удаляющее подтверждаете только вы кнопкой в карточке."
)
_FOREIGN_TEXT = "Действовать от имени чужого аккаунта нельзя."
_MASS_DELETE_TEXT = (
    "Массово удалять кадры, сцены или проекты нельзя: по правилам студии кадры и сцены не "
    "удаляются, а только мягко скрываются — по одному, с вашим подтверждением."
)
_TAIL = (
    "Я ничего не запускал и ничего не изменил. Если нужно конкретное действие, напишите его отдельно, "
    "например: «мягко скрой кадр 3 в проекте 1»."
)


@dataclass(frozen=True)
class Refusal:
    codes: tuple[str, ...]
    text: str


def _norm(text: str) -> str:
    return " ".join((text or "").lower().replace("ё", "е").split())


def mass_delete_intent(text: str) -> bool:
    return bool(_MASS_DELETE_RE.search(_norm(text)))


def self_approve_intent(text: str) -> bool:
    return bool(_SELF_APPROVE_RE.search(_norm(text)))


def checks_off_intent(text: str) -> bool:
    low = _norm(text)
    return bool(_CHECKS_OFF_RE.search(low)) and not _NODE_CHECK_RE.search(low)


def refusal_for(text: str) -> Refusal | None:
    from app.services.action_layer.planner import approval_bypass_intent, foreign_account_intent

    if _PAST_QUESTION_RE.search(_norm(text)):
        return None
    codes: list[str] = []
    lines: list[str] = []
    if approval_bypass_intent(text) or checks_off_intent(text):
        codes.append("approval_bypass_refused")
        lines.append(_BYPASS_TEXT)
    if self_approve_intent(text):
        codes.append("self_approve_refused")
        lines.append(_SELF_APPROVE_TEXT)
    if foreign_account_intent(text):
        codes.append("foreign_account_refused")
        lines.append(_FOREIGN_TEXT)
    if mass_delete_intent(text):
        codes.append("mass_delete_refused")
        lines.append(_MASS_DELETE_TEXT)
    if not codes:
        return None
    return Refusal(tuple(codes), "Это я делать не буду. " + " ".join(lines) + " " + _TAIL)
