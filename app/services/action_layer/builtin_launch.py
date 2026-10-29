from __future__ import annotations

import contextlib
import re
import time
import uuid
from dataclasses import replace
from typing import Any

from app.services import launch_inputs as li
from app.services import launch_risks as lr
from app.services.action_layer import builtin_wave5 as w5
from app.services.action_layer import chain_stop as cs
from app.services.action_layer import img_pr_guard as ipg
from app.services.action_layer import node_run_cost as nrc
from app.services.action_layer import project_gen_settings as pgs
from app.services.action_layer import sound_cost as sc
from app.services.action_layer import step_outcome as so
from app.services.action_layer.builtin_group import (
    _NODE_KEY,
    _PRE_NO_MONTAGE,
    _PRE_NOT_MANAGED,
    _effective_project,
    _get_project,
    _is_running,
    _node_exists,
    _node_is_gpt,
    _node_run_poll,
    _stop_project,
    node_label_ru,
)
from app.services.action_layer.builtin_montage import _PRE_PROJECT
from app.services.action_layer.builtin_montage_more import _GEN, _IMG_RES, _VID_RES
from app.services.action_layer.builtin_wave3 import _obj
from app.services.action_layer.context import ActionCallError, ActionContext
from app.services.action_layer.spec import (
    RISK_DESTRUCTIVE,
    RISK_LOCAL_SAFE,
    RISK_READ,
    ActionRegistry,
    ActionSpec,
    CostEstimate,
    JobState,
    Precondition,
)
from app.services.runtime_streams import WORKER_MAX

LAUNCH_IDS: tuple[str, ...] = (
    "project.create_batch",
    "queue.enqueue_many",
    "queue.set_parallel",
    "queue.dequeue_many",
)
LAUNCH_CHANGED_IDS: tuple[str, ...] = ("step.run_step", "step.get_run_cost_basis")
EXCLUDED_STEPS: tuple[str, ...] = ("publish",)
NODE_KEY_STEPS: tuple[str, ...] = ("excel_gpt", "scene_d", "scene_asm")
STOP_META_KEY = "excel_gpt_stop_after_key"
_CHAIN_LIMIT = 40
MAX_BATCH = 20
MAX_PARALLEL = WORKER_MAX

_EXTRA_BASIS = {
    "hero": "картинки героев: число генераций и модель из кода не определены",
    "items": "картинки предметов: число генераций и модель из кода не определены",
}
_LLM_BASIS = "текстовый шаг: число и размер запросов к модели из кода не определены"
_CHAIN_BASIS = (
    "после шага воркер сам пройдёт всю цепочку (включено автопродвижение): какие шаги "
    "и сколько они стоят, из параметров не определить"
)


def launch_step_codes() -> list[str]:
    from app.orchestrator.node_registry import STEP_CODE_TO_NODE_TYPE

    return sorted(c for c in STEP_CODE_TO_NODE_TYPE if c not in EXCLUDED_STEPS)


def unpriced_basis(step_code: str) -> str:
    return _EXTRA_BASIS.get(step_code, _LLM_BASIS)


DEFAULT_TEXT_CEILING_CREDITS = 200


def with_default_ceiling(params: dict) -> dict:
    code = str(params.get("step_code") or "")
    kind = nrc.media_step_kind(code)
    if (
        params.get("ceiling_credits") is not None
        or params.get("allow_auto_chain")
        or (kind and kind != "llm")
        or code in _EXTRA_BASIS
        or code in sc.SOUND_STEPS
        or code not in launch_step_codes()
    ):
        return params
    return {**params, "ceiling_credits": DEFAULT_TEXT_CEILING_CREDITS}


def estimate_any_step(params: dict) -> CostEstimate:
    code = str(params.get("step_code") or "")
    ceiling = params.get("ceiling_credits")
    if params.get("allow_auto_chain"):
        return nrc.apply_ceiling(CostEstimate.unknown(_CHAIN_BASIS), ceiling)
    if code in sc.SOUND_STEPS:
        return sc.estimate(params)
    if nrc.media_step_kind(code):
        return w5._estimate_step(params)
    return nrc.apply_ceiling(CostEstimate.unknown(unpriced_basis(code)), ceiling)


async def _verify_any_cost(ctx: ActionContext, params: dict) -> str | None:
    code = params["step_code"]
    if code in sc.SOUND_STEPS:
        reason = sc.cost_reason(await sc.load_basis(params["project_id"], code), params)
        if reason:
            return reason
    elif nrc.media_step_kind(code):
        reason = await w5._verify_step_cost(ctx, params)
        if reason:
            return reason
    elif params.get("ceiling_credits") is None:
        return (
            "стоимость шага по коду неизвестна: "
            + unpriced_basis(code)
            + ". Укажите ceiling_credits - потолок расхода на этот шаг"
        )
    if params.get("allow_auto_chain") and params.get("ceiling_credits") is None:
        return (
            "с автопродвижением воркер пройдёт всю цепочку: укажите ceiling_credits - потолок "
            "расхода на всю цепочку"
        )
    return None


async def _pre_cost(ctx, params):
    return await _verify_any_cost(ctx, params)


async def _pre_runnable(ctx, params):
    code = params["step_code"]
    if code == "excel_gpt":
        if not params.get("node_key"):
            return "не указано, какую ноду «Работа с GPT» запускать (нужен node_key): напишите, например, «запусти работу с gpt 1»"
        return await _node_is_gpt(ctx, params)
    if params.get("node_key") and code not in NODE_KEY_STEPS:
        return "node_key указывается только для шагов excel_gpt, scene_d и scene_asm"
    if params.get("node_key"):
        missing = await _node_exists(ctx, params)
        if missing:
            return missing
    return await w5._pre_step_runnable(ctx, params)


async def _pre_sound_inputs(ctx, params):
    """Озвучка, музыка и звуки: то, без чего шаг упадёт сразу (текст, план, ключ провайдера)."""
    if params["step_code"] not in sc.SOUND_STEPS:
        return None
    return (await sc.load_basis(params["project_id"], params["step_code"])).missing


def gpt_chain(meta: dict | None, start_key: str) -> list[str]:
    """Ноды «Работа с GPT», которые Студия пройдёт сама после ``start_key`` по стрелкам.

    Тот же переход, что у воркера после ноды: первый рабочий узел по стрелкам; цепочка
    идёт, пока он excel_gpt. Первый элемент - сама ``start_key``.
    """
    from types import SimpleNamespace

    from app.services.excel_gpt_node import first_work_successor_along_edges, is_excel_gpt_node_type

    project = SimpleNamespace(meta=meta if isinstance(meta, dict) else {})
    chain = [start_key]
    while len(chain) < _CHAIN_LIMIT:
        succ = first_work_successor_along_edges(project, chain[-1])
        if succ is None or not is_excel_gpt_node_type(succ[1]) or succ[0] in chain:
            break
        chain.append(succ[0])
    return chain


def _chain_ru(meta: dict | None, keys: list[str]) -> str:
    return " → ".join(f"«{node_label_ru(meta, key)}» ({key})" for key in keys)


async def _pre_stop_point(ctx, params):
    stop = params.get("stop_after_node_key")
    if not stop:
        return None
    if params.get("step_code") != "excel_gpt" or not params.get("node_key"):
        return (
            "точка остановки цепочки задаётся только для запуска ноды «Работа с GPT» (excel_gpt с node_key)"
        )
    meta = (await _effective_project(ctx, params["project_id"])).get("meta")
    chain = gpt_chain(meta, params["node_key"])
    if stop not in chain:
        return (
            f"нода {stop!r} не идёт по стрелкам после ноды {params['node_key']!r}; Студия сама "
            f"пройдёт: {_chain_ru(meta, chain)}"
        )
    return None


async def _arm_stop_point(ctx, params) -> None:
    """Стоп-ключ Студии (как ▶ «только эта нода»): цепочка нод встанет после выбранной.

    Без точки остановки снимаем ключ, оставшийся от прежнего запуска, если цепочка до него
    не дошла: иначе новая цепочка оборвалась бы на чужой ноде.
    """
    if params.get("step_code") != "excel_gpt":
        return
    stop = params.get("stop_after_node_key")
    if not stop:
        meta = (await _get_project(ctx, params["project_id"])).get("meta")
        if not (isinstance(meta, dict) and meta.get(STOP_META_KEY)):
            return
    await ctx.call("PATCH", f"/projects/{params['project_id']}", json={"meta": {STOP_META_KEY: stop or None}})


async def _disarm_stop_point(ctx, params) -> bool:
    """После цепочки: True, если стоп-ключ остался - цепочка встала раньше ноды-остановки.

    Ключ снимает только сама нода-остановка. Оставшийся снимаем здесь: иначе следующий
    запуск из Студии оборвался бы на этой ноде.
    """
    stop = params.get("stop_after_node_key")
    if not stop:
        return False
    try:
        meta = (await _get_project(ctx, params["project_id"])).get("meta")
    except ActionCallError:
        return False
    if not (isinstance(meta, dict) and meta.get(STOP_META_KEY) == stop):
        return False
    await ctx.call("PATCH", f"/projects/{params['project_id']}", json={"meta": {STOP_META_KEY: None}})
    return True


async def _pre_idle_or_preempt(ctx, params):
    if params.get("preempt_running"):
        return None
    proj = await _get_project(ctx, params["project_id"])
    if _is_running(proj.get("status")):
        return (
            f"в проекте выполняется шаг (статус {proj.get('status')}): запуск прервёт его. "
            "Если это нужно, укажите preempt_running=true"
        )
    return None


async def _pre_chain(ctx, params):
    if params.get("allow_auto_chain"):
        return None
    proj = await _get_project(ctx, params["project_id"])
    if proj.get("auto_mode"):
        return (
            f"у проекта #{params['project_id']} включено автопродвижение: после этого шага Студия "
            "сама запустит следующие, платные тоже, а лимит рассчитан только на один шаг. "
            "Напишите «пусть дальше идёт сама, лимит … на проект» или выключите автопродвижение"
        )
    return None


async def _node_runs(ctx, pid: int) -> list[dict]:
    try:
        run = await ctx.call("GET", f"/projects/{pid}/run")
    except ActionCallError:
        return []
    return [r for r in ((run or {}).get("node_runs") or []) if isinstance(r, dict)]


async def _board_frames(ctx, params) -> int | None:
    if not li.needs_frames(params.get("step_code")):
        return None
    try:
        board = await ctx.call("GET", f"/projects/{params['project_id']}/montage-board")
    except ActionCallError:
        return None
    return li.board_frame_count(board)


async def _pre_frames(ctx, params):
    if await _board_frames(ctx, params) == 0:
        return li.no_frames_text(params["step_code"])
    return None


async def _pre_img_pr_scenes(ctx, params):
    """Промты картинок не запускаем по кадрам без данных сцены: модель вернёт заглушки за деньги."""
    if params["step_code"] != ipg.STEP_CODE and not params.get("allow_auto_chain"):
        return None
    pid = params["project_id"]
    meta = (await _get_project(ctx, pid)).get("meta")
    runs = await _node_runs(ctx, pid)
    if not ipg.launch_skips_scene(
        meta,
        params["step_code"],
        params.get("node_key"),
        runs,
        auto_chain=bool(params.get("allow_auto_chain")),
    ):
        return None
    return await ipg.scene_gap(pid, meta, runs)


async def _img_pr_hollow(ctx, params, started, state):
    """После промтов картинок: пустые промты («нет исходных данных») - не успех."""
    code = params["step_code"]
    if code != ipg.STEP_CODE:
        if not params.get("allow_auto_chain"):
            return state
        ready = so._order("image_prompts_ready")
        before = so._order(str((started or {}).get("status_before") or ""))
        final = so._order(str((state.result or {}).get("final_status") or ""))
        if ready is None or final is None or final < ready or (before is not None and before >= ready):
            return state
    pid = params["project_id"]
    hollow = await ipg.hollow_outcome(pid, (await _get_project(ctx, pid)).get("meta"))
    if not hollow:
        return state
    return await _named_failure(ctx, params, replace(state, ok=False, error=hollow))


async def step_risks(ctx, params) -> list[dict]:
    proj = await _get_project(ctx, params["project_id"])
    return lr.launch_risks(
        step_code=params["step_code"],
        status=proj.get("status"),
        auto_mode=bool(proj.get("auto_mode")),
        meta=proj.get("meta"),
        node_runs=await _node_runs(ctx, params["project_id"]),
        frames=await _board_frames(ctx, params),
    )


async def _step_warnings(ctx, params) -> list[str]:
    out = [r["text"] for r in await step_risks(ctx, params)]
    if params["step_code"] in sc.SOUND_STEPS:
        notice = (await sc.load_basis(params["project_id"], params["step_code"])).notice
        if notice:
            out.append(notice)
    return out


def _chain_tag(ctx) -> str:
    tag = re.sub(r"[^A-Za-z0-9_-]", "", str(ctx.plan_id or ""))[:32]
    if ctx.step_index is not None:
        tag = f"{tag}-{ctx.step_index}"
    return tag or uuid.uuid4().hex[:16]


async def _run_any_step(ctx, params):
    reason = await _verify_any_cost(ctx, params)
    if reason:
        raise RuntimeError(f"оценка стоимости не совпадает с проектом: {reason}")
    query: dict[str, Any] = {"mode": "resume"}
    if params.get("node_key"):
        query["node_key"] = params["node_key"]
    await _arm_stop_point(ctx, params)
    if params.get("allow_auto_chain") and (params.get("ceiling_credits") or 0) > 0:
        # Потолок на всю цепочку доходит до воркера, а не только до резерва плана.
        query["run_cap_credits"] = params["ceiling_credits"]
        query["run_tag"] = _chain_tag(ctx)
    before = await _get_project(ctx, params["project_id"])
    launched_at = time.time()
    after = await ctx.call(
        "POST", f"/projects/{params['project_id']}/steps/{params['step_code']}/run", params=query
    )
    out = {
        "started": True,
        "step_code": params["step_code"],
        **so.launch_marks(params["step_code"], before, after, launched_at),
    }
    if params.get("node_key"):
        out["node_key"] = params["node_key"]
    if params.get("stop_after_node_key"):
        out["stop_after_node_key"] = params["stop_after_node_key"]
    return out


async def _guard_stop_point(ctx, params, started) -> JobState | None:
    """Точка остановки в коде: Студия пошла дальше готовой ноды-остановки - стоп проекта.

    Стоп-ключ Студии - главный механизм; это страховка на случай, если ключ потерялся или
    после ноды-остановки что-то запустило следующую работу. None - всё идёт как надо.
    """
    stop = params.get("stop_after_node_key")
    if not stop:
        return None
    try:
        proj = await _get_project(ctx, params["project_id"])
    except ActionCallError:
        return None
    proj = proj if isinstance(proj, dict) else {}
    runs = await _node_runs(ctx, params["project_id"])
    cs.observe(started, runs)
    ran = cs.overrun_node(stop, runs, started, proj.get("status"), proj.get("meta"))
    if not ran:
        return None
    try:
        stopped = bool((await _stop_project(ctx, params)).get("stopped"))
    except ActionCallError:
        stopped = False
    with contextlib.suppress(ActionCallError):
        await _disarm_stop_point(ctx, params)
    cs.forget(started)
    where, what = await _step_names(ctx, params)
    tail = cs.overrun_text(proj.get("meta"), stop, ran, stopped)
    if not stopped:
        return JobState(done=True, ok=False, error=f"{where}: {what} завершена, но {tail}.")
    try:
        final = str((await _get_project(ctx, params["project_id"])).get("status") or "")
    except ActionCallError:
        final = ""
    result = {
        "final_status": final,
        "overrun_node_key": ran,
        "project_stopped": True,
        "text_ru": f"{where}: {what} завершена. {tail}.",
    }
    return JobState(done=True, ok=True, result=result)


async def _chain_outcome(ctx, params, started, state: JobState, early: bool) -> tuple[JobState, str]:
    """Цепочка нод встала раньше или упала: какая нода готова последней, на какой встали, почему.

    Возвращает итог опроса (упавшая в этом запуске нода цепочки делает его ошибкой) и текст
    места остановки для «до ноды … не дошла».
    """
    if not state.done or params.get("step_code") != "excel_gpt" or not params.get("node_key"):
        return state, ""
    try:
        proj = await _get_project(ctx, params["project_id"])
    except ActionCallError:
        return state, ""
    meta = (proj if isinstance(proj, dict) else {}).get("meta")
    scope = cs.chain_scope(gpt_chain(meta, params["node_key"]), params.get("stop_after_node_key"))
    if len(scope) < 2:
        return state, ""
    runs = await _node_runs(ctx, params["project_id"])
    if state.ok and not early:
        if not cs.failed_in_scope(scope, runs, started):
            return state, ""
        state = replace(state, ok=False, error="")
    if state.ok:
        return state, cs.early_text(scope, runs, started, meta)
    error = state.error or ""
    for head in so.NOT_DONE_HEADS:
        if error.startswith(head):
            error = error[len(head) :]
    text = cs.failure_text(scope, runs, started, meta, error)
    if text is None:
        return (state if state.error else replace(state, error="нода цепочки завершилась с ошибкой")), ""
    return replace(state, error=so.NOT_DONE_HEADS[0] + text), ""


async def _poll_any_step(ctx, params, started):
    if params["step_code"] == "excel_gpt" and params.get("node_key"):
        overrun = await _guard_stop_point(ctx, params, started)
        if overrun is not None:
            return overrun
        state = await so.honest_state(ctx, params, started, await _node_run_poll(ctx, params, started))
        skipped = await so.gpt_skipped(ctx, params, started) if state.done and state.ok else None
        if skipped:
            state = replace(state, ok=False, error=skipped)
    else:
        state = await w5._poll_step(ctx, params, started)
    early = state.done and await _disarm_stop_point(ctx, params)
    state, stuck = await _chain_outcome(ctx, params, started, state, early)
    if state.done:
        cs.forget(started)
    if not (state.done and state.ok):
        return await _named_failure(ctx, params, state)
    state = await _img_pr_hollow(ctx, params, started, state)
    if not state.ok:
        return state
    from app.services.action_layer.builtin_insight import _status_ru

    result = dict(state.result or {})
    where, what = await _step_names(ctx, params)
    text = f"{where}: " + (f"{what} завершена" if params.get("node_key") else f"{what} завершён")
    if early:
        stop = params["stop_after_node_key"]
        try:
            meta = (await _get_project(ctx, params["project_id"])).get("meta")
        except ActionCallError:
            meta = None
        text += f", но до ноды «{node_label_ru(meta, stop)}» цепочка не дошла"
        if stuck:
            text += f": {stuck}"
        result["stopped_early"] = True
    if result.get("final_status"):
        text += f", статус проекта: {_status_ru(str(result['final_status']))}"
    text += "."
    if params["step_code"] in ("sfx_gen", "assemble"):
        # Звуки могли молча уйти в локальный синтез: человек должен это услышать от чата.
        try:
            proj = await _get_project(ctx, params["project_id"])
        except ActionCallError:
            proj = {}
        proj = proj if isinstance(proj, dict) else {}
        meta = proj.get("meta") if isinstance(proj.get("meta"), dict) else {}
        sfx = sc.sfx_outcome_text(meta)
        if sfx and (params["step_code"] == "sfx_gen" or meta.get("sfx_degraded") or meta.get("sfx_skipped")):
            text += " " + sfx
            result["sfx_provider"] = meta.get("sfx_provider")
    result["text_ru"] = text
    return replace(state, result=result)


async def _step_names(ctx, params) -> tuple[str, str]:
    """Проект и шаг для строки итога: «#12 «Название»» и «Шаг «…»» или «Нода «…»»."""
    what = f"Шаг «{lr.step_title(params['step_code'])}»"
    try:
        proj = await _get_project(ctx, params["project_id"])
    except ActionCallError:
        proj = {}
    proj = proj if isinstance(proj, dict) else {}
    stop = params.get("stop_after_node_key")
    if params.get("node_key"):
        what = f"Нода «{node_label_ru(proj.get('meta'), params['node_key'])}»"
    if stop and stop != params.get("node_key"):
        what = (
            f"Цепочка нод от «{node_label_ru(proj.get('meta'), params['node_key'])}» "
            f"до «{node_label_ru(proj.get('meta'), stop)}»"
        )
    # Несколько проектов в одной задаче: без имени проекта строки ответа одинаковые.
    where = f"#{params['project_id']}" + (f" «{proj.get('title')}»" if proj.get("title") else "")
    return where, what


async def _named_failure(ctx, params, state):
    """Ошибка шага с проектом и шагом впереди: в одном плане бывает несколько проектов."""
    if not state.done or state.ok:
        return state
    where, what = await _step_names(ctx, params)
    error = state.error or "шаг завершился с ошибкой"
    for head in so.NOT_DONE_HEADS:
        if error.startswith(head):
            done_word = "не выполнена" if params.get("node_key") else "не выполнен"
            return replace(state, error=f"{where}: {what} {done_word}: {error[len(head) :]}")
    return replace(state, error=f"{where}: {what}: {error}")


def _any_step_effects(params: dict) -> str:
    title = lr.step_title(params["step_code"])
    text = (
        f"Запуск шага «{title}» убивает активную генерацию проекта, снимает стоп и паузу "
        "очереди и перезаписывает результат шага; платные вызовы начнутся сразу."
    )
    if params["step_code"] in lr.EARLY_STEPS:
        text += " Готовые ноды ниже по цепочке сбрасываются и при следующем прогоне оплачиваются заново."
    return text + " " + lr.STOP_HINT


async def _any_basis(ctx, params):
    code = params["step_code"]
    if code in sc.SOUND_STEPS:
        return sc.basis_json(await sc.load_basis(params["project_id"], code))
    if nrc.media_step_kind(code):
        return await w5._get_step_cost_basis(ctx, params)
    return {
        "step_code": code,
        "kind": "other",
        "estimable": False,
        "uncalibrated": True,
        "basis": unpriced_basis(code),
        "needs_ceiling": True,
        "hint": (
            "цена по коду неизвестна: для запуска укажите ceiling_credits - потолок, "
            "который вы готовы потратить на этот шаг"
        ),
    }


def _uniq_ints(name: str):
    async def check(ctx, params):
        ids = params.get(name) or []
        if len(set(ids)) != len(ids):
            return f"в списке {name} есть повторы"
        return None

    return Precondition(f"{name}_unique", "Номера проектов без повторов", check)


def _batch_titles(params: dict) -> list[str]:
    topics = [str(t).strip() for t in params.get("topics") or []]
    titles = [str(t).strip() for t in params.get("titles") or []]
    prefix = str(params.get("title_prefix") or "").strip()
    out = []
    for i, topic in enumerate(topics):
        title = titles[i] if i < len(titles) and titles[i] else topic[:120]
        out.append(f"{prefix} {title}".strip()[:200] if prefix else title)
    return out


async def _pre_batch(ctx, params):
    topics = [str(t).strip() for t in params.get("topics") or []]
    if any(not t for t in topics):
        return "есть пустая тема"
    if params.get("titles") is not None and len(params["titles"]) != len(topics):
        return "число названий не совпадает с числом тем"
    titles = _batch_titles(params)
    if len(set(t.lower() for t in titles)) != len(titles):
        return "названия проектов повторяются: задайте разные темы или названия"
    tpl = params.get("template_project_id")
    if tpl is not None:
        proj = await _get_project(ctx, tpl)
        meta = proj.get("meta") if isinstance(proj.get("meta"), dict) else {}
        if meta.get("mass_parent_id") is not None and not meta.get("project_child_manual"):
            return "шаблон - дочерний проект фабрики: выберите родительский проект"
    return None


def _item_ctx(ctx: ActionContext, index: int) -> ActionContext:
    if not ctx.idempotency_key:
        return ctx
    return ActionContext(
        app=ctx.app,
        operator=ctx.operator,
        plan_id=ctx.plan_id,
        step_index=ctx.step_index,
        idempotency_key=f"{ctx.idempotency_key}:{index}",
        read_only=ctx.read_only,
        progress_sink=ctx.progress_sink,
        _client=ctx._client,
    )


async def _create_batch(ctx, params):
    topics = [str(t).strip() for t in params["topics"]]
    titles = _batch_titles(params)
    tpl = params.get("template_project_id")
    # Образец копирует свои настройки - поверх только названные; иначе дефолты Studio.
    gen = pgs.explicit_settings(params) if tpl is not None else pgs.new_project_settings(params)
    settings = {**({"hero_mode": params["hero_mode"]} if "hero_mode" in params else {}), **gen}
    aid = gen.get("aspect_ratio")
    created: list[dict] = []
    for i, (topic, title) in enumerate(zip(topics, titles, strict=False)):
        sub = _item_ctx(ctx, i)
        try:
            if tpl is not None:
                child = await sub.call("POST", f"/projects/{tpl}/child")
                pid = (child or {}).get("id")
                data = await sub.call(
                    "PATCH", f"/projects/{pid}", json={"title": title, "topic": topic, **settings}
                )
            else:
                data = await sub.call("POST", "/projects", json={"title": title, "topic": topic, **settings})
                pid = (data or {}).get("id")
            if aid and pid is not None:
                await pgs.sync_prompt_aspect(sub, pid, (data or {}).get("prompt_overrides"), aid)
        except ActionCallError as exc:
            done = ", ".join(f"#{c['project_id']}" for c in created) or "ни одного"
            raise ActionCallError(
                exc.status,
                f"создано {len(created)} из {len(topics)} ({done}); на теме {i + 1} ошибка: {exc.detail}",
            ) from exc
        created.append(
            {
                "project_id": pid,
                "title": (data or {}).get("title") or title,
                "status": (data or {}).get("status"),
            }
        )
    return {
        "count": len(created),
        "projects": created,
        "template_project_id": tpl,
        "settings": sorted(settings),
    }


async def _load_projects(ctx, ids: list[int]) -> list[dict]:
    return [await _get_project(ctx, pid) for pid in ids]


def _node_type_of(proj: dict, node_key: str) -> str | None:
    meta = proj.get("meta") if isinstance(proj.get("meta"), dict) else {}
    for node in (meta.get("canvas_graph") or {}).get("nodes") or []:
        if node.get("id") == node_key:
            return node.get("type")
    return None


async def _pre_queue(ctx, params):
    from app.services.actions_v1 import managed_project_enabled

    want_auto = bool(params.get("enable_auto_mode"))
    for proj in await _load_projects(ctx, params["project_ids"]):
        pid = proj.get("id")
        meta = proj.get("meta") if isinstance(proj.get("meta"), dict) else {}
        if await managed_project_enabled(pid):
            return f"проект #{pid} под управляемым исполнением Actions-v1: очередь ему недоступна"
        if meta.get("mass_parent_id") is not None and not meta.get("project_child_manual"):
            return f"проект #{pid} - дочерний проект фабрики, очередь генерации его пропускает"
        if not proj.get("auto_mode") and not want_auto:
            return (
                f"у проекта #{pid} выключено автопродвижение, а очередь работает только с ним: "
                "включите его или укажите enable_auto_mode=true"
            )
        if not proj.get("auto_mode") and want_auto and _is_running(proj.get("status")):
            return (
                f"в проекте #{pid} выполняется шаг (статус {proj.get('status')}): включить "
                "автопродвижение можно только когда ничего не идёт"
            )
    if params.get("mode") == "until_node" and not (
        params.get("target_node_key") or params.get("target_node_type")
    ):
        return "для режима until_node укажите целевую ноду (target_node_key или target_node_type)"
    if params.get("mode") == "until_node" and params.get("target_node_key"):
        for proj in await _load_projects(ctx, params["project_ids"]):
            if not _node_type_of(proj, params["target_node_key"]):
                return f"в графе проекта #{proj.get('id')} нет ноды {params['target_node_key']}"
    return None


async def _pre_queue_img_pr_scenes(ctx, params):
    """Очередь не должна дойти до промтов картинок мимо ноды сцены (её она не повторяет)."""
    mode = params.get("mode", "full")
    for proj in await _load_projects(ctx, params["project_ids"]):
        pid = proj.get("id")
        meta = proj.get("meta")
        target_type = params.get("target_node_type")
        if params.get("target_node_key"):
            target_type = _node_type_of(proj, params["target_node_key"])
        runs = await _node_runs(ctx, pid)
        if not ipg.queue_skips_scene(
            meta, runs, mode=mode, target_key=params.get("target_node_key"), target_type=target_type
        ):
            continue
        reason = await ipg.scene_gap(pid, meta, runs)
        if reason:
            return reason
    return None


async def _queue_warnings(ctx, params) -> list[str]:
    out: list[str] = []
    stopped, auto_on = [], []
    for proj in await _load_projects(ctx, params["project_ids"]):
        if lr.user_stopped(proj.get("meta")):
            stopped.append(f"#{proj.get('id')}")
        if not proj.get("auto_mode") and params.get("enable_auto_mode"):
            auto_on.append(f"#{proj.get('id')}")
    if stopped:
        out.append(
            "Проекты остановлены человеком кнопкой «Стоп», постановка в очередь снимет "
            f"остановку: {', '.join(stopped)}."
        )
    if auto_on:
        out.append(
            "У проектов включится автопродвижение (воркер сам пойдёт по платной цепочке): "
            f"{', '.join(auto_on)}."
        )
    from app.services.action_layer.plan import member_view
    from app.services.runtime_streams import get_worker_max_parallel

    # Число — прямо из настройки (GET /runtime-streams по дороге сбрасывает слоты
    # очереди). Менять его может только админ: участнику это не предлагаем.
    try:
        limit = int(get_worker_max_parallel())
    except Exception:  # noqa: BLE001
        limit = None
    if limit is not None:
        out.append(
            f"Одновременно идут не больше {limit} проектов очереди"
            + (
                "; это общая настройка студии, её меняет админ."
                if member_view()
                else "; это число можно поменять просьбой в чате."
            )
        )
    return out


from app.services.action_layer.chat_caller import TASK_OPERATOR_RE as _TASK_OPERATOR_RE  # noqa: E402


async def _plan_creator(ctx) -> str:
    """Кто создал план шага (``ActionPlan.operator``): у чата это ``orchestrator:task-N``.

    Шаг с подтверждением человек одобряет сам, и исполнитель идёт от его имени, так что
    ``ctx.operator`` — человек; задачу чата видно только по создателю плана.
    """
    from app.services.action_layer.chat_caller import plan_creator

    return await plan_creator(getattr(ctx, "plan_id", None))


async def _chat_session_of(ctx) -> str | bool:
    """Сессия чата, чья задача ставит номер (оператор или создатель плана ``orchestrator:task-N``);
    иначе ``True``."""
    import re

    for who in (str(getattr(ctx, "operator", "") or ""), await _plan_creator(ctx)):
        found = re.fullmatch(_TASK_OPERATOR_RE, who)
        if not found:
            continue
        try:
            from app.orchestrator.agents.task_store import TaskStore

            session_id = (TaskStore().get(int(found.group(1))) or {}).get("session_id")
        except Exception:  # noqa: BLE001
            session_id = None
        if session_id:
            return str(session_id)
    return True


async def _enqueue_many(ctx, params):
    want_auto = bool(params.get("enable_auto_mode"))
    body_base = {"mode": params.get("mode", "full")}
    for key in ("target_node_key", "target_node_type"):
        if params.get(key):
            body_base[key] = params[key]
    done: list[dict] = []
    owner = await _chat_session_of(ctx)
    for pid in params["project_ids"]:
        body = dict(body_base)
        try:
            proj = await _get_project(ctx, pid)
            if body["mode"] == "until_node" and body.get("target_node_key"):
                # тип из графа проекта, а не догадка планировщика ("video" вместо "videos")
                body["target_node_type"] = _node_type_of(proj, body["target_node_key"])
            if want_auto:
                if not proj.get("auto_mode"):
                    got = await ctx.call("PATCH", f"/projects/{pid}", json={"auto_mode": True})
                    if not bool((got or {}).get("auto_mode")):
                        raise ActionCallError(409, f"у проекта #{pid} автопродвижение не включилось")
            # by_chat: аварийный стоп чата потом снимет с очереди только номера своей сессии
            data = await ctx.call(
                "POST",
                "/sidebar-layout/gen-queue/enqueue",
                json={"project_id": pid, **body, "by_chat": owner},
            )
        except ActionCallError as exc:
            ok = ", ".join(f"#{d['project_id']}" for d in done) or "ни один"
            raise ActionCallError(
                exc.status,
                f"в очередь поставлено {len(done)} из "
                f"{len(params['project_ids'])} ({ok}); на проекте "
                f"#{pid} ошибка: {exc.detail}",
            ) from exc
        done.append(
            {
                "project_id": pid,
                "position": (data or {}).get("position"),
                "already_queued": bool((data or {}).get("already_queued")),
            }
        )
    return {"count": len(done), "enqueued": done, "mode": body_base["mode"]}


def _queue_effects(params: dict) -> str:
    from app.services.action_layer.card_text import projects_ru

    n = len(params.get("project_ids") or [])
    one = n % 10 == 1 and n % 100 != 11
    if n == 1:
        where, whose = "в нём", "у этого проекта"
    else:
        where, whose = "в каждом", "у этих проектов"
    return (
        f"{projects_ru(n)} {'встанет' if one else 'встанут'} в очередь генерации: воркер сам "
        f"пройдёт конвейер {where} и потратит деньги; стоп человека {whose} снимается. "
        "Убрать проект из очереди - кнопкой очереди или кнопкой «Стоп задачи»."
    )


async def _dequeue_many(ctx, params):
    removed: list[int] = []
    for pid in params["project_ids"]:
        try:
            await ctx.call("POST", "/sidebar-layout/gen-queue/toggle", json={"project_id": pid})
        except ActionCallError as exc:
            if exc.status != 400:  # 400 - проекта и так нет в очереди
                ok = ", ".join(f"#{p}" for p in removed) or "ни один"
                raise ActionCallError(
                    exc.status,
                    f"из очереди убрано {len(removed)} ({ok}); на проекте #{pid} ошибка: {exc.detail}",
                ) from exc
            continue
        removed.append(pid)
    return {"count": len(removed), "removed": removed}


PARALLEL_LOWER_NOTE = "Уже идущие доиграют, новые будут ждать."


async def _set_parallel(ctx, params):
    # Уменьшение числа не прерывает уже идущие шаги: они доигрывают, лимит действует
    # только на новые старты (см. gen_queue_reconcile / gen_queue_tick).
    from app.services.action_layer.plan import refuse_admin_only
    from app.services.runtime_streams import get_worker_max_parallel

    # Настройка одна на всю студию: участнику — отказ, каким бы путём ни пришёл вызов.
    refuse_admin_only("queue.set_parallel", params)
    # Прежнее число — прямо из настройки. GET /runtime-streams считает занятых и
    # по дороге сбрасывает слоты очереди (gen_queue_reconcile): чтению побочный
    # эффект ни к чему.
    try:
        before = int(get_worker_max_parallel())
    except Exception:  # noqa: BLE001
        before = None
    data = await ctx.call("PATCH", "/runtime-streams", json={"worker_max_parallel": params["workers"]})
    got = (data or {}).get("worker_max_parallel")
    if got != params["workers"]:
        raise ActionCallError(409, f"параллель не изменилась (сейчас {got})")
    out = {"worker_max_parallel": got, "worker_busy": (data or {}).get("worker_busy"), "before": before}
    if isinstance(before, int) and isinstance(got, int) and got < before:
        out["lowered"] = True
        out["text_ru"] = PARALLEL_LOWER_NOTE
    return out


def _specs() -> list[ActionSpec]:
    S = ActionSpec
    codes = launch_step_codes()
    run_props = {
        "step_code": {"type": "string", "enum": codes},
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
        "sound_credits": sc.SOUND_CREDITS,
        "ceiling_credits": w5._CEILING,
        "node_key": _NODE_KEY,
        "preempt_running": {"type": "boolean", "description": "Разрешить прервать идущий в проекте шаг"},
        "allow_auto_chain": {
            "type": "boolean",
            "description": "Разрешить запуск при включённом автопродвижении (потолок - на всю цепочку)",
        },
        "stop_after_node_key": {
            **_NODE_KEY,
            "description": "Для excel_gpt: после этой ноды цепочка нод «Работа с "
            "GPT» по стрелкам останавливается («до отчёта», «до "
            "ноды X»; равна node_key - только эта нода)",
        },
    }
    batch_props = {
        "topics": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_BATCH,
            "items": {"type": "string", "minLength": 1, "maxLength": 2000},
        },
        "titles": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_BATCH,
            "items": {"type": "string", "maxLength": 200},
        },
        "title_prefix": {"type": "string", "maxLength": 60},
        "template_project_id": {
            "type": "integer",
            "minimum": 1,
            "description": "Проект-образец: граф, промты и настройки копируются",
        },
        "hero_mode": {"type": "string", "enum": w5._HERO_MODES},
        "image_generator": _GEN,
        "image_resolution": _IMG_RES,
        "video_generator": _GEN,
        "video_resolution": _VID_RES,
        "aspect_ratio": pgs.ASPECT_SCHEMA,
    }
    queue_props = {
        "project_ids": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_BATCH,
            "items": {"type": "integer", "minimum": 1},
        },
        "mode": {"type": "string", "enum": ["full", "until_node"]},
        "target_node_key": _NODE_KEY,
        "target_node_type": {"type": "string", "minLength": 1, "maxLength": 40, "pattern": r"^[a-z0-9_]+$"},
        "enable_auto_mode": {"type": "boolean"},
    }
    return [
        S(
            id="step.get_run_cost_basis",
            title_ru="Оценка стоимости запуска шага конвейера",
            description_ru="Для любого шага конвейера (кроме публикации): для картинок и видео - "
            "сколько генераций по доске, какие значения заявить в step.run_step и "
            "верхняя оценка в кредитах по каталогу Kie; озвучка - по символам "
            "ElevenLabs, звуки - по секундам плана (заявить sound_credits), музыка - "
            "трек Suno по каталогу Kie плюс запрос к модели (нужен потолок не ниже "
            "цены трека); готовая озвучка или музыка на диске и локальный синтез "
            "звуков бесплатны; сборка бесплатна; для текстовых шагов, героев и "
            "предметов оценки по коду нет - нужен потолок ceiling_credits. Говорит, "
            "чего не хватает шагу и будут ли звуки локальным синтезом. Оценка не "
            "откалибрована по балансу.",
            params_schema=_obj({"step_code": run_props["step_code"]}, ["step_code"]),
            executor=_any_basis,
            risk=RISK_READ,
            idempotent=True,
            preconditions=(_PRE_PROJECT,),
            tags=("step", "cost"),
        ),
        S(
            id="step.run_step",
            title_ru="Запустить шаг конвейера проекта",
            description_ru="Ручной запуск любого шага конвейера (режим resume), кроме публикации: "
            "план, сценарий, разбивка, агенты и сборка сцен, герои, предметы, работа с "
            "Excel и GPT (нужен node_key), промты, картинки, видео, озвучка, музыка, "
            "звуки, сборка. Убивает активную генерацию проекта и перезаписывает "
            "результат шага; план, сценарий и разбивка сбрасывают готовые ноды ниже. "
            "Картинки и видео оцениваются по каталогу Kie, озвучка и звуки - по "
            "sound_credits из step.get_run_cost_basis, для остальных платных шагов "
            "нужен потолок ceiling_credits; потолок и заявленные значения входят "
            "в хэш подтверждения. Идущий шаг прерывается только с preempt_running, "
            "при автопродвижении запуск только с allow_auto_chain и потолком на всю "
            "цепочку. После ноды «Работа с GPT» Студия сама идёт по стрелкам дальше "
            "по нодам GPT; stop_after_node_key - где остановиться. Только с "
            "подтверждением человека.",
            params_schema=_obj(run_props, ["step_code"]),
            executor=_run_any_step,
            poll=_poll_any_step,
            stop=_stop_project,
            poll_interval_s=3.0,
            risk=RISK_DESTRUCTIVE,
            spends_money=True,
            destructive=True,
            reversible=False,
            idempotent=False,
            estimator=estimate_any_step,
            destructive_effects=_any_step_effects,
            warnings=_step_warnings,
            preconditions=(
                _PRE_PROJECT,
                Precondition("has_frames", "Есть кадры, из которых делать шаг", _pre_frames),
                _PRE_NOT_MANAGED,
                Precondition(
                    "project_idle",
                    "В проекте не выполняется шаг (или явно разрешено его прервать)",
                    _pre_idle_or_preempt,
                ),
                _PRE_NO_MONTAGE,
                Precondition(
                    "no_auto_chain",
                    "Автопродвижение выключено (или явно разрешено с потолком на всю цепочку)",
                    _pre_chain,
                ),
                Precondition("step_runnable", "Шаг доступен при статусе проекта", _pre_runnable),
                Precondition(
                    "sound_inputs",
                    "Для озвучки, музыки и звуков есть текст, план и провайдер",
                    _pre_sound_inputs,
                ),
                Precondition(
                    "stop_point_in_chain",
                    "Точка остановки стоит в цепочке нод после запускаемой",
                    _pre_stop_point,
                ),
                Precondition(
                    "img_pr_scene_input",
                    "У кадров для промтов картинок есть данные сцены от ноды «Работа с GPT»",
                    _pre_img_pr_scenes,
                ),
                Precondition(
                    "step_cost_declared",
                    "Заявленные число, модель и потолок покрывают расчёт по проекту",
                    _pre_cost,
                ),
            ),
            tags=("step", "run"),
        ),
        S(
            id="project.create_batch",
            title_ru="Создать пакет проектов по списку тем",
            description_ru="По одному новому проекту на каждую тему (до 20). Можно задать "
            "проект-образец (граф, промты и настройки копируются как у дочернего "
            "проекта) и общие настройки: режим героя, модели и разрешения картинок и "
            "видео, формат кадра. Ничего не запускает и денег не тратит.",
            params_schema=_obj(batch_props, ["topics"], project=False),
            executor=_create_batch,
            risk=RISK_LOCAL_SAFE,
            idempotent=False,
            scope="global",
            future_spend_note="Модели и разрешения новых проектов определяют цену их будущих "
            "генераций; если на сервере включено «автопродвижение новых "
            "проектов», оно применится к каждому.",
            preconditions=(
                Precondition(
                    "batch_valid", "Темы не пустые, названия не повторяются, образец подходит", _pre_batch
                ),
                Precondition("gen_settings_valid", "Настройки генерации из списков Studio", pgs.pre_valid),
            ),
            tags=("project", "create", "batch"),
        ),
        S(
            id="queue.enqueue_many",
            title_ru="Поставить проекты в очередь генерации",
            description_ru="Ставит проекты в общую очередь генерации (в конец, по порядку списка): "
            "воркер сам пройдёт конвейер целиком (mode=full) или до заданной ноды "
            "(until_node), одновременно - не больше окна параллели. Очередь работает "
            "только с автопродвижением (enable_auto_mode=true включит его). Снимает "
            "стоп человека у этих проектов. Траты по коду не оцениваются; только с "
            "подтверждением человека.",
            params_schema=_obj(queue_props, ["project_ids"], project=False),
            executor=_enqueue_many,
            risk=RISK_DESTRUCTIVE,
            destructive=True,
            reversible=False,
            idempotent=True,
            scope="global",
            destructive_effects=_queue_effects,
            warnings=_queue_warnings,
            future_spend_note="После постановки в очередь воркер сам запускает платные шаги в "
            "каждом проекте; цену всей очереди код не определяет.",
            preconditions=(
                _uniq_ints("project_ids"),
                Precondition(
                    "queue_ready",
                    "Проекты существуют, не под Actions-v1, не "
                    "дети фабрики, автопродвижение есть или будет включено",
                    _pre_queue,
                ),
                Precondition(
                    "img_pr_scene_input",
                    "Очередь не дойдёт до промтов картинок по кадрам без данных сцены",
                    _pre_queue_img_pr_scenes,
                ),
            ),
            tags=("queue", "run", "batch"),
        ),
        S(
            id="queue.dequeue_many",
            title_ru="Убрать проекты из очереди генерации",
            description_ru="Убирает проекты из общей очереди генерации, как кнопка очереди в Студии: "
            "воркер перестаёт вести их сам, цель прогона очереди сбрасывается. Сами "
            "проекты, их шаги и результаты не трогает. Проекты не в очереди пропускает.",
            params_schema=_obj({"project_ids": queue_props["project_ids"]}, ["project_ids"], project=False),
            executor=_dequeue_many,
            risk=RISK_LOCAL_SAFE,
            idempotent=True,
            scope="global",
            future_spend_note="Тратит меньше: воркер больше не запускает сам платные шаги этих проектов.",
            preconditions=(_uniq_ints("project_ids"),),
            tags=("queue", "settings", "batch"),
        ),
        S(
            id="queue.set_parallel",
            title_ru="Сколько проектов очереди идут одновременно",
            description_ru=f"Окно параллели очереди генерации: от 1 до {MAX_PARALLEL} проектов "
            "одновременно (общая настройка). Сама ничего не запускает. Если число "
            "уменьшить, уже идущие шаги не прерываются: доигрывают, а новые ждут.",
            params_schema=_obj(
                {"workers": {"type": "integer", "minimum": 1, "maximum": MAX_PARALLEL}},
                ["workers"],
                project=False,
            ),
            executor=_set_parallel,
            risk=RISK_LOCAL_SAFE,
            idempotent=True,
            scope="global",
            future_spend_note="Больше проектов одновременно: траты идут быстрее, нагрузка на машину выше.",
            tags=("queue", "settings"),
        ),
    ]


def register_launch(registry: ActionRegistry) -> None:
    specs = _specs()
    for spec in specs:
        registry.unregister(spec.id)
    registry.register_many(specs)
