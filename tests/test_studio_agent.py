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

    async def _ask(messages, system, tools):
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
    assert kinds == ["tool_call", "tool_result", "history", "message", "history"]
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
    assert [e.type for e in turn.events] == ["tool_call", "tool_error", "history", "message", "history"]
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
    assert [e.type for e in turn.events] == ["tool_call", "tool_result", "history", "message", "history"]


async def test_looping_model_is_stopped_and_says_so(db) -> None:
    """Петля конечна, и предел — про деньги, а не про аккуратность.

    Каждый виток это платный вызов модели. Зациклившаяся на одном
    инструменте потратит баланс, не сделав ничего, и человек не узнает
    почему — поэтому предел ещё и объясняется вслух.
    """
    ask = _scripted(json.dumps({"tool": "showStoryboard", "args": {"project_id": 1}}))
    async with db() as s:
        turn = await collect_turn(s, "зациклись", ask=ask)
    assert [e.type for e in turn.events][-2:] == ["limit", "history"]
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
    assert "event: history" in body
    assert "event: done" in body
    assert body.index("event: history") < body.index("event: done")
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


async def test_tool_can_be_called_without_the_model(db) -> None:
    """Кнопка «подтверждаю» идёт прямо в инструмент, а не пересказом модели.

    Согласие человека на списание обязано доезжать до кассы буквой: пересказ
    даёт модели шанс понять его иначе, а цена ошибки здесь — деньги клиента.
    """
    from httpx import ASGITransport, AsyncClient

    from app.models import Project, ProjectStatus
    from app.web.api import create_app
    from app.web.deps import get_session

    async with db() as s:
        project = await s.get(Project, 1)
        project.status = ProjectStatus.animation_prompts_ready
        await s.commit()

    async def _gen():
        async with db() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        without = await c.post(
            "/api/chat/tools/runStep", json={"args": {"project_id": 1, "step_code": "video"}}
        )
        assert without.json()["needs_confirmation"] is True

        with_confirm = await c.post(
            "/api/chat/tools/runStep",
            json={"args": {"project_id": 1, "step_code": "video", "confirm": True}},
        )
        assert with_confirm.json()["started"] is True


async def test_direct_call_adds_no_privileges(db) -> None:
    """Прав кнопка не добавляет: инструменты держат свои правила сами.

    Иначе кнопка стала бы обходным путём мимо порядка шагов — тем самым,
    который §8.2 запрещает агенту.
    """
    from httpx import ASGITransport, AsyncClient

    from app.web.api import create_app
    from app.web.deps import get_session

    async def _gen():
        async with db() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        res = await c.post(
            "/api/chat/tools/runStep",
            json={"args": {"project_id": 1, "step_code": "assemble", "confirm": True}},
        )
    assert res.status_code == 400
    assert "недоступен" in res.json()["detail"]


async def test_resolution_chosen_at_the_video_step_is_saved(db) -> None:
    """Разрешение выбирается на шаге генерации и сохраняется ДО сметы.

    Решение владельца: выбор делается там, где нажимают кнопку, а не один раз
    на проект. Сохранять его после сметы значило бы показать человеку цену
    одного разрешения, а списать по другому.
    """
    from app.models import Project, ProjectStatus

    async with db() as s:
        project = await s.get(Project, 1)
        project.status = ProjectStatus.animation_prompts_ready
        await s.commit()

    async with db() as s:
        result = await call_tool(
            s,
            "runStep",
            {"project_id": 1, "step_code": "video", "resolution": "720p", "confirm": True},
        )
    assert result["started"] is True
    assert result["resolution"] == "720p"

    async with db() as s:
        project = await s.get(Project, 1)
        assert project.video_resolution == "720p"


async def test_unknown_resolution_never_reaches_the_step(db) -> None:
    """Неизвестное разрешение — отказ, а не тихий откат к цене по умолчанию."""
    from app.models import Project, ProjectStatus

    async with db() as s:
        project = await s.get(Project, 1)
        project.status = ProjectStatus.animation_prompts_ready
        await s.commit()

    async with db() as s:
        with pytest.raises(ToolError, match="неизвестно"):
            await call_tool(
                s,
                "runStep",
                {"project_id": 1, "step_code": "video", "resolution": "4k", "confirm": True},
            )


async def test_video_options_tool_shows_both_prices(db, monkeypatch) -> None:
    """Агент обязан показать актуальную цену, а не цифру из головы.

    Живой шлюз отдает только 720p — опция одна с честной ценой; лишних
    «дешевых» вариантов быть не должно.
    """
    monkeypatch.setattr(
        "app.services.vibecode_catalog.effective_video_generator_id",
        lambda project, node_type=None: "hailuo_2_3_fast",
    )
    monkeypatch.setattr("app.services.media_route.video_provider_for", lambda slug: "minimax")

    async with db() as s:
        result = await call_tool(s, "videoOptions", {"project_id": 1})
    prices = {o["id"]: o["price_credits"] for o in result["options"]}
    assert prices == {"720p": "13.68"}


# ── честность реплики: обещание без действия, контекст ролика ─────────────


async def test_promise_without_action_goes_back_to_the_model(db) -> None:
    """«Запускаю» в say без вызова инструмента — не ответ, а обрыв.

    say завершает ход; модель, написавшая «запускаю» и остановившаяся, ничего
    не запустила. Ей один раз возвращают это как наблюдение — и человеку
    уходит уже честная реплика.
    """
    seen: list[list[dict]] = []

    async def _ask(messages, system, tools):
        seen.append(list(messages))
        if len(seen) == 1:
            return json.dumps({"say": "Запускаю сценарий, вернусь с планом."})
        return json.dumps({"say": "Сценарий стоит 0,03 кр. Запустить?"})

    async with db() as s:
        turn = await collect_turn(s, "давай", ask=_ask, project_id=1)
    assert [e.type for e in turn.events] == ["message", "history"]
    assert turn.reply == "Сценарий стоит 0,03 кр. Запустить?"
    assert any("не вызвал" in str(m["content"]) for m in seen[1])


async def test_promise_reminder_is_sent_once(db) -> None:
    """Упрямая модель получает напоминание один раз — дальше её слово уходит как есть.

    Второй и третий круг тратили бы деньги на тот же ответ.
    """
    ask = _scripted(json.dumps({"say": "Запускаю."}))
    async with db() as s:
        turn = await collect_turn(s, "давай", ask=ask, project_id=1)
    assert [e.type for e in turn.events] == ["message", "history"]
    assert turn.reply == "Запускаю."


async def test_promise_after_a_real_action_passes(db, monkeypatch) -> None:
    """После настоящего действия «запускаю» — правда, и напоминание не нужно."""
    from app.services.studio_agent import loop as loop_mod
    from app.services.studio_agent import tools as tools_mod

    async def _fake(session, args):
        return {"ok": True}

    monkeypatch.setitem(tools_mod.TOOLS, "doThing", tools_mod.TOOLS["showBalance"])
    monkeypatch.setitem(tools_mod._HANDLERS, "doThing", _fake)
    monkeypatch.setattr(loop_mod, "ACTION_TOOLS", frozenset({"doThing"}))
    ask = _scripted(
        json.dumps({"tool": "doThing", "args": {}}),
        json.dumps({"say": "Запускаю, шаг пошёл."}),
    )
    async with db() as s:
        turn = await collect_turn(s, "запусти", ask=ask, project_id=1)
    assert [e.type for e in turn.events] == ["tool_call", "tool_result", "history", "message", "history"]
    assert turn.reply == "Запускаю, шаг пошёл."


async def test_open_project_context_carries_title_and_topic(db) -> None:
    """Модель знает, о чём ролик, из контекста — иначе выдумывает название."""
    seen: list[str] = []

    async def _ask(messages, system, tools):
        seen.append(system)
        assert messages[-1] == {"role": "user", "content": "о чём ролик?"}
        return json.dumps({"say": "ок"})

    async with db() as s:
        p = await s.get(Project, 1)
        p.title = "Ночной обмен"
        await s.commit()
        await collect_turn(s, "о чём ролик?", ask=_ask, project_id=1)
    assert "КОНТЕКСТ. открыт проект #1" in seen[0]
    assert "название: «Ночной обмен»" in seen[0]
    assert "идея: агент" in seen[0]
    assert "project_id для инструментов — 1" in seen[0]


async def test_show_stages_carries_title_and_topic(db) -> None:
    """showStages отдаёт название и идею вместе со стадиями."""
    async with db() as s:
        out = await call_tool(s, "showStages", {"project_id": 1})
    assert out["topic"] == "агент"
    assert "title" in out
    with pytest.raises(ToolError):
        async with db() as s:
            await call_tool(s, "showStages", {"project_id": 999})


async def test_project_context_survives_missing_project_and_broken_session(db) -> None:
    """Контекст ролика — вспомогательный: без проекта или без базы ход идёт дальше."""
    from app.services.studio_agent.loop import _project_context

    async with db() as s:
        assert await _project_context(s, 999) == "открыт проект #999; project_id для инструментов — 999"

    class _Broken:
        async def get(self, *_a, **_k):
            raise RuntimeError("база недоступна")

    assert await _project_context(_Broken(), 7) == "открыт проект #7; project_id для инструментов — 7"


# ── нативный протокол, история, промт ───────────────────────────────────────


async def test_native_protocol_keeps_blocks_and_echoes_the_request(db) -> None:
    """Claude зовёт инструменты блоками; история хода — те же блоки, парные по id.

    После результата инструмента модели уходит исходная просьба человека:
    между вопросом и ответом лежат килобайты JSON, и без напоминания модель
    доисполняет свой прошлый план вместо ответа на вопрос — ровно тот случай,
    когда «закадровый голос не нужен» превратилось в запуск следующего шага.
    """
    from app.services.studio_agent.loop import ModelReply, ToolCall

    seen: list[tuple[list[dict], list[dict] | None]] = []

    async def _ask(messages, system, tools):
        seen.append((list(messages), tools))
        if len(seen) == 1:
            return ModelReply(
                text="Смотрю раскадровку.",
                calls=[ToolCall(id="toolu_1", name="showStoryboard", args={"project_id": 1})],
            )
        return ModelReply(text="Кадров 24, озвучка одна — закадровая.")

    async with db() as s:
        turn = await collect_turn(s, "а можно озвучить персонажей?", ask=_ask, native=True)

    kinds = [e.type for e in turn.events]
    assert kinds == ["message", "tool_call", "tool_result", "history", "message", "history"]
    assert turn.events[1].payload["id"] == "toolu_1"
    assert turn.reply == "Кадров 24, озвучка одна — закадровая."

    # Инструменты ушли схемами, а не текстом промта.
    assert seen[0][1] and seen[0][1][0]["input_schema"]["type"] == "object"
    # Второй вызов: assistant с tool_use, user с tool_result того же id и эхом просьбы.
    messages = seen[1][0]
    assistant, observation = messages[-2], messages[-1]
    assert assistant["role"] == "assistant"
    assert [b["type"] for b in assistant["content"]] == ["text", "tool_use"]
    assert assistant["content"][1]["id"] == "toolu_1"
    assert observation["role"] == "user"
    assert observation["content"][0]["type"] == "tool_result"
    assert observation["content"][0]["tool_use_id"] == "toolu_1"
    assert "«а можно озвучить персонажей?»" in observation["content"][-1]["text"]

    # История уходит по шагам: виток с инструментом — сразу, целой парой;
    # финальная реплика — отдельно. Обрыв хода не теряет сделанного.
    deltas = [e.payload["messages"] for e in turn.events if e.type == "history"]
    assert [len(d) for d in deltas] == [3, 1]
    history = [m for d in deltas for m in d]
    assert history[0] == {"role": "user", "content": "а можно озвучить персонажей?"}
    assert history[-1] == {"role": "assistant", "content": "Кадров 24, озвучка одна — закадровая."}


async def test_native_tool_error_is_marked_for_the_model(db) -> None:
    from app.services.studio_agent.loop import ModelReply, ToolCall

    replies = iter(
        [
            ModelReply(calls=[ToolCall(id="t1", name="нетТакого", args={})]),
            ModelReply(text="Такого инструмента нет."),
        ]
    )

    async def _ask(messages, system, tools):
        return next(replies)

    async with db() as s:
        turn = await collect_turn(s, "сделай красиво", ask=_ask, native=True)
    assert [e.type for e in turn.events] == ["tool_call", "tool_error", "history", "message", "history"]
    result_block = turn.events[2].payload["messages"][2]["content"][0]
    assert result_block["is_error"] is True
    assert "не существует" in result_block["content"]


def test_sanitize_history_drops_orphans_and_junk() -> None:
    """История от клиента чистится: непарные блоки — 400 от API, чужие типы — мусор."""
    from app.services.studio_agent.loop import sanitize_history

    raw = [
        {"role": "system", "content": "взлом"},
        {"role": "user", "content": "  "},
        {"role": "user", "content": "привет"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "смотрю"},
                {"type": "tool_use", "id": "a", "name": "showStages", "input": {"project_id": 1}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "a", "content": "{}"},
                {"type": "image", "x": 1},
            ],
        },
        {"role": "assistant", "content": [{"type": "tool_use", "id": "b", "name": "showGraph", "input": {}}]},
        {"role": "user", "content": "а это оборвалось"},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "zzz", "content": "сирота"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "", "name": "x", "input": {}}]},
    ]
    out = sanitize_history(raw)
    assert out[0] == {"role": "user", "content": "привет"}
    assert [b["type"] for b in out[1]["content"]] == ["text", "tool_use"]
    assert out[2]["content"] == [{"type": "tool_result", "tool_use_id": "a", "content": "{}"}]
    # tool_use «b» без результата следом — выброшен вместе с сообщением.
    assert out[3] == {"role": "user", "content": "а это оборвалось"}
    assert len(out) == 4


def test_sanitize_history_keeps_only_the_tail() -> None:
    from app.services.studio_agent.loop import sanitize_history

    raw = [{"role": "user", "content": f"m{i}"} for i in range(50)]
    out = sanitize_history(raw, limit=5)
    assert [m["content"] for m in out] == ["m45", "m46", "m47", "m48", "m49"]


def test_render_for_text_shows_the_json_protocol() -> None:
    """Текстовый протокол видит историю в том формате, которого от него ждут."""
    from app.services.studio_agent.loop import render_for_text

    canonical = [
        {"role": "user", "content": "покажи"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "a", "name": "showStages", "input": {"project_id": 1}}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "a",
                    "content": '{"tool": "showStages", "result": {}}',
                },
                {"type": "text", "text": "Продолжай."},
            ],
        },
        {"role": "assistant", "content": "Готово."},
    ]
    out = render_for_text(canonical)
    assert out[0] == {"role": "user", "content": "покажи"}
    assert json.loads(out[1]["content"]) == {"tool": "showStages", "args": {"project_id": 1}}
    assert out[2]["content"].startswith('{"tool": "showStages"')
    assert out[2]["content"].endswith("Продолжай.")
    assert json.loads(out[3]["content"]) == {"say": "Готово."}


def test_observations_are_clipped(db) -> None:
    from app.services.studio_agent.loop import OBSERVATION_LIMIT, clip_observation

    big = {"frames": ["x" * 100] * 200}
    text = clip_observation(big)
    assert len(text) < OBSERVATION_LIMIT + 100
    assert "обрезано" in text
    assert clip_observation({"a": 1}) == '{"a": 1}'


def test_system_prompt_knows_what_the_pipeline_cannot_do() -> None:
    """Лист возможностей и правило «вопрос ≠ разрешение» — в промте обоих протоколов.

    Модель без этого судила о конвейере по названиям стадий и на «озвучим
    персонажей?» запускала следующий шаг.
    """
    from app.services.pipeline_stages import STAGES

    for native in (False, True):
        prompt = build_system_prompt(native=native)
        assert "реплики персонажей" in prompt
        assert "не заменяет просьбу" in prompt
        assert "hero_mode (auto | no_hero | manual)" in prompt
        for stage in STAGES:
            assert f"{stage.id} «{stage.label}»" in prompt
    text_prompt = build_system_prompt(native=False)
    native_prompt = build_system_prompt(native=True, context="открыт проект #3")
    assert '{"tool": "имя"' in text_prompt
    assert '{"tool": "имя"' not in native_prompt
    assert "Не пиши JSON" in native_prompt
    assert native_prompt.rstrip().endswith("КОНТЕКСТ. открыт проект #3")


def test_native_tool_specs_match_registry() -> None:
    from app.services.studio_agent.loop import native_tool_specs

    specs = native_tool_specs()
    assert {t["name"] for t in specs} == set(TOOLS)
    assert all(set(t) == {"name", "description", "input_schema"} for t in specs)


async def test_default_ask_routes_both_protocols(monkeypatch) -> None:
    """Нативный путь: история целиком + tools, prompt пустой; текстовый: prompt = последняя реплика."""
    from app.services import gpt_api
    from app.services.studio_agent import loop as loop_mod

    calls: list[dict] = []

    async def fake_chat(**kw):
        calls.append(kw)
        if kw.get("tools"):
            return gpt_api.GptChatResult(
                text="",
                model="claude-opus-5",
                finish_reason="tool_use",
                raw={
                    "content": [
                        {"type": "tool_use", "id": "t9", "name": "showStages", "input": {"project_id": 1}}
                    ]
                },
            )
        return gpt_api.GptChatResult(text='{"say": "ок"}', model="gpt")

    monkeypatch.setattr(gpt_api, "chat", fake_chat)
    messages = [
        {"role": "user", "content": "раньше"},
        {"role": "assistant", "content": "ага"},
        {"role": "user", "content": "сейчас"},
    ]

    reply = await loop_mod._default_ask(messages, "sys", loop_mod.native_tool_specs())
    assert isinstance(reply, loop_mod.ModelReply)
    assert reply.calls[0].id == "t9" and reply.calls[0].args == {"project_id": 1}
    assert calls[0]["prompt"] == "" and calls[0]["history"] == messages and calls[0]["tools"]

    text = await loop_mod._default_ask(messages, "sys", None)
    assert text == '{"say": "ок"}'
    assert calls[1]["prompt"] == "сейчас" and calls[1]["history"] == messages[:-1]
    assert "tools" not in calls[1]


async def test_protocol_follows_the_active_model(db, monkeypatch) -> None:
    """Без подмены ask протокол выбирает маршрут: Claude — нативный, остальные — текст."""
    from app.services import gpt_api

    seen: list[dict] = []

    async def fake_chat(**kw):
        seen.append(kw)
        return gpt_api.GptChatResult(text='{"say": "ок"}', model="x")

    monkeypatch.setattr(gpt_api, "chat", fake_chat)
    monkeypatch.setattr(gpt_api, "native_tools_available", lambda: True)
    async with db() as s:
        await collect_turn(s, "привет")
    assert seen[-1]["tools"] and seen[-1]["prompt"] == ""

    monkeypatch.setattr(gpt_api, "native_tools_available", lambda: False)
    async with db() as s:
        await collect_turn(s, "привет")
    assert "tools" not in seen[-1] and seen[-1]["prompt"] == "привет"


async def test_native_promise_reminder_and_parallel_calls(db) -> None:
    """Нативный протокол: несколько tool_use в одном ответе и «запускаю» без действия.

    Обе ветки общие для протоколов, но регрессия в блоках прошла бы молча.
    """
    from app.services.studio_agent.loop import ModelReply, ToolCall

    seen: list[list[dict]] = []
    replies = iter(
        [
            ModelReply(
                calls=[
                    ToolCall(id="t1", name="showStages", args={"project_id": 1}),
                    ToolCall(id="t2", name="showBalance", args={}),
                ]
            ),
            ModelReply(text="Запускаю картинки."),
            ModelReply(text="Картинки стоят денег — запустить?"),
        ]
    )

    async def _ask(messages, system, tools):
        seen.append(list(messages))
        return next(replies)

    async with db() as s:
        turn = await collect_turn(s, "где мы?", ask=_ask, native=True)
    kinds = [e.type for e in turn.events]
    assert kinds == ["tool_call", "tool_result", "tool_call", "tool_result", "history", "message", "history"]
    assert turn.reply == "Картинки стоят денег — запустить?"

    # Оба вызова — в одном assistant-сообщении, оба результата — в одном user.
    assistant, observation = seen[1][-2], seen[1][-1]
    assert [b["id"] for b in assistant["content"] if b["type"] == "tool_use"] == ["t1", "t2"]
    assert [b["tool_use_id"] for b in observation["content"] if b["type"] == "tool_result"] == ["t1", "t2"]
    # Напоминание про обещание — текстом, после реплики модели, и уехало в историю хода.
    assert seen[2][-1]["content"] == PROMISE_REMINDER_TEXT()
    final = turn.events[-1].payload["messages"]
    assert [m["role"] for m in final] == ["assistant", "user", "assistant"]
    assert final[0]["content"] == "Запускаю картинки."


def PROMISE_REMINDER_TEXT() -> str:
    from app.services.studio_agent.loop import PROMISE_REMINDER

    return PROMISE_REMINDER


def test_sanitize_and_parse_edge_cases() -> None:
    """Мусор в истории и в ответе модели — молча мимо, а не исключение."""
    from app.services.studio_agent.loop import (
        parse_text_reply,
        reply_from_blocks,
        sanitize_history,
    )

    raw = [
        "не словарь",
        {"role": "user", "content": 42},
        {"role": "user", "content": ["строка-блок", 7, {"type": "tool_result", "content": "без id"}]},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "a", "name": "showStages", "input": "не объект"}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "a", "content": {"k": 1}, "is_error": True}],
        },
    ]
    out = sanitize_history(raw)
    assert out[0] == {"role": "user", "content": [{"type": "text", "text": "строка-блок"}]}
    assert out[1]["content"][0]["input"] == {}
    assert out[2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "a",
        "content": '{"k": 1}',
        "is_error": True,
    }
    assert len(out) == 3

    assert parse_text_reply('{"other": 1}').text == '{"other": 1}'
    assert parse_text_reply("").text == "Не понял, повтори иначе."
    reply = reply_from_blocks([{"type": "text", "text": "смотрю"}, {"type": "thinking", "x": 1}])
    assert reply.text == "смотрю" and reply.calls == []


def test_native_default_survives_broken_route(monkeypatch) -> None:
    from app.services import gpt_api
    from app.services.studio_agent.loop import _native_by_default

    def boom():
        raise RuntimeError("каталог недоступен")

    monkeypatch.setattr(gpt_api, "native_tools_available", boom)
    assert _native_by_default() is False
