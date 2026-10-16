"""Параметры шагов plan/script/split из Project.meta → блок в GPT-сообщении."""

from __future__ import annotations

from app.models import Project

CHARS_PER_SEC = 14
BLANK = "____"
_DURATION_STEPS = frozenset({"plan", "script"})


def _meta_params(project: Project) -> dict:
    meta = getattr(project, "meta", None) or {}
    raw = meta.get("node_step_params")
    return raw if isinstance(raw, dict) else {}


def _step_bucket(project: Project, step: str) -> dict:
    bucket = _meta_params(project).get(step)
    return bucket if isinstance(bucket, dict) else {}


def _parse_number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        n = float(value) if isinstance(value, (int, float)) else float(str(value))
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return n


def duration_seconds_for_step(project: Project, step_code: str) -> float | None:
    """Длительность для plan/script. Сценарий наследует план, если своё не задано."""
    if step_code not in _DURATION_STEPS:
        return None
    own = _parse_number(_step_bucket(project, step_code).get("duration_seconds"))
    if own is not None:
        return own
    if step_code == "script":
        return _parse_number(_step_bucket(project, "plan").get("duration_seconds"))
    return None


def _fmt_num(value: float | None) -> str:
    if value is None:
        return BLANK
    if value == int(value):
        return str(int(value))
    return str(value)


def build_duration_params_block(project: Project, step_code: str, *, header: str) -> str:
    dur = duration_seconds_for_step(project, step_code)
    dur_s = _fmt_num(dur)
    chars_s = _fmt_num(int(round(dur * CHARS_PER_SEC)) if dur is not None else None)
    return f"{header}\nДлина {dur_s} секунд\nКоличество символов (длина секунд × 14) = {chars_s}"


def split_cell_limits_from_project(
    project: Project,
) -> tuple[int | None, int | None, int | None, int | None]:
    """(min, max, avg_min, avg_max) из meta.node_step_params.split."""
    split = _step_bucket(project, "split")

    def as_int(key: str) -> int | None:
        n = _parse_number(split.get(key))
        if n is None:
            return None
        return int(round(n))

    return (
        as_int("cell_min_chars"),
        as_int("cell_max_chars"),
        as_int("cell_avg_min"),
        as_int("cell_avg_max"),
    )


def split_params_fingerprint(project: Project) -> str:
    mn, mx, amn, amx = split_cell_limits_from_project(project)
    return f"{mn or ''}:{mx or ''}:{amn or ''}:{amx or ''}"


def calculate_fallback_split_limits(
    project: Project,
) -> tuple[int, int, int, int]:
    """Вычисляет рекомендованные лимиты разбивки (min, max, avg_min, avg_max)
    на основе сценария и длины закадрового текста проекта."""
    import re
    from pathlib import Path

    char_count = len((getattr(project, "script_text", "") or "").strip())
    if not char_count:
        data_dir = getattr(project, "data_dir", None)
        if data_dir:
            vo_path = Path(data_dir) / "voiceover.txt"
            if vo_path.is_file():
                try:
                    char_count = len(vo_path.read_text(encoding="utf-8", errors="replace").strip())
                except Exception:
                    pass

    general_plan = (getattr(project, "general_plan", "") or "").strip()
    target_frames: int | None = None
    if general_plan:
        matches = [int(m) for m in re.findall(r"(?:Кадр|кадр|Сцена|сцена)\s*(\d+)", general_plan)]
        if matches:
            target_frames = max(matches)
        else:
            m = re.search(r"(\d+)\s*(?:кадр|кадров|кадра|сцен|сцены)", general_plan, re.IGNORECASE)
            if m:
                target_frames = int(m.group(1))

    if not target_frames:
        dur = duration_seconds_for_step(project, "plan")
        if dur:
            target_frames = max(8, min(30, int(round(dur / 3.5))))
        elif char_count > 0:
            target_frames = max(8, min(30, int(round(char_count / 50))))
        else:
            target_frames = 18
    else:
        target_frames = max(4, min(30, target_frames))

    if char_count > 0 and target_frames > 0:
        avg = max(20, round(char_count / target_frames))
        calc_min = max(15, round(avg * 0.5))
        calc_max = max(45, round(avg * 1.8))
        calc_avg_min = max(20, round(avg * 0.8))
        calc_avg_max = max(30, round(avg * 1.15))
    else:
        calc_min, calc_max, calc_avg_min, calc_avg_max = 20, 85, 35, 60

    return calc_min, calc_max, calc_avg_min, calc_avg_max


def build_split_params_block(project: Project) -> str:
    mn, mx, amn, amx = split_cell_limits_from_project(project)
    if mn is None or mx is None or amn is None or amx is None:
        def_mn, def_mx, def_amn, def_amx = calculate_fallback_split_limits(project)
        mn = mn if mn is not None else def_mn
        mx = mx if mx is not None else def_mx
        amn = amn if amn is not None else def_amn
        amx = amx if amx is not None else def_amx
    return (
        "Разбивка\n"
        f"Минимальное количество символов в ячейке {_fmt_num(float(mn) if mn else None)}\n"
        f"Максимальное количество символов в ячейке {_fmt_num(float(mx) if mx else None)}\n"
        f"Средние значения от {_fmt_num(float(amn) if amn else None)} "
        f"до {_fmt_num(float(amx) if amx else None)}"
    )


def build_step_params_block(project: Project, step_code: str) -> str:
    if step_code == "plan":
        return build_duration_params_block(project, "plan", header="Сценарий")
    if step_code == "script":
        return build_duration_params_block(project, "script", header="Закадровый текст")
    if step_code == "split":
        return build_split_params_block(project)
    return ""


def send_to_main_pc_for_project(project: Project) -> bool:
    """Отправка на главный ПК для монтажа (meta.node_step_params.assemble.send_to_main_pc).

    По умолчанию выключено.
    """
    val = _step_bucket(project, "assemble").get("send_to_main_pc")
    return val is True


def subtitles_enabled_for_project(project: Project) -> bool:
    """Субтитры при сборке: meta.node_step_params.assemble.subtitles_enabled.

    По умолчанию выключены.
    """
    val = _step_bucket(project, "assemble").get("subtitles_enabled")
    return val is True


def _parse_nonneg_seconds(value: object, *, default: float = 0.0, cap: float = 120.0) -> float:
    if value is None or value == "":
        return default
    try:
        n = float(value) if isinstance(value, (int, float)) else float(str(value))
    except (TypeError, ValueError):
        return default
    return max(0.0, min(cap, n))


def post_voiceover_tail_seconds_for_project(project: Project) -> float:
    """Секунды видео после конца озвучки (заморозка последнего кадра)."""
    val = _step_bucket(project, "assemble").get("post_voiceover_tail_seconds")
    return _parse_nonneg_seconds(val)


def skip_intro_enabled_for_project(project: Project) -> bool:
    """Тумблер «не учитывать первые секунды» (assemble.skip_intro_enabled)."""
    val = _step_bucket(project, "assemble").get("skip_intro_enabled")
    return val is True


def skip_intro_seconds_for_project(project: Project) -> float:
    """Сколько секунд от начала финального ролика отрезать (0.00–2.00).

    Учитывается только если skip_intro_enabled=True.
    """
    if not skip_intro_enabled_for_project(project):
        return 0.0
    val = _step_bucket(project, "assemble").get("skip_intro_seconds")
    return round(_parse_nonneg_seconds(val, cap=2.0), 2)


def assemble_bgm_level_from_meta(meta: dict) -> int | None:
    """0..100 из node_step_params.assemble.bgm_level."""
    nsp = meta.get("node_step_params")
    if not isinstance(nsp, dict):
        return None
    assemble = nsp.get("assemble")
    if not isinstance(assemble, dict):
        return None
    val = assemble.get("bgm_level")
    if val is None or val == "":
        return None
    try:
        return max(0, min(100, int(val)))
    except (TypeError, ValueError):
        return None


def assemble_bgm_level_for_project(project: Project) -> int | None:
    return assemble_bgm_level_from_meta(getattr(project, "meta", None) or {})


def append_step_params_to_gpt_text(project: Project, step_code: str, base_text: str) -> str:
    """Добавляет блок параметров к тексту, уходящему в ChatGPT."""
    block = build_step_params_block(project, step_code).strip()
    if not block:
        return base_text
    base = (base_text or "").rstrip()
    if not base:
        return block
    return f"{base}\n\n---\n{block}"
