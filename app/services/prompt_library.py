"""Файловая библиотека мастер-промтов по этапам пайплайна.

Структура на диске:
  prompts/
    01_plan/        → шаг 1 «План» (PLAN_SHORTS)
    02_script/      → шаг 2 «Закадровый текст» (SCRIPT_SHORTS)
    03_razbivka/    → шаг 3 «Разбивка на блоки» (RAZBIVKA_SLOV)
    04_hero/        → шаг 4 «Hero» (HERO_SHORTS)
    05_image_prompts/ → шаг 6 «Промты картинок» (IMAGE_SHORTS)
    07_animation/   → шаг 7 «Промты анимации» (VIDEO_SHORTS)

В каждой папке лежит `default.md` (дефолтный мастер-промт) + любые
дополнительные `<имя>.md` файлы — это варианты, между которыми проект
может переключаться. Имя файла без расширения = имя варианта.

В `Project.prompt_overrides` (JSON) сохраняется выбор юзера:
  {"plan": "horror_v2", "script": "default", ...}

Если override не указан или файл по нему не найден — берётся `default.md`.
Если и `default.md` нет — RuntimeError.

Также модуль безопасно валидирует имена вариантов (no path traversal,
ASCII + цифры + `_-`), чтобы юзер из TG не мог записать файл вне папки.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

# Корень папки `prompts/` — два уровня вверх от текущего файла:
# app/services/prompt_library.py  →  ../../prompts/
PROMPTS_ROOT = Path(__file__).resolve().parent.parent.parent / "prompts"

# Карта step_code (как в menu.py StepDef.code) → имя папки в `prompts/`.
# Шаги, у которых нет мастер-промта, тут не перечисляются.
# Ключи совпадают с `StepDef.code` в `app/orchestrator/pipeline_steps.py`.
STEP_FOLDERS: dict[str, str] = {
    "plan": "01_plan",
    # 1a. Режим героя: тема → hero | no_hero, один вызов до плана. Папки на
    # диске может не быть — встроенный промт в `app/services/hero_decision.py`,
    # файл его лишь замещает.
    "hero_decision": "01a_hero_decision",
    "script": "02_script",
    "split": "03_razbivka",
    "hero": "04_hero",
    # `hero_style` — НЕ отдельная кнопка в меню; это вспомогательная
    # библиотека стилей для шага «4. Hero». Бот сам показывает picker
    # перед запуском Hero-генерации, выбор сохраняется в
    # project.prompt_overrides["hero_style"]. Используем общую
    # инфраструктуру библиотеки промтов (prompt_picker, on_prompt_picker_cb).
    "hero_style": "04_hero_style",
    # 4b. «Предметы» — генерация реф-картинок предметов.
    "items": "04b_items",
    # 4c. «Разбор состава» — кого и что придётся нарисовать. Выводится из
    # плана и закадрового текста, когда описаний не дал никто: у владельца их
    # заполняет лист «Персонажи», а при учётных записях книги нет и заполнить
    # некому. Папка на диске может отсутствовать — у шага есть встроенный
    # промт (`app/services/cast_extract.py`), файл его лишь замещает.
    "cast": "04c_cast",
    # Слоты «Доработка данных» (xlsx round-trip с ChatGPT) — каждый
    # слот имеет свою папку, чтобы юзер мог хранить разные промты.
    "enrich_1": "05a_enrich_1",
    "enrich_2": "05b_enrich_2",
    "enrich_3": "05c_enrich_3",
    "enrich_4": "05d_enrich_4",
    "enrich_5": "05e_enrich_5",
    "excel_gpt": "05_excel_gpt",
    # Папки оставлены с историческими номерами (05/07), чтобы не ломать
    # уже существующие промты в `prompts/`. Меню-нумерация шагов
    # переехала, но имя папки на диске не зависит от позиции в меню.
    "img_pr": "05_image_prompts",
    "anim_pr": "07_animation",
    "music": "10_music",
    # Мульти-агентный дизайн сцен: все агенты и сборщик живут в одной
    # папке prompts/scene_design/ (characters.md, world.md, ..., assemble.md).
    "scene_d": "scene_design",
    "scene_asm": "scene_design",
}

# Человеческое имя шага (для текстовых сообщений в TG).
STEP_HUMAN_NAMES: dict[str, str] = {
    "plan": "1. Сценарий",
    "hero_decision": "1a. Режим героя (hero / no_hero)",
    "script": "2. Закадровый текст",
    "split": "3. Разбивка на блоки",
    "hero": "4. Персонажи (Объекты)",
    "hero_style": "4. Hero — стиль персонажа",
    "items": "4. Предметы (Объекты)",
    "cast": "4. Разбор состава (кого рисовать)",
    # Все слоты — суб-шаги одного wrapper-шага «5. Доработка данных»,
    # поэтому в названии номер шага не указываем (он зависит от
    # n_slots, и для UX-промтов важен номер слота, а не позиция в меню).
    "enrich_1": "Доработка данных #1",
    "enrich_2": "Доработка данных #2",
    "enrich_3": "Доработка данных #3",
    "enrich_4": "Доработка данных #4",
    "enrich_5": "Доработка данных #5",
    "excel_gpt": "Доработка данных",
    "img_pr": "6. Промты картинок",
    "anim_pr": "8. Промты анимации",
    "music": "10. Музыка",
    "audio": "Озвучка",
    "scene_d": "3.5. Сцены — агенты",
    "scene_asm": "3.6. Сцены — сборка",
}

# Шаги без мастер-промта — для красоты в списках и проверок.
STEPS_WITHOUT_PROMPT: set[str] = {"img", "video", "audio", "assemble"}

DEFAULT_NAME = "default"
_FILE_META = ".file_meta.json"

# Слоты enrich — только .md из prompts/05*_enrich_*; не blocks v2 compose.
ENRICH_STEP_CODES: frozenset[str] = frozenset({*(f"enrich_{i}" for i in range(1, 6)), "excel_gpt"})

EXCEL_GPT_UNIFIED_STEP = "excel_gpt"


def is_excel_gpt_prompt_step(step_code: str) -> bool:
    return step_code in ENRICH_STEP_CODES


def excel_gpt_source_steps() -> tuple[str, ...]:
    return (EXCEL_GPT_UNIFIED_STEP, *(f"enrich_{i}" for i in range(1, 6)))


def excel_gpt_template_dir() -> Path:
    """Git SoT промтов excel_gpt (prompts/05_excel_gpt часто gitignored)."""
    from app.project_root import find_project_root

    return find_project_root() / "templates" / "excel_gpt_agents"


# Промты группы — только templates/node_groups/script_frames_qc/,
# и только когда группа применена (group_id / нода группы).
# Промты агентов scene_design, на которые смотрят ноды группы:
# каркас (fw_script) и площадка (fw_action). Файл один — в prompts/scene_design/,
# копию в папку группы не кладём (правка в Studio пишет туда же).
SCRIPT_FRAMES_QC_SHARED_PROMPTS: dict[str, str] = {
    "scene_boundaries_agent": "prompts/scene_design/scene_boundaries_agent.md",
    "scene_skeleton_agent": "prompts/scene_design/scene_skeleton_agent.md",
    "action": "prompts/scene_design/action.md",
}

SCRIPT_FRAMES_QC_PROMPT_NAMES: frozenset[str] = frozenset(
    {
        "script_writer_ru",
        "main_action_from_bits_ru",
        "scenes_to_frames_ru",
        "shots_qc_ru",
        "frame_prompts_continuity_ru",
        "prompts_qc_continuity_ru",
        *SCRIPT_FRAMES_QC_SHARED_PROMPTS,
    }
)


def node_group_prompts_dir(group_id: str) -> Path:
    from app.project_root import find_project_root

    return find_project_root() / "templates" / "node_groups" / group_id


def _script_frames_qc_style_path(name: str | None) -> Path | None:
    """Стиль ноды «Стиль» (fw_style): templates/node_groups/script_frames_qc/styles/style_*.md."""
    from app.services.frame_style import style_path

    return style_path(name)


def script_frames_qc_style_names() -> list[str]:
    from app.services.frame_style import list_style_names

    return list_style_names()


def is_script_frames_qc_prompt(name: str) -> bool:
    clean = (name or "").strip()
    if clean.lower().endswith(".md"):
        clean = clean[:-3].rstrip()
    if _script_frames_qc_style_path(clean) is not None:
        return True
    return clean in SCRIPT_FRAMES_QC_PROMPT_NAMES


def script_frames_qc_group_id(group_id: str | None) -> str | None:
    if not group_id:
        return None
    base = str(group_id).split("#", 1)[0].strip()
    return base if base == "script_frames_qc" else None


# Ноды группы на канвасе: раннер обязан брать
# templates/node_groups/script_frames_qc/, не excel_gpt_agents (main v6).
_SCRIPT_FRAMES_QC_NODE_SUFFIXES = (
    "_fw_script",
    "_fw_check_script",
    "_fw_action",
    "_fw_shots",
    "_fw_frames",
    "_fw_qc",
    "_fw_style",
    "_fw_report",
)


def _group_id_from_canvas_node(project: Any, node_key: str | None) -> str | None:
    if not node_key:
        return None
    meta = getattr(project, "meta", None) if project is not None else None
    if not isinstance(meta, dict):
        return None
    graph = meta.get("canvas_graph") or {}
    if not isinstance(graph, dict):
        return None
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        if str(node.get("id") or "") != str(node_key):
            continue
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        raw = str((data or {}).get("groupId") or "").strip()
        return raw.split("#", 1)[0] or None
    return None


def uses_script_frames_qc_prompts(
    *,
    group_id: str | None = None,
    project: Any = None,
    node_key: str | None = None,
) -> bool:
    """Промты группы — только если группа на канвасе / это нода группы.

    Иначе ``scenes_to_frames_ru`` и соседние имена резолвятся в main
    ``templates/excel_gpt_agents`` (v6 «одна сцена = один кадр»).
    """
    if script_frames_qc_group_id(group_id) is not None:
        return True
    if script_frames_qc_group_id(_group_id_from_canvas_node(project, node_key)):
        return True
    nk = str(node_key or "")
    if any(nk.endswith(s) for s in _SCRIPT_FRAMES_QC_NODE_SUFFIXES):
        return True
    if project is not None:
        from app.services.node_groups import canvas_has_script_frames_qc

        return canvas_has_script_frames_qc(project)
    return False


def list_group_owned_prompts(group_id: str | None) -> list[str] | None:
    """Промты группы. None — общий список «Работа с GPT»."""
    if script_frames_qc_group_id(group_id) is None:
        return None
    names = {n for n in SCRIPT_FRAMES_QC_PROMPT_NAMES if resolve_script_frames_qc_prompt_path(n).is_file()}
    names.update(script_frames_qc_style_names())
    return sorted(names)


def script_frames_qc_prompt_relpath(name: str) -> str:
    """Путь промта группы от корня репо (для UI / библиотеки)."""
    clean = (name or "").strip()
    if clean.lower().endswith(".md"):
        clean = clean[:-3].rstrip()
    shared = SCRIPT_FRAMES_QC_SHARED_PROMPTS.get(clean)
    if shared:
        return shared
    if _script_frames_qc_style_path(clean) is not None:
        return f"templates/node_groups/script_frames_qc/styles/{clean}.md"
    return f"templates/node_groups/script_frames_qc/{clean}.md"


def resolve_script_frames_qc_prompt_path(name: str) -> Path:
    """Промт группы: templates/node_groups/script_frames_qc/.

    Каркас и площадка — общие промты scene_design
    (``SCRIPT_FRAMES_QC_SHARED_PROMPTS``).
    """
    from app.project_root import find_project_root

    clean = (name or "").strip()
    if clean.lower().endswith(".md"):
        clean = clean[:-3].rstrip()
    shared = SCRIPT_FRAMES_QC_SHARED_PROMPTS.get(clean)
    if shared:
        return find_project_root() / shared
    style = _script_frames_qc_style_path(clean)
    if style is not None:
        return style
    return node_group_prompts_dir("script_frames_qc") / f"{clean}.md"


def _group_prompt_path(name: str) -> Path | None:
    clean = name.strip()
    style = _script_frames_qc_style_path(clean)
    if style is not None:
        return style
    if clean in SCRIPT_FRAMES_QC_PROMPT_NAMES:
        p = resolve_script_frames_qc_prompt_path(clean)
        if p.is_file():
            return p
    return None


def excel_gpt_prompt_exists(name: str) -> bool:
    clean = _clean_variant_name(name) if name else ""
    if not clean:
        return False
    if _group_prompt_path(clean) is not None:
        return True
    for code in excel_gpt_source_steps():
        try:
            if prompt_path(code, clean).exists():
                return True
        except ValueError:
            continue
    return (excel_gpt_template_dir() / f"{clean}.md").is_file()


# Локальная копия 05_excel_gpt иногда остаётся v1 (пишет закадр) или
# «1 сцена = 1 кадр». Git-шаблон — SoT для цепочки script_frames_qc.
_STALE_EXCEL_GPT_MARKERS: dict[str, tuple[str, ...]] = {
    "script_writer_ru": ("Агент: сценарист закадра",),
    "scenes_to_frames_ru": (
        "Не плоди покрытие",
        "одна сцена, одно действие",
    ),
    "frame_prompts_continuity_ru": (
        '"frame_uuid": "u002"',
        "дубль первого",
        "промт_картинки_2` / `промты_детей",
    ),
}


def _excel_gpt_local_is_stale(name: str, path: Path) -> bool:
    markers = _STALE_EXCEL_GPT_MARKERS.get(name)
    if not markers or not path.is_file():
        return False
    try:
        head = path.read_text(encoding="utf-8")[:1200]
    except OSError:
        return False
    return any(m in head for m in markers)


def resolve_excel_gpt_prompt_path(
    name: str,
    *,
    group_id: str | None = None,
    project: Any = None,
    node_key: str | None = None,
) -> Path:
    """Читать из 05_excel_gpt, legacy enrich_* или templates/excel_gpt_agents.

    Промты группы script_frames_qc — если группа на канвасе, нода ``_fw_*``,
    или явно передан ``group_id``. Раннер должен передать project/node_key:
    без них те же имена молча берутся из main (v6).
    """
    clean = _sanitize_name(name) if not is_valid_prompt_name(name) else name
    if not clean:
        raise ValueError(f"некорректное имя промта: {name!r}")
    # Стили ноды «Стиль» (style_*.md) — только в папке группы, не в 05_excel_gpt.
    style = _script_frames_qc_style_path(clean)
    if style is not None:
        return style
    if (
        uses_script_frames_qc_prompts(group_id=group_id, project=project, node_key=node_key)
        and clean in SCRIPT_FRAMES_QC_PROMPT_NAMES
    ):
        group = _group_prompt_path(clean)
        if group is not None:
            return group
        return resolve_script_frames_qc_prompt_path(clean)
    primary = step_dir(EXCEL_GPT_UNIFIED_STEP) / f"{clean}.md"
    tmpl = excel_gpt_template_dir() / f"{clean}.md"
    if primary.is_file() and not (tmpl.is_file() and _excel_gpt_local_is_stale(clean, primary)):
        return primary
    if tmpl.is_file() and _excel_gpt_local_is_stale(clean, primary):
        logger.warning(
            "excel_gpt {}: локальный {} устарел, берём git-шаблон {}",
            clean,
            primary,
            tmpl,
        )
        return tmpl
    for code in (f"enrich_{i}" for i in range(1, 6)):
        legacy = step_dir(code) / f"{clean}.md"
        if legacy.is_file():
            return legacy
    if tmpl.is_file():
        return tmpl
    return primary


# Макс. длина имени варианта на диске (UTF-8 байты). Раньше было 40 из‑за TG callback_data;
# в веб-студии нужны длинные осмысленные имена файлов.
MAX_PROMPT_NAME_BYTES = 255

# Для inline-кнопок Telegram (callback_data ≤ 64 байта с префиксом).
TG_CALLBACK_PROMPT_NAME_BYTES = 40

# Запрещённые символы в имени файла (path traversal / fs-unsafe).
_UNSAFE_CHARS_RE = re.compile(r'[/\\:\*\?"<>|\x00]')


def _truncate_utf8(name: str, max_bytes: int) -> str:
    encoded = name.encode("utf-8")
    if len(encoded) <= max_bytes:
        return name
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _sanitize_name(raw: str, *, max_bytes: int = MAX_PROMPT_NAME_BYTES) -> str:
    """Убирает из строки символы, опасные для файловой системы.
    Пробелы, кириллица, цифры, `_`, `-` — остаются."""
    name = _UNSAFE_CHARS_RE.sub("_", raw).strip().strip(".")
    name = re.sub(r"_{2,}", "_", name)
    if not name:
        return ""
    return _truncate_utf8(name, max_bytes)


def step_folder_name(step_code: str) -> str | None:
    """Имя папки в `prompts/` для данного шага (или None если без промта)."""
    return STEP_FOLDERS.get(step_code)


def prompts_writable() -> bool:
    """Можно ли писать в `prompts/` на диске.

    На сервере каталог смонтирован `:ro` (deploy/studio/docker-compose.yml):
    библиотека там только семя для базы, а правится она в базе. Любой код,
    который хочет писать на диск, обязан спросить здесь — иначе `OSError:
    Read-only file system` из глубины роутера, а снаружи 500 без объяснения.
    """
    try:
        if PROMPTS_ROOT.is_dir():
            return os.access(PROMPTS_ROOT, os.W_OK)
        PROMPTS_ROOT.mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False


def step_dir(step_code: str) -> Path:
    """Абсолютный путь к папке промтов для шага.

    Создаёт её, если может. Не может — возвращает путь всё равно: на диске
    только для чтения папки нового шага (например `04c_cast`) нет и не будет,
    а промт при этом лежит в базе и читается оттуда. Падать здесь значило бы
    ронять список промтов целиком из-за одного шага без файлов.
    """
    folder = STEP_FOLDERS.get(step_code)
    if folder is None:
        raise ValueError(f"step_code {step_code!r} не имеет мастер-промта")
    path = PROMPTS_ROOT / folder
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def _file_meta_path(step_code: str) -> Path:
    return step_dir(step_code) / _FILE_META


def load_file_meta(step_code: str) -> dict[str, Any]:
    path = _file_meta_path(step_code)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_file_meta(step_code: str, data: dict[str, Any]) -> None:
    # Мета — только про диск: дата сохранения файла. На диске только для
    # чтения её негде хранить, и это не ошибка: источник правды там база.
    path = _file_meta_path(step_code)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def touch_prompt_meta(step_code: str, name: str, size: int) -> float:
    """Записать стабильную дату сохранения (не mtime файла)."""
    saved_at = datetime.now(UTC).timestamp()
    touch_prompt_meta_at(step_code, name, saved_at, size)
    return saved_at


def touch_prompt_meta_at(step_code: str, name: str, saved_at: float, size: int) -> None:
    meta = load_file_meta(step_code)
    prev = raw_prev if isinstance(raw_prev := meta.get(name), dict) else {}
    meta[name] = {**prev, "saved_at": saved_at, "size": size}  # keep title/description
    _save_file_meta(step_code, meta)


def get_prompt_saved_at(step_code: str, name: str) -> float | None:
    entry = load_file_meta(step_code).get(name)
    if isinstance(entry, dict) and entry.get("saved_at") is not None:
        return float(entry["saved_at"])
    return None


def rename_prompt_meta(step_code: str, old_name: str, new_name: str) -> None:
    meta = load_file_meta(step_code)
    if old_name in meta:
        meta[new_name] = meta.pop(old_name)
        _save_file_meta(step_code, meta)


def remove_prompt_meta(step_code: str, name: str) -> None:
    meta = load_file_meta(step_code)
    if name in meta:
        meta.pop(name, None)
        _save_file_meta(step_code, meta)


def is_valid_prompt_name(name: str, *, max_bytes: int = MAX_PROMPT_NAME_BYTES) -> bool:
    """Имя варианта: любые символы кроме path-traversal.
    Пробелы, кириллица, спецсимволы — допустимы."""
    if not name or not name.strip():
        return False
    if len(name.encode("utf-8")) > max_bytes:
        return False
    if ".." in name:
        return False
    return not _UNSAFE_CHARS_RE.search(name)


def prompt_name_fits_telegram_callback(name: str) -> bool:
    """Имя влезает в callback_data inline-кнопки Telegram."""
    return is_valid_prompt_name(name, max_bytes=TG_CALLBACK_PROMPT_NAME_BYTES)


def list_prompts(step_code: str) -> list[str]:
    """Список доступных вариантов (имена файлов без `.md`), отсортированный.
    `default` всегда идёт первым (если присутствует)."""
    if is_excel_gpt_prompt_step(step_code):
        return list_excel_gpt_prompts()
    return _list_prompts_in_dir(step_code)


def list_excel_gpt_prompts() -> list[str]:
    """Список вариантов промтов для «ИИ-редактор сцен»: 05_excel_gpt (+ legacy enrich_* при наличии).

    Шаблоны templates/excel_gpt_agents не подмешиваются в UI-список, чтобы
    не засорять выбор устаревшим веером агентов (resolve_excel_gpt_prompt_path
    по-прежнему находит их для обратной совместимости).

    Имена группы script_frames_qc сюда не входят — их список только по group_id.
    Стили ноды «Стиль» (группа script_frames_qc) видны в пикере Studio:
    фронт не шлёт group_id, файлы только в templates/node_groups/.../styles/.
    """
    names = _list_prompts_in_dir(EXCEL_GPT_UNIFIED_STEP)
    seen = set(names)
    for code in excel_gpt_source_steps()[1:]:
        for extra in _list_prompts_in_dir(code):
            if extra not in seen:
                names.append(extra)
                seen.add(extra)
    names = [n for n in names if n not in SCRIPT_FRAMES_QC_PROMPT_NAMES]
    for style in script_frames_qc_style_names():
        if style not in seen:
            names.append(style)
            seen.add(style)
    if DEFAULT_NAME in names:
        names.remove(DEFAULT_NAME)
        names.insert(0, DEFAULT_NAME)
    return names


def _list_prompts_in_dir(step_code: str) -> list[str]:
    d = step_dir(step_code)
    if not d.is_dir():
        return []
    names = sorted(p.stem for p in d.glob("*.md"))
    if DEFAULT_NAME in names:
        names.remove(DEFAULT_NAME)
        names.insert(0, DEFAULT_NAME)
    return names


def prompt_path(step_code: str, name: str) -> Path:
    """Путь к файлу `<step_dir>/<name>.md`. Не проверяет существование."""
    clean = _sanitize_name(name) if not is_valid_prompt_name(name) else name
    if not clean:
        raise ValueError(f"некорректное имя промта: {name!r}")
    return step_dir(step_code) / f"{clean}.md"


def read_prompt(
    step_code: str,
    name: str,
    *,
    group_id: str | None = None,
    project: Any = None,
    node_key: str | None = None,
) -> str:
    """Текст мастер-промта. Сначала база, потом файл на диске.

    Порядок именно такой, потому что в SaaS диска у клиента нет, а промт —
    то, что он приходит править (`docs/SAAS-PIVOT.md` §9.4). База даёт
    переопределение на арендатора и проект; файл остаётся источником правды
    режима владельца и наполняет системный уровень при первом запуске.

    Пока библиотека в базу не загружена, `resolve` возвращает `None`, и всё
    работает ровно как работало.

    ``group_id``/``project``/``node_key`` — для промтов группы script_frames_qc
    (см. ``resolve_excel_gpt_prompt_path``).
    """
    from app.services import prompt_store

    from_db = prompt_store.resolve(step_code, name)
    if from_db is not None:
        return from_db
    clean = _clean_variant_name(name) if name else ""
    if clean and clean != name:
        from_db = prompt_store.resolve(step_code, clean)
        if from_db is not None:
            return from_db
    alt_name = f"{clean}.md" if clean else ""
    if alt_name and alt_name != name:
        from_db = prompt_store.resolve(step_code, alt_name)
        if from_db is not None:
            return from_db
    if is_excel_gpt_prompt_step(step_code):
        p = resolve_excel_gpt_prompt_path(clean or name, group_id=group_id, project=project, node_key=node_key)
        if not p.is_file():
            raise FileNotFoundError(f"prompt file not found: {p}")
        return p.read_text(encoding="utf-8")
    p = prompt_path(step_code, clean or name)
    if not p.exists():
        if name == DEFAULT_NAME:
            available = _list_prompts_in_dir(step_code)
            if available:
                fallback_p = prompt_path(step_code, available[0])
                if fallback_p.is_file():
                    logger.warning(
                        "read_prompt: '{}' not found in {}, falling back to '{}'",
                        name,
                        step_dir(step_code),
                        available[0],
                    )
                    return fallback_p.read_text(encoding="utf-8")
        raise FileNotFoundError(f"prompt file not found: {p}")
    return p.read_text(encoding="utf-8")


def write_prompt(step_code: str, name: str, content: str) -> Path:
    if is_excel_gpt_prompt_step(step_code):
        clean = _clean_variant_name(name) if name else ""
        style = _script_frames_qc_style_path(clean)
        if style is not None:
            style.write_text(content, encoding="utf-8")
            return style
        if clean in SCRIPT_FRAMES_QC_PROMPT_NAMES:
            p = resolve_script_frames_qc_prompt_path(clean)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return p
        step_code = EXCEL_GPT_UNIFIED_STEP
    p = prompt_path(step_code, name)
    p.write_text(content, encoding="utf-8")
    touch_prompt_meta(step_code, name, len(content.encode("utf-8")))
    return p


def delete_prompt(step_code: str, name: str) -> bool:
    """Удалить файл варианта. `default` удалять нельзя.
    Возвращает True если файл был удалён."""
    if name == DEFAULT_NAME:
        raise ValueError("default удалять нельзя")
    if is_excel_gpt_prompt_step(step_code):
        p = resolve_excel_gpt_prompt_path(name)
        if not p.is_file():
            return False
        p.unlink()
        return True
    p = prompt_path(step_code, name)
    if not p.exists():
        return False
    p.unlink()
    return True


def _clean_variant_name(raw: str) -> str:
    """Имя .md без расширения, безопасное для `prompt_path`.

    Снимает только хвостовой суффикс ``.md`` (регистр неважен):
    ``my_plan.md`` → ``my_plan``, ``steven.txt.md`` → ``steven.txt``.
    Иначе ``prompt_path`` дописывает ещё один ``.md`` → файл не находится
    и резолвер молча падает в global/default (баг дочерних проектов).
    """
    if not raw or not str(raw).strip():
        return ""
    name = str(raw).strip()
    if name.lower().endswith(".md"):
        name = name[:-3].rstrip()
    if not name:
        return ""
    if not is_valid_prompt_name(name):
        name = _sanitize_name(name)
    return name if name else ""


# Какой slot_id в Node Studio соответствует step_code.
# hero_style живёт в слоте `style`, не в `main` (там sheet/агент).
_STEP_PREFERRED_SLOT: dict[str, str] = {
    "hero_style": "style",
}
# Если в 04_hero нет default.md / туда скопировали агент реестра.
_HERO_SHEET_FALLBACKS = (
    "character_model_sheet_16x9",
    "агент_лист_персонажа_со_всех_сторон.txt",
)


def _node_key_belongs_to_step(node_key: str, step_code: str) -> bool:
    """Слот ноды excel_gpt не должен становиться промтом hero/img_pr/…"""
    key = str(node_key or "").strip().lower()
    if not key:
        return False
    sc = str(step_code or "").strip().lower()
    if is_excel_gpt_prompt_step(step_code):
        if "excel_gpt" in key:
            return True
        # enrich_1…5 на канвасе: n_enrich_1, не n_excel_gpt_*.
    if sc == "hero_style":
        return key == "n_hero" or key.startswith("n_hero_")
    prefixes = [f"n_{sc}", sc]
    if sc == "img_pr":
        prefixes.extend(["n_image_prompts", "image_prompts"])
    elif sc == "anim_pr":
        prefixes.extend(["n_animation_prompts", "animation_prompts"])
    elif sc == "img":
        prefixes.extend(["n_images", "images"])
    elif sc == "video":
        prefixes.extend(["n_videos", "videos"])
    try:
        from app.orchestrator.node_registry import WORK_NODES

        for spec in WORK_NODES.values():
            if spec.step_code.lower() == sc or spec.node_type.lower() == sc:
                prefixes.append(f"n_{spec.node_type}")
                prefixes.append(spec.node_type)
    except Exception:
        pass
    for prefix in prefixes:
        pl = str(prefix).lower()
        if key == pl or key.startswith(pl + "_"):
            return True
    return False


def node_prompt_variants(meta: dict | None) -> dict[str, str]:
    """node_key → назначенный ноде вариант промта (слот `main`, иначе любой).

    Читает инспектор узла: он показывает, какой файл берёт именно этот узел,
    и пишет выбор туда же. Форма хранения — общая с Node Studio и группами
    узлов (`app/services/node_groups.py`), поэтому вариант, проставленный
    вставкой веера агентов, виден в интерфейсе как выбранный.
    """
    if not isinstance(meta, dict):
        return {}
    from app.services.node_config import all_prompt_slots

    out: dict[str, str] = {}
    for node_key, slots in all_prompt_slots(meta).items():
        name = ""
        for slot_id in ("main", "gpt", "prompt"):
            name = _clean_variant_name(str(slots.get(slot_id) or ""))
            if name:
                break
        if not name:
            for raw in slots.values():
                name = _clean_variant_name(str(raw or ""))
                if name:
                    break
        if name:
            out[str(node_key)] = name
    return out


def _canvas_node_steps(meta: dict, step_code: str) -> dict[str, str]:
    """node_key → код шага по `meta.canvas_graph` (типы из реестра нод).

    Пусто, если у самого `step_code` нет ноды в реестре: у `hero_style` её
    нет, промт живёт на узле `hero`, и отсекать такой слот по несовпадению
    кодов значило бы выключить его вовсе.
    """
    cg = meta.get("canvas_graph")
    nodes = cg.get("nodes") if isinstance(cg, dict) else None
    if not isinstance(nodes, list):
        return {}
    from app.orchestrator.node_registry import NODE_TYPE_TO_STEP_CODE

    if step_code not in set(NODE_TYPE_TO_STEP_CODE.values()):
        return {}
    out: dict[str, str] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "").strip()
        step = NODE_TYPE_TO_STEP_CODE.get(str(node.get("type") or "").strip())
        if node_id and step:
            out[node_id] = step
    return out


def _variant_from_studio_meta(meta: dict | None, step_code: str) -> str | None:
    """Вариант из Node Studio: `meta.prompt_slot_variants[node][slot]`.

    Зеркало `web/src/lib/prompt-slot-storage.ts` → `activeVariantForSlot`:
    для hero_style — слот `style`; иначе сначала `main`, потом любой
    существующий файл шага.

    Слоты чужих шагов пропускаются: `default` лежит в папке каждого шага, и
    без этой проверки привязка, выбранная на одном узле, молча становилась
    промтом всех остальных. Узлы, которых нет в `canvas_graph` (метаданные
    времён Node Studio), считаются как раньше — своими.
    """
    if not meta or step_code not in STEP_FOLDERS:
        return None
    from app.services.node_config import all_prompt_slots

    slot_variants = all_prompt_slots(meta)
    if not slot_variants:
        return None
    node_steps = _canvas_node_steps(meta, step_code)
    preferred_slot = _STEP_PREFERRED_SLOT.get(step_code, "main")
    found_preferred: str | None = None
    found_main: str | None = None
    found_other: str | None = None
    for node_key, slots in slot_variants.items():
        node_step = node_steps.get(str(node_key))
        if node_step and node_step != step_code:
            continue
        if not isinstance(slots, dict):
            continue
        for slot_id, variant in slots.items():
            clean = _clean_variant_name(str(variant or ""))
            if not clean:
                continue
            if not prompt_exists(step_code, clean):
                continue
            if slot_id == preferred_slot:
                found_preferred = clean
            elif slot_id == "main" and found_main is None:
                found_main = clean
            elif found_other is None:
                found_other = clean
    return found_preferred or found_main or found_other


def _coerce_hero_sheet_variant(name: str, source: str) -> tuple[str, str]:
    """Hero = turnaround sheet. Агент реестра / пустой default → лист 16:9."""
    from app.services.hero_prompt_contract import (
        hero_master_looks_like_registry_agent,
        hero_master_looks_like_sheet,
    )

    def _ok(variant: str) -> bool:
        text = _variant_text_db_first("hero", variant)
        if text is None:
            return False
        if hero_master_looks_like_registry_agent(text):
            return False
        return hero_master_looks_like_sheet(text)

    clean = _clean_variant_name(name)
    if clean and _ok(clean):
        return clean, source
    for fallback in _HERO_SHEET_FALLBACKS:
        if _ok(fallback):
            if clean and clean != fallback:
                logger.warning(
                    "hero: вариант {!r} (source={}) не лист генерации — {}",
                    clean,
                    source,
                    fallback,
                )
            return fallback, "default"
    return (clean or DEFAULT_NAME), source


def _variant_text_db_first(step_code: str, variant: str) -> str | None:
    """Текст варианта для проверки: сначала база, потом файл (как read_prompt).

    На сервере диск только для чтения, и вариант, сохранённый редактором,
    живёт лишь в базе — проверка ``path.is_file()`` его бы отбросила.
    ``None`` — варианта нет ни там, ни там.
    """
    from app.services import prompt_store

    from_db = prompt_store.resolve(step_code, variant)
    if from_db is not None:
        return from_db
    try:
        path = prompt_path(step_code, variant)
    except ValueError:
        return None
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _hero_style_file_ok(variant: str) -> bool:
    from app.services.hero_prompt_contract import (
        hero_style_looks_like_character_lock,
        hero_style_looks_like_img_pr_template,
    )

    text = _variant_text_db_first("hero_style", variant)
    if text is None:
        return False
    if hero_style_looks_like_img_pr_template(text):
        return False
    return hero_style_looks_like_character_lock(text) or len(text.strip()) >= 80


def _hero_style_fallback_names() -> list[str]:
    """Сначала «ДЛЯ ПЕРСОНАЖА», потом любой лок в 04_hero_style/."""
    names = _available_prompt_names("hero_style")
    preferred = [n for n in names if "персонаж" in n.lower()]
    rest = [n for n in names if n not in preferred and n != DEFAULT_NAME]
    return [*preferred, *rest]


def _coerce_hero_style_variant(name: str, source: str) -> tuple[str, str]:
    """Hero style = visual lock для листа. Шаблон кадров / пустой default → лок персонажа."""
    clean = _clean_variant_name(name)
    if clean and _hero_style_file_ok(clean):
        return clean, source
    for fallback in _hero_style_fallback_names():
        if _hero_style_file_ok(fallback):
            if clean and clean != fallback:
                logger.warning(
                    "hero_style: вариант {!r} (source={}) не лок персонажа — {}",
                    clean,
                    source,
                    fallback,
                )
            return fallback, "default"
    return (clean or DEFAULT_NAME), source


def resolve_project_prompt_name(
    overrides: dict | None,
    step_code: str,
    *,
    meta: dict | None = None,
) -> str:
    """Какой вариант .md использовать для шага."""
    return resolve_project_prompt_with_source(overrides, step_code, meta=meta)[0]


PROMPT_SOURCE_LABELS: dict[str, str] = {
    "slot": "слот ноды",
    "preferred": "слот ноды",
    "override": "оверрайд проекта",
    "global": "глобально активный",
    "default": "default",
    "fallback": "фоллбэк",
}


def _available_prompt_names(step_code: str) -> list[str]:
    """Варианты шага: база важнее диска — на сервере диск только для чтения.

    `read_prompt` читает «сначала база, потом файл», поэтому и фоллбэк
    обязан видеть промты из базы. Смотреть только на диск значило бы увести
    шаг на чужой файл мимо варианта, сохранённого через редактор.
    """
    from app.services import prompt_store

    names = list(prompt_store.list_names(step_code))
    seen = set(names)
    for extra in list_prompts(step_code):
        if extra not in seen:
            names.append(extra)
            seen.add(extra)
    if DEFAULT_NAME in names:
        names.remove(DEFAULT_NAME)
        names.insert(0, DEFAULT_NAME)
    return names


def prompt_exists(step_code: str, name: str) -> bool:
    """Существует ли вариант промта: в БД (prompt_store), среди excel_gpt или на диске."""
    clean = _clean_variant_name(name) if name else ""
    if not clean:
        return False
    from app.services import prompt_store

    if is_excel_gpt_prompt_step(step_code):
        for code in excel_gpt_source_steps():
            if prompt_store.resolve(code, clean) is not None:
                return True
        return excel_gpt_prompt_exists(clean)

    if prompt_store.resolve(step_code, clean) is not None:
        return True
    try:
        return prompt_path(step_code, clean).exists()
    except ValueError:
        return False


def _default_prompt_available(step_code: str) -> bool:
    """Есть ли у шага вариант `default` — в базе или на диске."""
    return prompt_exists(step_code, DEFAULT_NAME)


def resolve_project_prompt_with_source(
    overrides: dict | None,
    step_code: str,
    *,
    meta: dict | None = None,
    node_key: str | None = None,
    slot_id: str | None = None,
) -> tuple[str, str]:
    """(имя варианта, источник: slot|preferred|override|global|default).

    Для excel_gpt при нескольких нодах обязателен node_key: иначе все слоты
    получают один prompt_overrides["excel_gpt"]. Node Studio пишет выбор в
    meta.prompt_slot_variants[node_key]["main"].
    """
    name, source = _resolve_prompt_variant_raw(
        overrides,
        step_code,
        meta=meta,
        node_key=node_key,
        slot_id=slot_id,
    )
    if str(step_code) == "hero":
        return _coerce_hero_sheet_variant(name, source)
    if str(step_code) == "hero_style":
        return _coerce_hero_style_variant(name, source)
    return name, source


def _resolve_prompt_variant_raw(
    overrides: dict | None,
    step_code: str,
    *,
    meta: dict | None = None,
    node_key: str | None = None,
    slot_id: str | None = None,
) -> tuple[str, str]:
    overrides = overrides or {}
    # Node Studio gpt-слот по умолчанию id=main.
    effective_slot = (slot_id or "").strip() or ("main" if node_key else None)

    if node_key and effective_slot:
        from app.services.node_config import prompt_slots_for_node

        # Привязка узла: `node.data.config.promptSlots` важнее
        # `meta.prompt_slot_variants` — один слой доступа, находка 12.
        node_slots = prompt_slots_for_node(meta, node_key)
        if node_slots:
            bound = _clean_variant_name(str(node_slots.get(effective_slot) or ""))
            if not bound and effective_slot == "main":
                # Любой gpt-слот ноды, если main пуст.
                for _sid, variant in node_slots.items():
                    clean = _clean_variant_name(str(variant or ""))
                    if clean:
                        bound = clean
                        break
            if bound and prompt_exists(step_code, bound):
                return bound, "slot"
        if effective_slot and effective_slot != "main":
            preferred = _clean_variant_name(effective_slot)
            if preferred and prompt_exists(step_code, preferred):
                return preferred, "preferred"

    if is_excel_gpt_prompt_step(step_code):
        for key in excel_gpt_source_steps():
            chosen = overrides.get(key)
            if chosen:
                clean = _clean_variant_name(str(chosen))
                if clean and prompt_exists(step_code, clean):
                    return clean, "override"
        # Слоты проекта (в т.ч. унаследованные ребёнком) важнее global —
        # иначе active_variants.json с «default» перекрывает выбор родителя.
        #
        # НО только когда узел не назван. Спросили про конкретный узел, у него
        # своей привязки нет — значит её нет, и брать чужую нельзя: перебор
        # ниже идёт по ВСЕМ узлам графа. Живой прогон 2026-08-31: нода
        # «Проверка кадров», встав в слот 2, получила `sd_assemble_chrono_dyn`
        # — промт сборщика сцен. Там это было безвредно (checkPromptSource
        # =agent перебивает встроенным агентом), но течь та же, что чинил
        # коммит 5b747d1, и в другом месте она молча подменит промт.
        if not node_key:
            for key in excel_gpt_source_steps():
                from_meta = _variant_from_studio_meta(meta, key)
                if from_meta:
                    return from_meta, "slot"
        from app.services.prompt_active_global import get_global_active

        global_name = get_global_active(EXCEL_GPT_UNIFIED_STEP)
        if global_name and prompt_exists(EXCEL_GPT_UNIFIED_STEP, global_name):
            return global_name, "global"
        # `default` у шага может отсутствовать (перенесли/переименовали).
        # Молча вернуть его имя — значит уронить шаг на FileNotFoundError вместо
        # того, чтобы взять первый доступный вариант и сказать об этом источником.
        if _default_prompt_available(EXCEL_GPT_UNIFIED_STEP):
            return DEFAULT_NAME, "default"
        available = _available_prompt_names(EXCEL_GPT_UNIFIED_STEP)
        if available:
            return available[0], "fallback"
        return DEFAULT_NAME, "default"

    chosen = overrides.get(step_code)
    if chosen:
        clean = _clean_variant_name(str(chosen))
        if clean and prompt_exists(step_code, clean):
            return clean, "override"

    # Project-level слоты Node Studio / child inheritance — до global.
    # Тот же запрет, что и в excel_gpt-ветке выше: спросили про конкретный
    # узел и своей привязки у него нет — чужую не берём.
    if not node_key:
        from_meta = _variant_from_studio_meta(meta, step_code)
        if from_meta:
            return from_meta, "slot"

    from app.services.prompt_active_global import get_global_active

    global_name = get_global_active(step_code)
    if global_name:
        return global_name, "global"

    if _default_prompt_available(step_code):
        return DEFAULT_NAME, "default"

    available = _available_prompt_names(step_code)
    if available:
        return available[0], "fallback"

    return DEFAULT_NAME, "default"


def read_resolved_project_prompt(
    project, step_code: str, *, node_key: str | None = None, slot_id: str | None = None
) -> tuple[str, Path, str, str]:
    """(имя варианта, путь к .md, текст, источник) — единая точка для шагов и логов."""
    overrides = getattr(project, "prompt_overrides", None) or {}
    meta = getattr(project, "meta", None) or {}
    tenant_id = getattr(project, "tenant_id", None)
    from app.services.tenant import current_tenant, tenant_scope

    effective_tenant = str(tenant_id) if tenant_id else current_tenant()
    with tenant_scope(effective_tenant):
        name, source = resolve_project_prompt_with_source(
            overrides, step_code, meta=meta, node_key=node_key, slot_id=slot_id
        )
        gid = _group_id_from_canvas_node(project, node_key)
        if is_excel_gpt_prompt_step(step_code):
            path = resolve_excel_gpt_prompt_path(name, group_id=gid, project=project, node_key=node_key)
            text = read_prompt(step_code, name, group_id=gid, project=project, node_key=node_key)
        else:
            path = prompt_path(step_code, name)
            text = read_prompt(step_code, name)
    from app.services.gpt_text_builder import inject_topic_placeholders
    from app.services.prompt_style_vars import apply_style_vars

    text = apply_style_vars(text, path, meta, step_code, name)
    topic = str(getattr(project, "topic", None) or "")
    if step_code in ENRICH_STEP_CODES:
        text = _render_enrich_template(project, step_code, text, topic)
    return name, path, inject_topic_placeholders(text, topic), source


def _render_enrich_template(project, step_code: str, text: str, topic: str) -> str:
    from app.services.prompt_composer import (
        assert_prompt_resolved,
        merge_project_prompt_config,
        render_template_text,
    )

    if "{{" not in text:
        return text
    heroes = getattr(project, "hero_descriptions", None)
    blocks, vars_ = merge_project_prompt_config(
        getattr(project, "prompt_overrides", None) or {},
        hero_description=heroes[0] if isinstance(heroes, list) and heroes else None,
        topic=topic or None,
    )
    rendered = render_template_text(text, blocks, vars_)
    assert_prompt_resolved(rendered, label=step_code)
    return rendered


def get_project_prompt(project, step_code: str) -> str:
    """Прочитать выбранный для проекта мастер-промт с диска.

    Проект приводится к dict-like через `getattr(project, "prompt_overrides", {})`
    — так удобно работать и со SQLAlchemy-моделью, и с обычным dict.

    Если в `prompt_overrides` включена компонентная сборка (blocks / use_blocks_v2)
    и для шага есть template в `prompts/steps/` — собираем из блоков.
    """
    overrides = getattr(project, "prompt_overrides", None) or {}
    from app.services.prompt_composer import (
        STEP_CODE_TO_COMPOSE,
        compose_step,
        merge_project_prompt_config,
        project_uses_blocks_v2,
    )

    # enrich_* всегда из выбранного .md в Studio — не из blocks/steps template.
    # Пресет с compose=file (НИИ 67) тоже читается файлом: шаблон blocks
    # запрещает выдумывать факты, а этот агент как раз достраивает пустые поля.
    full_file = False
    if step_code not in ENRICH_STEP_CODES and project_uses_blocks_v2(overrides):
        meta = getattr(project, "meta", None) or {}
        variant_name, _variant_source = resolve_project_prompt_with_source(
            overrides,
            step_code,
            meta=meta if isinstance(meta, dict) else None,
        )
        from app.services.prompt_step_presets import resolve_prompt_preset

        preset = resolve_prompt_preset(step_code, variant_name)
        full_file = isinstance(preset, dict) and preset.get("compose") == "file"
    if step_code not in ENRICH_STEP_CODES and project_uses_blocks_v2(overrides) and not full_file:
        step_id = STEP_CODE_TO_COMPOSE.get(step_code)
        if step_id:
            blocks, vars_ = merge_project_prompt_config(
                overrides,
                hero_description=(
                    (getattr(project, "hero_descriptions", None) or [None])[0]
                    if isinstance(getattr(project, "hero_descriptions", None), list)
                    else None
                ),
                topic=getattr(project, "topic", None),
            )
            try:
                composed = compose_step(step_id, blocks, vars_)
                from app.services.gpt_text_builder import inject_topic_placeholders

                topic = str(getattr(project, "topic", None) or "")
                return inject_topic_placeholders(composed, topic)
            except FileNotFoundError:
                pass

    name, path, text, source = read_resolved_project_prompt(project, step_code)
    logger.info(
        "get_project_prompt: step={} variant={!r} source={} path={}",
        step_code,
        name,
        source,
        path,
    )
    return text


def make_template_for_new(step_code: str, name: str) -> str:
    """Стартовый шаблон для нового файла, чтобы юзеру было что заполнять."""
    folder = STEP_FOLDERS.get(step_code, "?")
    return (
        f"# Master-prompt для шага «{step_code}» (вариант: {name})\n"
        f"# Файл: prompts/{folder}/{name}.md\n"
        "#\n"
        "# Замени этот текст на свой мастер-промт целиком.\n"
        "# Можно использовать markdown — он уйдёт в ChatGPT как обычный текст.\n"
        "# Бот добавит технический блок (генератор/aspect/2K) и контекст\n"
        "# (план/сценарий/закадровый кадр/etc.) сам перед отправкой.\n"
        "\n"
        "Опиши задачу для модели здесь...\n"
    )


APPROVED_PROFILE_PROMPTS = {
    "original-contract-v1": "",
    "repair-json-only-v1": (
        "\n\n[Approved profile: repair-json-only-v1] Repair formatting only. "
        "Return one JSON value conforming to the ORIGINAL node schema. "
        "Preserve source facts and identifiers; do not invent missing information or bypass validation."
    ),
    "require-apply-ops-v1": (
        "\n\n[Approved synthetic profile: require-apply-ops-v1] "
        "Return the mandatory apply-ops JSON specified by the original node contract."
    ),
}


def apply_approved_profile_prompt(resolved: str, variant: str) -> str:
    if variant not in APPROVED_PROFILE_PROMPTS:
        raise ValueError("unknown approved profile prompt variant")
    suffix = APPROVED_PROFILE_PROMPTS[variant]
    return resolved if not suffix or resolved.endswith(suffix) else resolved + suffix
