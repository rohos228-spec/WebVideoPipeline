from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

from loguru import logger

from app.services.action_layer import node_run_cost as nrc
from app.services.action_layer import project_gen_settings as pgs
from app.services.action_layer import step_outcome as so
from app.services.action_layer.builtin_group import (
    _NODE_KEY,
    _PRE_IDLE,
    _PRE_NO_MONTAGE,
    _PRE_NODE,
    _PRE_NOT_MANAGED,
    _effective_project,
    _get_project,
    _stop_project,
)
from app.services.action_layer.builtin_montage import _PRE_PROJECT, _base
from app.services.action_layer.builtin_montage_more import _GEN, _IMG_RES, _VID_RES, _effective_settings
from app.services.action_layer.builtin_wave3 import (
    _PRE_NODE_KEY_SAFE,
    _SRC_PROPS,
    _obj,
    _poll_project_idle,
    _post_file,
    _pre_source,
    _unknown_start_cost,
)
from app.services.action_layer.context import ActionCallError, ActionContext
from app.services.action_layer.spec import (
    RISK_DESTRUCTIVE,
    RISK_LOCAL_SAFE,
    RISK_READ,
    ActionRegistry,
    ActionSpec,
    CostEstimate,
    Precondition,
)

WAVE5_IDS: tuple[str, ...] = (
    "step.get_run_cost_basis",
    "step.run_step",
    "hitl.list_pending",
    "hitl.approve",
    "hitl.reject",
    "hitl.regenerate",
    "project.create",
    "project.create_child",
    "project.patch_settings",
    "massfactory.start",
    "kill_switch.engage",
    "project.upload_xlsx",
    "hero.replace_image",
    "storage.upload_file",
    "storage.clear_files",
    "history.list_snapshots",
    "history.rollback",
)
WAVE5_CHANGED_IDS: tuple[str, ...] = (
    "project.resume",
    "project.continue_pipeline",
    "montage.set_voiceover",
    "montage.set_coverage",
    "montage.set_anchors",
    "montage.set_scene_chain",
    "shot.edit_cell_voiceover",
    "shot.edit_field",
    "montage.upload_voice",
    "montage.upload_music",
    "prompt.save_master",
)
EXCLUDED_CONTROLS: dict[str, str] = {
    "Публикация на площадки": "остаётся действием человека (node_capabilities: publish human_only, "
    "управляемый агентский режим запрещён); в каталог не входит",
    "Снятие аварийного стопа": "включённый стоп блокирует любые шаги слоя (executor), снять его "
    "можно только человеческой кнопкой; слой умеет лишь включать",
    "Удаление проекта": "необратимо удаляет папку проекта; планировщик намеренно держит его вне каталога",
    "Воркфлоу и холст": "переписывание графа целиком, гонка с открытым "
    "холстом (docs/ACTION_LAYER.md, «Не вошло в каталог»)",
    "GPT-workspace, meta-agent, gpt-verdict": "платный LLM вне проекта: стоимость запроса не "
    "определяется кодом, отдельная волна",
    "Baza (правка кадров /db/*)": "частично покрыто shot.* и montage.*, полный набор правок - "
    "отдельная волна",
    "Outsee/Kie create-workspace, gen-assistant": "автономная генерация вне проекта, тарифы Outsee "
    "в репозитории не заданы",
    "Fleet pull/push/sync": "запуск на основном ПК, не локальный контроль; есть только флаг "
    "montage.set_send_to_main_pc",
    "Дублирование проекта": "эндпоинта дублирования нет; есть только дочерний проект (project.create_child)",
}

_CEILING = {
    "type": "number",
    "minimum": 0.01,
    "maximum": 100000,
    "description": "Потолок расхода в кредитах, который назвал оператор",
}
_STEP_CODES = list(nrc.MEDIA_STEPS)
_HERO_MODES = ["hero", "no_hero", "auto"]
_TOPIC = {"type": "string", "maxLength": 2000}
_TITLE = {"type": "string", "minLength": 1, "maxLength": 200}


async def _no_auto_chain(ctx, params):
    if params.get("allow_auto_chain"):
        # Человек сказал «пусть дальше идёт сама»: дальше Студия ведёт проект своими лимитами.
        return None
    proj = await _get_project(ctx, params["project_id"])
    if proj.get("auto_mode"):
        return (
            f"у проекта #{params['project_id']} включено автопродвижение: после этого Студия "
            "сама запустит следующие шаги, платные тоже. Напишите «пусть дальше идёт сама» "
            "или выключите автопродвижение"
        )
    return None


_PRE_NO_AUTO_CHAIN = Precondition(
    "no_auto_chain", "Автопродвижение выключено (шаг один, цену покрывает потолок)", _no_auto_chain
)


def _ceiling_estimate(base: CostEstimate, params: dict) -> CostEstimate:
    return nrc.apply_ceiling(base, params.get("ceiling_credits"))


async def _step_basis(ctx: ActionContext, params: dict) -> dict:
    code = params["step_code"]
    kind = nrc.media_step_kind(code)
    detail = await _get_project(ctx, params["project_id"])
    eff = _effective_settings(detail)
    board = await ctx.call("GET", _base(params["project_id"]))
    units = nrc.media_units((board or {}).get("frames") or [])
    declare: dict[str, Any] = {}
    count = None
    if kind == "image":
        count = units["images"]
        declare = {
            "count": count,
            "image_generator": eff["image_generator"],
            "image_resolution": eff["image_resolution"],
        }
    elif kind == "video":
        count = units["videos"]
        declare = {
            "count": count,
            "video_generator": eff["video_generator"],
            "video_resolution": eff["video_resolution"],
        }
    est = nrc.media_code_estimate(
        code,
        units=count,
        image_generator=eff["image_generator"],
        image_resolution=eff["image_resolution"],
        video_generator=eff["video_generator"],
        video_resolution=eff["video_resolution"],
    )
    return {"kind": kind, "units": units, "declare": declare, "estimate": est, "effective": eff}


async def declared_media_values(params: dict) -> dict:
    """Недостающие count / генератор / разрешение шага картинок или видео — по данным проекта.

    Озвучке и звукам так же подставляется sound_credits, если цена по коду известна целиком.
    Те же значения проверяют _verify_step_cost и sound_cost.cost_reason; названное человеком не трогаем."""
    from app.services.action_layer import sound_cost as sc

    out = dict(params)
    code = str(out.get("step_code") or "")
    if code not in sc.SOUND_STEPS:
        out.pop("sound_credits", None)
    if nrc.media_step_kind(code) not in ("image", "video"):
        # Текстовому шагу (сценарий, разбивка…) генераторы и число кадров не нужны: планировщик
        # иногда их придумывает, на карточке это вводит в заблуждение.
        for key in ("count", "image_generator", "image_resolution", "video_generator", "video_resolution"):
            out.pop(key, None)
        if code in sc.SOUND_STEPS and out.get("project_id") is not None:
            try:
                basis = await sc.load_basis(out["project_id"], code)
            except Exception as exc:  # noqa: BLE001
                logger.warning("step plan: sound price for #{} not read: {}", out.get("project_id"), exc)
                return out
            if not basis.priced:
                # Цену музыки код целиком не знает: придуманный планировщиком sound_credits сорвал бы
                # план, нужен только потолок ceiling_credits.
                out.pop("sound_credits", None)
            elif out.get("sound_credits") is None and not basis.missing:
                out["sound_credits"] = basis.credits
        return out
    if out.get("project_id") is None:
        return out
    from app.orchestrator import app_ref

    try:
        ctx = ActionContext(app=app_ref.current(), operator="system", read_only=True)
        declare = (await _step_basis(ctx, out))["declare"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("step plan: media values for #{} not read: {}", out.get("project_id"), exc)
        return out
    for key, value in declare.items():
        if out.get(key) is None and value is not None:
            out[key] = value
    return out


async def _get_step_cost_basis(ctx, params):
    basis = await _step_basis(ctx, params)
    est: CostEstimate = basis["estimate"]
    out = {
        "step_code": params["step_code"],
        "kind": basis["kind"],
        "units": basis["units"],
        "declare": basis["declare"],
        "estimable": est.known,
        "uncalibrated": True,
        "basis": est.basis,
        "needs_ceiling": (not est.known) and basis["kind"] != "local",
    }
    if est.known:
        out["upper_bound_credits"] = est.high
    else:
        out["hint"] = (
            "цена по коду неизвестна: для запуска укажите ceiling_credits - потолок, "
            "который вы готовы потратить на этот шаг"
        )
    return out


def _estimate_step(params: dict) -> CostEstimate:
    base = nrc.media_code_estimate(
        params.get("step_code", ""),
        units=params.get("count"),
        image_generator=params.get("image_generator"),
        image_resolution=params.get("image_resolution"),
        video_generator=params.get("video_generator"),
        video_resolution=params.get("video_resolution"),
    )
    return _ceiling_estimate(base, params)


async def _verify_step_cost(ctx: ActionContext, params: dict) -> str | None:
    params["step_code"]  # прежняя проверка наличия ключа (KeyError)
    basis = await _step_basis(ctx, params)
    kind = basis["kind"]
    if kind in ("image", "video"):
        need = basis["declare"]
        if params.get("count") is None:
            return f"заявите count: число генераций по данным проекта {need['count']}"
        if params["count"] < need["count"]:
            return f"count: заявлено {params['count']}, по доске нужно не меньше {need['count']}"
        for name in (
            ("image_generator", "image_resolution")
            if kind == "image"
            else ("video_generator", "video_resolution")
        ):
            if params.get(name) is None:
                return f"укажите {name} (в проекте: {need[name]})"
            if params[name] != need[name]:
                return f"{name}: заявлено {params[name]}, в проекте {need[name]}"
    actual = basis["estimate"]
    ceiling = params.get("ceiling_credits")
    if actual.known and ceiling is not None and ceiling < actual.high:
        return f"ceiling_credits {ceiling:g} ниже расчёта {actual.high:g} кр по данным проекта"
    final = _estimate_step(params)
    if kind != "local" and not final.known:
        return (
            "стоимость шага неизвестна: "
            + actual.basis
            + ". Укажите ceiling_credits - потолок расхода на этот шаг"
        )
    return None


async def _pre_step_cost(ctx, params):
    return await _verify_step_cost(ctx, params)


async def _pre_step_runnable(ctx, params):
    from app.models import ProjectStatus
    from app.orchestrator.pipeline_steps import is_step_runnable, step_by_code

    step = step_by_code(params["step_code"])
    if step is None:
        return f"неизвестный шаг {params['step_code']!r}"
    proj = await _get_project(ctx, params["project_id"])
    missing = _enrich_slot_missing(proj, params["step_code"])
    if missing:
        return missing
    try:
        status = ProjectStatus(proj.get("status"))
    except ValueError:
        return f"неизвестный статус проекта {proj.get('status')!r}"
    if not is_step_runnable(step, status):
        if await _studio_allows(params["project_id"], step, paused=status is ProjectStatus.paused):
            return None
        return (
            f"шаг «{step.title}» сейчас недоступен при статусе проекта {status.value}: "
            "предыдущие шаги не выполнены или проект на паузе"
        )
    return None


def _enrich_slot_missing(proj: dict, step_code: str) -> str | None:
    """enrich_N без N-й ноды «Работа с GPT» в графе: планировщик угадал шаг по линейному меню."""
    from app.services.action_layer.builtin_insight import _current_next, _pipeline

    if not step_code.startswith("enrich_"):
        return None
    meta = proj.get("meta") if isinstance(proj.get("meta"), dict) else {}
    if not (meta.get("canvas_graph") or {}).get("nodes"):
        return None
    status = str(proj.get("status") or "")
    steps = _pipeline(status, meta)
    if any(s.get("step") == step_code for s in steps):
        return None
    _cur, nxt = _current_next(steps, status)
    hint = f"; следующий шаг по графу: «{nxt['title']}» ({nxt['step']})" if nxt else ""
    return f"в графе проекта #{proj.get('id')} нет ноды для шага {step_code}{hint}"


async def _studio_allows(project_id: int, step, *, paused: bool = False) -> bool:
    """Линейное меню не знает графа проекта (например, две ноды «Работа с GPT» вместо трёх).

    Решает та же проверка данных, что у запуска шага в Студии; шаги провайдеров
    (их dry_run Студия запрещает) остаются на линейном порядке. Исключение — пауза:
    ручной запуск в Студии снимает паузу после ошибок для любого шага (start_step).
    """
    from app.db import session_scope
    from app.models import Project
    from app.services.project_state import is_running_status
    from app.services.step_data_guard import can_enter_running
    from app.web.studio_dry_run import FORBIDDEN_DRY_RUN_STEPS

    if step.code in FORBIDDEN_DRY_RUN_STEPS and not paused:
        return False
    try:
        async with session_scope() as session:
            project = await session.get(Project, int(project_id))
            if project is None or is_running_status(project.status):
                return False
            if paused:
                # Данные судим по статусу до паузы; сессия откатывается, проект не меняется.
                before = _status_before_pause(project)
                if before is not None:
                    project.status = before
            ok, _reason, _fix = await can_enter_running(session, project, step.running_status)
            await session.rollback()
            return bool(ok)
    except Exception:
        return False


def _status_before_pause(project):
    """paused_from_status (⏸ человека) или шаг, на котором остановили ошибки (failure_state)."""
    from app.models import ProjectStatus

    meta = project.meta if isinstance(project.meta, dict) else {}
    fs = meta.get("step_failure") if isinstance(meta.get("step_failure"), dict) else {}
    for raw in (meta.get("paused_from_status"), fs.get("last_running")):
        if not raw:
            continue
        try:
            return ProjectStatus(str(raw))
        except ValueError:
            continue
    return None


async def _run_media_step(ctx, params):
    reason = await _verify_step_cost(ctx, params)
    if reason:
        raise RuntimeError(f"оценка стоимости не совпадает с проектом: {reason}")
    before = await _get_project(ctx, params["project_id"])
    launched_at = time.time()
    after = await ctx.call(
        "POST", f"/projects/{params['project_id']}/steps/{params['step_code']}/run", params={"mode": "resume"}
    )
    return {
        "started": True,
        "step_code": params["step_code"],
        **so.launch_marks(params["step_code"], before, after, launched_at),
    }


async def _poll_step(ctx, params, started):
    state = await _poll_project_idle(ctx, params, started)
    return await so.honest_state(ctx, params, started, state)


async def _stop_reason(ctx, project_id) -> str:
    """Почему Студия остановила шаг: её же запись в meta (step_failure / auto_paused_reason)."""
    try:
        meta = (await _get_project(ctx, project_id)).get("meta") or {}
    except Exception:  # noqa: BLE001
        return ""
    return so.stop_reason(meta)


def _step_effects(params: dict) -> str:
    title = nrc.MEDIA_STEPS.get(params["step_code"], ("", params["step_code"]))[1]
    return (
        f"Запуск шага «{title}» убивает активную генерацию проекта, снимает стоп и паузу "
        "очереди и перезаписывает результат шага; платные вызовы провайдеров начнутся сразу. "
        "Остановить: кнопка «Стоп задачи» или аварийный стоп в «Регистраторе»."
    )


async def _hitl_rows(ctx, project_id: int) -> list[dict]:
    rows = await ctx.call("GET", f"/hitl/project/{project_id}")
    return [r for r in rows or [] if isinstance(r, dict)]


HITL_KIND_RU = {
    "approve_plan": "сценарий",
    "approve_script": "закадровый текст",
    "approve_hero": "персонажи",
    "approve_images": "картинки",
    "approve_videos": "видео",
    "approve_final": "финальный ролик",
    "approve_publish": "публикация",
}


def _pending_text(rows: list[dict]) -> str:
    if not rows:
        return "Ничего не ждёт подтверждения."
    parts = []
    for r in rows[:20]:
        what = HITL_KIND_RU.get(str(r.get("kind")), str(r.get("kind")))
        frame = f", кадр {r['frame_id']}" if r.get("frame_id") else ""
        parts.append(f"№{r.get('id')} — {what}{frame}")
    return f"Ждут подтверждения ({len(rows)}): " + "; ".join(parts) + "."


async def _list_pending(ctx, params):
    rows = [r for r in await _hitl_rows(ctx, params["project_id"]) if str(r.get("decision")) == "pending"]
    return {
        "count": len(rows),
        "text_ru": _pending_text(rows),
        "requests": [
            {
                "hitl_id": r.get("id"),
                "kind": r.get("kind"),
                "kind_ru": HITL_KIND_RU.get(str(r.get("kind")), r.get("kind")),
                "frame_id": r.get("frame_id"),
                "created_at": r.get("created_at"),
                "payload_keys": sorted(r.get("payload") or {})[:20],
            }
            for r in rows[:50]
        ],
    }


async def _resolve_hitl(ctx, params) -> tuple[int | None, str | None]:
    """hitl_id из параметров или единственный ожидающий запрос вида ``kind``."""
    if params.get("hitl_id"):
        return int(params["hitl_id"]), None
    kind = params.get("kind")
    if not kind:
        return None, "укажите номер запроса или что подтвердить (сценарий, персонажи, картинки…)"
    rows = [
        r
        for r in await _hitl_rows(ctx, params["project_id"])
        if str(r.get("decision")) == "pending" and str(r.get("kind")) == kind
    ]
    what = HITL_KIND_RU.get(kind, kind)
    if not rows:
        return None, f"в проекте {params['project_id']} нет ожидающего подтверждения: {what}"
    if len(rows) > 1 and kind != "approve_plan":
        ids = ", ".join(f"№{r.get('id')}" for r in rows[:10])
        return None, f"ждут подтверждения несколько запросов ({what}: {ids}): назовите номер"
    # Сценарий после перегенераций копит запросы: актуален последний — его текст сейчас в проекте.
    return max(int(r["id"]) for r in rows), None


async def _hitl_pending(ctx, params):
    hitl_id, problem = await _resolve_hitl(ctx, params)
    if problem:
        return problem
    for row in await _hitl_rows(ctx, params["project_id"]):
        if row.get("id") == hitl_id:
            if str(row.get("decision")) != "pending":
                return f"решение по запросу {hitl_id} уже принято ({row.get('decision')})"
            return None
    return f"запроса подтверждения {hitl_id} в проекте {params['project_id']} нет"


_PRE_HITL = Precondition("hitl_pending", "Запрос подтверждения существует и ждёт решения", _hitl_pending)


def _decide(decision: str):
    async def executor(ctx, params):
        body = {"decision": decision, "project_id": params.get("project_id")}
        if decision == "approve":
            # «Пусть дальше идёт сама»: как кнопка Студии с продвижением; иначе только решение.
            body["advance"] = bool(params.get("allow_auto_chain"))
        hitl_id, problem = await _resolve_hitl(ctx, params)
        if problem:
            raise RuntimeError(problem)
        data = await ctx.call("POST", f"/hitl/{hitl_id}/decision", json=body)
        kind = (data or {}).get("kind")
        word = {"approve": "Подтвердил", "reject": "Отклонил", "regenerate": "Отправил на перегенерацию"}
        what = HITL_KIND_RU.get(str(kind), str(kind or "запрос"))
        try:
            title = (await _get_project(ctx, params["project_id"])).get("title")
        except Exception:  # noqa: BLE001
            title = None
        where = f"#{params['project_id']}" + (f" «{title}»" if title else "")
        return {
            "hitl_id": hitl_id,
            "decision": (data or {}).get("decision"),
            "kind": kind,
            "frame_id": (data or {}).get("frame_id"),
            "text_ru": f"{where}: {word.get(decision, decision).lower()} {what} (запрос №{hitl_id}).",
        }

    return executor


def _hitl_ref(p: dict) -> str:
    if p.get("hitl_id"):
        return f"по запросу {p['hitl_id']}"
    return f"по запросу «{HITL_KIND_RU.get(str(p.get('kind')), p.get('kind'))}»"


_HITL_PARAMS = {
    "hitl_id": {"type": "integer", "minimum": 1, "description": "id запроса из hitl.list_pending"},
    "kind": {
        "type": "string",
        "enum": sorted(HITL_KIND_RU),
        "description": "вид запроса, если номер неизвестен: approve_plan — сценарий, "
        "approve_hero — персонажи, approve_images — картинки, "
        "approve_videos — видео, approve_final — финальный ролик",
    },
}


async def _create_project(ctx, params):
    body = {"title": params["title"].strip()}
    if params.get("topic"):
        body["topic"] = params["topic"].strip()
    if params.get("hero_mode"):
        body["hero_mode"] = params["hero_mode"]
    gen = pgs.new_project_settings(params)
    body.update(gen)
    data = await ctx.call("POST", "/projects", json=body)
    pid, title = (data or {}).get("id"), (data or {}).get("title")
    if pid is not None:
        await pgs.sync_prompt_aspect(ctx, pid, (data or {}).get("prompt_overrides"), gen["aspect_ratio"])
    fmt = pgs.aspect_slug(gen["aspect_ratio"])
    return {
        "project_id": pid,
        "slug": (data or {}).get("slug"),
        "title": title,
        "status": (data or {}).get("status"),
        "settings": gen,
        "text_ru": f"Создал проект #{pid} «{title}» (формат {fmt}). Следующий шаг — «Сценарий».",
    }


async def _title_nonblank(ctx, params):
    return None if params["title"].strip() else "название проекта пустое"


async def _create_child(ctx, params):
    data = await ctx.call("POST", f"/projects/{params['project_id']}/child")
    return {
        "child_id": (data or {}).get("id"),
        "slug": (data or {}).get("slug"),
        "parent_id": params["project_id"],
    }


_SETTINGS_KEYS = (
    "title",
    "topic",
    "hero_mode",
    "image_generator",
    "image_resolution",
    "video_generator",
    "video_resolution",
    "aspect_ratio",
)


def _same_setting(key: str, current: Any, wanted: Any) -> bool:
    if current is None or wanted is None:
        return False
    if key == "aspect_ratio":
        aid = pgs.aspect_id(wanted)
        return aid is not None and aid == pgs.aspect_id(current)
    return " ".join(str(current).split()) == " ".join(str(wanted).split())


def unchanged_settings(project: Any, params: dict) -> list[str]:
    """Ключи правки настроек, у которых значение уже такое же, как в проекте."""
    if not isinstance(project, dict):
        return []
    return [k for k in _SETTINGS_KEYS if k in params and _same_setting(k, project.get(k), params[k])]


SETTINGS_NO_IDENTITY_TEXT = (
    "Не могу прочитать настройки проекта: запрос пришёл без входа в студию. Войдите и повторите просьбу."
)


async def changed_settings_only(params: dict) -> dict:
    """План правки настроек: без ключей, которые и так совпадают с проектом.

    Карточка «поменяются title, topic, …» перечисляла и то, что не менялось. Если совпадает
    всё, шаг остаётся как есть: пустую правку проверка отклонит понятным текстом."""
    out = dict(params)
    if out.get("project_id") is None:
        return out
    from app.orchestrator import app_ref
    from app.orchestrator.contracts import PlanResolveRefused
    from app.services.studio_auth import current_identity
    from app.settings import settings

    # Режим учётных записей без личности задачи: проект прочитать нечем, а прежний
    # запасной путь отдавал исходные параметры — на карточку шли ключи, которых
    # человек не просил. Отказ, а не правка вслепую.
    if settings.accounts_enabled and current_identity() is None:
        raise PlanResolveRefused(SETTINGS_NO_IDENTITY_TEXT)
    try:
        ctx = ActionContext(app=app_ref.current(), operator="system", read_only=True)
        project = await _get_project(ctx, out["project_id"])
    except ActionCallError as exc:
        if exc.status in (401, 403):
            raise PlanResolveRefused(SETTINGS_NO_IDENTITY_TEXT) from exc
        logger.warning("settings plan: project #{} not read: {}", out.get("project_id"), exc)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("settings plan: project #{} not read: {}", out.get("project_id"), exc)
        return out
    same = unchanged_settings(project, out)
    present = [k for k in _SETTINGS_KEYS if k in out]
    if not same or len(same) == len(present):
        return out
    return {k: v for k, v in out.items() if k not in same}


async def _settings_given(ctx, params):
    if not any(k in params for k in _SETTINGS_KEYS):
        return f"укажите хотя бы одну настройку: {', '.join(_SETTINGS_KEYS)}"
    return None


async def _aspect_valid(ctx, params):
    """Формат кадра из списка Studio; то же сообщение, что у project.create."""
    if params.get("aspect_ratio") is None:
        return None
    return pgs.validation_error({"aspect_ratio": params["aspect_ratio"]})


async def _media_plan(ctx, params) -> list[dict]:
    if not any(k in params for kind in pgs.MEDIA_KINDS.values() for k in kind):
        return []
    return pgs.media_changes(await _get_project(ctx, params["project_id"]), params)


async def _media_valid(ctx, params):
    """Генераторы из списков Studio: иначе цену не сравнить, а сервер всё равно откажет."""
    return pgs.validation_error(
        {k: params[k] for k in ("image_generator", "video_generator") if params.get(k) is not None}
    )


async def _media_not_costlier(ctx, params):
    bad = await _media_valid(ctx, params)
    if bad:
        return bad
    costly = [c for c in await _media_plan(ctx, params) if c["needs_confirmation"]]
    if not costly:
        return None
    return (
        " ".join(pgs.media_change_text(c) for c in costly) + " Модель или разрешение дороже "
        "текущих или цена неизвестна: используйте «Изменить модель или разрешение проекта "
        "(дороже или цена неизвестна)» (нужно подтверждение)."
    )


async def _patch_settings(ctx, params):
    body = {k: params[k] for k in _SETTINGS_KEYS if k in params}
    aid = None
    if "aspect_ratio" in body:
        aid = pgs.aspect_id(body["aspect_ratio"])
        if aid is None:
            raise ActionCallError(400, pgs.validation_error({"aspect_ratio": body["aspect_ratio"]}))
        body["aspect_ratio"] = aid
    pid = params["project_id"]
    media = await _media_plan(ctx, params)
    data = await ctx.call("PATCH", f"/projects/{pid}", json=body)
    data = data if isinstance(data, dict) else {}
    changed = {
        k: data.get(k)
        for k, v in body.items()
        if str(data.get(k) if data.get(k) is not None else "") != str(v).strip()
    }
    if changed:
        raise ActionCallError(409, f"сервер сохранил другие значения: {changed}")
    out: dict = {"updated": sorted(body)}
    lines: list[str] = []
    if aid is not None:
        # Промты картинок/анимации берут формат из ASPECT_RATIO_VIDEO, шаги - из aspect_ratio.
        await pgs.sync_prompt_aspect(ctx, pid, data.get("prompt_overrides"), aid)
        out["aspect_ratio"] = aid
        lines.append(f"Формат кадра проекта #{pid}: {pgs.aspect_slug(aid)}.")
    if media:
        out["media"] = media
        lines.extend(pgs.media_change_text(c) for c in media)
    if any(k in body for k in ("image_generator", "video_generator")):
        try:
            meta = (await _effective_project(ctx, pid)).get("meta")
            notes = pgs.node_overrides(meta, data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("patch_settings #{}: node override check failed: {}", pid, exc)
            notes = []
        if notes:
            out["node_overrides"] = notes
            lines.extend(notes)
    # Без text_ru чат отвечал полями «updated: title; rollback possible: да».
    if "title" in body:
        lines.insert(0, f"Проект #{pid} теперь называется «{body['title']}».")
    if "topic" in body:
        lines.insert(0, f"Тема проекта #{pid}: «{body['topic']}».")
    if not lines:
        names = ", ".join(_SETTING_RU.get(k, k) for k in sorted(body))
        lines.append(f"В проекте #{pid} изменено: {names}.")
    out["text_ru"] = " ".join(lines)
    return out


_SETTING_RU = {
    "hero_mode": "режим героя",
    "image_resolution": "разрешение картинок",
    "video_resolution": "разрешение видео",
    "image_generator": "модель картинок",
    "video_generator": "модель видео",
    "aspect_ratio": "формат кадра",
}


async def _mass_start(ctx, params):
    data = await ctx.call(
        "POST", f"/projects/{params['project_id']}/mass-lanes/start", json={"topics": list(params["topics"])}
    )
    return {
        "started_id": (data or {}).get("started_id"),
        "queue_size": (data or {}).get("queue_size"),
        "remaining": (data or {}).get("remaining"),
    }


def _mass_estimate(params: dict) -> CostEstimate:
    n = len(params.get("topics") or [])
    return _ceiling_estimate(
        CostEstimate.unknown(
            f"{n} видео по полному конвейеру в дочерних проектах: цена по коду не определяется"
        ),
        params,
    )


async def _kill_engage(ctx, params):
    data = await ctx.call(
        "POST",
        f"/projects/{params['project_id']}/observations/kill-switch",
        json={"active": True, "rationale": params["rationale"]},
    )
    data = data if isinstance(data, dict) else {}
    if data.get("active") is not True:
        raise ActionCallError(409, f"аварийный стоп не включился: {str(data)[:200]}")
    return {"active": True}


async def _upload_xlsx(ctx, params):
    data, info = await _post_file(ctx, "xlsx", f"/projects/{params['project_id']}/xlsx/upload", params)
    return {"status": data.get("status"), **info}


async def _replace_hero(ctx, params):
    data, info = await _post_file(
        ctx, "ref_image", f"/projects/{params['project_id']}/assets/hero/replace", params
    )
    return {"path": data.get("path"), "id": data.get("id"), **info}


async def _storage_upload(ctx, params):
    data, info = await _post_file(
        ctx, "node_file", f"/projects/{params['project_id']}/storage/{params['node_key']}/upload", params
    )
    return {"ok": bool(data.get("ok")), "file_name": data.get("fileName"), **info}


async def _storage_clear(ctx, params):
    data = await ctx.call("DELETE", f"/projects/{params['project_id']}/storage/{params['node_key']}/files")
    return {"ok": bool((data or {}).get("ok")), "removed": (data or {}).get("removed")}


def _settings_effects(params: dict) -> str:
    from app.services.action_layer.settings_diff import target_text

    names = target_text(params) or "настройки"
    return (
        f"В проекте #{params['project_id']} поменяются: {names}. Новая модель или разрешение "
        "дороже текущих или их цена неизвестна: следующие генерации картинок и видео могут "
        "стоить больше. Прежние значения сохраняются снимком, вернуть их можно откатом "
        "(history.rollback)."
    )


def _specs() -> list[ActionSpec]:
    S = ActionSpec
    step_props = {
        "step_code": {"type": "string", "enum": _STEP_CODES},
        "count": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100000,
            "description": "Число генераций из step.get_run_cost_basis (для img и video)",
        },
        "image_generator": _GEN,
        "image_resolution": _IMG_RES,
        "video_generator": _GEN,
        "video_resolution": _VID_RES,
        "ceiling_credits": _CEILING,
    }
    settings_schema = _obj(
        {
            "title": _TITLE,
            "topic": _TOPIC,
            "hero_mode": {"type": "string", "enum": _HERO_MODES},
            "image_generator": _GEN,
            "image_resolution": _IMG_RES,
            "video_generator": _GEN,
            "video_resolution": _VID_RES,
            "aspect_ratio": pgs.ASPECT_SCHEMA,
        },
        [],
    )
    settings_pre = (
        _PRE_PROJECT,
        _PRE_NOT_MANAGED,
        _PRE_IDLE,
        Precondition("settings_given", "Указана хотя бы одна настройка", _settings_given),
        Precondition("aspect_valid", "Формат кадра из списка Studio", _aspect_valid),
    )
    return [
        S(
            id="step.get_run_cost_basis",
            title_ru="Оценка стоимости запуска шага конвейера",
            description_ru="Для шагов картинок, видео, озвучки, музыки, сборки и LLM-промтов: сколько "
            "генераций по доске, какие значения заявить в step.run_step и верхняя оценка "
            "в кредитах. Картинки и видео оцениваются по каталогу Kie (Outsee и прочие "
            "модели - нет тарифа), озвучка, музыка и промты - оценки по коду нет, тогда "
            "нужен потолок ceiling_credits. Оценка не откалибрована по балансу.",
            params_schema=_obj({"step_code": step_props["step_code"]}, ["step_code"]),
            executor=_get_step_cost_basis,
            risk=RISK_READ,
            idempotent=True,
            preconditions=(_PRE_PROJECT,),
            tags=("step", "cost"),
        ),
        S(
            id="step.run_step",
            title_ru="Запустить шаг конвейера проекта",
            description_ru="Ручной запуск шага (режим resume): картинки, видео, озвучка, музыка, "
            "финальная сборка, промты картинок и анимации. Убивает активную генерацию "
            "проекта и перезаписывает результат шага. Картинки и видео оцениваются по "
            "каталогу Kie, для остальных платных шагов нужен потолок ceiling_credits; "
            "потолок и заявленные значения входят в хэш подтверждения. Автопродвижение "
            "должно быть выключено. Публикация сюда не входит.",
            params_schema=_obj(step_props, ["step_code"]),
            executor=_run_media_step,
            poll=_poll_step,
            stop=_stop_project,
            poll_interval_s=3.0,
            risk=RISK_DESTRUCTIVE,
            spends_money=True,
            destructive=True,
            reversible=False,
            idempotent=False,
            estimator=_estimate_step,
            destructive_effects=_step_effects,
            preconditions=(
                _PRE_PROJECT,
                _PRE_NOT_MANAGED,
                _PRE_IDLE,
                _PRE_NO_MONTAGE,
                _PRE_NO_AUTO_CHAIN,
                Precondition("step_runnable", "Шаг доступен при статусе проекта", _pre_step_runnable),
                Precondition(
                    "step_cost_declared",
                    "Заявленные число, модель и потолок покрывают расчёт по проекту",
                    _pre_step_cost,
                ),
            ),
            tags=("step", "run"),
        ),
        S(
            id="hitl.list_pending",
            title_ru="Показать ожидающие подтверждения",
            description_ru="Запросы подтверждения проекта (HITL), по которым решение ещё не принято: "
            "id, вид и кадр. Ничего не меняет.",
            params_schema=_obj({}, []),
            executor=_list_pending,
            risk=RISK_READ,
            idempotent=True,
            preconditions=(_PRE_PROJECT,),
            tags=("hitl",),
        ),
        S(
            id="hitl.approve",
            title_ru="Одобрить запрос подтверждения (HITL)",
            description_ru="Принимает результат: кадр получает статус одобренного. Следующий шаг "
            "НЕ запускается (платный запуск идёт отдельным действием step.run_step с оценкой "
            "цены и потолком). Решение необратимо. Выполняется с токеном оператора. "
            "Автопродвижение должно быть выключено, либо allow_auto_chain=true, если "
            "человек сказал, что дальше проект идёт сам.",
            params_schema=_obj({**_HITL_PARAMS, "allow_auto_chain": {"type": "boolean"}}, []),
            executor=_decide("approve"),
            risk=RISK_DESTRUCTIVE,
            destructive=True,
            reversible=False,
            idempotent=False,
            destructive_effects=lambda p: (
                f"Решение {_hitl_ref(p)} будет принято "
                "«одобрено» и повторно не меняется; следующий шаг "
                "не запускается."
            ),
            preconditions=(_PRE_PROJECT, _PRE_HITL, _PRE_NO_AUTO_CHAIN),
            tags=("hitl",),
        ),
        S(
            id="hitl.reject",
            title_ru="Отклонить запрос подтверждения (HITL)",
            description_ru="Отклоняет результат: кадр получает статус failed, проект может уйти на "
            "паузу. Решение необратимо. Выполняется с токеном оператора.",
            params_schema=_obj(_HITL_PARAMS, []),
            executor=_decide("reject"),
            risk=RISK_DESTRUCTIVE,
            destructive=True,
            reversible=False,
            idempotent=False,
            destructive_effects=lambda p: (
                f"Решение {_hitl_ref(p)} будет принято "
                "«отклонено» и повторно не меняется; кадр помечается "
                "неудачным."
            ),
            preconditions=(_PRE_PROJECT, _PRE_HITL),
            tags=("hitl",),
        ),
        S(
            id="hitl.regenerate",
            title_ru="Запросить перегенерацию по HITL",
            description_ru="Возвращает кадр на перегенерацию: воркер заново сгенерирует картинку или "
            "клип и потратит деньги. Стоимость по коду не определяется, поэтому нужен "
            "потолок ceiling_credits; он входит в хэш подтверждения. Автопродвижение "
            "должно быть выключено. Решение необратимо.",
            params_schema=_obj({**_HITL_PARAMS, "ceiling_credits": _CEILING}, ["ceiling_credits"]),
            executor=_decide("regenerate"),
            risk=RISK_DESTRUCTIVE,
            spends_money=True,
            deferred_spend=True,
            destructive=True,
            reversible=False,
            idempotent=False,
            estimator=lambda p: _ceiling_estimate(
                CostEstimate.unknown(
                    "перегенерация: число попыток и цена модели из параметров не определяются"
                ),
                p,
            ),
            destructive_effects=lambda p: (
                f"Решение {_hitl_ref(p)} будет принято «перегенерировать»; прежний результат кадра заменится."
            ),
            preconditions=(_PRE_PROJECT, _PRE_NOT_MANAGED, _PRE_HITL, _PRE_NO_AUTO_CHAIN),
            tags=("hitl", "run"),
        ),
        S(
            id="project.create",
            title_ru="Создать проект",
            description_ru="Новый проект с названием, темой и режимом героя. Настройки генерации "
            "(формат кадра, модели и разрешения картинок и видео) - дефолты Studio, "
            "названные в просьбе перекрывают их. Автопродвижение не "
            "включается (если стоит серверная настройка «автопродвижение новых "
            "проектов», она применится). Ничего не запускает и денег не тратит.",
            params_schema=_obj(
                {
                    "title": _TITLE,
                    "topic": _TOPIC,
                    "hero_mode": {"type": "string", "enum": _HERO_MODES},
                    "aspect_ratio": pgs.ASPECT_SCHEMA,
                    "image_generator": _GEN,
                    "image_resolution": _IMG_RES,
                    "video_generator": _GEN,
                    "video_resolution": _VID_RES,
                },
                ["title"],
                project=False,
            ),
            executor=_create_project,
            risk=RISK_LOCAL_SAFE,
            idempotent=False,
            scope="global",
            future_spend_note="Если на сервере включено «автопродвижение новых проектов», проект получит его "
            "сразу: дальнейшие шаги могут быть платными.",
            preconditions=(
                Precondition("title_nonblank", "Название не пустое", _title_nonblank),
                Precondition("gen_settings_valid", "Настройки генерации из списков Studio", pgs.pre_valid),
            ),
            tags=("project", "create"),
        ),
        S(
            id="project.create_child",
            title_ru="Создать дочерний проект",
            description_ru="Дочерний проект: настройки, промты и тексты родителя, без озвучки, Excel "
            "и результатов. Ничего не запускает.",
            params_schema=_obj({}, []),
            executor=_create_child,
            risk=RISK_LOCAL_SAFE,
            idempotent=False,
            preconditions=(_PRE_PROJECT,),
            tags=("project", "create"),
            future_spend_note="Копирует настройки родителя (модель, разрешение), от них зависит цена "
            "будущих генераций дочернего проекта.",
        ),
        S(
            id="project.patch_settings",
            title_ru="Изменить настройки проекта",
            description_ru="Название, тема, режим героя, модель и разрешение картинок и видео, формат "
            "кадра. Модель и разрешение - только если не дороже текущих; иначе "
            "«Изменить модель или разрешение проекта (дороже или цена неизвестна)». "
            "Сам ничего не запускает. Проверяет, что сервер сохранил именно эти "
            "значения, и предупреждает, если модель с ноды перекрывает модель проекта. "
            "Автопродвижение, мета и промты здесь не меняются.",
            params_schema=settings_schema,
            executor=_patch_settings,
            risk=RISK_LOCAL_SAFE,
            idempotent=True,
            reversible=True,
            future_spend_note="Меняет модель и разрешение, то есть цену будущих генераций проекта.",
            preconditions=(
                *settings_pre,
                Precondition(
                    "media_not_costlier",
                    "Модель и разрешение из списков Studio и не дороже текущих",
                    _media_not_costlier,
                ),
            ),
            tags=("project", "settings"),
        ),
        S(
            id="project.patch_settings_costly",
            title_ru="Изменить модель или разрешение проекта (дороже или цена неизвестна)",
            description_ru="То же, что «Изменить настройки проекта», но модель или разрешение "
            "картинок и видео дороже текущих или их цена неизвестна: только после "
            "подтверждения человека. Сам ничего не запускает.",
            params_schema=settings_schema,
            executor=_patch_settings,
            risk=RISK_DESTRUCTIVE,
            destructive=True,
            reversible=True,
            idempotent=True,
            destructive_effects=_settings_effects,
            future_spend_note="Следующие генерации картинок и видео проекта могут стоить дороже.",
            preconditions=(
                *settings_pre,
                Precondition("media_valid", "Модели картинок и видео из списков Studio", _media_valid),
            ),
            tags=("project", "settings"),
        ),
        S(
            id="massfactory.start",
            title_ru="Запустить массовую фабрику видео",
            description_ru="Очередь тем: первый дочерний проект стартует с плана, остальные идут по "
            "очереди сами. Удаляет новые (нетронутые) дочерние проекты прежней "
            "очереди. Цена по коду не определяется (полный конвейер на каждую тему), "
            "поэтому нужен потолок ceiling_credits на всю очередь; воркер потолок не "
            "знает - остановить можно кнопкой «Стоп задачи» или аварийным стопом.",
            params_schema=_obj(
                {
                    "topics": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 20,
                        "items": {"type": "string", "minLength": 1, "maxLength": 300},
                    },
                    "ceiling_credits": _CEILING,
                },
                ["topics", "ceiling_credits"],
            ),
            executor=_mass_start,
            risk=RISK_DESTRUCTIVE,
            spends_money=True,
            deferred_spend=True,
            destructive=True,
            reversible=False,
            idempotent=False,
            estimator=_mass_estimate,
            destructive_effects=lambda p: (
                f"Очередь из {len(p['topics'])} тем стартует; прежние нетронутые дочерние проекты "
                "очереди удаляются; воркер пойдёт по полному конвейеру и потратит деньги."
            ),
            preconditions=(_PRE_PROJECT, _PRE_NOT_MANAGED, _PRE_IDLE),
            tags=("project", "factory", "run"),
        ),
        S(
            id="kill_switch.engage",
            title_ru="Включить аварийный стоп",
            description_ru="Включает глобальный аварийный стоп: платные и разрушающие шаги слоя "
            "перестают выполняться. Причина обязательна и пишется в журнал. Снять стоп "
            "может только человек кнопкой в панели.",
            params_schema=_obj(
                {"rationale": {"type": "string", "minLength": 3, "maxLength": 1000}}, ["rationale"]
            ),
            executor=_kill_engage,
            risk=RISK_LOCAL_SAFE,
            idempotent=True,
            preconditions=(_PRE_PROJECT,),
            tags=("safety", "kill_switch"),
        ),
        S(
            id="project.upload_xlsx",
            title_ru="Загрузить Excel проекта",
            description_ru="Заменяет project.xlsx файлом .xlsx (проверяется сервером) и перечитывает "
            "его в базу: поля проекта, закадр и кадры перезаписываются значениями из "
            "файла. Прежний файл не архивируется.",
            params_schema=_obj({**_SRC_PROPS}, []),
            executor=_upload_xlsx,
            risk=RISK_DESTRUCTIVE,
            destructive=True,
            reversible=False,
            idempotent=True,
            destructive_effects=lambda p: (
                "project.xlsx проекта будет заменён загруженным файлом, "
                "кадры и поля проекта в базе перезапишутся его данными; "
                "прежний файл не сохраняется."
            ),
            preconditions=(_PRE_PROJECT, _PRE_NOT_MANAGED, _PRE_IDLE, _PRE_NO_MONTAGE, _pre_source("xlsx")),
            tags=("project", "upload", "xlsx"),
        ),
        S(
            id="hero.replace_image",
            title_ru="Загрузить картинку героя",
            description_ru="Кладёт картинку (png, jpg, webp) в папку characters проекта под её "
            "именем; файл с таким же именем перезаписывается без архива.",
            params_schema=_obj({**_SRC_PROPS}, []),
            executor=_replace_hero,
            risk=RISK_DESTRUCTIVE,
            destructive=True,
            reversible=False,
            idempotent=True,
            destructive_effects=lambda p: (
                "Картинка героя в characters/ с тем же именем файла будет перезаписана без копии."
            ),
            preconditions=(_PRE_PROJECT, _PRE_IDLE, _pre_source("ref_image")),
            tags=("hero", "upload"),
        ),
        S(
            id="storage.upload_file",
            title_ru="Загрузить файл в хранилище ноды",
            description_ru="Добавляет файл в хранилище ноды проекта под новым именем с отметкой "
            "времени; существующие файлы не затрагиваются.",
            params_schema=_obj({"node_key": _NODE_KEY, **_SRC_PROPS}, ["node_key"]),
            executor=_storage_upload,
            risk=RISK_LOCAL_SAFE,
            idempotent=False,
            preconditions=(_PRE_PROJECT, _PRE_NODE, _PRE_NODE_KEY_SAFE, _pre_source("node_file")),
            tags=("storage", "upload"),
        ),
        S(
            id="storage.clear_files",
            title_ru="Очистить хранилище ноды",
            description_ru="Удаляет все файлы из хранилища ноды проекта.",
            params_schema=_obj({"node_key": _NODE_KEY}, ["node_key"]),
            executor=_storage_clear,
            risk=RISK_DESTRUCTIVE,
            destructive=True,
            reversible=False,
            idempotent=True,
            destructive_effects=lambda p: (
                f"Все файлы хранилища ноды {p['node_key']} будут удалены без копии."
            ),
            preconditions=(_PRE_PROJECT, _PRE_IDLE, _PRE_NODE, _PRE_NODE_KEY_SAFE),
            tags=("storage",),
        ),
    ]


def _with_ceiling(spec: ActionSpec) -> ActionSpec:
    schema = {
        **spec.params_schema,
        "properties": {**spec.params_schema["properties"], "ceiling_credits": _CEILING},
    }
    return replace(
        spec,
        params_schema=schema,
        estimator=lambda p: _ceiling_estimate(_unknown_start_cost(p), p),
        description_ru=spec.description_ru.replace(
            "Стоимость запуска неизвестна, поэтому план нельзя подтвердить.",
            "Стоимость запуска по коду неизвестна: без потолка ceiling_credits план нельзя "
            "подтвердить; с потолком (и выключенным автопродвижением) подтверждение привязано к "
            "хэшу плана вместе с ним. Воркер потолок не знает - предел расхода держат лимиты "
            "spend_guard и аварийный стоп.",
        ),
        preconditions=(
            *spec.preconditions,
            Precondition("ceiling_chain", "С потолком: автопродвижение выключено", _chain_if_ceiling),
        ),
    )


async def _chain_if_ceiling(ctx, params):
    if params.get("ceiling_credits") is None:
        return None
    return await _no_auto_chain(ctx, params)


def register_wave5(registry: ActionRegistry) -> None:
    from app.services.action_layer import builtin_wave5_history as hist

    for action_id in ("project.resume", "project.continue_pipeline"):
        spec = registry.find(action_id)
        if spec is not None:
            registry.unregister(action_id)
            registry.register(_with_ceiling(spec))
    registry.register_many([*_specs(), *hist.history_specs()])
    hist.wrap_catalog(registry)
