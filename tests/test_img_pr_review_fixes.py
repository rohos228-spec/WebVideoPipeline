"""Ревью fx/data: единый детектор пустых промтов, смешанные батчи, terminal note, код ошибки.

Формы взяты из живых промтов _chat_test2 (project.db #10/#12/#14).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.generation_options import image_prompt_missing
from app.services import img_pr_batches as ipb
from app.settings import settings

STYLE = (" STYLE: Archival Noir Watercolor Grunge Dossier Poster Illustration, dark watercolor wash, "
         "distressed printmaking, halftone dots, charcoal shadow masses.") * 4

HOLLOW_REAL = [
    # #12 кадры 14–20
    "Референс: персонажи в кадре не заданы. Фон: исходные данные о локации, поверхностях, "
    "материалах, цвете отсутствуют. Действие: исходные данные о действии отсутствуют." + STYLE,
    # #14 кадры 9–13
    "Фон: данных о фоне и локации в записи кадра нет; не добавлять окружение или предметы. "
    "Действие: не изображать выдуманную сцену; исходных визуальных данных для действия нет." + STYLE,
    # #10 кадры 1–8
    "Фон: исходные данные о фоне отсутствуют; тёмный лес и детали окружения не добавлять без данных базы."
    " Действие: исходные данные о физическом действии отсутствуют." + STYLE,
    # обёрнутые формы
    "«Нет исходных данных для заполнения»." + STYLE,
    "**Нет исходных данных для заполнения.**" + STYLE,
    "Сцена: нет исходных данных." + STYLE,
]
REAL = [
    "Фон: подземная камера муравьиного гнезда, тесные земляные стенки с влажными слоями почвы. "
    "Действие: рабочий муравей несёт личинку." + STYLE,
    # оговорка + сцена — не пустой промт
    "Фон: нет исходных данных о фоне, но лиса бежит по снегу у кромки ельника." + STYLE,
    # #14 кадр 1: про фон нет данных, но действие есть
    "Фон: исходные place и shot01_bg не предоставлены; не добавлять окружение. Действие: взрослая "
    "сухопутная черепаха неподвижно стоит на четырёх лапах." + STYLE,
]


@pytest.mark.parametrize("text", HOLLOW_REAL)
def test_hollow_forms_count_as_missing(text: str) -> None:
    assert image_prompt_missing(text) is True


@pytest.mark.parametrize("text", REAL)
def test_real_scene_is_not_missing(text: str) -> None:
    assert image_prompt_missing(text) is False


@pytest.mark.parametrize("text", HOLLOW_REAL[:3])
def test_writeable_drops_long_hollow_ops(text: str) -> None:
    ops = [{"frame_uuid": "a" * 24, "fields": {"промт_картинки": text}}]
    assert ipb.writeable_img_pr_ops(ops) == []


def test_allow_hollow_flag_is_respected_by_img_pr(monkeypatch) -> None:
    # На этой линии поля в Settings нет: флаг читается через getattr (generation_options).
    monkeypatch.setitem(settings.__dict__, "images_allow_hollow_prompts", True)
    text = HOLLOW_REAL[0]
    assert image_prompt_missing(text) is False
    ops = [{"frame_uuid": "a" * 24, "fields": {"промт_картинки": text}}]
    assert len(ipb.writeable_img_pr_ops(ops)) == 1


# --- платный гейт картинок -----------------------------------------------------------------


class _Reached(Exception):
    pass


def _frame(n: int, prompt: str) -> SimpleNamespace:
    return SimpleNamespace(id=n, number=n, uuid=f"{n:024x}", voiceover_text=f"vo {n}",
                           image_prompt=prompt, attrs={}, status=None)


async def _images_gate(monkeypatch, frames) -> None:
    from app.models import ProjectStatus
    from app.orchestrator.steps import generate_images as gi

    async def _no_recover(*_a, **_k):
        return []

    class _Res:
        def scalars(self):
            return SimpleNamespace(all=lambda: list(frames))

    class _Session:
        async def execute(self, *_a, **_k):
            return _Res()

    def _past_gate(*_a, **_k):
        raise _Reached()

    monkeypatch.setattr("app.services.anim_pr_sidecar.ensure_anim_pr_sidecar", lambda pid: None)
    monkeypatch.setattr("app.services.artifact_recovery.recover_scene_images_from_disk", _no_recover)
    monkeypatch.setattr(gi, "is_skippable_empty_prompt", _past_gate)
    project = SimpleNamespace(id=14, status=ProjectStatus.generating_images, meta={},
                              data_dir=Path(tempfile.gettempdir()))
    await gi.run(_Session(), project, None)


@pytest.mark.asyncio
async def test_paid_images_gate_blocks_single_phrase_no_data_prompt(monkeypatch) -> None:
    # #14 кадры 5–8: одна фраза + STYLE — раньше нужно было ≥3 фраз, и кадр шёл в оплату.
    frames = [_frame(5, "Нет исходных данных для заполнения." + STYLE), _frame(1, REAL[0])]
    with pytest.raises(RuntimeError, match="промты картинок пустые"):
        await _images_gate(monkeypatch, frames)


@pytest.mark.asyncio
async def test_paid_images_gate_lets_real_prompts_through(monkeypatch) -> None:
    with pytest.raises(_Reached):
        await _images_gate(monkeypatch, [_frame(1, REAL[0]), _frame(2, REAL[1])])


# --- раннер: смешанный батч -----------------------------------------------------------------


def _runner_env(monkeypatch, tmp_path, frames, answer):
    from app.services import chatgpt_xlsx as cx
    from app.services import xlsx_gpt_flow as xgf
    from app.services import xlsx_step_runners as xsr

    prompt_file = tmp_path / "prompt_img_pr.txt"
    prompt_file.write_text("master", encoding="utf-8")
    calls: list[list[int]] = []
    applied: list[str] = []
    by_uuid = {fr.uuid: fr for fr in frames}

    class FakeGpt:
        async def new_conversation(self):
            return None

        async def ask_with_files(self, msg, attach, **_kw):
            uuids = [ln.split(" = ")[1].strip() for ln in msg.splitlines()
                     if ln.startswith("кадр ") and " = " in ln]
            nums = [by_uuid[u].number for u in uuids if u in by_uuid]
            calls.append(nums)
            return json.dumps({"ops": [op for u in uuids if u in by_uuid
                                       for op in answer(by_uuid[u], len(calls))]}, ensure_ascii=False)

    async def _ctx(_project, **_kw):
        return list(frames), [], "", list(frames)

    async def _under_lock(_pid, _name, fn):
        return await fn()

    def _db_frames(_p, tmp_dir, batch, *_a, batch_tag="", **_kw):
        out = Path(tmp_dir) / f"db_{batch_tag}.json"
        out.write_text("{}", encoding="utf-8")
        return out

    async def _apply(_project, ops, **_kw):
        applied.extend(ipb.uuid_of_op(op) for op in ops)
        return ops

    monkeypatch.setattr(xsr, "_ensure_project_xlsx", lambda p: tmp_path / "project.xlsx")
    monkeypatch.setattr(cx, "tmp_gpt_dir", lambda p: tmp_path)
    monkeypatch.setattr(cx, "write_img_pr_prompt_file", lambda p, d, ts=None: prompt_file)
    monkeypatch.setattr(cx, "ensure_current_voiceover", lambda p: None)
    monkeypatch.setattr(cx, "chat_message", lambda *a, **k: "img_pr\n")
    monkeypatch.setattr("app.services.img_pr_style.resolve_project_img_style", lambda *a, **k: None)
    monkeypatch.setattr(xsr, "_load_img_pr_context", _ctx)
    monkeypatch.setattr(xsr, "_write_img_pr_db_frames_for", _db_frames)
    monkeypatch.setattr(xsr, "_apply_img_pr_ops_now", _apply)
    monkeypatch.setattr(xsr, "img_pr_live_streams", lambda p: 1)
    monkeypatch.setattr("app.services.gpt_client.ApiGptClient", FakeGpt)
    monkeypatch.setattr(xgf, "run_under_xlsx_lock", _under_lock)
    monkeypatch.setattr(xsr, "_EMPTY_OPS_BACKOFF_S", (0.0,), raising=False)
    project = SimpleNamespace(id=14, slug="t", data_dir=tmp_path, meta={})
    return xsr, project, calls, applied


def _no_data(fr):
    return [{"frame_uuid": fr.uuid, "fields": {"промт_картинки": "Нет исходных данных для заполнения"}}]


def _real(fr):
    return [{"frame_uuid": fr.uuid, "fields": {"промт_картинки": REAL[0]}}]


@pytest.mark.asyncio
@pytest.mark.parametrize("numbers,no_source", [((5, 6, 7), {5, 6}), ((1, 2), {1})])
async def test_mixed_batch_reasks_omitted_frame(monkeypatch, tmp_path, numbers, no_source) -> None:
    frames = [SimpleNamespace(number=n, uuid=f"{n:024x}", voiceover_text=f"vo {n}",
                              image_prompt=None, attrs={}) for n in numbers]
    omitted = [n for n in numbers if n not in no_source]

    def answer(fr, call_no):
        if fr.number in no_source:
            return _no_data(fr)
        return [] if call_no == 1 else _real(fr)  # 1-й ответ кадр пропускает

    xsr, project, calls, applied = _runner_env(monkeypatch, tmp_path, frames, answer)
    with pytest.raises(ipb.ImgPrNoSourceData) as err:
        await xsr.run_img_pr_xlsx(project, n_frames=len(frames), project_id=14)
    assert err.value.frame_numbers == sorted(no_source)
    assert len(calls) >= 2, f"пропущенный кадр не переспрошен: {calls}"
    assert all(set(c) <= set(omitted) for c in calls[1:]), calls  # no-source не переспрашиваем
    # Эта линия пишет в базу один раз, в шаге: готовые промты едут в ошибке.
    assert applied == []
    assert {f"{n:024x}" for n in omitted} <= {ipb.uuid_of_op(op) for op in err.value.apply_ops}


@pytest.mark.asyncio
async def test_partial_success_does_not_note_terminal_unusable(monkeypatch, tmp_path) -> None:
    frames = [SimpleNamespace(number=n, uuid=f"{n:024x}", voiceover_text=f"vo {n}",
                              image_prompt=None, attrs={}) for n in (1, 2)]

    def answer(fr, _call_no):
        return _no_data(fr) if fr.number == 1 else _real(fr)

    xsr, project, _calls, _applied = _runner_env(monkeypatch, tmp_path, frames, answer)
    with patch("app.services.node_capabilities.note_image_prompt_unusable") as note:
        with pytest.raises(ipb.ImgPrNoSourceData):
            await xsr.run_img_pr_xlsx(project, n_frames=2, project_id=14)
    note.assert_not_called()


# --- код ошибки -----------------------------------------------------------------------------


def test_error_code_and_plain_text() -> None:
    from app.services.error_catalog import ERROR_CATALOG, describe_error

    code, text = describe_error(ipb.ImgPrNoSourceData([6, 5]))
    assert code == "img_pr_no_scene_data" and code in ERROR_CATALOG
    assert text == ("У кадров 5, 6 нет данных сцены (место, фон, действие) — "
                    "сначала нужна работа с GPT, потом промты картинок.")
    for word in ("img_pr", "generate_image_prompts", "place", "shot01"):
        assert word not in text


# --- шаг: 2-я попытка только если есть пропущенные кадры --------------------------------------


async def _run_step(monkeypatch, answer, *, prerun_runner: bool = False):
    """Настоящий раннер img_pr + настоящий шаг; GPT подменён, база — SQLite.

    ``answer(frame_number, call_no)`` → текст промта или None (кадр пропущен).
    ``prerun_runner`` — сначала отдельный прогон раннера (чекпоинт есть, база
    пуста): так шаг видит рестарт из чекпоинта.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.db as app_db
    from app.models import Base, Frame, Project, ProjectStatus
    from app.orchestrator.steps import generate_image_prompts
    from app.services import chatgpt_xlsx as cx
    from app.services import db_v2
    from app.services import xlsx_gpt_flow as xgf
    from app.services import xlsx_step_runners as xsr

    data = Path(tempfile.mkdtemp())
    engine = create_async_engine("sqlite+aiosqlite:///" + str(data / "t.db"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(app_db, "SessionLocal", sessions)
    calls: list[list[int]] = []
    prompt_file = data / "prompt_img_pr.txt"
    prompt_file.write_text("master", encoding="utf-8")

    async def _ctx(project, **_kw):
        async with sessions() as s:
            frs = (await s.scalars(select(Frame).where(Frame.project_id == project.id))).all()
        frs = [fr for fr in frs if not (fr.image_prompt or "").strip()]
        return list(frs), [], "", list(frs)

    class FakeGpt:
        async def new_conversation(self):
            return None

        async def ask_with_files(self, msg, attach, **_kw):
            # Адресация батча; справочная карта uuid всего шага — не батч.
            head = msg.split("# uuid map")[0]
            uuids = [ln.split(" = ")[1].strip() for ln in head.splitlines()
                     if ln.startswith("кадр ") and " = " in ln]
            calls.append([int(u, 16) for u in uuids])
            ops = []
            for u in uuids:
                text = answer(int(u, 16), len(calls))
                if text is not None:
                    ops.append({"frame_uuid": u, "fields": {"промт_картинки": text}})
            return json.dumps({"ops": ops}, ensure_ascii=False)

    async def _under_lock(_pid, _name, fn):
        return await fn()

    def _db_frames(_p, tmp_dir, batch, *_a, batch_tag="", **_kw):
        out = Path(tmp_dir) / f"db_{batch_tag}.json"
        out.write_text("{}", encoding="utf-8")
        return out

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(xsr, "_ensure_project_xlsx", lambda p: data / "project.xlsx")
    monkeypatch.setattr(cx, "tmp_gpt_dir", lambda p: data)
    monkeypatch.setattr(cx, "write_img_pr_prompt_file", lambda p, d, ts=None: prompt_file)
    monkeypatch.setattr(cx, "ensure_current_voiceover", lambda p: None)
    monkeypatch.setattr(cx, "chat_message", lambda *a, **k: "img_pr\n")
    monkeypatch.setattr("app.services.img_pr_style.resolve_project_img_style", lambda *a, **k: None)
    monkeypatch.setattr(xsr, "_load_img_pr_context", _ctx)
    monkeypatch.setattr(xsr, "_write_img_pr_db_frames_for", _db_frames)
    monkeypatch.setattr(xsr, "img_pr_live_streams", lambda p: 1)
    monkeypatch.setattr(xsr, "_EMPTY_OPS_BACKOFF_S", (0.0,), raising=False)
    monkeypatch.setattr("app.services.gpt_client.ApiGptClient", FakeGpt)
    monkeypatch.setattr(xgf, "run_under_xlsx_lock", _under_lock)
    monkeypatch.setattr(db_v2, "backfill_project_v2", _noop)
    monkeypatch.setattr(generate_image_prompts, "_sheet_for_project",
                        lambda p: SimpleNamespace(ensure_initialized=lambda **k: None))
    try:
        async with sessions() as session:
            project = Project(id=2, slug="s", title="s", topic="s",
                              status=ProjectStatus.generating_image_prompts, meta={})
            session.add(project)
            for n in (5, 6, 7):
                session.add(Frame(project_id=2, number=n, uuid=f"{n:024x}", sort_key=n,
                                  # main: шаг не идёт к модели без постановки кадра (место/фон/…).
                                  voiceover_text=f"vo {n}", attrs={"place": "лес"}))
            await session.commit()
            with patch.object(Project, "data_dir", property(lambda _: data)):
                if prerun_runner:
                    with pytest.raises(ipb.ImgPrNoSourceData):
                        await xsr.run_img_pr_xlsx(project, n_frames=3, project_id=2)
                    assert sorted(ipb.load_checkpoint(data).get("done_uuids") or []) == [f"{7:024x}"]
                with pytest.raises(Exception) as err:
                    await generate_image_prompts.run(session, project, SimpleNamespace())
            async with sessions() as s:
                filled = {fr.number: (fr.image_prompt or "").strip()
                          for fr in (await s.scalars(select(Frame))).all()}
        return calls, err.value, filled
    finally:
        await engine.dispose()


def _mixed(n: int, _call_no: int) -> str | None:
    return "Нет исходных данных для заполнения" if n in (5, 6) else REAL[0]


@pytest.mark.asyncio
async def test_step_mixed_batch_writes_ready_prompts_and_stops(monkeypatch) -> None:
    """5, 6 — «нет данных», 7 — сцена: 7 попадает в базу, 5 и 6 не переспрашиваются."""
    from app.services.error_catalog import describe_error

    calls, err, filled = await _run_step(monkeypatch, _mixed)
    assert calls == [[5, 6, 7]], calls
    assert isinstance(err, ipb.ImgPrNoSourceData) and err.frame_numbers == [5, 6]
    assert describe_error(err)[0] == "img_pr_no_scene_data"
    assert filled[7] == REAL[0]
    assert filled[5] == "" and filled[6] == ""


@pytest.mark.asyncio
async def test_step_restart_from_checkpoint_writes_checkpointed_prompts(monkeypatch) -> None:
    """Рестарт: 7 только в чекпоинте — шаг кладёт его в базу, 5 и 6 спрашивает один раз."""
    calls, err, filled = await _run_step(monkeypatch, _mixed, prerun_runner=True)
    assert calls == [[5, 6, 7], [5, 6]], calls
    assert isinstance(err, ipb.ImgPrNoSourceData) and err.frame_numbers == [5, 6]
    assert filled[7] == REAL[0]


@pytest.mark.asyncio
async def test_step_reasks_only_omitted_frame(monkeypatch) -> None:
    """7 модель сперва пропустила: переспрашивается только 7, кадры «нет данных» — нет."""

    def answer(n: int, call_no: int) -> str | None:
        if n in (5, 6):
            return "Нет исходных данных для заполнения"
        return None if call_no == 1 else REAL[0]

    calls, err, filled = await _run_step(monkeypatch, answer)
    assert calls[0] == [5, 6, 7] and len(calls) >= 2, calls
    assert all(c == [7] for c in calls[1:]), calls
    assert isinstance(err, ipb.ImgPrNoSourceData) and err.frame_numbers == [5, 6]
    assert filled[7] == REAL[0]
