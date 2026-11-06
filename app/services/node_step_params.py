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
    res = f"{header}\nДлина {dur_s} секунд\nКоличество символов (длина секунд × 14) = {chars_s}"
    meta = getattr(project, "meta", None) or {}
    briefing = meta.get("briefing") if isinstance(meta, dict) else None
    if isinstance(briefing, dict) and briefing:
        extra_lines = []
        if step_code == "plan":
            if briefing.get("scenario_type"):
                extra_lines.append(f"Тип сценария: {briefing['scenario_type']}")
            if briefing.get("hero_description"):
                extra_lines.append(f"Главный герой: {briefing['hero_description']}")
            if briefing.get("target_frames"):
                extra_lines.append(f"Целевое количество сцен/кадров: {briefing['target_frames']}")
            if briefing.get("aspect_ratio"):
                extra_lines.append(f"Формат кадра (соотношение сторон): {briefing['aspect_ratio']}")
            if briefing.get("structure"):
                extra_lines.append(f"Структура и темп сцен: {briefing['structure']}")
            if briefing.get("visual_style"):
                extra_lines.append(f"Визуальный стиль: {briefing['visual_style']}")
            if briefing.get("items_note"):
                extra_lines.append(f"Ключевые предметы (консистентность): {briefing['items_note']}")
            elif briefing.get("items_consistent") is False:
                extra_lines.append("Ключевых предметов для консистентности нет — не перегружай описание вещей.")
        elif step_code == "script":
            if briefing.get("voiceover_mode"):
                extra_lines.append(f"Формат начитки: {briefing['voiceover_mode']}")
            if briefing.get("voiceover_chars"):
                extra_lines.append(f"Строгий лимит символов закадра: {briefing['voiceover_chars']}")
        if extra_lines:
            res += "\n" + "\n".join(extra_lines)
    return res


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
            target_frames = max(6, int(round(dur / 4.0)))
        elif char_count > 0:
            target_frames = max(6, int(round(char_count / 70)))
        else:
            target_frames = 12
    else:
        target_frames = max(2, target_frames)

    if char_count > 0 and target_frames > 0:
        avg = max(20, round(char_count / target_frames))
        calc_min = max(15, round(avg * 0.5))
        calc_max = max(45, round(avg * 1.8))
        calc_avg_min = max(20, round(avg * 0.8))
        calc_avg_max = max(30, round(avg * 1.15))
    else:
        calc_min, calc_max, calc_avg_min, calc_avg_max = 25, 110, 45, 80

    return calc_min, calc_max, calc_avg_min, calc_avg_max


def build_split_params_block(project: Project) -> str:
    mn, mx, amn, amx = split_cell_limits_from_project(project)
    if mn is None or mx is None or amn is None or amx is None:
        def_mn, def_mx, def_amn, def_amx = calculate_fallback_split_limits(project)
        mn = mn if mn is not None else def_mn
        mx = mx if mx is not None else def_mx
        amn = amn if amn is not None else def_amn
        amx = amx if amx is not None else def_amx
    res = (
        "Разбивка\n"
        f"Минимальное количество символов в ячейке {_fmt_num(float(mn) if mn else None)}\n"
        f"Максимальное количество символов в ячейке {_fmt_num(float(mx) if mx else None)}\n"
        f"Средние значения от {_fmt_num(float(amn) if amn else None)} "
        f"до {_fmt_num(float(amx) if amx else None)}"
    )
    meta = getattr(project, "meta", None) or {}
    briefing = meta.get("briefing") if isinstance(meta, dict) else None
    if isinstance(briefing, dict) and briefing:
        extra = []
        if briefing.get("structure"):
            extra.append(f"Темп склеек: {briefing['structure']}")
        if briefing.get("transitions"):
            extra.append(f"Стиль переходов: {briefing['transitions']}")
        if briefing.get("montage"):
            extra.append(f"Монтаж: {briefing['montage']}")
        if extra:
            res += "\n" + "\n".join(extra)
    return res


def build_step_params_block(project: Project, step_code: str) -> str:
    if step_code == "plan":
        return build_duration_params_block(project, "plan", header="Сценарий")
    if step_code == "script":
        return build_duration_params_block(project, "script", header="Закадровый текст")
    if step_code == "split":
        return build_split_params_block(project)
    return ""


def resolve_max_split_frames(project: Project, vo_text: str | None = None) -> int:
    """Определяет верхнюю границу для clamp_parent_frames.

    Если проект короткий (Shorts) без настроек — стандартные 30 кадров.
    Если ролик длинный (>1500 зн) или есть явные настройки (сценарий, лимиты символов),
    лимит кадров рассчитывается динамически, чтобы не схлопывать длинный текст в 30 кадров.
    """
    import math

    from app.services import content_checks as cc

    requested = cc.requested_frame_count(project)
    if requested is not None and requested > 0:
        return max(30, requested + 10)

    vo_len = len((vo_text or "").strip())
    if not vo_len:
        vo_len = len((getattr(project, "script_text", "") or "").strip())
    if not vo_len:
        data_dir = getattr(project, "data_dir", None)
        if data_dir:
            from pathlib import Path

            vo_path = Path(data_dir) / "voiceover.txt"
            if vo_path.is_file():
                try:
                    vo_len = len(vo_path.read_text(encoding="utf-8", errors="replace").strip())
                except Exception:
                    pass

    mn, mx, amn, _ = split_cell_limits_from_project(project)

    min_required_frames = 2
    max_by_limits = 30
    if mx is not None and mx > 0 and vo_len > 0:
        min_required_frames = max(2, int(math.ceil(vo_len / mx)))
    if mn is not None and mn > 0 and vo_len > 0:
        max_by_limits = max(30, int(math.ceil(vo_len / max(15, mn))))
    elif amn is not None and amn > 0 and vo_len > 0:
        max_by_limits = max(30, int(math.ceil(vo_len / max(20, amn))))

    if vo_len > 1500:
        # Для длинных роликов при средней длине кадра ~35-50 символов
        natural_max = max(30, int(math.ceil(vo_len / 35)))
        return max(natural_max, min_required_frames, max_by_limits)

    return max(30, min_required_frames, max_by_limits)


def build_split_db_hint(project: Project, vo_text: str | None = None) -> str:
    """Генерирует hint для LLM с учётом настроек ячеек проекта и длины закадра."""
    from app.services import content_checks as cc

    mn, mx, amn, amx = split_cell_limits_from_project(project)
    requested = cc.requested_frame_count(project)

    vo_len = len((vo_text or "").strip())
    if not vo_len:
        vo_len = len((getattr(project, "script_text", "") or "").strip())
    if not vo_len:
        data_dir = getattr(project, "data_dir", None)
        if data_dir:
            from pathlib import Path

            vo_path = Path(data_dir) / "voiceover.txt"
            if vo_path.is_file():
                try:
                    vo_len = len(vo_path.read_text(encoding="utf-8", errors="replace").strip())
                except Exception:
                    pass

    base = (
        "\n\n# РАЗБИВКА — ИСТОЧНИК ПРАВДЫ БАЗА\n"
        "Верни ТОЛЬКО JSON apply-ops:\n"
        '{"ops":[{"target":"replace_frames","frames":['
        '{"закадр":"текст кадра 1","длительность":3},'
        '{"закадр":"текст кадра 2"}'
        "]}]}\n"
    )

    rules = [
        "Запрещено дробить на микро-фразы по 1-2 слова.",
        "Один кадр = законченная мысль/сцена.",
        "Весь закадровый текст должен быть полностью сохранён в кадрах слово в слово без пропусков и изменений.",
    ]

    has_custom_limits = any(v is not None for v in (mn, mx, amn, amx))
    if requested is not None:
        rules.append(f"В сценарии/настройках явно задано число кадров: строго сделай ровно {requested} кадров.")
    elif has_custom_limits:
        limit_parts = []
        if mn is not None:
            limit_parts.append(f"минимум {mn} символов в кадре")
        if mx is not None:
            limit_parts.append(f"максимум {mx} символов в кадре")
        if amn is not None and amx is not None:
            limit_parts.append(f"в среднем {amn}–{amx} символов на кадр")
        elif amn is not None:
            limit_parts.append(f"в среднем от {amn} символов на кадр")
        elif amx is not None:
            limit_parts.append(f"в среднем до {amx} символов на кадр")
        rules.append(
            f"СТРОГИЕ НАСТРОЙКИ РАЗБИВКИ ЯЧЕЕК: {', '.join(limit_parts)}. "
            "Разбей весь закадровый текст строго в соответствии с этими лимитами."
        )
    elif vo_len > 1500:
        approx_frames = max(10, round(vo_len / 70))
        rules.append(
            f"Длинный ролик (~{vo_len} символов): разбей текст примерно на {approx_frames} кадров "
            "(в среднем 50–100 символов на кадр, 3–6 сек озвучки)."
        )
    else:
        rules.append("ФОРМАТ SHORTS: для ролика 60–75 сек делай 15–25 кадров (максимум 30). Нужно ≥2 и ≤30 кадров.")

    rules.append("Каждый кадр — отдельный объект с полем закадр.\n")
    return base + " ".join(rules)


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


_TOP_STYLE_KEYS = ("img_pr_style_id", "img_style", "image_style", "visual_style", "style")


def build_briefing_media_block(project: Project, step_code: str) -> str:
    """Ответы брифинга для промтов картинок (img_pr) и анимации (anim_pr).

    Стиль картинок из брифинга идёт только если у проекта нет своего стиля
    (паспорт агента стиля или выбранный стиль сильнее ответа в брифинге).
    """
    meta = getattr(project, "meta", None) or {}
    briefing = meta.get("briefing") if isinstance(meta, dict) else None
    if not isinstance(briefing, dict) or not briefing:
        return ""
    lines: list[str] = []
    if step_code == "anim_pr":
        if briefing.get("animation_style"):
            lines.append(f"Стиль анимации: {briefing['animation_style']}")
        if briefing.get("visual_style"):
            lines.append(f"Визуальный стиль ролика: {briefing['visual_style']}")
        if briefing.get("transitions"):
            lines.append(f"Переходы: {briefing['transitions']}")
        if briefing.get("montage"):
            lines.append(f"Монтаж: {briefing['montage']}")
    elif step_code == "img_pr":
        has_own_style = any(str(meta.get(k) or "").strip() for k in _TOP_STYLE_KEYS)
        if briefing.get("visual_style") and not has_own_style:
            lines.append(f"Визуальный стиль: {briefing['visual_style']}")
    if not lines:
        return ""
    return "Пожелания из брифинга\n" + "\n".join(lines)


def append_step_params_to_gpt_text(project: Project, step_code: str, base_text: str) -> str:
    """Добавляет блок параметров к тексту, уходящему в ChatGPT."""
    block = build_step_params_block(project, step_code).strip()
    if not block:
        return base_text
    base = (base_text or "").rstrip()
    if not base:
        return block
    return f"{base}\n\n---\n{block}"
