"""img_pr: крупные батчи + одна GPT-сессия (мастер во вложении на каждый батч).

Промт картинки — как вернул GPT (без пайплайн-обёртки STYLE_HEAD/TAIL).
"""

from __future__ import annotations

import contextlib
import json
import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypeVar

from app.contracts.prompt_ops import MIN_IMAGE_PROMPT_CHARS
from app.services.db_apply import extract_apply_ops_json
from app.services.img_pr_budget import (
    OUTSEE_IMAGE_PROMPT_MAX,
    STYLE_COMPACT,
    STYLE_FULL,
)
from app.services.volume_batches import (
    MIN_CONTINUE_SIZE,
    plan_remainder_batches,
)

# Fallback only when caller omits size (xlsx uses plan_batch_size).
_FRAMES_PER_BATCH = 25
_T = TypeVar("_T")
_CHECKPOINT_NAME = "img_pr_checkpoint.json"
_GPT_ATTEMPTS = 3

_BATCH_FOOTER = """
# BATCH {batch_i}/{batch_n} — только эти {n} кадров из db_frames.json
Верни ТОЛЬКО валидный JSON (без markdown, без прозы):
{{"ops":[{{"frame_uuid":"<uuid>","fields":{{"промт_картинки":"…","персонажи":"c01"}}}}]}}

ЖЁСТКО:
- `персонажи` ТОЛЬКО внутри `fields`, не рядом с frame_uuid;
- в тексте промта НЕ используй символ ASCII двойной кавычки \"; пиши «ёлочки»;
- ровно одна закрывающая скобка на конец каждого op; не закрывай массив ops раньше времени;
- пиши только полные ops (сцена + STYLE + Negative); если не все uuid влезли —
  верни сколько полных влезло, остальные uuid не включай;
- пустой {{"ops":[]}} запрещён.

В `промт_картинки` пиши ПОЛНЫЙ промт: сцена на русском (Референс?/Фон/
Расстановка/Действие/Свет/Акцент/Смысл/План-ракурс/Эмоция/Детали/Место) +
стилевой замок из мастера. Оркестратор НИЧЕГО не дописывает.
{style_rule}
Тело ≤ {limit} символов — это потолок генератора, куда поедет кадр: длиннее
он режет хвост сам и молча. Режь сюжет, не расстановку и не замок.

=== РАССТАНОВКА (КРИТИЧНО, ПРОВЕРЯЕТСЯ) ===
- У кадра есть поле `continuity` — его значение уходит в промт ДОСЛОВНО,
  целиком, отдельной строкой сразу после `Фон:` и ДО `Действие:`.
- Копируй как есть, вместе со словом «Непрерывность.». Не перефразируй,
  не сокращай, не переставляй людей и предметы, не дописывай своих.
- Это посчитанная кодом геометрия кадра (ось действия, взгляды, владелец
  предмета), а не пожелание: композиция подчиняется ей, не наоборот.
- Нет `continuity` у кадра — строки нет, выдумывать её запрещено.

=== ФОТО ПРОТИВ СЛОВ (КРИТИЧНО, ПРОВЕРЯЕТСЯ) ===
- Генератор берёт ОДНУ фотографию персонажа на кадр (`ref_character`).
- `describe_appearance` пусто — внешность не описывай, фотография справится.
- `describe_appearance` непусто — опиши ВСЕХ перечисленных, включая того, у
  кого фотография есть: 100–150 символов на человека из карточки `characters`
  (возраст, телосложение, лицо, волосы, одежда). Пропустишь одного — он
  выйдет с лицом и полом соседа.
- Не влезает в лимит — режь детали и эмоцию, не внешность.

=== ФОН / ПЛАН / РЕФ / ТЕКСТ (КРИТИЧНО) ===
- Фон = подробно из shot01_bg (священно); план/ракурс = scene_feature + shot01_description.
- При укорачивании до {limit} НЕ выкидывай расстановку, фон, план/ракурс и STYLE LOCK.
- Один cXX = один референс = одно тело.
- Два+ cXX = столько же РАЗНЫХ лиц; запрещено копировать лицо с одного рефа на оба тела.
- «Клоны/близнецы» = два тела с одним лицом, не «в кадре два человека».
- Без читаемого текста, если надпись не задана во входе (никакой каши/латиницы).

=== ДЕЙСТВИЕ / АНТИ-TWIN (КРИТИЧНО) ===
- Action = mid-motion freeze (тело/рука/предмет В ДВИЖЕНИИ), не поза.
- БРАК: стоит/сидит/смотрит/замер/posed/standing looking без глагола движения.
- Один place на соседних кадрах = ЦЕПЬ фаз (не 4 одинаковых постера).
- Соседи отличаются ≥2 из: фаза действия, поза тела, состояние пропа, камера/accent.
- Визуальный twin (тот же силуэт+prop+дистанция) = брак — перепиши Action/камеру.

=== COVERAGE K2/K3 (КРИТИЧНО) ===
Если у кадра есть coverage_parent — это НЕ новая сцена, а следующий ракурс той же.
Первый абзац промта:
Image 1 is the previous coverage still of the SAME scene (layout / cast-count / prop-identity lock).
Preserve: [place, shot01_bg, lighting, персонажи — то же число тел, key props = тот же экземпляр].
Change: camera + действие ЭТОГО шота (shot01_description / shot01_action).
Запрещено: новая локация; второй человек, которого не было в master;
крупный план другой картины/документа (нужен punch-in той же).
Фон/мебель/стены/люди/предметы бери из coverage_parent.
""".strip()

_FOLLOWUP_MSG = """
Следующий батч. Те же правила. Полный промт: сцена + STYLE LOCK / Negative.
`continuity` кадра — дословной строкой после «Фон:», до «Действие:».
`describe_appearance` непусто — внешность ВСЕХ перечисленных обязательна.
Фон и план священны; 1 cXX = 1 тело; 2 cXX = 2 разных лица; mid-motion; анти-клон.
K2/K3 с coverage_parent: Preserve/Change, тот же сет/состав/предметы что у родителя, только камера.
Мастер-промт и db_frames.json во вложении — только кадры этого батча.
{footer}
""".strip()

_PLASTILIN_BATCH_FOOTER = """
# BATCH {batch_i}/{batch_n} — только эти {n} кадров из db_frames.json
Верни ТОЛЬКО валидный JSON (без markdown, без прозы):
{{"ops":[{{"frame_uuid":"<uuid>","fields":{{"промт_картинки":"…","персонажи":"c01"}}}}]}}

ЖЁСТКО:
- `персонажи` ТОЛЬКО внутри `fields`, не рядом с frame_uuid;
- в тексте промта НЕ используй символ ASCII двойной кавычки \"; пиши «ёлочки»;
- ровно одна закрывающая скобка на конец каждого op;
- пиши только полные ops; если не все uuid влезли — верни сколько полных
  влезло, остальные uuid не включай;
- пустой {{"ops":[]}} запрещён.

В `промт_картинки` пиши ПОЛНЫЙ промт: стиль пластилина ТРИ раза + сцена + Negative.
Пайплайн НЕ допишет watercolor/noir. Не копируй Archival Noir.
Тело ≤ {limit} символов — потолок генератора, длиннее он режет хвост сам.
Без текста на картинке. Либо люди, либо крупный план предмета без рук.
""".strip()

_PLASTILIN_FOLLOWUP_MSG = """
Следующий батч. Те же правила. Стиль пластилина оставь в промт_картинки (три раза).
Watercolor/noir не пиши. 1 cXX = 1 тело; без текста на картинке.
Мастер-промт и db_frames.json во вложении — только кадры этого батча.
{footer}
""".strip()


def _checkpoint_path(project_dir: Path) -> Path:
    return project_dir / "tmp_gpt" / _CHECKPOINT_NAME


def load_checkpoint(project_dir: Path, *, input_hash: str | None = None) -> dict[str, Any]:
    empty: dict[str, Any] = {"done_uuids": [], "ops": []}
    path = _checkpoint_path(project_dir)
    if not path.is_file():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return empty
    if not isinstance(data, dict):
        return empty
    done = data.get("done_uuids") or []
    ops = data.get("ops") or []
    # Этап 2 (C.2): чекпоинт валиден только для того же входа; legacy без
    # hash = mismatch — «протухший результат не подтягивается».
    if input_hash is not None and data.get("input_hash") != input_hash:
        if done or ops:
            from loguru import logger

            logger.info(
                "img_pr checkpoint invalidated: input changed (was={}, now={}, дропнуто done={} ops={})",
                str(data.get("input_hash"))[:24],
                input_hash[:24],
                len(done),
                len(ops),
            )
        return empty
    return {
        "done_uuids": [str(u) for u in done if str(u).strip()],
        "ops": [o for o in ops if isinstance(o, dict)],
    }


def save_checkpoint(
    project_dir: Path,
    *,
    done_uuids: list[str],
    ops: list[dict],
    input_hash: str | None = None,
) -> None:
    path = _checkpoint_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"done_uuids": done_uuids, "ops": ops}
    if input_hash is not None:
        payload["input_hash"] = input_hash
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_checkpoint(project_dir: Path) -> None:
    path = _checkpoint_path(project_dir)
    if path.is_file():
        with contextlib.suppress(OSError):
            path.unlink()


def batch_attach_files(
    *,
    batch_i: int,
    prompt_file: Path,
    db_path: Path,
    voiceover: Path | None = None,
) -> list[Path]:
    """Мастер-промт на каждый батч; voiceover — только на первом."""
    files = [prompt_file, db_path]
    if batch_i == 1 and voiceover is not None:
        files.append(voiceover)
    return files


def plan_batch_size(
    n_frames: int,
    *,
    target_batches: int = 3,
    min_size: int = MIN_CONTINUE_SIZE,
    max_size: int = 40,
) -> int:
    """Initial img_pr chunk size: aim for ``target_batches`` (110 → 37 → 3).

    ``n_frames <= min_size`` → one batch (return n, not a split below floor).
    ``max_size`` is a soft cap: exceeded when needed to keep ~target batches
    (199 → ~67 × 3, not 40 × 5).
    """
    n = int(n_frames)
    tb = max(1, int(target_batches))
    mn = max(1, int(min_size))
    mx = max(mn, int(max_size))
    if n <= 0:
        return mn
    if n <= mn:
        return n
    size = max(mn, math.ceil(n / tb))
    capped = min(size, mx)
    # Clamp to max_size only when that still yields <= target_batches.
    if math.ceil(n / capped) <= tb:
        return max(mn, capped)
    return size


def chunk_frames(frames: list[Any], *, size: int = _FRAMES_PER_BATCH) -> list[list[Any]]:
    if size < 1:
        size = _FRAMES_PER_BATCH
    return [frames[i : i + size] for i in range(0, len(frames), size)]


def repartition_remaining(
    remaining: Sequence[_T],
    delivered: int,
    *,
    min_size: int = MIN_CONTINUE_SIZE,
) -> list[list[_T]]:
    """Rechunk leftover frames after a partial batch; never continue by onesie."""
    return plan_remainder_batches(remaining, delivered=delivered, min_size=min_size)


_PROMPT_FIELD_KEYS = (
    "промт_картинки",
    "image_prompt",
    "промпт_картинки",
    "промт_картинки_2",
    "image_prompt_shot2",
)


def is_degenerate_prompt(text: Any) -> bool:
    """Ответ формально есть, а задания в нём нет: «...», «—», обрывок строки."""
    body = str(text or "").strip()
    if len(body) < MIN_IMAGE_PROMPT_CHARS:
        return True
    # Одни знаки препинания и пробелы — длина ничего не значит.
    return not re.search(r"[^\W\d_]", body, flags=re.UNICODE)


def filter_prompt_ops(ops: list[Any]) -> list[dict]:
    clean: list[dict] = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        if str(op.get("target") or "frame") not in {"frame", ""}:
            continue
        raw_fields = op.get("fields")
        if raw_fields is None:
            fields = {}
        elif isinstance(raw_fields, dict):
            fields = dict(raw_fields)
        else:
            continue
        # Модель часто пишет персонажи рядом с fields — заберём внутрь.
        for k in ("персонажи", "characters"):
            if k in op and k not in fields and op.get(k) is not None:
                fields[k] = op.get(k)
        if not any(k in fields for k in _PROMPT_FIELD_KEYS):
            # Иногда промт лежит на верхнем уровне op.
            for k in _PROMPT_FIELD_KEYS:
                if k in op and str(op.get(k) or "").strip():
                    fields[k] = op.get(k)
        if not any(k in fields for k in _PROMPT_FIELD_KEYS):
            continue
        # Вырожденный промт хуже отсутствующего: кадр считается готовым, шаг
        # зеленеет, а картинка рисуется по трём точкам. Выбрасываем op — кадр
        # остаётся пустым, и батч-цикл переспросит именно его.
        degenerate = {
            k: len(str(fields.get(k) or "").strip())
            for k in _PROMPT_FIELD_KEYS
            if k in fields and is_degenerate_prompt(fields.get(k))
        }
        for k in degenerate:
            fields.pop(k, None)
        if degenerate:
            from loguru import logger

            logger.warning(
                "img_pr: кадр {} — промт короче {} симв. ({}), поле отброшено",
                str(op.get("frame_uuid") or op.get("uuid") or "?"),
                MIN_IMAGE_PROMPT_CHARS,
                ", ".join(f"{k}={n}" for k, n in degenerate.items()),
            )
        if not any(k in fields for k in _PROMPT_FIELD_KEYS):
            continue
        out = {**op, "fields": fields}
        clean.append(out)
    return clean


def _read_json_string(text: str, start: int) -> tuple[str, int] | None:
    """Прочитать JSON-строку начиная с start (символ сразу после открывающей \")."""
    if start < 0 or start > len(text):
        return None
    i = start
    chars: list[str] = []
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            if i + 1 >= len(text):
                return None
            chars.append(text[i : i + 2])
            i += 2
            continue
        if ch == '"':
            # decode escapes via json
            raw = '"' + "".join(chars) + '"'
            try:
                return json.loads(raw), i + 1
            except Exception:  # noqa: BLE001
                return "".join(chars), i + 1
        chars.append(ch)
        i += 1
    return None


def salvage_img_pr_ops(reply: str) -> list[dict]:
    """Достать ops даже из битого JSON (лишние }, персонажи вне fields)."""
    if not reply:
        return []
    ops: list[dict] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'"frame_uuid"\s*:\s*"([a-fA-F0-9]+)"',
        reply,
    ):
        uuid = m.group(1).strip()
        if not uuid or uuid in seen:
            continue
        window = reply[m.end() : m.end() + 12000]
        pm = re.search(
            r'"промт_картинки"\s*:\s*"',
            window,
        ) or re.search(
            r'"image_prompt"\s*:\s*"',
            window,
        )
        if not pm:
            continue
        read = _read_json_string(window, pm.end())
        if not read:
            continue
        prompt, after = read
        if not str(prompt).strip():
            continue
        fields: dict[str, Any] = {"промт_картинки": prompt}
        rest = window[after : after + 200]
        ch = re.search(r'"персонажи"\s*:\s*"([^"]*)"', rest)
        if ch:
            fields["персонажи"] = ch.group(1)
        ops.append({"frame_uuid": uuid, "fields": fields})
        seen.add(uuid)
    return ops


def is_empty_ops_reply(reply: str) -> bool:
    """True если ответ — валидный JSON с пустым ops (заглушка/отказ)."""
    text = (reply or "").strip()
    if not text:
        return False
    data = extract_apply_ops_json(text)
    if isinstance(data, dict) and isinstance(data.get("ops"), list):
        if data["ops"]:
            return False
        return not filter_prompt_ops(salvage_img_pr_ops(text))
    compact = re.sub(r"\s+", "", text)
    return compact == '{"ops":[]}'


def parse_img_pr_ops(
    reply: str,
    *,
    wrap_style: bool = False,
    style_id: str | None = None,
    style_block: str | None = None,
) -> list[dict]:
    _ = wrap_style, style_id, style_block  # legacy kwargs, wrap отключён
    data = extract_apply_ops_json(reply or "")
    ops = list((data or {}).get("ops") or []) if isinstance(data, dict) else []
    clean = filter_prompt_ops(ops)
    salvaged = filter_prompt_ops(salvage_img_pr_ops(reply or ""))
    partial = bool(isinstance(data, dict) and data.get("_salvaged_partial"))
    # Битый/обрезанный JSON: extract взял мало ops — regex достаёт все uuid.
    if not clean or (partial or len(clean) <= 1) and len(salvaged) > len(clean):
        clean = salvaged
    return clean


_STYLE_RULE = {
    STYLE_FULL: "Замок — ПОЛНАЯ пара блоков мастера (STYLE + Final style lock / Negative).",
    STYLE_COMPACT: (
        "Замок — КОРОТКАЯ пара блоков мастера (раздел «короткий замок»): полная "
        "пара занимает ≈1065 знаков и при этом лимите не оставит места кадру. "
        "Копируй короткую дословно, не смешивай с полной."
    ),
}


def batch_footer(
    *,
    batch_i: int,
    batch_n: int,
    n: int,
    plastilin: bool = False,
    limit: int = OUTSEE_IMAGE_PROMPT_MAX,
    style_mode: str = STYLE_FULL,
) -> str:
    tmpl = _PLASTILIN_BATCH_FOOTER if plastilin else _BATCH_FOOTER
    return tmpl.format(
        batch_i=batch_i,
        batch_n=batch_n,
        n=n,
        limit=int(limit),
        style_rule=_STYLE_RULE.get(style_mode, _STYLE_RULE[STYLE_FULL]),
    )


def followup_message(
    *,
    batch_i: int,
    batch_n: int,
    n: int,
    plastilin: bool = False,
    limit: int = OUTSEE_IMAGE_PROMPT_MAX,
    style_mode: str = STYLE_FULL,
) -> str:
    tmpl = _PLASTILIN_FOLLOWUP_MSG if plastilin else _FOLLOWUP_MSG
    return tmpl.format(
        footer=batch_footer(
            batch_i=batch_i,
            batch_n=batch_n,
            n=n,
            plastilin=plastilin,
            limit=limit,
            style_mode=style_mode,
        )
    )


def write_rejected_reply(tmp_dir: Path, *, batch_i: int, attempt: int, reply: str, reason: str) -> Path:
    path = tmp_dir / f"img_pr_rejected_b{batch_i}_a{attempt}.txt"
    path.write_text(
        f"# reason: {reason}\n# reply_len: {len(reply or '')}\n\n{reply or ''}",
        encoding="utf-8",
    )
    return path


def uuid_of_op(op: dict) -> str:
    return str(op.get("frame_uuid") or op.get("uuid") or "").strip()
