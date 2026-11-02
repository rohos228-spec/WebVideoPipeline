"""Ответ чата проекта — человеческим языком: без кодов шагов, ключей нод и «кода».

Модель видит в контексте служебные ключи (``img_pr``, ``n_excel_gpt_1``,
``waiting_hitl``, типы нод) и норовит повторить их пользователю в обратных
кавычках. Человек на канвасе видит только названия нод («Промты картинок»),
поэтому перед показом ответа ключи меняются на названия, а технические
блоки кода убираются. Сам ответ модели (с JSON действий) для исполнения
не меняется — правится только текст, который видит человек.
"""

from __future__ import annotations

import re
from collections import Counter

from app.services.action_layer import builtin_insight as bi

# Как ноды называются на канвасе (подпись по умолчанию у нового проекта).
_TYPE_DEFAULT: dict[str, str] = {**bi._TYPE_RU, "plan": "Сценарий", "hitl_gate": "Проверка человеком"}

_STEP_EXTRA: dict[str, str] = {f"enrich_{i}": f"Обогащение {i}" for i in range(1, 6)}

# Статусы нод и настройки — простыми словами (статусы в «»).
_STATUS_WORDS: dict[str, str] = {
    "waiting_hitl": "«ждёт проверки человеком»",
    "pending": "«ждёт»",
    "queued": "«в очереди»",
    "running": "«выполняется»",
    "failed": "«ошибка»",
    "skipped": "«пропущена»",
    "paused": "«на паузе»",
    # Ключи настроек из раздела НАСТРОЙКИ.
    "image_generator": "генератор картинок",
    "video_generator": "генератор видео",
    "aspect_ratio": "формат кадра",
    "image_resolution": "разрешение картинок",
    "video_resolution": "разрешение видео",
    "image_quality": "качество картинок",
    "hero_mode": "режим персонажей",
    "auto_mode": "автоматический режим",
}

_HITL = "«Проверка человеком»"
_WORDS = (
    # Окончание русского слова сохраняется: «HITL-ноды» → «ноды «Проверка человеком»».
    (re.compile(r"HITL[-‑ ]?гейт(\w*)", re.IGNORECASE), r"этап\1 " + _HITL),
    (re.compile(r"HITL[-‑ ]?нод(\w*)", re.IGNORECASE), r"нод\1 " + _HITL),
    (re.compile(r"HITL[-‑ ]?окн(\w*)", re.IGNORECASE), r"окн\1 проверки"),
    (re.compile(r"HITL[-‑ ]?запрос(\w*)", re.IGNORECASE), r"запрос\1 на проверку"),
    (re.compile(r"\bHITL\b"), _HITL),
    (re.compile(r"провер(\w*)\s*\(?harness\)?", re.IGNORECASE), r"автопровер\1"),
    (re.compile(r"\bharness\b", re.IGNORECASE), "прогон автопроверки"),
    (re.compile(r"\bnode_key\b"), "нода"),
    (re.compile(r"\buuid\b"), "кадр"),
)

_CODE_LANGS = {"json", "python", "py", "js", "ts", "tsx", "bash", "sh", "diff", "sql", "yaml", "toml"}
_FENCE_RE = re.compile(r"```([\w+-]*)[ \t]*\n?([\s\S]*?)```")
_INLINE_RE = re.compile(r"`([^`\n]{1,80})`")
# Голые ключи без кавычек: только «технического» вида (с подчёркиванием или n_…),
# чтобы не трогать обычные слова вроде video/music в тексте.
_BARE_RE = re.compile(r"(?<![\w«/.-])([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?![\w»/.-])")
_DUP_PAREN_RE = re.compile(r"«([^«»]+)»\s*\(\s*«?([^()«»]{1,60})»?\s*\)")


def _project_status_words() -> dict[str, str]:
    """``images_ready`` → «Картинки» готовы, ``generating_images`` → идут «Картинки»."""
    from app.orchestrator.node_registry import WORK_NODES

    out: dict[str, str] = {}
    for spec in WORK_NODES.values():
        label = _TYPE_DEFAULT.get(spec.node_type) or bi._type_ru(spec.node_type)
        out.setdefault(spec.running_status.value, f"идёт шаг «{label}»")
        out.setdefault(spec.ready_status.value, f"шаг «{label}» готов")
    return out


def build_labels(canvas_keymap: dict[str, dict] | None) -> dict[str, str]:
    """Служебный токен → как его показать человеку."""
    from app.orchestrator.node_registry import STEP_CODE_TO_NODE_TYPE

    keymap = canvas_keymap or {}
    raw = {k: str((v or {}).get("label") or "").strip() for k, v in keymap.items()}
    counts = Counter(raw.values())
    by_key: dict[str, str] = {}
    for key, label in raw.items():
        typ = str((keymap.get(key) or {}).get("type") or "")
        label = label if label and label != typ else (_TYPE_DEFAULT.get(typ) or bi._type_ru(typ))
        num = "".join(ch for ch in key if ch.isdigit())
        if num and counts.get(raw[key], 0) > 1:
            label = f"{label} {num}"
        by_key[key] = label

    by_type: dict[str, str] = {}
    types = Counter(str((v or {}).get("type") or "") for v in keymap.values())
    for key, v in keymap.items():
        typ = str((v or {}).get("type") or "")
        if typ and types[typ] == 1:
            by_type[typ] = by_key[key]
    for typ, label in _TYPE_DEFAULT.items():
        by_type.setdefault(typ, label)

    labels: dict[str, str] = {}
    labels.update(_project_status_words())
    labels.update(_STATUS_WORDS)
    for code, typ in STEP_CODE_TO_NODE_TYPE.items():
        name = by_type.get(typ) or _STEP_EXTRA.get(code) or bi._STEP_RU.get(code, "").capitalize()
        if typ.startswith("enrich_") and typ not in by_type:
            name = _STEP_EXTRA.get(code, name)
        if name:
            labels[code] = f"«{name}»"
    for typ, label in by_type.items():
        labels[typ] = f"«{label}»"
    for key, label in by_key.items():
        labels[key] = f"«{label}»"
    return labels


def _same_thing(a: str, b: str) -> bool:
    """«Картинки» и «Генерация картинок» — одно и то же (общая основа слова)."""

    def stems(s: str) -> set[str]:
        return {w[:5] for w in re.findall(r"\w+", s.lower()) if len(w) >= 5}

    a_s, b_s = stems(a), stems(b)
    return a.strip().lower() == b.strip().lower() or bool(a_s & b_s)


def _replace_token(tok: str, labels: dict[str, str]) -> str | None:
    t = tok.strip()
    if t in labels:
        return labels[t]
    low = t.lower()
    if low in labels:
        return labels[low]
    # Ключа нет на канвасе (старый проект, текст ошибки): «n_excel_gpt_1» → «Работа с GPT 1».
    m = _NODE_KEY_RE.match(low)
    if m:
        name = _TYPE_DEFAULT.get(m.group(1)) or (
            bi._type_ru(m.group(1)) if m.group(1).startswith("hitl_") else None
        )
        if name:
            return f"«{name} {m.group(2)}»" if m.group(2) else f"«{name}»"
    return None


_NODE_KEY_RE = re.compile(r"^n_([a-z_]+?)(?:_(\d+))?$")


def humanize_reply(
    text: str, canvas_keymap: dict[str, dict] | None = None, *, keep_code: bool = False
) -> str:
    """Текст ответа для человека: ключи → названия нод, без технических блоков кода.

    ``keep_code`` — режим «почини код»: блоки кода и пути оставляем как есть.
    """
    if not text:
        return text
    labels = build_labels(canvas_keymap)

    def fence(m: re.Match) -> str:
        lang = (m.group(1) or "").lower()
        body = m.group(2) or ""
        if keep_code:
            return m.group(0)
        if lang in _CODE_LANGS or body.lstrip().startswith(("{", "[", "def ", "class ", "import ", "from ")):
            return ""
        return body.strip()

    out = _FENCE_RE.sub(fence, text)

    def inline(m: re.Match) -> str:
        tok = m.group(1)
        rep = _replace_token(tok, labels)
        if rep is not None:
            return rep
        return m.group(0) if keep_code else tok

    out = _INLINE_RE.sub(inline, out)

    def bare(m: re.Match) -> str:
        rep = _replace_token(m.group(1), labels)
        return rep if rep is not None else m.group(0)

    out = _BARE_RE.sub(bare, out)
    if not keep_code:
        for rx, word in _WORDS:
            out = rx.sub(word, out)

    def dup(m: re.Match) -> str:
        return f"«{m.group(1)}»" if _same_thing(m.group(1), m.group(2)) else m.group(0)

    out = _DUP_PAREN_RE.sub(dup, out)
    out = re.sub(r"««([^«»]+)»»", r"«\1»", out)
    out = re.sub(r"[ \t]+([,.;:])", r"\1", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


_JOINS_PREV = re.compile(r"\s*(?:\(|harness)", re.IGNORECASE)


class StreamHumanizer:
    """То же для потока: копит хвост до границы слова и закрытой кавычки/блока.

    После начала технического JSON в хвосте ответа текст отдаётся как есть —
    его вырезает фронт и итоговый ответ.
    """

    _HOLD_MAX = 4000

    def __init__(self, canvas_keymap: dict[str, dict] | None = None, *, keep_code: bool = False) -> None:
        self._keymap = canvas_keymap
        self._keep = keep_code
        self._buf = ""
        self._raw = False

    def _json_start(self, s: str) -> int:
        idx = [i for i in (s.find('{"actions"'), s.find('{"ops"'), s.find("```json")) if i >= 0]
        return min(idx) if idx else -1

    def feed(self, delta: str) -> str:
        if self._raw:
            return delta
        self._buf += delta
        j = self._json_start(self._buf)
        if j >= 0:
            head, tail = self._buf[:j], self._buf[j:]
            self._buf = ""
            self._raw = True
            return self._human(head) + tail
        # Два последних слова держим, и не режем перед словом, которое склеивается
        # с предыдущим («проверки harness», «Картинки» (`img`)).
        spaces = [i for i, ch in enumerate(self._buf) if ch in " \n"]
        cut = -1
        for i in reversed(spaces[:-2]):
            if not _JOINS_PREV.match(self._buf[i + 1 :]):
                cut = i
                break
        if cut < 0:
            return ""
        head = self._buf[: cut + 1]
        if (head.count("```") % 2 or (head.count("`") - 3 * head.count("```")) % 2) and len(
            self._buf
        ) < self._HOLD_MAX:
            return ""
        self._buf = self._buf[cut + 1 :]
        return self._human(head)

    def flush(self) -> str:
        rest, self._buf = self._buf, ""
        return rest if self._raw else self._human(rest)

    def _human(self, s: str) -> str:
        if not s:
            return s
        lead = s[: len(s) - len(s.lstrip())]
        trail = s[len(s.rstrip()) :]
        body = humanize_reply(s, self._keymap, keep_code=self._keep)
        return (lead + body + trail) if body else (lead or trail)


def step_title(code: str, canvas_keymap: dict[str, dict] | None = None) -> str:
    """Название шага так, как нода подписана на канвасе (без кавычек)."""
    return build_labels(canvas_keymap).get(code, code).strip("«»")
