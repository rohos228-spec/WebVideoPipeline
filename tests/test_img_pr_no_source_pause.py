"""img_pr «нет исходных данных»: пауза вместо повтора, ноль вызовов модели, причина видна.

Решение владельца (вариант б): код img_pr_no_scene_data не крутит soft retry (раньше до 9
платных повторов). Шаг встаёт на паузу с понятной причиной, хорошие опы смешанного батча
остаются записанными, а повтор при тех же данных сцен не зовёт модель вовсе.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, ProjectStatus
from app.services import img_pr_batches as ipb

REAL = (
    "Фон: подземная камера муравьиного гнезда, тесные земляные стенки с влажными слоями почвы. "
    "Действие: рабочий муравей несёт личинку."
) + " STYLE: dark watercolor wash, distressed printmaking, halftone dots." * 4

CANVAS = {
    "nodes": [
        {"id": "n_split", "type": "split", "data": {"label": "Разбивка"}},
        {"id": "n_excel_gpt_1", "type": "excel_gpt", "data": {"label": "Работа с GPT"}},
        {"id": "n_hero", "type": "hero", "data": {"label": "Персонажи"}},
        {"id": "n_excel_gpt_2", "type": "excel_gpt", "data": {"label": "Работа с GPT"}},
        {"id": "n_img_pr", "type": "image_prompts", "data": {"label": "Промты картинок"}},
    ],
    "edges": [
        {"source": "n_split", "target": "n_excel_gpt_1"},
        {"source": "n_excel_gpt_1", "target": "n_hero"},
        {"source": "n_hero", "target": "n_excel_gpt_2"},
        {"source": "n_excel_gpt_2", "target": "n_img_pr"},
    ],
}


class _Db:
    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.engine = create_async_engine("sqlite+aiosqlite:///" + str(self.dir / "project.db"))
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def setup(self, meta: dict) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.sessions() as s:
            s.add(
                Project(
                    id=2,
                    slug="s",
                    title="s",
                    topic="s",
                    status=ProjectStatus.generating_image_prompts,
                    meta=meta,
                )
            )
            for n in (5, 6, 7):
                s.add(
                    Frame(
                        project_id=2,
                        number=n,
                        uuid=f"{n:024x}",
                        sort_key=n,
                        voiceover_text=f"vo {n}",
                        meaning=f"смысл {n}",
                        attrs={},
                    )
                )
            await s.commit()


async def _run_step(db: _Db, runner) -> tuple[Project, BaseException | None]:
    from app.orchestrator.steps import generate_image_prompts
    from app.services import xlsx_step_runners

    async with db.sessions() as session:
        project = await session.get(Project, 2)
        err: BaseException | None = None
        with (
            patch.object(Project, "data_dir", property(lambda _: db.dir)),
            patch.object(xlsx_step_runners, "run_img_pr_xlsx", side_effect=runner),
        ):
            try:
                await generate_image_prompts.run(session, project, SimpleNamespace())
            except Exception as e:  # noqa: BLE001
                err = e
        return project, err


def _mixed_batch_runner(db: _Db, calls: list[int]):
    """Батч: кадр 7 — хороший оп (пишется), кадры 5 и 6 — «нет исходных данных»."""

    async def runner(*_a, **_k):
        calls.append(1)
        async with db.sessions() as writer:
            for fr in (await writer.scalars(select(Frame))).all():
                if fr.number == 7:
                    fr.image_prompt = REAL
            await writer.commit()
        raise ipb.ImgPrNoSourceData([5, 6])

    return runner


async def _record_failure(project: Project, err: Exception) -> str:
    from app.services.step_failure_policy import record_step_failure

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as s:
            p = Project(
                id=project.id,
                slug="s",
                title="s",
                topic="s",
                status=project.status,
                meta=dict(project.meta or {}),
            )
            s.add(p)
            await s.flush()
            with (
                patch(
                    "app.services.step_failure_policy._soft_retry_without_wipe", new_callable=AsyncMock
                ) as soft,
                patch("app.services.run_sync.mark_running_node_failed", new_callable=AsyncMock),
                patch("app.services.run_sync.stop_active_running_node", new_callable=AsyncMock),
            ):
                action = await record_step_failure(s, p, error=err)
            assert soft.await_count == 0, "soft retry для «нет исходных данных» запрещён"
            project.meta = dict(p.meta or {})
            project.status = p.status
            return action
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_no_source_pauses_keeps_good_ops_and_retry_makes_zero_model_calls() -> None:
    db = _Db()
    await db.setup({"canvas_graph": CANVAS})
    calls: list[int] = []
    try:
        project, err = await _run_step(db, _mixed_batch_runner(db, calls))
        assert len(calls) == 1
        assert isinstance(err, ipb.ImgPrNoSourceData)
        assert err.context.get("scene_fingerprint")

        # Смешанный батч: хороший оп кадра 7 записан, кадры 5/6 пустые.
        async with db.sessions() as s:
            prompts = {fr.number: fr.image_prompt for fr in (await s.scalars(select(Frame))).all()}
        assert prompts[7] == REAL
        assert not prompts[5] and not prompts[6]

        # Политика: пауза, без soft retry, причина простыми словами + шаг выше по стрелке.
        action = await _record_failure(project, err)
        assert action == "pause_infra"
        assert project.status is ProjectStatus.paused
        assert project.meta["paused_from_status"] == "generating_image_prompts"
        fs = project.meta["step_failure"]
        assert fs["last_error_code"] == "img_pr_no_scene_data"
        assert "у сцен нет исходных данных" in fs["last_error"]
        assert "«Работа с GPT 2»" in fs["last_error"]
        assert "5, 6" in fs["last_error"]
        assert fs[ipb.NO_SOURCE_GUARD_KEY]["frame_numbers"] == [5, 6]
        assert not fs.get("total_fails")

        # Продолжение без правки данных: шаг падает ДО модели (ноль вызовов).
        async with db.sessions() as s:
            p = await s.get(Project, 2)
            p.meta = project.meta
            p.status = ProjectStatus.generating_image_prompts
            await s.commit()
        calls.clear()
        _project2, err2 = await _run_step(db, _mixed_batch_runner(db, calls))
        assert calls == []
        assert isinstance(err2, ipb.ImgPrNoSourceData)
        assert err2.frame_numbers == [5, 6]
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_resume_after_scene_data_fixed_calls_model_again() -> None:
    db = _Db()
    await db.setup({"canvas_graph": CANVAS})
    calls: list[int] = []
    try:
        project, err = await _run_step(db, _mixed_batch_runner(db, calls))
        await _record_failure(project, err)
        # «Работа с GPT» заполнила место/фон/действие у кадров 5 и 6.
        async with db.sessions() as s:
            p = await s.get(Project, 2)
            p.meta = project.meta
            p.status = ProjectStatus.generating_image_prompts
            for fr in (await s.scalars(select(Frame))).all():
                if fr.number in (5, 6):
                    fr.attrs = {"место": "нора", "фон": "земля", "главное_действие": "копает"}
            await s.commit()

        calls.clear()

        async def ok_runner(*_a, **_k):
            calls.append(1)
            async with db.sessions() as writer:
                for fr in (await writer.scalars(select(Frame))).all():
                    fr.image_prompt = REAL
                await writer.commit()
            return SimpleNamespace(apply_ops=[], ops_applied_inline=True)

        from app.services import agent_harness

        with (
            patch.object(agent_harness, "harness_gate_or_raise", new_callable=AsyncMock),
            patch("app.services.node_xlsx_snapshot.snapshot_and_bind_node_xlsx", new_callable=AsyncMock),
        ):
            project2, err2 = await _run_step(db, ok_runner)
        assert err2 is None, err2
        assert calls == [1]
        assert project2.status is ProjectStatus.image_prompts_ready
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_pause_reason_reaches_chat_notice_and_overview() -> None:
    from app.services import chat_notice
    from app.services.action_layer.builtin_insight import _pause_info
    from app.services.action_layer.step_outcome import stop_reason

    p = Project(id=9, slug="s", title="s", topic="s", status=ProjectStatus.generating_image_prompts, meta={})
    action = await _record_failure(p, ipb.ImgPrNoSourceData([3]))
    assert action == "pause_infra"
    reason = p.meta["step_failure"]["last_error"]
    # Без канваса — общее имя шага.
    assert "Сначала запустите шаг «Работа с GPT»" in reason

    sent: list[tuple] = []

    async def fake_notice(pid, level, title, text="", **kw):
        sent.append((pid, level, title, text, kw.get("kind")))
        return {"ok": True}

    with patch.object(chat_notice, "notice", side_effect=fake_notice):
        await chat_notice.step_failure(p, action, ipb.ImgPrNoSourceData([3]))
    assert sent and sent[0][4] == "step_failure_pause_no_source"
    assert "у сцен нет исходных данных" in sent[0][3]
    assert "браузер" not in sent[0][3]

    info = _pause_info("paused", p.meta)
    assert "у сцен нет исходных данных" in info["reason"]
    assert "у сцен нет исходных данных" in stop_reason(p.meta)


def test_wrapped_error_still_pauses_and_success_clears_guard() -> None:
    from app.services.step_failure_policy import _no_source_error, clear_failure_on_success

    inner = ipb.ImgPrNoSourceData([1])
    try:
        try:
            raise inner
        except Exception as e:
            raise RuntimeError("обёртка") from e
    except RuntimeError as wrapped:
        assert _no_source_error(wrapped) is inner

    p = Project(
        id=1,
        slug="t",
        status=ProjectStatus.generating_image_prompts,
        meta={"step_failure": {ipb.NO_SOURCE_GUARD_KEY: {"frame_numbers": [1], "fingerprint": "x"}}},
    )
    clear_failure_on_success(p, ProjectStatus.generating_image_prompts)
    assert ipb.NO_SOURCE_GUARD_KEY not in p.meta["step_failure"]


def test_manual_start_of_img_pr_lifts_guard() -> None:
    from app.services.step_failure_policy import clear_failure_backoff_for_manual_start

    p = Project(
        id=1,
        slug="t",
        status=ProjectStatus.image_prompts_ready,
        meta={"step_failure": {ipb.NO_SOURCE_GUARD_KEY: {"frame_numbers": [1], "fingerprint": "x"}}},
    )
    assert clear_failure_backoff_for_manual_start(p, running_key="generating_image_prompts")
    assert ipb.NO_SOURCE_GUARD_KEY not in p.meta["step_failure"]


def test_fingerprint_ignores_prompt_fields_but_sees_scene_fields() -> None:
    a = SimpleNamespace(
        number=1,
        voiceover_text="vo",
        meaning=None,
        transition_from=None,
        transition_to=None,
        scene_id=None,
        attrs={},
    )
    b = SimpleNamespace(**{**a.__dict__, "attrs": {"промт_картинки": "что-то"}})
    c = SimpleNamespace(**{**a.__dict__, "attrs": {"фон": "лес"}})
    assert ipb.scene_input_fingerprint([a]) == ipb.scene_input_fingerprint([b])
    assert ipb.scene_input_fingerprint([a]) != ipb.scene_input_fingerprint([c])


@pytest.mark.asyncio
async def test_mixed_run_second_attempt_skips_no_source_frames() -> None:
    """Кадр 5 — «нет данных», 6/7 модель пропустила: 2-я попытка шлёт только 6/7."""
    db = _Db()
    await db.setup({"canvas_graph": CANVAS})
    kwargs_seen: list[dict] = []

    async def runner(*_a, **kw):
        kwargs_seen.append(dict(kw))
        if len(kwargs_seen) == 1:
            raise ipb.ImgPrNoSourceData([5])
        async with db.sessions() as writer:
            for fr in (await writer.scalars(select(Frame))).all():
                if fr.number in (6, 7):
                    fr.image_prompt = REAL
            await writer.commit()
        return SimpleNamespace(apply_ops=[], ops_applied_inline=True)

    try:
        with patch("app.services.node_xlsx_snapshot.snapshot_and_bind_node_xlsx", new_callable=AsyncMock):
            _project, err = await _run_step(db, runner)
        assert len(kwargs_seen) == 2
        assert "skip_frame_numbers" not in kwargs_seen[0]
        assert kwargs_seen[1]["skip_frame_numbers"] == {5}
        assert isinstance(err, ipb.ImgPrNoSourceData)
        assert err.frame_numbers == [5]
        assert err.context.get("scene_fingerprint")
        async with db.sessions() as s:
            prompts = {fr.number: fr.image_prompt for fr in (await s.scalars(select(Frame))).all()}
        assert prompts[6] == REAL and prompts[7] == REAL and not prompts[5]
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
async def test_mixed_run_second_attempt_merges_new_no_source_frames() -> None:
    db = _Db()
    await db.setup({"canvas_graph": CANVAS})
    n = [0]

    async def runner(*_a, **_kw):
        n[0] += 1
        raise ipb.ImgPrNoSourceData([5] if n[0] == 1 else [6])

    try:
        _project, err = await _run_step(db, runner)
        assert n[0] == 2
        assert isinstance(err, ipb.ImgPrNoSourceData)
        assert err.frame_numbers == [5, 6]
        assert err.context.get("scene_fingerprint")
    finally:
        await db.engine.dispose()


def _runner_env(tmp_path, monkeypatch, frames, calls):
    import json

    from app.services import chatgpt_xlsx as cx
    from app.services import xlsx_gpt_flow as xgf
    from app.services import xlsx_step_runners as xsr

    prompt_file = tmp_path / "prompt_img_pr.txt"
    prompt_file.write_text("master", encoding="utf-8")

    class FakeGpt:
        async def new_conversation(self):
            return None

        async def ask_with_files(self, msg, attach, **_kw):
            uuids = [
                line.split(" = ")[1]
                for line in msg.splitlines()
                if line.startswith("кадр ") and " = " in line
            ]
            calls.append(uuids)
            return json.dumps(
                {
                    "ops": [
                        {"frame_uuid": u, "fields": {"промт_картинки": "Нет исходных данных для заполнения"}}
                        for u in uuids
                    ]
                },
                ensure_ascii=False,
            )

    async def _ctx(_project, **_kw):
        return list(frames), [], "", list(frames)

    async def _under_lock(_pid, _name, fn):
        return await fn()

    def _db_frames(_p, tmp_dir, batch, *_a, batch_tag="", **_kw):
        out = Path(tmp_dir) / f"db_{batch_tag}.json"
        out.write_text("{}", encoding="utf-8")
        return out

    monkeypatch.setattr(xsr, "_ensure_project_xlsx", lambda p: tmp_path / "project.xlsx")
    monkeypatch.setattr(cx, "tmp_gpt_dir", lambda p: tmp_path)
    monkeypatch.setattr(cx, "write_img_pr_prompt_file", lambda p, d, ts=None: prompt_file)
    monkeypatch.setattr(cx, "ensure_current_voiceover", lambda p: None)
    monkeypatch.setattr(cx, "chat_message", lambda *a, **k: "img_pr\n")
    monkeypatch.setattr("app.services.img_pr_style.resolve_project_img_style", lambda *a, **k: None)
    monkeypatch.setattr(xsr, "_load_img_pr_context", _ctx)
    monkeypatch.setattr(xsr, "_write_img_pr_db_frames_for", _db_frames)
    monkeypatch.setattr(xsr, "img_pr_live_streams", lambda p: 8)
    monkeypatch.setattr("app.services.gpt_client.ApiGptClient", FakeGpt)
    monkeypatch.setattr(xgf, "run_under_xlsx_lock", _under_lock)
    return xsr


@pytest.mark.asyncio
async def test_runner_skip_frame_numbers_not_sent_to_model(tmp_path, monkeypatch) -> None:
    frames = [
        SimpleNamespace(number=n, uuid=f"{n:024x}", voiceover_text=f"vo {n}", image_prompt=None, attrs={})
        for n in (5, 6, 7)
    ]
    project = SimpleNamespace(id=2, slug="t", data_dir=tmp_path, meta={})
    calls: list[list[str]] = []
    xsr = _runner_env(tmp_path, monkeypatch, frames, calls)

    with pytest.raises(ipb.ImgPrNoSourceData):
        await xsr.run_img_pr_xlsx(project, n_frames=3, project_id=2, skip_frame_numbers={5})
    sent = {u for c in calls for u in c}
    assert f"{5:024x}" not in sent
    assert sent == {f"{6:024x}", f"{7:024x}"}

    # Остались только «нет данных» — модель не зовётся вовсе.
    calls.clear()
    res = await xsr.run_img_pr_xlsx(project, n_frames=3, project_id=2, skip_frame_numbers={5, 6, 7})
    assert calls == []
    assert res.ops_applied_inline is True


@pytest.mark.asyncio
async def test_guard_without_fingerprint_logs_warning() -> None:
    from loguru import logger

    from app.orchestrator.steps.generate_image_prompts import _raise_if_no_source_unchanged

    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="WARNING")
    try:
        p = SimpleNamespace(
            id=3,
            meta={"step_failure": {ipb.NO_SOURCE_GUARD_KEY: {"frame_numbers": [1], "fingerprint": None}}},
        )
        await _raise_if_no_source_unchanged(None, p, [], [])
    finally:
        logger.remove(sink)
    assert any("без слепка" in ln for ln in lines)


@pytest.mark.asyncio
async def test_no_source_and_credits_pause_have_distinct_dedup_keys() -> None:
    from app.services import chat_notice

    keys: dict[str, str] = {}

    async def fake_notice(pid, level, title, text="", **kw):
        keys[kw.get("kind")] = kw.get("dedup_key")
        return {"ok": True}

    def _p(code: str) -> Project:
        return Project(
            id=9,
            slug="s",
            title="s",
            topic="s",
            status=ProjectStatus.paused,
            meta={
                "paused_from_status": "generating_image_prompts",
                "step_failure": {
                    "last_error_code": code,
                    "last_error": "x",
                    "last_running": "generating_image_prompts",
                },
            },
        )

    with patch.object(chat_notice, "notice", side_effect=fake_notice):
        await chat_notice.step_failure(_p("img_pr_no_scene_data"), "pause_infra", ipb.ImgPrNoSourceData([3]))
        await chat_notice.step_failure(_p("gpt_credits"), "pause_infra", RuntimeError("no credits"))
    assert set(keys) == {"step_failure_pause_no_source", "step_failure_pause_infra"}
    assert keys["step_failure_pause_no_source"] != keys["step_failure_pause_infra"]
