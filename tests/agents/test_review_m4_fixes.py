"""Замечания ревью m4 на линии port/today.

1. Цитата промта: только когда просили показать промт; смена модели (node.describe перед
   node.set_model) промт не цитирует; одинаковые тексты один раз; не больше 2 промтов по
   1500 знаков, про остальные - «ещё N промтов — спросите отдельно».
2. Превью кадров из xlsx: кэш на процесс по (проект, путь, mtime, размер), разбор в отдельном
   потоке, логи импорта в превью - DEBUG с пометкой, без «создан».
3. xlsx берётся, только когда у проекта нет ни одной строки кадра (архивные тоже в счёт).
4. «Продолжить» с enrich_N_ready: стрелки читаются от ноды, на которую указывает статус;
   «Работа с GPT» без номера слота делает правило неоднозначным, и оно не блокирует.
5. Карточка шага, которую не удалось собрать, не заменяет карточку критика; если платных шагов
   несколько, заблокированная карточка говорит, что у остальных будут свои карточки.
6. Имя модели, которой нет в каталоге, не длиннее 60 знаков.
Все вызовы модели — заглушки.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest
from loguru import logger

from app.orchestrator.agents import summarizer as sm
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import PlanStep, TaskStatus
from app.services import xlsx_preview_log as xpl
from app.services.action_layer import img_pr_guard as ipg
from app.services.action_layer import settings_diff as sd
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedCritic, ScriptedRouter

# --- 1. цитата промта -------------------------------------------------------------------------


def _describe(name: str, body: str, *, shown: int | None = None, total: int | None = None) -> sm.StepNote:
    tail = f"\n(показаны первые {shown} из {total} знаков)" if shown else ""
    text = (
        f"Нода «Работа с GPT» (Работа с GPT).\nПромт: «{name}» (общий для всех проектов).\n"
        f"Текст промта «{name}» (системный):\n{body}{tail}"
    )
    return sm.StepNote("node.describe", "Показать настройки ноды", ok=True, text=text)


_BODY = "Ты — главный режиссёр-постановщик сцен.\nОпиши план, ракурс, свет."


def _model_change_facts() -> sm.TaskFacts:
    """Смена модели у пяти нод: планировщик читает каждую через node.describe."""
    steps: list[sm.StepNote] = []
    for i in range(5):
        steps.append(_describe("cinematic_scene_director", _BODY * 40, shown=1500, total=5647))
        steps.append(
            sm.StepNote(
                "node.set_model", f"Сменить модель ноды {i}", ok=True, text="модель сменена", readonly=False
            )
        )
    return sm.TaskFacts(
        goal="смени модель у всех работ с гпт в проекте 7 на gpt-5", status="done", steps=steps
    )


def test_model_change_does_not_quote_prompt() -> None:
    facts = _model_change_facts()
    assert sm.prompt_quotes(facts) == ([], 0)
    assert sm.ensure_prompt_quoted("Модель сменена у 5 нод.", facts) == "Модель сменена у 5 нод."
    assert sm.PROMPT_QUOTE_RULE not in sm._facts_prompt(facts)[0]["content"]


def test_model_change_waiting_or_failed_still_no_quote() -> None:
    """node.set_model не прошёл: в задаче остались только чтения, но промт не просили."""
    facts = sm.TaskFacts(
        goal="смени модель у работы с гпт в проекте 7",
        status="done",
        steps=[
            _describe("p", _BODY),
            sm.StepNote("node.set_model", "Сменить модель", ok=False, error="нет модели", readonly=False),
        ],
    )
    assert sm.prompt_excerpts(facts) == []


def test_model_question_reads_node_but_does_not_quote_prompt() -> None:
    facts = sm.TaskFacts(
        goal="какая модель стоит у работы с гпт в проекте 7", status="done", steps=[_describe("p", _BODY)]
    )
    assert sm.prompt_excerpts(facts) == []
    system = sm._facts_prompt(facts)[0]["content"]
    assert sm.PROMPT_QUOTE_RULE not in system
    # Текст промта модели не отдаём: процитировать нечего.
    assert "главный режиссёр" not in system and "текст не показан" in system


def test_prompt_question_without_show_verb_but_only_describe_steps() -> None:
    facts = sm.TaskFacts(
        goal="промт первой работы с гпт в проекте 7", status="done", steps=[_describe("p", _BODY)]
    )
    (item,) = sm.prompt_excerpts(facts)
    assert item.body == _BODY


def test_prompt_change_request_is_not_a_quote_request() -> None:
    facts = sm.TaskFacts(
        goal="замени промт у работы с гпт в проекте 7", status="done", steps=[_describe("p", _BODY)]
    )
    assert sm.prompt_excerpts(facts) == []


def test_show_prompts_dedupes_caps_and_counts_the_rest() -> None:
    long_body = "\n".join(f"строка {i} " + "x" * 60 for i in range(80))  # ~5.6k знаков
    steps = [
        _describe("a", long_body, shown=len(long_body), total=9000),
        _describe("a", long_body, shown=len(long_body), total=9000),
        _describe("a", long_body, shown=len(long_body), total=9000),
        _describe("b", "второй промт"),
        _describe("c", "третий промт"),
        _describe("d", "четвёртый промт"),
    ]
    facts = sm.TaskFacts(goal="покажи промты всех работ с гпт в проекте 7", status="done", steps=steps)
    items, more = sm.prompt_quotes(facts)
    assert [i.head for i in items] == ["Текст промта «a» (системный)", "Текст промта «b» (системный)"]
    assert more == 2
    assert len(items[0].body) <= 1500 and long_body.startswith(items[0].body)
    assert items[0].tail == f"(показаны первые {len(items[0].body)} из 9000 знаков)"
    out = sm.ensure_prompt_quoted("Прочитал промты нод.", facts)
    assert out.count("> строка 0 ") == 1
    assert "> второй промт" in out and "третий промт" not in out
    assert out.endswith("Ещё 2 промта — спросите отдельно.")
    assert len(out) < 2 * 1500 + 600
    system = sm._facts_prompt(facts)[0]["content"]
    assert sm.PROMPT_QUOTE_RULE in system and "1500" in sm.PROMPT_QUOTE_RULE
    assert "«Ещё 2 промта — спросите отдельно.»" in system
    assert "тот же текст, что выше" in system and "четвёртый промт" not in system
    assert system.count("строка 0 ") == 1


def test_more_prompts_note_is_not_repeated_if_model_wrote_it() -> None:
    steps = [_describe(n, f"текст {n}") for n in "abc"]
    facts = sm.TaskFacts(goal="покажи промты", status="done", steps=steps)
    told = "> текст a\n> текст b\nЕщё 1 промт — спросите отдельно."
    assert sm.ensure_prompt_quoted(told, facts) == told


@pytest.mark.asyncio
async def test_summarize_quotes_at_most_two_prompts() -> None:
    class Router:
        async def call(self, *args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(text="Промты прочитаны.", approval=None)

    steps = [_describe(n, f"особый текст промта {n}") for n in "abcde"]
    facts = sm.TaskFacts(goal="покажи промты нод в проекте 7", status="done", steps=steps)
    text = await sm.TaskSummarizer(Router()).summarize(facts, run_id="r1", task_id=1)
    assert text.count("> особый текст промта") == 2
    assert "Ещё 3 промта — спросите отдельно." in text


# --- 2-3. превью кадров из xlsx -----------------------------------------------------------------


class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _Xlsx:
    def __init__(self, mtime: int = 1) -> None:
        self.mtime = mtime

    def is_file(self) -> bool:
        return True

    def stat(self) -> Any:
        return SimpleNamespace(st_mtime_ns=self.mtime, st_size=100)

    def __str__(self) -> str:
        return "C:/data/videos/p10/project.xlsx"


class _Dir:
    def __init__(self, xlsx: _Xlsx) -> None:
        self.xlsx = xlsx

    def __truediv__(self, name: str) -> Any:
        return self.xlsx


class _Session:
    def __init__(self, rows: list[Any], xlsx: _Xlsx) -> None:
        self.rows = rows
        self.xlsx = xlsx

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def scalars(self, _query: Any) -> _Scalars:
        return _Scalars(list(self.rows))

    async def get(self, _model: Any, pid: int) -> Any:
        return SimpleNamespace(id=pid, data_dir=_Dir(self.xlsx))

    async def rollback(self) -> None:
        self.rows.clear()


def _frame(number: int, **kw: Any) -> SimpleNamespace:
    base = {
        "number": number,
        "uuid": f"u{number}",
        "voiceover_text": f"закадр {number}",
        "meaning": None,
        "image_prompt": None,
        "attrs": {},
    }
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _fresh_preview_cache() -> Any:
    ipg.clear_preview_cache()
    yield
    ipg.clear_preview_cache()


def _patch_db(monkeypatch: pytest.MonkeyPatch, db_rows: list[Any], xlsx: _Xlsx) -> None:
    from app.services.action_layer import builtin_insight as bi

    monkeypatch.setattr(bi, "_sessions", lambda: lambda: _Session(list(db_rows), xlsx))


@pytest.mark.asyncio
async def test_preview_is_cached_and_runs_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    xlsx = _Xlsx()
    _patch_db(monkeypatch, [], xlsx)
    calls: list[int] = []
    main = threading.get_ident()

    async def fake_import(session: _Session, project: Any, path: Any) -> bool:
        calls.append(threading.get_ident())
        session.rows.extend([_frame(2), _frame(1)])
        return True

    monkeypatch.setattr(ipg, "_import_xlsx_rolled_back", fake_import)
    first = await ipg.load_live_frames(10)
    second = await ipg.load_live_frames(10)
    assert [f.number for f in first] == [1, 2] == [f.number for f in second]
    assert len(calls) == 1 and calls[0] != main
    # Файл поменялся: разбираем заново.
    xlsx.mtime = 2
    await ipg.load_live_frames(10)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_failed_preview_is_cached_too(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_db(monkeypatch, [], _Xlsx())
    calls: list[int] = []

    async def broken(*_a: Any) -> bool:
        calls.append(1)
        raise ValueError("битый xlsx")

    monkeypatch.setattr(ipg, "_import_xlsx_rolled_back", broken)
    assert await ipg.load_live_frames(10) == []
    assert await ipg.load_live_frames(10) == []
    assert calls == [1]


@pytest.mark.asyncio
async def test_all_archived_frames_do_not_fall_back_to_xlsx(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_frame(1_000_001), _frame(2, attrs={"archived": True})]
    _patch_db(monkeypatch, rows, _Xlsx())

    async def boom(*_a: Any) -> bool:
        raise AssertionError("кадры в базе есть (в архиве): xlsx не читаем")

    monkeypatch.setattr(ipg, "_import_xlsx_rolled_back", boom)
    assert await ipg.load_live_frames(10) == []


def test_preview_logs_are_debug_and_tagged() -> None:
    records: list[Any] = []
    sink = logger.add(lambda m: records.append(m.record), level="DEBUG")
    try:
        xpl.log_info("[#{}] xlsx→DB: создан Frame {}", 10, 3)
        with xpl.preview_logs():
            xpl.log_info("[#{}] xlsx→DB: создан Frame {}", 10, 3)
            xpl.log_info("[#{}] xlsx-v8→DB: создано {} Frame'ов", 10, 5)
        assert not xpl.in_preview()
    finally:
        logger.remove(sink)
    real, preview, many = records
    assert real["level"].name == "INFO" and real["message"] == "[#10] xlsx→DB: создан Frame 3"
    assert preview["level"].name == "DEBUG" and preview["message"].startswith(xpl.PREVIEW_TAG)
    assert "создан" not in preview["message"] and "создан" not in many["message"]
    # Имя вызывающего модуля сохраняется (depth=1).
    assert real["name"] == __name__


def test_preview_thread_marks_logs_as_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    async def fake(_pid: int) -> list[Any]:
        seen.append(xpl.in_preview())
        return []

    monkeypatch.setattr(ipg, "_preview_rolled_back", fake)
    assert ipg._preview_in_thread(10) == []
    assert seen == [True] and not xpl.in_preview()


# --- 4. «Продолжить» с enrich_N_ready -----------------------------------------------------------


def _node(key: str, typ: str, slot: int | None = None) -> dict[str, Any]:
    return {"id": key, "type": typ, "data": {"slotIndex": slot} if slot is not None else {}}


def _graph_meta(nodes: list[dict[str, Any]], edges: list[tuple[str, str]], **extra: Any) -> dict[str, Any]:
    return {
        "canvas_graph": {"nodes": nodes, "edges": [{"source": a, "target": b} for a, b in edges]},
        **extra,
    }


def test_unnumbered_gpt_node_does_not_block_continue_into_hero() -> None:
    """Слот 1 ведёт в героев; «Работа с GPT» без номера (тоже «слот 1») - в промты картинок."""
    meta = _graph_meta(
        [
            _node("g1", "excel_gpt", 1),
            _node("hero", "hero"),
            _node("gx", "excel_gpt"),
            _node("ip", "image_prompts"),
        ],
        [("g1", "hero"), ("hero", "gx"), ("gx", "ip")],
    )
    assert not ipg._ready_slot_leads_to_img_pr(meta, "enrich_1_ready")


def test_single_unnumbered_gpt_node_is_ambiguous() -> None:
    meta = _graph_meta([_node("gx", "excel_gpt"), _node("ip", "image_prompts")], [("gx", "ip")])
    assert not ipg._ready_slot_leads_to_img_pr(meta, "enrich_1_ready")


def test_numbered_slot_still_follows_its_arrows() -> None:
    meta = _graph_meta(
        [
            _node("g1", "excel_gpt", 1),
            _node("hero", "hero"),
            _node("g2", "excel_gpt", 2),
            _node("ip", "image_prompts"),
        ],
        [("g1", "hero"), ("hero", "g2"), ("g2", "ip")],
    )
    assert ipg._ready_slot_leads_to_img_pr(meta, "enrich_2_ready")
    assert not ipg._ready_slot_leads_to_img_pr(meta, "enrich_1_ready")


def test_active_node_resolves_slot_collision() -> None:
    nodes = [
        _node("g2a", "excel_gpt", 2),
        _node("hero", "hero"),
        _node("g2b", "excel_gpt", 2),
        _node("ip", "image_prompts"),
    ]
    edges = [("g2a", "hero"), ("hero", "g2b"), ("g2b", "ip")]
    assert not ipg._ready_slot_leads_to_img_pr(_graph_meta(nodes, edges), "enrich_2_ready")
    assert ipg._ready_slot_leads_to_img_pr(
        _graph_meta(nodes, edges, active_excel_gpt_node_key="g2b"), "enrich_2_ready"
    )
    assert not ipg._ready_slot_leads_to_img_pr(
        _graph_meta(nodes, edges, active_excel_gpt_node_key="g2a"), "enrich_2_ready"
    )


# --- 5. карточка шага вместо карточки критика -------------------------------------------------

_META_ACTIONS = {
    "project.status": {"risk": "none", "read_only": True, "destructive": False, "paid": False},
    "project.continue_pipeline": {"risk": "high", "read_only": False, "destructive": False, "paid": True},
    "project.render_final": {"risk": "high", "read_only": False, "destructive": False, "paid": True},
}
_SCENE = "шаг 1: условие img_pr_scene_input: у проекта #10 кадры 1, 2, 3 без данных сцены"
_CRITIC_WHY = "Запуск картинок запрещён, когда в проекте нет кадров"


class StepActions(MockActions):
    def __init__(self, card: dict[str, Any]) -> None:
        super().__init__()
        self.card = card

    def metadata_of(self, name: str) -> dict[str, Any]:
        if name not in _META_ACTIONS:
            raise KeyError(name)
        return {"name": name, **_META_ACTIONS[name]}

    async def prepare_card(self, name: str, params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        return dict(self.card) if name == "project.continue_pipeline" else {}


class RejectingCritic(ScriptedCritic):
    last_reason = _CRITIC_WHY


def _step(action: str) -> PlanStep:
    return PlanStep(action=action, params={"project_id": 10}, paid=True, mutating=True, risk="high")


def _cards(rt: Any, session: str) -> list[dict[str, Any]]:
    return [dict(e.payload) for e in rt.bus.replay(session, 0) if e.type == "approval_required"]


async def _run(actions: StepActions, plan: list[PlanStep], session: str) -> tuple[Any, int]:
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(plan),
        critic=RejectingCritic(["reject"]),
    )
    set_runtime(rt)
    task_id = await rt.supervisor.start_task(session, "продолжи проект 10 и собери финал", project_id=10)
    await rt.supervisor.wait(task_id, timeout=5)
    return rt, task_id


@pytest.mark.asyncio
async def test_card_build_failure_keeps_critic_card(token: str) -> None:
    del token
    failed = {
        "preview_errors": ["не удалось посчитать оценку и проверить условия шага"],
        "blocked": True,
        "preview_failed": True,
    }
    actions = StepActions(failed)
    try:
        rt, _task_id = await _run(actions, [_step("project.continue_pipeline")], "rv-m4-build-failed")
        cards = _cards(rt, "rv-m4-build-failed")
        assert cards and cards[-1]["reason"] == "critic_reject"
        assert not cards[-1].get("blocked")
        assert actions.calls == []
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_blocked_card_mentions_other_paid_steps(token: str) -> None:
    del token
    actions = StepActions({"blocked": True, "preview_errors": [_SCENE], "warnings": []})
    plan = [_step("project.continue_pipeline"), _step("project.render_final")]
    try:
        rt, task_id = await _run(actions, plan, "rv-m4-two-paid")
        (card,) = _cards(rt, "rv-m4-two-paid")
        assert card["blocked"] is True and card["action"] == "project.continue_pipeline"
        assert f"Проверяющая модель против этого плана: {_CRITIC_WHY}" in card["warnings"]
        assert any("Это не весь план" in w and "(ещё 1)" in w for w in card["warnings"])
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert actions.calls == []
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_single_paid_step_card_has_no_other_steps_note(token: str) -> None:
    del token
    actions = StepActions({"blocked": True, "preview_errors": [_SCENE], "warnings": []})
    try:
        rt, _task_id = await _run(actions, [_step("project.continue_pipeline")], "rv-m4-one-paid")
        (card,) = _cards(rt, "rv-m4-one-paid")
        assert not any("Это не весь план" in w for w in card["warnings"])
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_bridge_marks_failed_card_build(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import orch_bridge as ob

    spec = SimpleNamespace(
        id="project.continue_pipeline", title_ru="Продолжить", risk="high", destructive_effects=None
    )
    monkeypatch.setattr(ob, "_spec", lambda _name: spec)
    monkeypatch.setattr(ob, "plan_required", lambda _id: True)

    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("preview down")

    monkeypatch.setattr(ob.service, "create_plan", boom)
    card = await ob.prepare_card("project.continue_pipeline", {"project_id": 10}, {})
    assert card["blocked"] is True and card["preview_failed"] is True


# --- 6. имя модели не из каталога -----------------------------------------------------------------


def test_unknown_model_label_is_capped() -> None:
    label = sd.unknown_model_ru("_".join(["verylongword"] * 20))
    name = label.removesuffix(" (нет в каталоге)")
    assert len(name) <= 60 and name.endswith("…")
    assert sd.unknown_model_ru("gpt_image_2_vip") == "GPT Image 2 VIP (нет в каталоге)"
