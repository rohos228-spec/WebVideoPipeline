"""Тесты REST-эндпоинтов /api/prompt-studio/step-template/{step_id} —
блочный редактор шаблонов шагов (Studio UI, GET/PUT карточек 1..N)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.services import prompt_composer as pc
from app.web.api import create_app

app = create_app()


@pytest.fixture
def step_templates_dir(tmp_path, monkeypatch):
    steps_root = tmp_path / "steps"
    step_dir = steps_root / "99_test"
    step_dir.mkdir(parents=True)
    (step_dir / "template.md").write_text(
        "# Шаг 99 — Тест\n\n"
        "## 1. ТЕХНИЧЕСКАЯ ЧАСТЬ\n\nоткуда читаю / куда пишу / внимание\n\n"
        "## 2. РОЛЬ\n\nроль\n\n"
        "## 3. ТЕМА\n\nтема\n\n"
        "## 4. ЗАПРЕТЫ\n\nзапреты\n\n"
        "## 5. ФОРМАТ\n\nформат\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(pc, "STEPS_ROOT", steps_root)
    return "99_test"


@pytest.mark.asyncio
async def test_get_step_template_returns_parsed_blocks(step_templates_dir) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/prompt-studio/step-template/{step_templates_dir}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["step_id"] == step_templates_dir
    assert len(data["blocks"]) == 5
    assert data["blocks"][0]["title"] == "ТЕХНИЧЕСКАЯ ЧАСТЬ"


@pytest.mark.asyncio
async def test_get_step_template_404_for_unknown_step() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/prompt-studio/step-template/no_such_step")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_put_step_template_saves_edited_blocks(step_templates_dir) -> None:
    payload = {
        "blocks": [
            {"number": 1, "title": "ТЕХНИЧЕСКАЯ ЧАСТЬ", "body": "новый техтекст"},
            {"number": 2, "title": "РОЛЬ", "body": "новая роль"},
            {"number": 3, "title": "ТЕМА", "body": "тема"},
            {"number": 4, "title": "ЗАПРЕТЫ", "body": "запреты"},
            {"number": 5, "title": "ФОРМАТ", "body": "формат"},
        ]
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(f"/api/prompt-studio/step-template/{step_templates_dir}", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["blocks"][1]["body"] == "новая роль"
    # Реально записалось на диск.
    assert pc.parse_step_template_blocks(step_templates_dir)[0]["body"] == "новый техтекст"


@pytest.mark.asyncio
async def test_put_step_template_rejects_too_few_blocks(step_templates_dir) -> None:
    payload = {
        "blocks": [
            {"number": 1, "title": "ТЕХНИЧЕСКАЯ ЧАСТЬ", "body": "x"},
            {"number": 2, "title": "РОЛЬ", "body": "y"},
        ]
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(f"/api/prompt-studio/step-template/{step_templates_dir}", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_put_step_template_rejects_non_technical_first_block(step_templates_dir) -> None:
    payload = {
        "blocks": [
            {"number": 1, "title": "РОЛЬ", "body": "x"},
            {"number": 2, "title": "ТЕМА", "body": "y"},
            {"number": 3, "title": "СТИЛЬ", "body": "z"},
            {"number": 4, "title": "ЗАПРЕТЫ", "body": "w"},
            {"number": 5, "title": "ФОРМАТ", "body": "v"},
        ]
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(f"/api/prompt-studio/step-template/{step_templates_dir}", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_put_step_template_rejects_bad_numbering(step_templates_dir) -> None:
    payload = {
        "blocks": [
            {"number": 1, "title": "ТЕХНИЧЕСКАЯ ЧАСТЬ", "body": "x"},
            {"number": 3, "title": "РОЛЬ", "body": "y"},
            {"number": 4, "title": "ТЕМА", "body": "z"},
            {"number": 5, "title": "ЗАПРЕТЫ", "body": "w"},
            {"number": 6, "title": "ФОРМАТ", "body": "v"},
        ]
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(f"/api/prompt-studio/step-template/{step_templates_dir}", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_put_step_template_unknown_step_404(step_templates_dir) -> None:
    payload = {
        "blocks": [
            {"number": 1, "title": "ТЕХНИЧЕСКАЯ ЧАСТЬ", "body": "x"},
            {"number": 2, "title": "РОЛЬ", "body": "y"},
            {"number": 3, "title": "ТЕМА", "body": "z"},
            {"number": 4, "title": "ЗАПРЕТЫ", "body": "w"},
            {"number": 5, "title": "ФОРМАТ", "body": "v"},
        ]
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put("/api/prompt-studio/step-template/no_such_step", json=payload)
    assert resp.status_code == 404


# ════════════════════════════════════════════════════════════════════════════
# Ручки студии, которые ПИШУТ: сопроводительный текст шага, каталог блоков,
# настройки промтов проекта, прогон «Вердикта».
#
# Перенос форка заказчика заменил здесь голый `session.commit()` на
# `commit_with_retry` (повтор на sqlite «database is locked»), а покрытия у
# файла не было вовсе. Поэтому проверка не «200», а «запись пережила
# транзакцию»: состояние перечитывается отдельной сессией или GET-ручкой.
#
# Блоки лежат на диске в `prompts/blocks/` — фикстура уводит корень в tmp,
# чтобы ручки создания/переименования/удаления не трогали репозиторий.
# ════════════════════════════════════════════════════════════════════════════

import pytest_asyncio
from sqlalchemy import select

import app.db as app_db
from app.models import LibraryEvent, LibraryItem, Project, ProjectStatus
from app.services import prompt_blocks as pb
from app.web.deps import get_session


@pytest_asyncio.fixture
async def db_client():
    """Клиент над изолированной БД conftest.

    `app/web/deps.py` связывает `SessionLocal` на импорте, и подмена движка в
    conftest до него не доходит — без `dependency_overrides` роутер писал бы в
    боевую `data/state.db`.
    """

    async def _gen():
        async with app_db.SessionLocal() as session:
            yield session

    local_app = create_app()
    local_app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=local_app), base_url="http://test") as c:
        yield c
    local_app.dependency_overrides.clear()


@pytest.fixture
def blocks_dir(tmp_path, monkeypatch):
    """Каталог блоков в tmp.

    Патчатся оба конца: `prompt_blocks.PROMPTS_ROOT` — им считается путь файла
    блока, `prompt_composer.BLOCKS_ROOT` — по нему строится список категорий.
    Без второго `sync_blocks` перечислял бы блоки репозитория.
    """
    prompts_root = tmp_path / "prompts"
    (prompts_root / "blocks" / "world").mkdir(parents=True)
    (prompts_root / "blocks" / "world" / "cats.md").write_text(
        "# Коты\n\nантропоморфные коты\n", encoding="utf-8"
    )
    monkeypatch.setattr(pb, "PROMPTS_ROOT", prompts_root)
    monkeypatch.setattr(pc, "BLOCKS_ROOT", prompts_root / "blocks")
    return prompts_root


async def _make_project(slug: str = "studio-proj", status: ProjectStatus = ProjectStatus.new) -> int:
    async with app_db.SessionLocal() as s:
        project = Project(slug=slug, topic="тема ролика", status=status, meta={})
        s.add(project)
        await s.commit()
        return project.id


async def _reread_project(project_id: int) -> Project:
    async with app_db.SessionLocal() as s:
        project = await s.get(Project, project_id)
    assert project is not None
    return project


# ── сопроводительный текст шага ─────────────────────────────────────────────


async def test_save_gpt_text_override_переживает_транзакцию(db_client) -> None:
    project_id = await _make_project()
    res = await db_client.put(
        f"/api/prompt-studio/projects/{project_id}/gpt-text/plan",
        json={"text": "мой сопроводительный текст"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["is_override"] is True
    assert body["text"] == "мой сопроводительный текст"

    stored = await _reread_project(project_id)
    assert (stored.gpt_text_overrides or {})["plan"] == "мой сопроводительный текст"

    fresh = (await db_client.get(f"/api/prompt-studio/projects/{project_id}/gpt-text/plan")).json()
    assert fresh["is_override"] is True and fresh["text"] == "мой сопроводительный текст"


async def test_save_gpt_text_пустой_строкой_снимает_override(db_client) -> None:
    """Пустой текст — это не «сохранить пустоту», а «вернуть дефолт»."""
    project_id = await _make_project()
    await db_client.put(
        f"/api/prompt-studio/projects/{project_id}/gpt-text/plan", json={"text": "свой текст"}
    )
    res = await db_client.put(f"/api/prompt-studio/projects/{project_id}/gpt-text/plan", json={"text": ""})
    assert res.status_code == 200, res.text
    assert res.json()["is_override"] is False

    stored = await _reread_project(project_id)
    assert "plan" not in (stored.gpt_text_overrides or {})


async def test_save_gpt_text_на_шаг_без_текста_это_400(db_client) -> None:
    project_id = await _make_project()
    res = await db_client.put(
        f"/api/prompt-studio/projects/{project_id}/gpt-text/нет-такого-шага", json={"text": "x"}
    )
    assert res.status_code == 400
    res = await db_client.put("/api/prompt-studio/projects/4242/gpt-text/plan", json={"text": "x"})
    assert res.status_code == 404


async def test_reset_gpt_text_удаляет_override(db_client) -> None:
    project_id = await _make_project()
    await db_client.put(
        f"/api/prompt-studio/projects/{project_id}/gpt-text/plan", json={"text": "свой текст"}
    )

    res = await db_client.delete(f"/api/prompt-studio/projects/{project_id}/gpt-text/plan")
    assert res.status_code == 200, res.text
    assert res.json()["is_override"] is False

    stored = await _reread_project(project_id)
    assert "plan" not in (stored.gpt_text_overrides or {})


async def test_reset_gpt_text_на_неизвестном_шаге_не_падает(db_client) -> None:
    """Сбрасывать нечего, но ручка обязана ответить, а не 500."""
    project_id = await _make_project()
    res = await db_client.delete(f"/api/prompt-studio/projects/{project_id}/gpt-text/нет-такого-шага")
    assert res.status_code == 200, res.text
    assert res.json() == {
        "step_code": "нет-такого-шага",
        "text": "",
        "supported": False,
        "is_override": False,
    }
    assert (await db_client.delete("/api/prompt-studio/projects/4242/gpt-text/plan")).status_code == 404


# ── блоки ───────────────────────────────────────────────────────────────────


async def test_sync_blocks_заводит_элементы_библиотеки(db_client, blocks_dir) -> None:
    res = await db_client.post("/api/prompt-studio/blocks/sync")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["blocks_total"] == 1
    assert body["imported_to_library"] == 1
    assert body["discovered"] == [{"category": "world", "block_id": "cats"}]

    async with app_db.SessionLocal() as s:
        keys = (await s.execute(select(LibraryItem.key).where(LibraryItem.kind == "block"))).scalars().all()
    assert keys == ["prompts/blocks/world/cats.md"], "найденный блок не доехал до коммита"

    # Повторная сверка уже ничего не импортирует — элемент в библиотеке есть.
    again = (await db_client.post("/api/prompt-studio/blocks/sync")).json()
    assert again["imported_to_library"] == 0


async def test_post_block_activity_пишет_событие(db_client, blocks_dir) -> None:
    project_id = await _make_project("studio-activity")
    res = await db_client.post(
        "/api/prompt-studio/block-activity",
        json={
            "event_type": "block_selected",
            "category": "world",
            "block_id": "cats",
            "project_id": project_id,
            "step_code": "hero",
        },
    )
    assert res.status_code == 200 and res.json() == {"ok": True}

    async with app_db.SessionLocal() as s:
        events = (
            (await s.execute(select(LibraryEvent).where(LibraryEvent.event_type == "block_selected")))
            .scalars()
            .all()
        )
    assert len(events) == 1, "событие выбора блока не дожило до отдельной сессии"
    assert events[0].payload["block_id"] == "cats"
    assert events[0].payload["step_code"] == "hero"

    listed = (await db_client.get("/api/prompt-studio/block-activity?category=world")).json()
    assert any(e["event_type"] == "block_selected" for e in listed)


async def test_post_block_activity_с_кривой_категорией_это_400(db_client, blocks_dir) -> None:
    res = await db_client.post(
        "/api/prompt-studio/block-activity",
        json={"event_type": "block_viewed", "category": "не/категория", "block_id": "cats"},
    )
    assert res.status_code == 400


async def test_put_block_пишет_файл_и_версию(db_client, blocks_dir) -> None:
    res = await db_client.put(
        "/api/prompt-studio/blocks/world/cats",
        json={"content": "# Коты\n\nправленые коты\n", "message": "правка из теста"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["changed"] is True and body["created"] is False

    on_disk = (blocks_dir / "blocks" / "world" / "cats.md").read_text(encoding="utf-8")
    assert on_disk == "# Коты\n\nправленые коты\n"
    async with app_db.SessionLocal() as s:
        item = await s.get(LibraryItem, body["library_item_id"])
    assert item is not None, "версия блока не дожила до отдельной сессии"

    fresh = (await db_client.get("/api/prompt-studio/blocks/world/cats")).json()
    assert fresh["body"] == "# Коты\n\nправленые коты\n"


async def test_put_block_несуществующего_это_404(db_client, blocks_dir) -> None:
    res = await db_client.put("/api/prompt-studio/blocks/world/нет-бла", json={"content": "x"})
    assert res.status_code == 400
    res = await db_client.put("/api/prompt-studio/blocks/world/no_such_block", json={"content": "x"})
    assert res.status_code == 404


async def test_post_block_создаёт_новый(db_client, blocks_dir) -> None:
    res = await db_client.post(
        "/api/prompt-studio/blocks/world",
        json={"block_id": "dogs", "content": "# Псы\n\nантропоморфные псы\n"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["created"] is True
    assert (blocks_dir / "blocks" / "world" / "dogs.md").is_file()

    async with app_db.SessionLocal() as s:
        item = await s.get(LibraryItem, res.json()["library_item_id"])
    assert item is not None and item.key == "prompts/blocks/world/dogs.md"

    dup = await db_client.post("/api/prompt-studio/blocks/world", json={"block_id": "dogs"})
    assert dup.status_code == 409


async def test_post_block_с_кривой_категорией_это_400(db_client, blocks_dir) -> None:
    res = await db_client.post("/api/prompt-studio/blocks/не-категория", json={"block_id": "dogs"})
    assert res.status_code == 400


async def test_delete_block_убирает_файл_и_пишет_событие(db_client, blocks_dir) -> None:
    res = await db_client.delete("/api/prompt-studio/blocks/world/cats")
    assert res.status_code == 200, res.text
    assert res.json() == {"category": "world", "id": "cats", "deleted": True}
    assert not (blocks_dir / "blocks" / "world" / "cats.md").exists()

    async with app_db.SessionLocal() as s:
        events = (
            (await s.execute(select(LibraryEvent).where(LibraryEvent.event_type == "block_deleted")))
            .scalars()
            .all()
        )
    assert len(events) == 1, "событие удаления блока не доехало до коммита"

    assert (await db_client.delete("/api/prompt-studio/blocks/world/cats")).status_code == 404
    assert (await db_client.delete("/api/prompt-studio/blocks/не-категория/cats")).status_code == 400


async def test_rename_block_переносит_файл(db_client, blocks_dir) -> None:
    res = await db_client.post(
        "/api/prompt-studio/blocks/world/cats/rename", json={"new_block_id": "kitties"}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == "kitties" and body["renamed_from"] == "cats"
    assert not (blocks_dir / "blocks" / "world" / "cats.md").exists()
    assert (blocks_dir / "blocks" / "world" / "kitties.md").read_text(encoding="utf-8").startswith("# Коты")

    async with app_db.SessionLocal() as s:
        keys = (await s.execute(select(LibraryItem.key).where(LibraryItem.kind == "block"))).scalars().all()
    assert "prompts/blocks/world/kitties.md" in keys, "переименованный блок не доехал до коммита"


async def test_rename_block_ошибки(db_client, blocks_dir) -> None:
    assert (
        await db_client.post("/api/prompt-studio/blocks/world/нет-бла/rename", json={"new_block_id": "x"})
    ).status_code == 400
    assert (
        await db_client.post("/api/prompt-studio/blocks/world/no_such/rename", json={"new_block_id": "x"})
    ).status_code == 404
    await db_client.post("/api/prompt-studio/blocks/world", json={"block_id": "dogs", "content": "псы"})
    assert (
        await db_client.post("/api/prompt-studio/blocks/world/cats/rename", json={"new_block_id": "dogs"})
    ).status_code == 409


# ── настройки промтов проекта ───────────────────────────────────────────────


async def test_patch_prompt_config_сохраняет_переопределения(db_client) -> None:
    project_id = await _make_project("studio-config")
    res = await db_client.patch(
        f"/api/prompt-studio/projects/{project_id}/prompt-config",
        json={
            "style_profile": "нуар",
            "blocks": {"world": "cats_anthropomorphic"},
            "vars": {"VIDEO_DURATION_SEC": 42},
            "legacy": {"plan": "старый override"},
        },
    )
    assert res.status_code == 200, res.text
    po = res.json()["prompt_overrides"]
    assert po["style_profile"] == "нуар"
    assert po["use_blocks_v2"] is True, "передали blocks — режим v2 обязан включиться сам"
    assert po["plan"] == "старый override"
    assert res.json()["resolved_vars"]["VIDEO_DURATION_SEC"] == "42"

    stored = await _reread_project(project_id)
    assert (stored.prompt_overrides or {})["style_profile"] == "нуар"
    assert (stored.prompt_overrides or {})["vars"] == {"VIDEO_DURATION_SEC": 42}


async def test_patch_prompt_config_выключает_v2_явно(db_client) -> None:
    project_id = await _make_project("studio-config-off")
    await db_client.patch(
        f"/api/prompt-studio/projects/{project_id}/prompt-config",
        json={"blocks": {"world": "cats_anthropomorphic"}},
    )
    res = await db_client.patch(
        f"/api/prompt-studio/projects/{project_id}/prompt-config", json={"use_blocks_v2": False}
    )
    assert res.status_code == 200, res.text
    assert res.json()["prompt_overrides"]["use_blocks_v2"] is False

    stored = await _reread_project(project_id)
    assert (stored.prompt_overrides or {})["use_blocks_v2"] is False


async def test_patch_prompt_config_на_неизвестном_проекте_404(db_client) -> None:
    res = await db_client.patch("/api/prompt-studio/projects/4242/prompt-config", json={})
    assert res.status_code == 404


# ── прогон «Вердикта» ───────────────────────────────────────────────────────


def _stub_gpt(monkeypatch) -> list[str]:
    """Подменить провайдера: проверяем ручку, а не GPT."""
    from app.services import gpt_client as gc

    calls: list[str] = []

    class _Gpt:
        async def new_conversation(self):
            calls.append("new_conversation")

    monkeypatch.setattr(gc, "get_gpt_client", lambda *a, **kw: _Gpt())
    return calls


async def test_run_gpt_verdict_продвигает_проект_и_коммитит(db_client, monkeypatch) -> None:
    from app.orchestrator import auto_advance as aa
    from app.services import gpt_verdict_review as gvr

    project_id = await _make_project("studio-verdict", status=ProjectStatus.plan_ready)
    calls = _stub_gpt(monkeypatch)

    async def _run(session, project, step_code, gpt, *, user_prompt=None):
        assert step_code == "plan"
        assert user_prompt == "проверь по-своему"
        return gvr.VerdictRunResult(approved=True, rounds=1, last_raw="Одобрено", history=["Одобрено"])

    async def _advance(session, project, step_code, *, approved, fix_applied):
        # Настоящее продвижение тянет весь оркестратор; ручке важно одно —
        # что после `True` она фиксирует транзакцию и перечитывает проект.
        assert approved is True and fix_applied is False
        project.status = ProjectStatus.scripting
        return True

    monkeypatch.setattr(gvr, "run_verdict_review", _run)
    monkeypatch.setattr(aa, "advance_after_gpt_verdict", _advance)

    res = await db_client.post(
        f"/api/prompt-studio/projects/{project_id}/gpt-verdict/plan/run",
        json={"prompt": "проверь по-своему"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["approved"] is True
    assert body["advanced"] is True
    assert body["status"] == ProjectStatus.scripting.value
    assert calls == ["new_conversation"]

    stored = await _reread_project(project_id)
    assert stored.status == ProjectStatus.scripting, "новый статус не пережил транзакцию"


async def test_run_gpt_verdict_без_продвижения_не_меняет_статус(db_client, monkeypatch) -> None:
    from app.orchestrator import auto_advance as aa
    from app.services import gpt_verdict_review as gvr

    project_id = await _make_project("studio-verdict-no", status=ProjectStatus.plan_ready)
    _stub_gpt(monkeypatch)

    async def _run(session, project, step_code, gpt, *, user_prompt=None):
        return gvr.VerdictRunResult(approved=False, rounds=2, last_raw="Не одобрено")

    async def _advance(session, project, step_code, *, approved, fix_applied):
        return False

    monkeypatch.setattr(gvr, "run_verdict_review", _run)
    monkeypatch.setattr(aa, "advance_after_gpt_verdict", _advance)

    res = await db_client.post(f"/api/prompt-studio/projects/{project_id}/gpt-verdict/plan/run")
    assert res.status_code == 200, res.text
    assert res.json()["advanced"] is False
    assert (await _reread_project(project_id)).status == ProjectStatus.plan_ready


async def test_run_gpt_verdict_остановка_оператором_это_499(db_client, monkeypatch) -> None:
    from app.services import gpt_verdict_review as gvr
    from app.services.step_cancel import StepCancelledError

    project_id = await _make_project("studio-verdict-stop", status=ProjectStatus.plan_ready)
    _stub_gpt(monkeypatch)

    async def _run(session, project, step_code, gpt, *, user_prompt=None):
        raise StepCancelledError("остановлено оператором")

    monkeypatch.setattr(gvr, "run_verdict_review", _run)
    res = await db_client.post(f"/api/prompt-studio/projects/{project_id}/gpt-verdict/plan/run")
    assert res.status_code == 499


async def test_run_gpt_verdict_отбивает_шаг_без_проверки(db_client) -> None:
    project_id = await _make_project("studio-verdict-400")
    res = await db_client.post(f"/api/prompt-studio/projects/{project_id}/gpt-verdict/music/run")
    assert res.status_code == 400
    assert (await db_client.post("/api/prompt-studio/projects/4242/gpt-verdict/plan/run")).status_code == 404
