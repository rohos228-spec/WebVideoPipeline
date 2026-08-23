"""Агент студии: что он может, чего не может и где его останавливают.

Проверяется не «умеет ли модель разговаривать» — это не проверить тестом, —
а границы, поставленные вокруг неё. Их три, и все три из спеки:

* §8.2 — агент дирижёр, а не автор конвейера: порядок шагов задан системой;
* §7.3 — дороже одного кредита выполняется только с явного согласия;
* деньги — петля конечна, потому что каждый виток это платный вызов.

Модель здесь всегда подменена: настоящая стоила бы денег и отвечала бы каждый
раз иначе, а проверяются рамки, а не её сообразительность.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameStatus, Project, ProjectStatus
from app.services.studio_agent import TOOLS, ToolError, call_tool, tool_manifest
from app.services.studio_agent.loop import build_system_prompt, collect_turn
from app.settings import settings


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "allow_unisolated_tenants", True)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agent.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", factory)
    async with factory() as s:
        p = Project(slug="agent-test", topic="агент", status=ProjectStatus.frames_ready)
        s.add(p)
        await s.flush()
        for n in range(1, 25):
            s.add(
                Frame(
                    project_id=p.id,
                    number=n,
                    voiceover_text="а" * 40,
                    image_prompt=f"кадр {n}",
                    status=FrameStatus.image_prompt_ready,
                )
            )
        await s.commit()
    yield factory
    await engine.dispose()


def _scripted(*replies: str):
    """Модель, отвечающая заранее заданным. Последний ответ повторяется."""
    queue = list(replies)

    async def _ask(prompt, system, history):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return _ask


def test_manifest_and_registry_cannot_diverge() -> None:
    """Описание для модели и реестр обработчиков — один источник.

    Инструмент, попавший в промт и не имеющий обработчика, выглядит для
    модели рабочим: она его позовёт, получит отказ и попробует ещё раз.
    """
    names = {t["name"] for t in tool_manifest()}
    assert names == set(TOOLS)
    prompt = build_system_prompt()
    for name in names:
        assert name in prompt, f"{name} не попал в системный промт"


async def test_unknown_tool_gets_a_usable_error(db) -> None:
    """Ошибка написана так, чтобы по ней можно было исправиться."""
    async with db() as s:
        with pytest.raises(ToolError, match="не существует"):
            await call_tool(s, "делайКрасиво", {})


async def test_storyboard_reads_the_feed(db) -> None:
    async with db() as s:
        result = await call_tool(s, "showStoryboard", {"project_id": 1})
    assert len(result["frames"]) == 24
    assert result["frames"][0]["number"] == 1
    assert result["frames"][0]["image_prompt"] == "кадр 1"


async def test_expensive_step_is_not_started_without_consent(db) -> None:
    """§7.3: дороже одного кредита — сначала спроси.

    Порог живёт в кассе, а не в интерфейсе: интерфейсов будет несколько
    (чат, канвас, однажды мобильный), а цена одна. Проверка в интерфейсе
    означала бы, что каждый следующий интерфейс должен вспомнить про неё.

    Проект доведён до `animation_prompts_ready`: до него шаг «Видео» просто
    недостижим, и тест проверял бы не порог, а порядок шагов.
    """
    async with db() as s:
        project = await s.get(Project, 1)
        project.status = ProjectStatus.animation_prompts_ready
        await s.commit()
    async with db() as s:
        result = await call_tool(s, "runStep", {"project_id": 1, "step_code": "video"})
    assert result["needs_confirmation"] is True
    assert result.get("started") is None
    assert "confirm=true" in result["reason"]


async def test_cheap_step_needs_no_ceremony(db) -> None:
    """Обратная сторона порога: спрашивать про копейки — приучать жать «да».

    Подтверждение, которое спрашивают всегда, перестают читать, и тогда оно
    не сработает там, где действительно дорого.
    """
    async with db() as s:
        result = await call_tool(s, "runStep", {"project_id": 1, "step_code": "plan"})
    assert result.get("needs_confirmation") is None
    assert result["started"] is True


async def test_agent_cannot_invent_a_step(db) -> None:
    """§8.2: порядок задан конвейером, а не сообразительностью модели."""
    async with db() as s:
        with pytest.raises(ToolError, match="в конвейере нет"):
            await call_tool(s, "runStep", {"project_id": 1, "step_code": "сделатьШедевр"})


async def test_agent_cannot_skip_ahead(db) -> None:
    """Существующий, но недостижимый шаг — тоже отказ.

    Это и есть §8.2 целиком: агент не строит конвейер. Проект на
    `frames_ready` не может прыгнуть в монтаж, как бы убедительно модель ни
    объяснила, что так быстрее. Оператору у своего канваса это можно —
    агенту нет, и разница здесь не в вежливости, а в том, что за оператора
    отвечает оператор.
    """
    async with db() as s:
        with pytest.raises(ToolError, match="недоступен"):
            await call_tool(s, "runStep", {"project_id": 1, "step_code": "video", "confirm": True})


async def test_frame_edit_and_regenerate_are_two_actions(db) -> None:
    """Правка промта не перерисовывает: это отдельное решение и отдельные деньги.

    Слить их в одно действие значит списывать за каждую опечатку в
    редакторе.
    """
    async with db() as s:
        await call_tool(s, "editFramePrompt", {"project_id": 1, "frame_number": 3, "image_prompt": "новый"})
    async with db() as s:
        frames = (await call_tool(s, "showStoryboard", {"project_id": 1}))["frames"]
        assert frames[2]["image_prompt"] == "новый"
        assert frames[2]["status"] == "image_prompt_ready"

    async with db() as s:
        result = await call_tool(s, "regenerateFrame", {"project_id": 1, "frame_number": 3, "what": "video"})
    assert result["reset_to"] == "animation_prompt_ready"


async def test_loop_runs_tool_then_answers(db) -> None:
    """Виток целиком: модель просит инструмент, получает результат, отвечает."""
    ask = _scripted(
        json.dumps({"tool": "showStoryboard", "args": {"project_id": 1}}),
        json.dumps({"say": "В раскадровке 24 кадра."}),
    )
    async with db() as s:
        turn = await collect_turn(s, "покажи раскадровку", ask=ask)
    kinds = [e.type for e in turn.events]
    assert kinds == ["tool_call", "tool_result", "message"]
    assert turn.reply == "В раскадровке 24 кадра."


async def test_tool_error_goes_back_to_the_model(db) -> None:
    """Модель получает свою ошибку и может исправиться сама.

    Отдать её человеку значило бы показать ему внутреннее имя инструмента
    вместо ответа на вопрос.
    """
    ask = _scripted(
        json.dumps({"tool": "runStep", "args": {"project_id": 1, "step_code": "нетТакого"}}),
        json.dumps({"say": "Такого шага нет, давай посмотрим, что доступно."}),
    )
    async with db() as s:
        turn = await collect_turn(s, "запусти", ask=ask)
    assert [e.type for e in turn.events] == ["tool_call", "tool_error", "message"]
    assert "нет" in turn.reply.lower()


async def test_prose_instead_of_json_is_treated_as_an_answer(db) -> None:
    """Модель, ответившая прозой, чаще всего просто ответила человеку.

    Ронять разговор на манере провайдера значит наказывать пользователя за
    выбор поставщика.
    """
    async with db() as s:
        turn = await collect_turn(s, "привет", ask=_scripted("Привет! Чем помочь?"))
    assert turn.reply == "Привет! Чем помочь?"


async def test_thinking_models_do_not_break_the_contract(db) -> None:
    """`<think>` прямо в тексте ответа — штатное поведение части провайдеров.

    Канонический экстрактор достаёт сбалансированный объект из болтовни,
    поэтому JSON находится и здесь.
    """
    noisy = '<think>надо посмотреть кадры</think>\n{"tool": "showStoryboard", "args": {"project_id": 1}}'
    ask = _scripted(noisy, json.dumps({"say": "24 кадра."}))
    async with db() as s:
        turn = await collect_turn(s, "кадры", ask=ask)
    assert [e.type for e in turn.events] == ["tool_call", "tool_result", "message"]


async def test_looping_model_is_stopped_and_says_so(db) -> None:
    """Петля конечна, и предел — про деньги, а не про аккуратность.

    Каждый виток это платный вызов модели. Зациклившаяся на одном
    инструменте потратит баланс, не сделав ничего, и человек не узнает
    почему — поэтому предел ещё и объясняется вслух.
    """
    ask = _scripted(json.dumps({"tool": "showStoryboard", "args": {"project_id": 1}}))
    async with db() as s:
        turn = await collect_turn(s, "зациклись", ask=ask)
    assert turn.events[-1].type == "limit"
    assert len([e for e in turn.events if e.type == "tool_call"]) == 8
    assert turn.reply, "предел без объяснения — молчаливый обрыв"


async def test_balance_tool_works_for_a_tenant(db) -> None:
    """`showBalance` отдаёт то же, что и ручка баланса: один источник цифр."""
    from app.services import credit_ledger as cl
    from app.services.tenant import tenant_scope

    tenant = str(uuid.uuid4())
    async with db() as s:
        await cl.topup(s, tenant, 7 * 10**6, memo="пакет")
        await s.commit()

    with tenant_scope(tenant):
        async with db() as s:
            result = await call_tool(s, "showBalance", {})
    assert result["balance_micro"] == 7 * 10**6
    assert result["entries"][0]["memo"] == "пакет"


async def test_chat_endpoint_streams_events(db, monkeypatch) -> None:
    """Ручка отдаёт события по мере появления, а не одним куском в конце.

    Шаг конвейера идёт минутами. Ответ, собранный целиком и отданный
    последним, означает для человека пустой экран всё это время — то есть
    «сломалось». Поэтому проверяется именно кадрирование SSE: вызов
    инструмента должен приехать отдельным событием раньше реплики.
    """
    from httpx import ASGITransport, AsyncClient

    from app.web.api import create_app
    from app.web.deps import get_session

    ask = _scripted(
        json.dumps({"tool": "showStoryboard", "args": {"project_id": 1}}),
        json.dumps({"say": "Двадцать четыре кадра."}),
    )
    monkeypatch.setattr("app.services.studio_agent.loop._default_ask", ask)

    async def _gen():
        async with db() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        res = await c.post("/api/chat", json={"message": "покажи кадры", "history": []})
        body = res.text

    assert res.status_code == 200
    assert "event: tool_call" in body
    assert "event: tool_result" in body
    assert "event: message" in body
    assert "event: done" in body
    # Порядок важнее наличия: реплика после инструмента, а не наоборот.
    assert body.index("event: tool_call") < body.index("event: message")


async def test_chat_tools_endpoint_matches_registry(db) -> None:
    """Клиент узнаёт набор инструментов оттуда же, откуда его узнаёт модель."""
    from httpx import ASGITransport, AsyncClient

    from app.web.api import create_app
    from app.web.deps import get_session

    async def _gen():
        async with db() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        tools = (await c.get("/api/chat/tools")).json()
    assert {t["name"] for t in tools} == set(TOOLS)
