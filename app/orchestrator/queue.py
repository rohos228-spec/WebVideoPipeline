"""Очередь входящих сообщений.

Ack («принял, разбираю») уходит сразу, до классификации. Классификация —
сначала детерминированные правила, спорное отдаётся ``router.classify``.
Задача супервизора это не ждёт: разбор идёт отдельной asyncio-задачей.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger
from sqlalchemy import text

from app.orchestrator.contracts import (
    ORCH_DEFAULT_TIER,
    ORCH_REASONING_EFFORT,
    AgentRole,
    BudgetScope,
    ClassifyResult,
    MessageMode,
    budget_exhausted_response,
    orch_call_tier,
    orch_run_id,
)
from app.orchestrator.db.migrations import connect, utc_now
from app.orchestrator.events import EventBus
from app.orchestrator.safety_gate import Refusal, refusal_for
from app.orchestrator.small_talk import (
    CARD_WAITING_TEXT,
    DECLINE_TEXT,
    OK_TEXT,
    THANKS_TEXT,
    is_decline,
    small_talk_reply,
)

_TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_STOP_TOKENS = frozenset(
    {
        "стой",
        "отмена",
        "останови",
        "остановись",
        "стоп",
        "прерви",
        "прекрати",
        "stop",
        "cancel",
    }
)
_STOP_OBJECTS = ("проект", "генерац", "очеред", "шаг", "картин", "видео", "озвуч", "нод")
# «отмени последнее действие», «отмена правки» — откат правки, а не стоп задачи чата.
_UNDO_RE = re.compile(
    r"(?<![а-яa-z])(?:отмени(?:те)?|отменить|отмена)\s+(?:[а-я]+\s+){0,2}?"
    r"(?:последн|правк|изменени|действи|смен|настройк)[а-я]*"
    r"|(?<![а-яa-z])undo(?![a-z])"
)
# "отмени последнюю задачу", "отмена последнего запуска" — это стоп задачи, а не откат правки.
_CANCEL_TASK_RE = re.compile(
    r"(?<![а-яa-z])(?:отмени(?:те)?|отменить|отмена)\s+(?:[а-я]+\s+){0,2}?(?:задач|задани|запуск)"
)
_UNDO_EXCLUDED = ("задач", "задани", "запуск", "генерац", "карточк")
_KILL_PHRASES = (
    "аварийный стоп",
    "kill switch",
    "kill-switch",
)
_STEER_PHRASES = (
    "измени план",
    "поменяй план",
    "перепланируй",
    "скорректируй план",
    "вместо этого",
    "учти что",
    "steer:",
)
_QUESTION_STARTS = frozenset(
    {
        "что",
        "как",
        "почему",
        "зачем",
        "когда",
        "где",
        "кто",
        "какой",
        "какая",
        "какие",
        "какое",
        "сколько",
        "можно",
        "нужно",
    }
)
_TASK_MARKERS = ("задач", "план", "шаг", "нод", "кадр", "сцен")
# «Открой муравьев»: открыть вкладку чат не может, ответ подсказывает, где проект слева.
_OPEN_STARTS = frozenset({"открой", "откройте", "открыть"})
_ASK_STARTS = _QUESTION_STARTS - {"можно", "нужно"}
_IMPERATIVE_WORDS = (
    # «перемонтируй», «доведи до конца» после уточнения чата склеивались с прошлой просьбой.
    "перемонтируй",
    "пересобери",
    "доведи",
    "доделай",
    "прогони",
    "перегенери",
    "сгенери",
    "сгенерь",
    "перерисуй",
    "дорисуй",
    "сократи",
    "убавь",
    "прибавь",
    "поставь",
    "ставь",
    "установи",
    "смени",
    "поменяй",
    "замени",
    "переключи",
    "назначь",
    "запусти",
    "перезапусти",
    "стартуй",
    "продолжи",
    "повтори",
    "сделай",
    "переделай",
    "создай",
    "добавь",
    "убери",
    "удали",
    "включи",
    "выключи",
    "отключи",
    "верни",
    "откати",
    "исправь",
    "почини",
    "перегенерируй",
    "сгенерируй",
    "сформируй",
    "подготовь",
    "собери",
    "выбери",
    "обнови",
    "примени",
    "перенеси",
    "переименуй",
    "скопируй",
    "сохрани",
    "выгрузи",
    "загрузи",
    "настрой",
    "снизь",
    "понизь",
    "уменьши",
    "увеличь",
    "подними",
    "отредактируй",
    "перепиши",
    "пропусти",
    "подтверди",
    "одобри",
    "отклони",
    "допиши",
    "дополни",
    "пусть",
    # Глаголы шагов: «разбей 26 на кадры» модель-классификатор принимала за вопрос.
    "разбей",
    "раздели",
    "озвучь",
    "переозвучь",
    "смонтируй",
    "анимируй",
    "нарисуй",
    "оживи",
    "опубликуй",
    "выложи",
    # Разговорные и несовершенные формы: «давай уже картинки», «генери», «залей в тикток»,
    # «дожми проект». Без них фраза шла к модели-классификатору, и та иногда брала её вопросом.
    "генери",
    "генерируй",
    "перегень",
    "перегенерь",
    "догенерь",
    "догенери",
    "сгенерь",
    "рисуй",
    "делай",
    "переделывай",
    "запускай",
    "продолжай",
    "начинай",
    "дожми",
    "двигай",
    "упрости",
    "переведи",
    "выкладывай",
    "залей",
    "заливай",
    "закинь",
    "отправь",
    "публикуй",
    "сними",
    "наделай",
    "замути",
    "запили",
    "переставь",
    "подбери",
    "впиши",
    "озвучивай",
    "монтируй",
    "собирай",
    "переозвучь",
)
_STATUS_PHRASES = (
    "статус",
    "как дела",
    "как там",
    "как идет",
    "что с ",
    "что там",
    "что сейчас",
    "что происходит",
    "где сейчас",
    "на каком",
    "прогресс",
    "сколько потрат",
    "сколько потрач",
    "сколько осталось",
    "готов ли",
    "готово ли",
    "готовы ли",
    "идет ли",
    "закончил",
    "закончилась",
    "доделал",
)
# Глаголы запуска, которых нет в _IMPERATIVE_WORDS: «прогони», «пусти», «запускай».
_RUN_VERBS_RE = re.compile(r"(?<![а-я])(?:прогони|прогоняй|пусти|запускай|гони|продолжай)(?:те)?(?![а-я])")
_IMPERATIVES = frozenset(_IMPERATIVE_WORDS + tuple(word + "те" for word in _IMPERATIVE_WORDS))
# «Я ноду запускал!», «запустил, а он отработал 3 секунды»: рассказ о прошлом запуске —
# вопрос о том, что случилось, а не просьба запустить ещё раз (была карточка запуска).
# Срок разбора фразы моделью: дольше человек ждать не будет.
CLASSIFY_DEADLINE_S = 25.0
_PAST_RUN_RE = re.compile(
    r"(?<![а-я])(?:за|про)?(?:пускал|пустил|гонял|гнал)(?:а|и|ось)?(?![а-я])|(?<![а-я])отработал\w*"
)
_ROLE_Q_RE = re.compile(r"что\s+(?:вообще\s+)?делает|зачем\s+нуж|для\s+чего|что\s+значит|как\s+работает")
_NEGATIONS = frozenset({"не", "ни"})


# «Назови проект 12 «Муравьи»» — переименование; «назови модели» — вопрос. Различает новое имя в кавычках.
_NAME_IT_RE = re.compile(r"(?<![а-я])назови(?:те)?\s[^«\"]*[«\"]")


# «Зопусти кортинки», «сгинирируй видосы», «чё с 9», «скок ещё ждать»: опечатки и разговорные
# формы — чинить до классификатора и планировщика (планировщик отвечал «не понял»).
_TYPOS = (
    (re.compile(r"(?<![а-я])зопуст"), "запуст"),
    (re.compile(r"(?<![а-я])запуск(?=ай(?![а-я]))"), "запуск"),
    (re.compile(r"кортин"), "картин"),
    (re.compile(r"(?<![а-я])сгинир"), "сгенер"),
    (re.compile(r"(?<![а-я])генир"), "генер"),
    (re.compile(r"(?<![а-я])перегинир"), "перегенер"),
    (re.compile(r"(?<![а-я])видос(?:ы|ики|ов|ик)?(?![а-я])"), "видео"),
    (re.compile(r"(?<![а-я])(?:чё|че|чо)(?=\s+(?:с|со|там|по|за|у|в)(?![а-я]))"), "что"),
    (re.compile(r"(?<![а-я])скок(?:о)?(?![а-я])"), "сколько"),
    (re.compile(r"(?<![а-я])пиресобер"), "пересобер"),
    (re.compile(r"(?<![а-я])озвуч(?=ка(?![а-я]))"), "озвуч"),
)


_ALL_RUN_RE = re.compile(r"^(?:доведи|прогони|догони)\s+(?=(?:все|всё)\s+проект)", re.IGNORECASE)


_RUN_THEN_PRICE_RE = re.compile(
    r"[,\s]+(?:и\s+)?(?:скажи|покажи|напиши|посчитай|прикинь|сообщи)(?:\s+мне)?,?\s+"
    r"(?:сколько|во\s+сколько|какая\s+цена|почем)[^.?!]*(?:стои\w*|обойд\w*|выйд\w*|цен\w*|денег)?[?.!]*$"
)


_BATCH_THEN_RUN_RE = re.compile(
    r"^((?:сделай|создай|сгенерируй|запили|наделай)\s+(?:\d{1,3}|пару|несколько|два|три|пять|десять)\s+"
    r"(?:ролик\w*|видео|проект\w*|клип\w*)\s+.+?)[,\s]+(?:и\s+)?(?:сразу\s+)?"
    r"(?:запусти|доведи|прогони|сгенери\w*)\s+(?:их|все|всё|каждый)(?:\s+.*)?$",
    re.IGNORECASE,
)


_TAIL_LINKS = frozenset({"и", "а", "—", "-", "–", "потом", "затем", "нет", "то", "тогда", "или"})


def fix_typos(text: str) -> str:
    out = str(text or "")
    low = out.lower()
    for rx, good in _TYPOS:
        if rx.search(low):
            low = rx.sub(good, low)
            out = low
    # «Все проекты до монтажа доведи»: глагол в конце — переносим вперёд, как пишут планировщику.
    # Только когда глагол в фразе один: «почини и перезапусти», «если нет — исправь» не трогаем.
    tokens = out.split()
    if len(tokens) >= 3:
        last = tokens[-1].lower().strip(".!?")
        head = [t.lower().strip(".,!?:;") for t in tokens[:-1]]
        if (
            last in _IMPERATIVES
            and not any(t in _IMPERATIVES for t in head)
            and head[-1] not in _TAIL_LINKS
            and not tokens[-2].endswith((",", ":", ";"))
        ):
            out = " ".join([tokens[-1].strip(".!?"), *tokens[:-1]])
    # «Запусти 5 и скажи, сколько это будет стоить»: цена и так в карточке запуска — вторую
    # просьбу убираем, иначе планировщик отвечал «чат не умеет».
    if _RUN_THEN_PRICE_RE.search(out) and has_imperative(out):
        out = _RUN_THEN_PRICE_RE.sub("", out).strip(" ,.")
        # «Запусти 5»: голое число после глагола — номер проекта.
        out = re.sub(r"^((?:запусти|продолжи|доведи|прогони)\s+)(\d{1,5})(?=\s|$)", r"\1проект \2", out)
    # «Сделай 10 роликов про собак и запусти все»: сначала создание пакета (карточка), запуск —
    # следующей фразой; вместе планировщик просил «шаблон и общий лимит».
    batch = _BATCH_THEN_RUN_RE.match(out)
    if batch:
        out = batch.group(1).strip(" ,.") + " (запуск новых проектов — отдельной просьбой после создания)"
    # «Доведи все проекты до монтажа»: планировщик понимал только «запусти все проекты до …».
    return _ALL_RUN_RE.sub("запусти ", out)


def has_imperative(text: str) -> bool:
    norm = " ".join((text or "").lower().replace("ё", "е").split())
    tokens = _TOKEN_RE.findall(norm)
    return bool(_NAME_IT_RE.search(norm)) or any(
        token in _IMPERATIVES and (index == 0 or tokens[index - 1] not in _NEGATIONS)
        for index, token in enumerate(tokens)
    )


def is_plain_question(text: str) -> bool:
    """«Какая модель стоит на сценарии?»: вопрос без просьбы. Пока ждёт карточка, отвечается сразу."""
    norm = " ".join((text or "").lower().replace("ё", "е").split())
    if not norm or has_imperative(norm) or _RUN_VERBS_RE.search(norm):
        return False
    tokens = _TOKEN_RE.findall(norm)
    # «А в рублях сколько», «а с 7?», «и какой голос»: союз перед вопросом — тоже вопрос.
    while tokens and tokens[0] in ("а", "и", "ну", "так", "ок", "окей"):
        tokens = tokens[1:]
    asks = any(t in _ASK_STARTS for t in tokens[:4]) and len(tokens) <= 8
    # «а с 7?», «а в 9?»: короткое продолжение после «а».
    asks = asks or (norm.startswith("а ") and 0 < len(tokens) <= 3)
    return norm.endswith("?") or (bool(tokens) and tokens[0] in _ASK_STARTS) or asks


def is_status_question(text: str) -> bool:
    """Вопрос о ходе дел без просьбы что-то сделать: отвечается сразу, задачей не ставится."""
    norm = " ".join((text or "").lower().replace("ё", "е").split()) + " "
    if has_imperative(norm) or _RUN_VERBS_RE.search(norm):
        # «прогони дальше, что с лимитом?» - просьба с вопросом, это задача.
        return False
    return any(phrase in norm for phrase in _STATUS_PHRASES)


def _undo_request(text: str) -> bool:
    from app.services.action_layer.chat_undo import undo_phrase

    return undo_phrase(text)


_STOP_STEMS = ("останов", "прекрат", "прерв", "отмен", "хватит", "пауз", "приостанов")


_PROJECT_NAMED_RE = re.compile(r"проект\w*\s*(?:№|#)?\s*\d{1,6}(?!\d)|(?<![\w#])#\d{1,6}(?!\d)")


def _names_project(text: str) -> bool:
    return bool(_PROJECT_NAMED_RE.search((text or "").lower()))


# «че с 12м проектом», «в 12-м проекте», «пачему праект 12»: номер до слова и опечатка «праект».
# Без этого номер не находился, и чат читал открытый проект. «3 проекта» / «5 проектов» не трогаем.
_PROJECT_TYPO_RE = re.compile(r"(?<![а-яё])пра([еэ])кт", re.IGNORECASE)
_NUMBER_FIRST_RE = re.compile(
    r"(?<![\d#№\w])(\d{1,5})(?:\s*-?\s*(?:м|й|ом|ым|ый|ой|го|ого|ем))?\s+(проект(?:е|ом|у)?)(?![а-яё])",
    re.IGNORECASE,
)


def canon_project_refs(text: str) -> str:
    """Номер проекта в обычном виде «проект N» для разбора; сама реплика в истории остаётся как была."""
    out = _PROJECT_TYPO_RE.sub(lambda m: "прое" + m.group(0)[4:], text or "")
    return _NUMBER_FIRST_RE.sub(lambda m: f"{m.group(2)} {m.group(1)}", out)


# «Да, запускай» в ответ на подсказку «запусти закадровый текст в проекте 26»: согласие без
# названия шага. Без этого чат отвечал «не указан шаг» на собственную подсказку.
_GO_WORDS = frozenset(
    [
        "да",
        "ага",
        "угу",
        "ок",
        "окей",
        "ok",
        "давай",
        "давайте",
        "го",
        "поехали",
        "запускай",
        "запусти",
        "делай",
        "сделай",
        "жми",
        "можно",
        "конечно",
        "вперед",
        "вперёд",
        "хорошо",
        "ладно",
        "применяй",
        "примени",
        "включай",
        "дальше",
        "продолжай",
        "продолжи",
        "погнали",
    ]
)
_QUOTED_TAIL_RE = re.compile(r"«([^«»]{6,200})»\s*[.!]?\s*$")


_MEDIA_PATH_RE = re.compile(r"[\\/][^\\/\s]+\.(?:wav|mp3|m4a|ogg|flac|aac|png|jpe?g|webp|mp4|mov|webm)\b")
_SHOW_WORDS = frozenset({"покажи", "скажи", "расскажи", "напомни", "проверь", "посмотри"})
_FIELD_WORDS = r"освещени|свет|стык|движени|ракурс|крупн|план|действи|якор|закадр"
_FRAME_FIELD_RE = re.compile(
    rf"(?<![а-я])кадр\w*\s*\d+[^?]{{0,40}}(?:{_FIELD_WORDS})|(?:{_FIELD_WORDS})\w*[^?]{{0,40}}кадр\w*\s*\d+"
)
_TO_QUEUE_RE = re.compile(r"(?<![а-я])в очередь(?![а-я])")
_FRAME_VALUE_RE = re.compile(r"(?<![а-я])кадр\w*\s*\d+[^«\"?]{0,60}[«\"][^»\"]{2,}")
_SPEND_LIMIT_RE = re.compile(r"(?<![а-я])(?:лимит\w*|потолок\w*)\s*(?:в\s+)?\$?\s*\d")


def is_go_ahead(text: str) -> bool:
    words = _TOKEN_RE.findall((text or "").lower())
    return 0 < len(words) <= 4 and all(word in _GO_WORDS for word in words)


def suggested_command(previous: str | None) -> str | None:
    """Команда, которой чат закончил прошлый ответ: «…» в самом конце сообщения."""
    found = _QUOTED_TAIL_RE.search((previous or "").strip())
    command = found.group(1).strip() if found else ""
    # «Следующий шаг — «Сценарий»» — название, не команда: команда со строчной и из двух слов.
    if not command[:1].islower() or len(command.split()) < 2:
        return None
    return command


def _previous_messages(session_id: str, before_id: int, limit: int = 2) -> list[tuple[str, str]]:
    """Реплики перед ``before_id``, свежая первой."""
    conn = connect()
    try:
        rows = conn.execute(
            text(
                "SELECT role, content FROM orch_messages WHERE session_id = :sid AND id < :mid "
                "ORDER BY id DESC LIMIT :lim"
            ),
            {"sid": session_id, "mid": int(before_id), "lim": int(limit)},
        ).all()
    finally:
        conn.close()
    return [(str(row[0]), str(row[1] or "")) for row in rows]


# «Уточните, для какого шота…» → «шот 1»: короткий ответ без исходной просьбы терял её
# («Не указано, что нужно сделать с шотом 1»). Склеиваем ответ с просьбой, которую уточняли.
# «Какой взять: шот 1 или шот 2?» — тоже уточнение, хотя слова «уточните» в нём нет.
_CLARIFY_RE = re.compile(
    r"(?:^|[.!?;]\s+)уточните(?![а-я])|какой из|какую из|какого из"
    r"|(?<![а-я])(?:какой|какую|какое|какие|котор\w+)(?![а-я])[^?]{0,80}\?\s*$",
    re.IGNORECASE,
)
_ANSWER_MAX_WORDS = 5


# «А в рублях сколько?», «а с 7?», «а другие какие есть?»: вопрос продолжает прошлый ответ.
# Без последних реплик ответ спрашивал «какую сумму перевести» и «какой проект».
_DIALOG_TURNS = 4
_FOLLOW_UP_RE = re.compile(r"^(?:а|и|ну|так)\s")
# «Скинь ссылку на ролик», «где скачать видео»: ответ из итогового видео ноды «Монтаж», а не задача
# (у задачи нода «Сборка» с «0 файлов», и чат отвечал, что ролика нет).
_LINK_RE = re.compile(r"(?<![а-я])ссылк\w*|скачать\s+(?:готов\w*\s+)?(?:ролик|видео)|(?:где|как)\s+(?:скачать|посмотреть|найти)\s+(?:готов\w*\s+)?(?:ролик|видео)")
# «Придумай 5 тем», «посоветуй тему для тикток»: совет текстом, план студии не нужен.
_ADVICE_RE = re.compile(
    r"^(?:ну\s+|а\s+)?(?:придумай|посоветуй|подскажи|предложи|накидай|дай\s+идеи|какую\s+тему|какие\s+темы|"
    r"как\s+сделать\s+ролик|как\s+сделать\s+видео|как\s+набрать)"
)
# «Сделай подешевле», «как сэкономить»: без предмета — совет с ценами и моделями проекта,
# а не «уточните, какую ноду удешевить».
_CHEAPER_RE = re.compile(
    r"^(?:ну\s+|а\s+)?(?:сделай|сделать|можно|давай|хочу)?\s*(?:вс[её]\s+|это\s+|ролик\s+|проект\s+)?"
    r"(?:подешевле|дешевле|бюджетнее|экономнее)[?.!]*$"
    r"|^(?:как|где|на\s+ч[её]м)\s+(?:можно\s+)?(?:сэкономить|сократить\s+расходы|потратить\s+меньше)"
)


def _fact_check(low: str) -> bool:
    """«Проверь факты в тексте»: вопрос о сценарии, а не задача (импорт здесь — цикл с agents)."""
    from app.orchestrator.agents.question_context import _FACT_CHECK_RE

    return bool(_FACT_CHECK_RE.search(low))


_STUDIO_ACT_RE = re.compile(r"запуст|создай|сделай\s+проект|в\s+проект\w*\s+\d|поставь|поменяй|удали")
_DIALOG_CHARS = 900


def _recent_dialog(session_id: str, current: str) -> list[dict[str, str]]:
    """Последние реплики сессии перед текущим вопросом, по порядку, для модели ответа."""
    conn = connect()
    try:
        rows = conn.execute(
            text(
                "SELECT role, content FROM orch_messages WHERE session_id = :sid "
                "ORDER BY id DESC LIMIT :lim"
            ),
            {"sid": session_id, "lim": _DIALOG_TURNS + 1},
        ).all()
    finally:
        conn.close()
    turns = [(str(r[0]), " ".join(str(r[1] or "").split())) for r in rows]
    if turns and turns[0][0] == "user" and turns[0][1] == " ".join(str(current or "").split()):
        turns = turns[1:]
    out = [
        {"role": role, "content": body[:_DIALOG_CHARS]}
        for role, body in reversed(turns[:_DIALOG_TURNS])
        if role in ("user", "assistant") and body
    ]
    while out and out[0]["role"] != "user":
        out = out[1:]
    return out


def _follow_up_reads(text: str, dialog: list[dict[str, str]]) -> str:
    """«А другие какие есть» после «какой голос»: факты читать по прошлому вопросу вместе с этим.

    Без номера и коротко: «а с 7?» решает свой номер сам, а «а другие?» без прошлого вопроса
    читало общий статус и отвечало «списка голосов в фактах нет».
    """
    body = " ".join(str(text or "").split())
    words = body.split()
    if not words or len(words) > 7 or re.search(r"\d", body):
        return body
    low = body.lower()
    if not (low.startswith(("а ", "и ", "а?", "ещё", "еще")) or any(w in low for w in ("други", "дешевл", "дорож", "из них"))):
        return body
    if "из них" in low or "обоих" in low:
        # «А что дешевле из них доделать» после «что с 5» и «а с 7?»: номера из прошлых вопросов.
        nums: list[str] = []
        for m in dialog or []:
            if m.get("role") == "user":
                nums.extend(n for n in re.findall(r"(?<!\d)\d{1,5}(?!\d)", m.get("content", "")) if n not in nums)
        if len(nums) >= 2:
            return f"{body} (проекты {', '.join(nums[-5:])})"
    prev = next((m["content"] for m in reversed(dialog or []) if m.get("role") == "user"), "")
    if not prev or len(prev) > 200:
        return body
    return f"{prev.rstrip(' ?.!')}? {body}"


# «И пятый тоже» после «перерисуй третий», «и 7 туда же» после «запусти 5 и 6», «а ещё два про зебр»
# после «сделай ролик про жирафов»: короткое продолжение прошлой ПРОСЬБЫ. Без склейки чат брал его
# вопросом и отвечал подсказкой команды («перерисуй пятый кадр в проекте 10») вместо карточки.
_CONTINUE_START_RE = re.compile(r"^(?:и|а|ещ[её]|плюс|также|так\s+же|тоже)(?![а-я])")
_CONTINUE_WORD_RE = re.compile(r"(?<![а-я])(?:тоже|туда\s+же|так\s+же|также|ещ[её]|то\s+же\s+самое)(?![а-я])")
_CONTINUE_MAX_WORDS = 7
# Хвост с прошлой просьбой: его читают модель и планировщик; правила кодом его отрезают
# (``strip_continuation``), иначе служебные слова попадали в промт кадра.
CONTINUE_MARK = "(продолжение прошлой просьбы «"


def strip_continuation(text: str) -> str:
    """Слова человека без хвоста ``continued_request``."""
    body = str(text or "")
    cut = body.find(CONTINUE_MARK)
    return body[:cut].rstrip() if cut >= 0 else body


def continued_request(previous: list[tuple[str, str]], reply: str) -> str | None:
    """Короткое продолжение прошлой просьбы → просьба целиком для планировщика; иначе None."""
    text = " ".join(str(reply or "").lower().replace("ё", "е").split())
    words = _TOKEN_RE.findall(text)
    if (
        not words
        or len(words) > _CONTINUE_MAX_WORDS
        or text.rstrip().endswith("?")
        or not (_CONTINUE_START_RE.search(text) or _CONTINUE_WORD_RE.search(text))
        or not _CONTINUE_WORD_RE.search(text) and not re.search(r"\d|перв|втор|трет|четв|пят|шест|седьм|восьм|девят|десят|два|две|три", text)
        or is_status_question(text)
    ):
        return None
    prev = next((body for role, body in previous if role == "user"), "")
    prev_norm = " ".join(prev.lower().replace("ё", "е").split())
    if not prev_norm or len(prev_norm) > 200:
        return None
    asks_work = (
        has_imperative(prev_norm) or wish_task(prev_norm) or polite_task(prev_norm)
        or bool(_LETS_TASK_RE.search(prev_norm))
    )
    if not asks_work or is_plain_question(prev_norm):
        return None
    # Номера прошлой просьбы — «N»: правила кодом не должны взять старый номер вместо нового.
    shape = re.sub(r"(?<!\d)\d{1,5}(?!\d)", "N", prev.strip().rstrip(".!"))
    return f"{reply.strip()} {CONTINUE_MARK}{shape}» — то же самое для нового)"


def answered_request(previous: list[tuple[str, str]], reply: str) -> str | None:
    """Исходная просьба вместе с коротким ответом на уточняющий вопрос чата; иначе None."""
    words = _TOKEN_RE.findall((reply or "").lower())
    if (
        not words
        or len(words) > _ANSWER_MAX_WORDS
        or has_imperative(reply)
        or _RUN_VERBS_RE.search(" ".join(words))
        or len(previous) < 2
    ):
        return None
    if is_plain_question(reply) or is_status_question(reply):
        # «Какой голос в озвучке» после уточнения чата — новый вопрос, а не ответ: иначе он
        # склеивался с прошлой просьбой («…; уточнение: какой голос») в карточку смены голоса.
        return None
    (role_a, asked), (role_u, request) = previous[0], previous[1]
    if " ".join(request.lower().split()).rstrip(".!?") == " ".join(reply.lower().split()).rstrip(".!?"):
        return None  # та же просьба ещё раз — не ответ на уточнение
    if role_a != "assistant" or role_u != "user" or not _CLARIFY_RE.search(asked.strip()):
        return None
    return f"{request.strip().rstrip('.')}; уточнение: {reply.strip()}"


def _has_stop_word(text: str) -> bool:
    """Есть ли во фразе слово остановки: без него «прервать задачу» модели не верим."""
    low = (text or "").lower()
    tokens = _TOKEN_RE.findall(low)
    return any(token in _STOP_TOKENS or token.startswith(_STOP_STEMS) for token in tokens) or any(
        phrase in low for phrase in _KILL_PHRASES
    )


def cancel_or_undo(text: str) -> bool:
    """«Отмени последнее» при живой задаче: остановить её или вернуть правку — решает классификатор."""
    norm = " ".join((text or "").lower().split())
    tokens = _TOKEN_RE.findall(norm)
    if any(token in _STOP_TOKENS for token in tokens):
        return False
    return any(token.startswith("отмен") for token in tokens) and _undo_request(norm)


# «Нужно 5 видео про кошек», «надо озвучку», «хочу голос Джорджа»: желание без вопроса — просьба.
# Классификатор такие фразы иногда брал вопросом и отвечал «в каком проекте…».
_WISH_TASK_RE = re.compile(
    r"^(?:ну\s+|а\s+|мне\s+|слушай,?\s+)*(?:нужн[оаыe]?|нужен|надо|хочу|хотелось\s+бы|хотим)\s+"
    r"(?!(?:ли\b|узнать|понять|спросить|посмотреть|уточнить|знать|чтобы\s+ты\s+(?:рассказал|объяснил)))"
)


# «Давай уже картинки делать», «го картинки», «погнали рисовать кадры»: просьба запустить дело студии.
# «Давай посмотрим, что там» — не просьба: без слова о деле студии правило молчит.
_LETS_TASK_RE = re.compile(
    r"(?<![а-я])(?:давай|давайте|го|погнали|поехали)(?![а-я])[^?]{0,40}?"
    r"(?:картин|кадр|видео|анимац|озвуч|голос|музык|монтаж|ролик|рисова|рисуй|генер|запуск|сценари|публик|выклад)"
)


# «Можно запустить картинки?», «не мог бы ты перерисовать кадр 4?», «а можно музыку потише сделать?»:
# вежливая просьба с глаголом действия — задача, хотя в конце «?». «Можно узнать…», «можешь
# сказать…», «можно ли» — вопрос, правило молчит.
_POLITE_TASK_RE = re.compile(
    r"^(?:а\s+|ну\s+|слушай,?\s+)*(?:можно|можешь|можете|сможешь|не\s+мог(?:ла|ли)?\s+бы(?:\s+ты|\s+вы)?|"
    r"будь\s+добр\w*|будьте\s+добр\w*|пожалуйста)(?!\s+ли(?![а-я]))[,\s]+"
    r"(?:[а-я]+\s+){0,4}?"
    r"(?!(?:узнать|сказать|подсказать|объяснить|рассказать|посмотреть|понять|спросить|показать|уточнить)(?![а-я]))"
    r"(?:пере|за|с|до|по|у|вы|от|рас|при|на|об)?[а-я]{2,}(?:ть|ти|чь|й|и)(?:те)?(?![а-я])"
)


def polite_task(norm: str) -> bool:
    """Вежливая просьба сделать («можно запустить…?», «не мог бы ты перерисовать…»)."""
    text = " ".join((norm or "").lower().replace("ё", "е").split())
    found = _POLITE_TASK_RE.search(text)
    if not found:
        return False
    verb = found.group(0).split()[-1].strip(",?!.")
    return verb not in _NOT_ACTION_WORDS


# Слова на -ть/-и/-й, которые не глагол действия: «можно ли», «можешь мне», «можно какой-то».
_NOT_ACTION_WORDS = frozenset(
    {"мне", "ли", "еще", "ещё", "сейчас", "тут", "там", "какой", "какие", "этой", "этот", "свой", "мой",
     "твой", "тебе", "ты", "вы", "быть", "есть", "стать", "знать", "видеть", "найти", "идти", "пойти"}
)


def wish_task(norm: str) -> bool:
    """Просьба в форме желания («нужно …», «хочу …») без вопросительного знака."""
    text = " ".join((norm or "").lower().replace("ё", "е").split())
    return bool(_WISH_TASK_RE.search(text)) and not text.rstrip().endswith("?") and len(text.split()) >= 2


def classify_deterministic(text: str) -> ClassifyResult | None:
    """Правила до LLM. None — спорный случай, нужен fast-классификатор."""
    norm = " ".join((text or "").lower().split())
    tokens = _TOKEN_RE.findall(norm)
    if any(phrase in norm for phrase in _KILL_PHRASES):
        return ClassifyResult(
            tier="fast",
            mode=MessageMode.interrupt,
            deterministic=True,
            confidence=1.0,
            kill_switch=True,
        )
    plain = norm.replace("ё", "е")
    # «стоп, отмени последнее»: явное стоп-слово важнее отката («отмена» само по себе не в счёт).
    stop_word = any(token in _STOP_TOKENS and token != "отмена" for token in tokens)
    if _CANCEL_TASK_RE.search(plain) and not any(obj in norm for obj in _STOP_OBJECTS):
        return ClassifyResult(
            tier="fast",
            mode=MessageMode.interrupt,
            deterministic=True,
            confidence=1.0,
        )
    # Откат — только если и разбор отката согласен: вопрос "как отменить…?" и "отмени это действие" — нет.
    if (
        not stop_word
        and not any(obj in plain for obj in _UNDO_EXCLUDED)
        and _UNDO_RE.search(plain)
        and _undo_request(plain)
    ):
        return ClassifyResult(
            tier="strong",
            mode=MessageMode.task,
            deterministic=True,
            confidence=1.0,
        )
    if any(token in _STOP_TOKENS for token in tokens) and any(obj in norm for obj in _STOP_OBJECTS):
        # «Останови все проекты» — остановить генерацию в Студии, а не только задачу чата.
        return ClassifyResult(
            tier="strong",
            mode=MessageMode.task,
            deterministic=True,
            confidence=1.0,
        )
    steer = any(phrase in norm for phrase in _STEER_PHRASES)
    if not steer and not any(token in _STOP_TOKENS for token in tokens) and _undo_request(norm):
        # «Отмени последнее» — вернуть свою правку, а не прервать задачу.
        return ClassifyResult(
            tier="strong",
            mode=MessageMode.task,
            deterministic=True,
            confidence=1.0,
        )
    if any(token in _STOP_TOKENS for token in tokens):
        return ClassifyResult(
            tier="fast",
            mode=MessageMode.interrupt,
            deterministic=True,
            confidence=1.0,
        )
    if steer:
        return ClassifyResult(
            tier="strong",
            mode=MessageMode.steer,
            deterministic=True,
            confidence=1.0,
        )
    # «Теперь промты картинок для 26, лимит $0.5»: потолок расхода — это просьба запустить, не вопрос.
    # «Кадр 14: действие «сова поворачивает голову»» — новое значение поля кадра в кавычках.
    # «Проект 4: в очередь ИИзменение видео кадра 1» — поставить правку, а не вопрос об очереди.
    # «Проект 12, кадр 8: ночное освещение» — значение поля кадра без глагола: классификатор
    # то брал это задачей, то вопросом и отвечал подсказкой команды.
    field_value = bool(_FRAME_FIELD_RE.search(norm)) and not (
        tokens and (tokens[0] in _QUESTION_STARTS or tokens[0] in _SHOW_WORDS)
    )
    # «Музыка для проекта 4 — файл C:\...gm.wav» — путь к медиафайлу без вопроса: это загрузка.
    media_file = bool(_MEDIA_PATH_RE.search(norm)) and not (tokens and tokens[0] in _QUESTION_STARTS)
    sets_field = bool(
        _FRAME_VALUE_RE.search(norm) or _TO_QUEUE_RE.search(norm) or field_value or media_file
    ) and not (norm.rstrip().endswith("?"))
    if (
        has_imperative(norm)
        or sets_field
        or wish_task(norm)
        or polite_task(norm)
        or (bool(_LETS_TASK_RE.search(norm.replace('ё', 'е'))) and not norm.rstrip().endswith('?'))
        or (_SPEND_LIMIT_RE.search(norm) and not norm.rstrip().endswith("?"))
    ):
        return ClassifyResult(
            tier="strong",
            mode=MessageMode.task,
            deterministic=True,
            confidence=1.0,
        )
    # «Что делает нода работа с гпт», «зачем нужна разбивка»: вопрос о роли, не задача.
    lead = [t for t in tokens if t not in {"а", "ну", "и", "так", "слушай"}]
    role_q = bool(lead) and lead[0] in _QUESTION_STARTS and bool(_ROLE_Q_RE.search(norm))
    if role_q or (_PAST_RUN_RE.search(norm) and not _RUN_VERBS_RE.search(norm)):
        return ClassifyResult(
            tier="fast",
            mode=MessageMode.append,
            deterministic=True,
            confidence=1.0,
        )
    # «Сценарий» — имя ноды, а не «сцена»: вопрос про модель на сценарии остаётся вопросом.
    mentions_task = any(marker in norm.replace("сценари", "") for marker in _TASK_MARKERS)
    is_question = norm.endswith("?") or (bool(tokens) and tokens[0] in _QUESTION_STARTS)
    asks = norm.endswith("?") and bool(tokens) and tokens[0] in _ASK_STARTS
    if asks or (is_question and not mentions_task) or (bool(tokens) and tokens[0] in _OPEN_STARTS):
        return ClassifyResult(
            tier="fast",
            mode=MessageMode.append,
            deterministic=True,
            confidence=1.0,
        )
    return None


# «Останови проект 7 и запусти 8»: смешанная просьба идёт обычной задачей.
_STOP_VERB = r"(?:останови(?:те)?|стоп|прекрати(?:те)?|прерви(?:те)?)"
_PROJECT_REF = r"(?:№\s*|#)?\d{1,9}"
_PROJECT_LIST = _PROJECT_REF + r"(?:\s*(?:,|и|,\s*и)\s*" + _PROJECT_REF + r")*"
# Вся реплика — только «стоп-глагол + номера проектов». Всё прочее («кроме», «не трогай»,
# «стоп-кадр», вопрос, второе действие, «через 10 минут») идёт обычной задачей.
_TARGETED_STOP_RE = re.compile(
    r"(?:пожалуйста\s*,?\s*)?"
    + _STOP_VERB
    + r"\s+(?:(?:проект|проекты|проекта|проектов)\s+"
    + _PROJECT_LIST
    + r"|(?:№\s*|#)\d{1,9}(?:\s*(?:,|и|,\s*и)\s*(?:№\s*|#)\d{1,9})*)"
    + r"(?:\s*,?\s*пожалуйста)?[.!]*"
)


_TARGETED_STOP_MAX = 10


def targeted_stop_projects(text: str) -> list[int] | None:
    """«Останови проект 7», «стоп проекты 7 и 9», «останови #7, #8»: номера, иначе None.

    Только когда реплика целиком — стоп названных проектов. Пока в чате идёт задача,
    такая реплика не встаёт в очередь за ней: там она дождалась бы конца всех генераций.
    """
    norm = " ".join((text or "").lower().split()).replace("ё", "е")
    if not _TARGETED_STOP_RE.fullmatch(norm):
        return None
    found: list[int] = []
    for raw in re.findall(r"\d+", norm):
        value = int(raw)
        if value <= 0:
            return None
        if value not in found:
            found.append(value)
    if len(found) > _TARGETED_STOP_MAX:
        # Длинный список — обычной задачей, а не молча первые десять.
        return None
    return found or None


def _stop_outcome(result: Any) -> tuple[bool, str]:
    if result is None:
        return False, ""
    if isinstance(result, dict):
        ok = bool(result.get("ok")) if "ok" in result else result.get("status") == "ok"
        error = result.get("error")
    else:
        ok = bool(getattr(result, "ok", False))
        data = getattr(result, "data", None)
        error = data.get("error") if isinstance(data, dict) else None
    return ok, str(error or "").strip()


class MessageQueue:
    def __init__(self, bus: EventBus, router: Any, supervisor: Any, context: Any | None = None) -> None:
        self.bus = bus
        self.router = router
        self.supervisor = supervisor
        self.context = context
        self.app: Any = None
        self._pending: set[asyncio.Task[Any]] = set()

    async def accept(
        self,
        session_id: str,
        text: str,
        mode: MessageMode | None = None,
        project_id: int | None = None,
        *,
        kill_switch: bool = False,
        app: Any = None,
    ) -> int:
        """Записать реплику, опубликовать ack и поставить разбор в фон.

        ``run_id`` резервируется здесь, до ``classify`` и ``call``.
        Явный ``mode`` не классифицируется, кроме фразы kill switch
        и поля ``kill_switch``: оба перебивают mode. Неизвестное значение
        mode отсекает HTTP. Возвращает seq события ack. Не ждёт разбор.
        """
        if app is not None:
            self.app = app
        task_id = self.supervisor.active_task(session_id)
        run_id = orch_run_id(task_id=task_id, session_id=session_id)
        message_id = await asyncio.to_thread(
            _insert_message,
            session_id,
            "user",
            text,
            task_id=task_id,
            mode=None,
        )
        ack = await self.bus.publish(
            session_id,
            "ack",
            {"text": text, "accepted": True},
            task_id=task_id,
        )
        parsed = text
        if mode in (None, MessageMode.task) and not self._card_waiting(session_id):
            previous = await asyncio.to_thread(_previous_messages, session_id, message_id)
            last = previous[0] if previous else None
            command = (
                suggested_command(last[1]) if is_go_ahead(text) and last and last[0] == "assistant" else None
            )
            parsed = command or answered_request(previous, text) or continued_request(previous, text) or text
        job = asyncio.create_task(
            self._classify(
                session_id,
                canon_project_refs(fix_typos(parsed)),
                run_id,
                mode,
                project_id,
                message_id,
                kill_switch=kill_switch,
            ),
            name=f"orch-classify-{session_id}",
        )
        self._pending.add(job)
        job.add_done_callback(self._pending.discard)
        return ack.seq

    async def drain(self, timeout: float = 2.0) -> None:
        """Дождаться разборов, которые уже стоят в фоне."""
        pending = [job for job in list(self._pending) if not job.done()]
        if not pending:
            return
        await asyncio.wait_for(asyncio.gather(*pending), timeout)

    async def _classify(
        self,
        session_id: str,
        text: str,
        run_id: str,
        mode: MessageMode | None,
        project_id: int | None = None,
        message_id: int | None = None,
        *,
        kill_switch: bool = False,
    ) -> None:
        from app.orchestrator.routing.classifier import is_control_request
        from app.services import spend_guard

        with spend_guard.chat_control(kill_switch or is_control_request(text)):
            await self._classify_turn(
                session_id, text, run_id, mode, project_id, message_id, kill_switch=kill_switch
            )

    async def _classify_turn(
        self,
        session_id: str,
        text: str,
        run_id: str,
        mode: MessageMode | None,
        project_id: int | None,
        message_id: int | None,
        *,
        kill_switch: bool,
    ) -> None:
        published = False
        try:
            if not kill_switch and mode is not MessageMode.interrupt:
                refusal = refusal_for(text)
                if refusal is not None:
                    published = True
                    await self._refuse(session_id, refusal, message_id)
                    return
            if not kill_switch and mode in (None, MessageMode.task):
                # Явный append (API) остаётся вопросом к модели: готовый ответ только для реплик из чата.
                reply = small_talk_reply(text)
                # «Спасибо, не надо» при открытой карточке — не отказ: кнопка «Отклонить» на карточке.
                if (
                    reply in (OK_TEXT, DECLINE_TEXT) or (reply == THANKS_TEXT and is_decline(text))
                ) and self._card_waiting(
                    session_id
                ):
                    reply = CARD_WAITING_TEXT
                if reply is not None:
                    published = True
                    await self._small_talk(session_id, reply, message_id)
                    return
            forced = classify_deterministic(text)
            phrase = forced is not None and forced.kill_switch
            if kill_switch or phrase:
                decided = ClassifyResult(
                    tier="fast",
                    mode=MessageMode.interrupt,
                    deterministic=True,
                    confidence=1.0,
                    kill_switch=True,
                )
            elif mode is not None:
                decided = ClassifyResult(
                    tier=ORCH_DEFAULT_TIER,
                    mode=mode,
                    deterministic=True,
                    confidence=1.0,
                )
            else:
                decided = forced
                if (
                    decided is not None
                    and decided.mode is MessageMode.task
                    and not decided.kill_switch
                    and _CHEAPER_RE.search(" ".join(text.lower().replace("ё", "е").split()))
                ):
                    # «Сделай подешевле»: «сделай» делало это задачей — а нужен совет с ценами проекта.
                    decided = decided.model_copy(update={"mode": MessageMode.append, "tier": "fast"})
                task_id = self.supervisor.active_task(session_id)
                if decided is not None and task_id is not None and cancel_or_undo(text):
                    # Идёт генерация или ждёт карточка: просьба со словом «отмени» может значить «останови».
                    decided = None
                if (
                    decided is None
                    and task_id is not None
                    and (is_status_question(text) or is_plain_question(text))
                ):
                    # Пока идёт задача, вопрос «как там проект» иначе встал бы в очередь за ней.
                    decided = ClassifyResult(
                        tier="fast",
                        mode=MessageMode.append,
                        deterministic=True,
                        confidence=1.0,
                    )
                if decided is None:
                    try:
                        decided = await asyncio.wait_for(
                            self.router.classify(
                                text,
                                run_id=run_id,
                                task_id=None if task_id is None else str(task_id),
                            ),
                            CLASSIFY_DEADLINE_S,
                        )
                        decided = decided.model_copy(update={"deterministic": False})
                    except TimeoutError:
                        # Провайдер тянул классификатор по 3–4 минуты, чат стоял, ответы приходили
                        # на чужие фразы. Не дождались — решаем по фразе: вопрос — ответ, иначе задача.
                        logger.warning("orch classify deadline {}s for session {}", CLASSIFY_DEADLINE_S, session_id)
                        question = is_status_question(text) or is_plain_question(text)
                        decided = ClassifyResult(
                            tier="fast" if question else ORCH_DEFAULT_TIER,
                            mode=MessageMode.append if question else MessageMode.task,
                            deterministic=True,
                            confidence=0.5,
                        )
                    if decided.mode is MessageMode.interrupt and not _has_stop_word(text):
                        # «Проект 12 на каком шаге стоит?»: модель читала «стоит» как «стоп» и отвечала
                        # «нечего останавливать». Без стоп-слова это вопрос или просьба.
                        question = is_status_question(text) or is_plain_question(text)
                        decided = decided.model_copy(
                            update={"mode": MessageMode.append}
                            if question
                            else {"mode": MessageMode.task, "tier": "strong"}
                        )
                    if (
                        decided.mode is MessageMode.interrupt
                        and task_id is None
                        and not decided.kill_switch
                        and _names_project(text)
                    ):
                        # «Приостанови проект 21»: задач в чате нет, просьба — пауза проекта, а не стоп чата.
                        decided = decided.model_copy(update={"mode": MessageMode.task, "tier": "strong"})
                    if decided.mode is MessageMode.steer and task_id is None:
                        # Уточнять нечего: прошлая задача закончена — это новая просьба.
                        decided = decided.model_copy(update={"mode": MessageMode.task, "tier": "strong"})
                    low = " ".join(text.lower().replace("ё", "е").split())
                    if decided.mode is MessageMode.task and (
                        _LINK_RE.search(low)
                        or _CHEAPER_RE.search(low)
                        or (_fact_check(low) and not _STUDIO_ACT_RE.search(low))
                        or (_ADVICE_RE.match(low) and not _STUDIO_ACT_RE.search(low))
                    ):
                        decided = decided.model_copy(update={"mode": MessageMode.append, "tier": "fast"})
                    if decided.mode is MessageMode.task and _FOLLOW_UP_RE.match(low) and is_plain_question(text):
                        # «А в рублях сколько», «а с 7?»: продолжение прошлого ответа — вопрос с историей
                        # диалога, а не задача (у планировщика истории нет, он просил «уточните сумму»).
                        decided = decided.model_copy(update={"mode": MessageMode.append, "tier": "fast"})
            await self.bus.publish(
                session_id,
                "classification",
                {
                    "mode": decided.mode.value,
                    "tier": decided.tier,
                    "deterministic": decided.deterministic,
                    "confidence": decided.confidence,
                    "kill_switch": decided.kill_switch,
                },
            )
            published = True
            if message_id is not None:
                await asyncio.to_thread(_set_message_mode, message_id, decided.mode.value)
            await self._apply(session_id, text, decided, project_id)
        except Exception as exc:
            logger.exception("orch classification failed for session {}", session_id)
            if not published:
                if message_id is not None:
                    await asyncio.to_thread(_set_message_mode, message_id, MessageMode.append.value)
                await self.bus.publish(
                    session_id,
                    "classification",
                    {"mode": MessageMode.append.value, "deterministic": False, "error": "classifier_failed"},
                )
            try:
                await self.explain_failure(session_id, exc)
            except Exception:
                logger.exception("orch failure message not sent for session {}", session_id)

    async def _refuse(self, session_id: str, refusal: Refusal, message_id: int | None) -> None:
        await self.bus.publish(
            session_id,
            "classification",
            {
                "mode": MessageMode.append.value,
                "tier": "fast",
                "deterministic": True,
                "confidence": 1.0,
                "kill_switch": False,
                "refused": True,
            },
        )
        if message_id is not None:
            await asyncio.to_thread(_set_message_mode, message_id, MessageMode.append.value)
        await asyncio.to_thread(
            _insert_message,
            session_id,
            "assistant",
            refusal.text,
            task_id=None,
            mode=MessageMode.append.value,
        )
        await self.bus.publish(
            session_id,
            "message",
            {"role": "assistant", "text": refusal.text, "kind": "refusal", "codes": list(refusal.codes)},
        )

    async def _small_talk(self, session_id: str, text: str, message_id: int | None) -> None:
        """«привет», «спасибо», «помоги»: ответ без задачи, планировщика и модели."""
        await self.bus.publish(
            session_id,
            "classification",
            {
                "mode": MessageMode.append.value,
                "tier": "fast",
                "deterministic": True,
                "confidence": 1.0,
                "kill_switch": False,
            },
        )
        if message_id is not None:
            await asyncio.to_thread(_set_message_mode, message_id, MessageMode.append.value)
        await self._reply(session_id, text, "small_talk")

    def _card_waiting(self, session_id: str) -> bool:
        row = self._active_row(session_id)[1]
        return str(row.get("status") or "") == "waiting_approval"

    def _active_row(self, session_id: str) -> tuple[int | None, dict[str, Any]]:
        task_id = self.supervisor.active_task(session_id)
        tasks = getattr(self.supervisor, "tasks", None)
        row = (tasks.get(task_id) if tasks is not None and task_id is not None else None) or {}
        return task_id, row

    async def _reply(self, session_id: str, text: str, kind: str) -> None:
        await asyncio.to_thread(
            _insert_message,
            session_id,
            "assistant",
            text,
            task_id=None,
            mode=MessageMode.append.value,
        )
        await self.bus.publish(session_id, "message", {"role": "assistant", "text": text, "kind": kind})

    async def _interrupt(self, session_id: str) -> None:
        """Стоп без объекта: отменить задачу чата и всегда сказать, что остановлено."""
        task_id, row = self._active_row(session_id)
        status = str(row.get("status") or "")
        closes = getattr(self.supervisor, "stop_closes_card", None)
        if task_id is not None and status == "waiting_approval" and callable(closes) and closes(task_id):
            # Карточка после неизвестного исхода платного шага: стоп сам её закрывает, кнопки уже не будет.
            status = "cancelling"
        self.supervisor.request_cancel(session_id)
        await self._reply(
            session_id, stop_reply(str(row.get("goal") or ""), status, task_id is not None), "stop"
        )

    async def _apply(
        self,
        session_id: str,
        text: str,
        decided: ClassifyResult,
        project_id: int | None = None,
    ) -> None:
        if decided.kill_switch:
            idle = self.supervisor.active_task(session_id) is None
            job = self.supervisor.request_kill(session_id)
            payload = await job if job is not None else None
            body = payload if isinstance(payload, dict) else {}
            removed, stopped = body.get("queue_removed"), body.get("queue_stopped")
            note = queue_removed_note(
                removed if isinstance(removed, list) else [], stopped if isinstance(stopped, list) else []
            )
            if idle:
                # Без задачи итоговой сводки не будет: ответить сразу, иначе чат молчит.
                text = note or stop_reply("", "", False)
                await self._reply(session_id, text, "stop")
            elif note:
                await self._reply(session_id, note, "stop")
            return
        if decided.mode is MessageMode.interrupt:
            await self._interrupt(session_id)
            return
        if decided.mode is MessageMode.steer:
            if self.supervisor.active_task(session_id) is None:
                await self._reply(session_id, STEER_IDLE_TEXT, "steer_idle")
                return
            self.supervisor.request_steer(session_id, text)
            return
        if decided.mode is MessageMode.task:
            targets = targeted_stop_projects(text)
            if targets and self.supervisor.active_task(session_id) is not None:
                await self._stop_projects_now(session_id, targets)
                return
            await self.supervisor.submit_task(session_id, text, project_id=project_id)
            return
        self.supervisor.note_context(session_id, text)
        await self._stream_fast(session_id, text, decided.tier or "fast", project_id)

    async def _stop_projects_now(self, session_id: str, project_ids: list[int]) -> None:
        """Стоп названных проектов сразу, мимо очереди задач: задача чата и другие проекты идут дальше."""
        task_id = self.supervisor.active_task(session_id)
        actions = getattr(self.supervisor, "actions", None)
        app = self.app if self.app is not None else getattr(self.supervisor, "app", None)
        if app is None:
            from app.orchestrator import app_ref

            app = app_ref.current()
        lines: list[str] = []
        for project in project_ids:
            result: Any = None
            if actions is not None:
                ctx = {
                    "task_id": task_id,
                    "session_id": session_id,
                    "app": app,
                    "bus": self.bus,
                    "operator": f"orchestrator:task-{task_id}" if task_id is not None else "orchestrator",
                }
                try:
                    result = await actions.execute("project.stop", {"project_id": int(project)}, ctx)
                except Exception:
                    logger.exception("orch targeted stop of project {} failed", project)
            ok, error = _stop_outcome(result)
            if ok:
                lines.append(f"Проект #{project}: стоп отправлен.")
            else:
                lines.append(
                    f"Проект #{project} остановить не удалось" + (f": {error}" if error else "") + "."
                )
        lines.extend(self._stop_task_note(task_id, project_ids))
        await self._reply(session_id, " ".join(lines), "stop")

    def _stop_task_note(self, task_id: int | None, project_ids: list[int]) -> list[str]:
        """Что будет с задачей чата: соседи идут дальше, только этот проект или проект не из задачи."""
        if task_id is None:
            return []
        mine: set[int] = set()
        stored = getattr(self.supervisor, "_stored_plan", None)
        try:
            for step in stored(task_id) if callable(stored) else []:
                raw = (step.params or {}).get("project_id")
                if raw is not None and int(raw) > 0:
                    mine.add(int(raw))
        except Exception:
            logger.exception("orch targeted stop: plan of task {} not read", task_id)
        stopped = set(project_ids)
        outside = sorted(stopped - mine) if mine else []
        notes: list[str] = []
        if outside:
            listed = ", ".join(f"#{pid}" for pid in outside)
            notes.append(f"Задача чата {listed} не запускала, она идёт как шла.")
        rest = sorted(mine - stopped)
        if mine & stopped:
            if rest:
                notes.append(
                    "Остальные проекты задачи идут дальше: " + ", ".join(f"#{pid}" for pid in rest) + "."
                )
            else:
                notes.append("Других проектов у задачи нет: она закончится, когда шаг остановится.")
        return notes

    async def _question_messages(
        self,
        session_id: str,
        text: str,
        project_id: int | None,
    ) -> list[dict[str, Any]]:
        plain = [{"role": "user", "content": text}]
        if self.context is None:
            return plain
        try:
            if project_id is None:
                project_id = await asyncio.to_thread(self._session_project, session_id)
            app = self.app if self.app is not None else getattr(self.supervisor, "app", None)
            try:
                dialog = await asyncio.to_thread(_recent_dialog, session_id, text)
            except Exception:
                dialog = []
            read_text = _follow_up_reads(text, dialog)
            messages = await self.context.messages(read_text, project_id, app, session_id)
            if read_text != text:
                # Факты читались по прошлому вопросу, а спрашивает человек своими словами.
                messages = [
                    {**m, "content": text} if m.get("role") == "user" and m.get("content") == read_text else m
                    for m in messages
                ]
            if dialog and messages and messages[0].get("role") == "system":
                note = {
                    "role": "system",
                    "content": "Ниже — последние реплики этого чата. Короткий вопрос («а в рублях?», "
                    "«а с 7?», «а другие?») продолжает их: отвечай про то же самое. Факты бери из "
                    "системного сообщения выше, а не из старых ответов, если они расходятся.",
                }
                return [messages[0], note, *dialog, *messages[1:]]
            return messages
        except Exception:
            logger.exception("orch question context failed for session {}", session_id)
            return plain

    def _session_project(self, session_id: str) -> int | None:
        task_id = getattr(self.supervisor, "_session_task", {}).get(session_id)
        tasks = getattr(self.supervisor, "tasks", None)
        if task_id is None or tasks is None:
            return None
        raw = (tasks.get(task_id) or {}).get("project_id")
        try:
            value = int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    async def _stream_fast(
        self,
        session_id: str,
        text: str,
        tier: str,
        project_id: int | None = None,
    ) -> None:
        """Заглушка стрима: слова из готового ``response.text``.

        Это не контракт UI. Боевой путь — keyword-only ``on_delta`` у
        ``router.call`` (WS5): каждый delta публикуется как token.
        Если роутер не зовёт ``on_delta``, остаётся нарезка готового текста.
        """
        task_id = self.supervisor.active_task(session_id)
        deltas: list[str] = []

        def on_delta(piece: str) -> None:
            deltas.append(str(piece))

        messages = await self._question_messages(session_id, text, project_id)
        try:
            response = await self.router.call(
                orch_call_tier(tier),
                messages,
                True,
                run_id=orch_run_id(task_id=task_id, session_id=session_id),
                task_id=None if task_id is None else str(task_id),
                agent_role=AgentRole.summarizer.value,
                budget_scope=BudgetScope.internal.value,
                reasoning_effort=ORCH_REASONING_EFFORT,
                on_delta=on_delta,
            )
        except Exception as exc:
            logger.warning("orch answer failed for session {}: {}", session_id, type(exc).__name__)
            fallback = _answer_fallback(text)
            if fallback is None:
                await self.explain_failure(session_id, exc, task_id)
            else:
                # Число известно без модели (предел параллели): ответить им, а не ошибкой.
                await self._reply(session_id, fallback, "fallback")
            return
        from app.orchestrator.agents.summarizer import normalize_links, strip_order_prefix

        # Первый провайдер мог успеть прислать куски (ошибку шлюза), а ответ дал запасной:
        # куски берём, только если они и есть итоговый текст.
        parts = deltas if "".join(deltas) == str(response.text or "") else _token_pieces(response.text)
        # Сохранённый и итоговый текст — со ссылками без пробела после «(»: иначе они видны как текст.
        answer = normalize_links(str(response.text or ""))
        # «Проверьте журнал сервера…: да, …» — приказ-пересказ перед сутью ответа не показываем.
        trimmed = strip_order_prefix(answer)
        if trimmed != answer:
            answer, parts = trimmed, _token_pieces(trimmed)
        if not answer.strip():
            # Пустую реплику не пишем и не показываем: пустой пузырь «оркестратор:» без слов.
            logger.warning("orch answer empty for session {}", session_id)
            answer = _answer_fallback(text) or (
                EMPTY_BUDGET_TEXT if budget_exhausted_response(response) else EMPTY_ANSWER_TEXT
            )
            parts = _token_pieces(answer)
        message_id = await asyncio.to_thread(
            _insert_message,
            session_id,
            "assistant",
            answer,
            task_id=task_id,
            mode=MessageMode.append.value,
        )
        await self.bus.publish(
            session_id,
            "message_start",
            {"message_id": message_id, "role": "assistant"},
            task_id=task_id,
        )
        for part in parts:
            await self.bus.publish(
                session_id,
                "token",
                {"text": part, "message_id": message_id},
                task_id=task_id,
            )
        await self.bus.publish(
            session_id,
            "message_end",
            {"message_id": message_id, "role": "assistant"},
            task_id=task_id,
        )
        if budget_exhausted_response(response) and task_id is not None:
            await self.supervisor.absorb_budget(task_id, session_id, response)
        elif budget_exhausted_response(response):
            await self.supervisor.publish_approval_required(
                session_id,
                {"reason": "budget_exhausted", "task_id": None},
                task_id=None,
            )
        await self.bus.publish(
            session_id,
            "message",
            {"role": "assistant", "text": answer},
            task_id=task_id,
        )

    async def explain_failure(self, session_id: str, exc: BaseException, task_id: int | None = None) -> None:
        from app.orchestrator.agents.summarizer import explain_failure

        text = "Ответить не получилось. " + explain_failure(exc)
        await asyncio.to_thread(
            _insert_message,
            session_id,
            "assistant",
            text,
            task_id=task_id,
            mode=MessageMode.append.value,
        )
        await self.bus.publish(
            session_id,
            "message",
            {"role": "assistant", "text": text, "kind": "error"},
            task_id=task_id,
        )


_PROJECT_STOP = "напишите «останови проект 5» с нужным номером"
_STOP_HINT = (
    "Генерацию в проектах это не останавливает: для неё нажмите кнопку «Стоп задачи» (в большом окне "
    "чата она называется «Остановить задачу» и останавливает задачу вместе с её проектом) или "
    + _PROJECT_STOP
    + "."
)
EMPTY_ANSWER_TEXT = "Не получилось ответить: модель вернула пустой ответ. Повторите вопрос."
EMPTY_BUDGET_TEXT = (
    "Не получилось ответить: закончился лимит трат на ответы чата. "
    "Подтвердите продолжение на карточке и повторите вопрос."
)


def _answer_fallback(text: str) -> str | None:
    """Ответ без модели, если он есть: предел параллели знает ``runtime_streams``."""
    from app.orchestrator.agents.question_context import parallel_limit_answer

    try:
        return parallel_limit_answer(text)
    except Exception:
        logger.exception("orch answer fallback failed")
        return None


STEER_IDLE_TEXT = (
    "Сейчас нет задачи, к которой это относится, поэтому я ничего не менял. "
    "Напишите просьбу целиком, например «запусти картинки в проекте 6»."
)


def queue_removed_note(project_ids: list[Any], stopped_ids: list[Any] | None = None) -> str:
    """Сколько проектов, поставленных чатом, аварийный стоп убрал из очереди генерации."""
    from app.services.action_layer.card_text import projects_ru

    def _ints(items: list[Any] | None) -> list[int]:
        return [int(item) for item in items or [] if isinstance(item, int) and not isinstance(item, bool)]

    ids = _ints(project_ids)
    if not ids:
        return ""
    stopped = [pid for pid in _ints(stopped_ids) if pid in ids]
    listed = ", ".join(f"#{pid}" for pid in ids)
    text = f"Из очереди генерации убрал {projects_ru(len(ids))}, которые ставил чат: {listed}. "
    if stopped:
        text += (
            f"У {', '.join(f'#{pid}' for pid in stopped)} шаг уже шёл - он останавливается, "
            "дальше эти проекты не пойдут. "
        )
    return text + "Сами они не стартуют, запустить снова можно кнопкой ▶ или просьбой в чате."


def stop_reply(goal: str, status: str, active: bool) -> str:
    """Ответ на «стоп» / «останови все»: что остановлено, а что нет."""
    name = " ".join(goal.split())
    name = f" «{name[:80]}»" if name else ""
    if not active:
        return (
            "Сейчас нечего останавливать: задач в чате нет. "
            "Чтобы остановить генерацию проекта, " + _PROJECT_STOP + "."
        )
    if status == "waiting_approval":
        return (
            f"Задача{name} ждёт вашего решения на карточке и сама ничего не запустит. "
            "Чтобы отказаться, нажмите «Отклонить». " + _STOP_HINT
        )
    return f"Останавливаю задачу чата{name}: новых шагов она не начнёт. " + _STOP_HINT


def _token_pieces(text: str) -> list[str]:
    if not text:
        return []
    bits = text.split(" ")
    pieces: list[str] = []
    for index, bit in enumerate(bits):
        pieces.append(bit if index == len(bits) - 1 else bit + " ")
    return pieces


def _insert_message(
    session_id: str,
    role: str,
    content: str,
    *,
    task_id: int | None,
    mode: str | None,
) -> int:
    conn = connect()
    try:
        new_id: int = conn.execute(
            text(
                """
                INSERT INTO orch_messages (session_id, task_id, role, content, mode, created_at)
                VALUES (:sid, :task_id, :role, :content, :mode, :stamp)
                RETURNING id
                """
            ),
            {
                "sid": session_id,
                "task_id": None if task_id is None else int(task_id),
                "role": role,
                "content": content,
                "mode": mode,
                "stamp": utc_now(),
            },
        ).scalar_one()
        conn.commit()
        return int(new_id)
    finally:
        conn.close()


def _set_message_mode(message_id: int, mode: str) -> None:
    conn = connect()
    try:
        conn.execute(
            text("UPDATE orch_messages SET mode = :mode WHERE id = :id AND mode IS NULL"),
            {"mode": mode, "id": int(message_id)},
        )
        conn.commit()
    finally:
        conn.close()
