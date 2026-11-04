"""Отчёт по данным работы с GPT (report.gpt_data): действие, формат, маршрут просьбы, ответ чата.

Просьба заказчика: «отчёты в определённом формате по данным, которые генерируют GPT ноды».
Раньше чат ставил node.output на каждую ноду (статус, модель и промт — дважды одной строкой
«Работа с GPT») или пересказывал просьбу. Здесь: что записали ноды — по кадрам, персонажи и сцены,
таблица в чате и файлы xlsx и html; ноды названы различимо; нет данных — честно.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from app.models import Entity, Frame, Project, ProjectStatus
from app.orchestrator.agents import data_report as dr
from app.services.action_layer import builtin_insight as bi
from app.services.action_layer import report_data_format as fmt
from app.services.action_layer import report_gpt_data as rgd
from app.services.action_layer.context import ActionCallError
from app.settings import settings
from tests.action_layer_support import env  # noqa: F401

_SLUGS = itertools.count(1)


class FakeCtx:
    def __init__(self, routes):
        self.routes = routes

    async def call(self, method, path, *, params=None, json=None, timeout=0):
        val = self.routes.get((method, path))
        if val is None:
            raise ActionCallError(404, "нет")
        return val


def _meta() -> dict:
    return {
        "canvas_graph": {
            "nodes": [
                {"id": "n_split", "type": "split", "data": {"label": "Разбивка"}},
                {"id": "n_excel_gpt_1", "type": "excel_gpt", "data": {"label": "Работа с GPT"}},
                {"id": "n_excel_gpt_2", "type": "excel_gpt", "data": {"label": "Работа с GPT"}},
            ],
            "edges": [],
        }
    }


REPLY_1 = """```json
{"ops": [
  {"target": "frame", "frame_uuid": "u1", "fields": {"персонажи": "c01", "место": "кухня",
   "действие": "кот прыгает"}},
  {"target": "frame", "frame_uuid": "u2", "fields": {"персонажи": "c01, c02", "место": "двор"}},
  {"target": "frame", "frame_uuid": "u-gone", "fields": {"место": "старый кадр"}}
 ],
 "characters": [{"id": "c01", "имя": "Кот Барсик", "внешность": "рыжий, пушистый"},
                {"id": "c02", "имя": "Пёс Шарик", "характер": "весёлый"}],
 "scenes": [{"id_scene": "s1", "место": "кухня", "смысл_сцены": "утро", "персонажи_сцены": "c01"}]}
```"""


async def _seed(env, *, frames=3, meta=None, entities=(), attrs=None) -> tuple[int, str]:  # noqa: F811
    async with env.factory() as s:
        p = Project(
            topic="тема",
            title="Ролик про кота",
            slug=f"gd-{next(_SLUGS)}",
            status=ProjectStatus.images_ready,
            meta=dict(meta if meta is not None else _meta()),
        )
        s.add(p)
        await s.flush()
        for i in range(1, frames + 1):
            s.add(
                Frame(
                    project_id=p.id,
                    number=i,
                    voiceover_text=f"реплика {i}",
                    image_prompt=f"промт картинки {i}",
                    attrs=dict((attrs or {}).get(i) or {}),
                    uuid=f"u{i}",
                )
            )
        for code, name in entities:
            s.add(Entity(project_id=p.id, type="character", code=code, name=name))
        await s.commit()
        return p.id, p.slug


def _routes(pid: int, slug: str, meta: dict, runs=None) -> dict:
    detail = {"id": pid, "slug": slug, "title": "Ролик про кота", "topic": "тема", "meta": meta}
    return {
        ("GET", f"/projects/{pid}"): detail,
        ("GET", f"/projects/{pid}/run"): {"id": 1, "node_runs": runs or []},
    }


def _reply(slug: str, key: str, text: str) -> None:
    up = settings.data_dir / "videos" / slug / "excel_gpt_uploads" / key
    up.mkdir(parents=True, exist_ok=True)
    (up / "gpt_reply.txt").write_text(text, encoding="utf-8")


RUNS = [
    {"node_key": "n_excel_gpt_1", "node_type": "excel_gpt", "status": "done"},
    {"node_key": "n_excel_gpt_2", "node_type": "excel_gpt", "status": "pending"},
]


async def _run(env, pid, slug, params, runs=RUNS, meta=None):  # noqa: F811
    ctx = FakeCtx(_routes(pid, slug, meta or _meta(), runs))
    return await rgd.report_gpt_data(ctx, {"project_id": pid, **params})


# ── действие ──


async def test_report_rows_characters_scenes_and_files(env) -> None:  # noqa: F811
    pid, slug = await _seed(env, frames=3)
    _reply(slug, "n_excel_gpt_1", REPLY_1)
    out = await _run(env, pid, slug, {})

    assert out["summary"]["frames"] == 3 and out["summary"]["characters"] == 2
    # Запись в кадр, которого в проекте нет (uuid не найден), в таблицу не идёт и не выдумывается.
    assert "старый кадр" not in out["text_ru"]
    text = out["text_ru"]
    assert "Отчёт по данным работы с GPT, проект #" in text
    assert "«Работа с GPT 1»: готово, записала кадров 2" in text
    assert "- Нода «Работа с GPT 2» ещё не запускалась, данных нет." in text
    assert "| № кадра | Закадровый текст | Сцена | Место | Персонажи | Действие |" in text
    assert "| 1 | реплика 1 | — | кухня | c01 (Кот Барсик) | кот прыгает |" in text
    assert "c02 (Пёс Шарик)" in text
    assert "- c01 Кот Барсик (кадры 1–2): рыжий, пушистый" in text
    assert "Сцены:\n- s1: кухня" in text
    assert "[Скачать отчёт (xlsx)](/api/projects/" in text and "(html)" in text

    wb = load_workbook(out["xlsx_path"])
    assert wb.sheetnames == ["Кадры", "Персонажи", "Сцены", "Ноды GPT", "Итог"]
    rows = list(wb["Кадры"].iter_rows(values_only=True))
    assert rows[0] == ("№ кадра", "Закадровый текст", "Сцена", "Место", "Персонажи", "Действие")
    assert rows[1] == (1, "реплика 1", None, "кухня", "c01 (Кот Барсик)", "кот прыгает")
    assert len(rows) == 4
    chars = list(wb["Персонажи"].iter_rows(values_only=True))
    assert chars[1][:3] == ("c01", "Кот Барсик", "рыжий, пушистый") and len(chars) == 3
    scenes = list(wb["Сцены"].iter_rows(values_only=True))
    assert scenes[1][0] == "s1" and scenes[1][1] == "кухня"
    html = Path(out["html_path"]).read_text(encoding="utf-8")
    assert "Кот Барсик" in html and "кот прыгает" in html and "<script" not in html


async def test_columns_preset_and_extra(env) -> None:  # noqa: F811
    pid, slug = await _seed(env, frames=2)
    _reply(slug, "n_excel_gpt_1", REPLY_1)
    only = await _run(env, pid, slug, {"preset": "scenes_characters", "kind": "json"})
    assert [c["label"] for c in only["columns"]] == ["№ кадра", "Сцена", "Место", "Персонажи"]
    assert set(only["rows"][0]) == {"frame", "scene", "place", "characters"}
    img = await _run(env, pid, slug, {"columns": [*fmt.PRESETS["standard"], "image_prompt"], "kind": "json"})
    assert img["rows"][0]["image_prompt"] == "промт картинки 1"
    assert fmt.columns_from_text("только сцены и персонажи") == ("scenes_characters", [])
    assert fmt.columns_from_text("отчёт по данным гпт с промтами картинок") == ("standard", ["image_prompt"])
    assert fmt.columns_from_text("выгрузи все поля") == ("full", [])
    assert fmt.columns_for(None, ["characters", "frame", "bogus"]) == ["frame", "characters"]


async def test_one_node_and_chat_key_resolution(env) -> None:  # noqa: F811
    meta = {
        "canvas_graph": {
            "nodes": [
                {"id": "excel_gpt_1", "type": "excel_gpt", "data": {"label": "Работа с GPT"}},
                {"id": "excel_gpt_2", "type": "excel_gpt", "data": {"label": "Работа с GPT"}},
            ],
            "edges": [],
        }
    }
    pid, slug = await _seed(env, frames=3, meta=meta)
    _reply(slug, "excel_gpt_1", REPLY_1)
    out = await _run(env, pid, slug, {"node_key": "n_excel_gpt_1", "kind": "json"}, runs=[], meta=meta)
    assert out["node_key"] == "excel_gpt_1"
    assert [r["frame"] for r in out["rows"]] == [1, 2]
    with pytest.raises(ActionCallError) as err:
        await _run(env, pid, slug, {"node_key": "n_excel_gpt_9"}, runs=[], meta=meta)
    assert err.value.status == 404 and "«Работа с GPT 1» (excel_gpt_1)" in str(err.value.detail)


async def test_empty_is_honest_and_writes_no_files(env) -> None:  # noqa: F811
    pid, slug = await _seed(env, frames=4)
    out = await _run(env, pid, slug, {}, runs=[])
    assert out["empty"] is True
    assert "данных работы с GPT нет" in out["text_ru"]
    assert "Нода «Работа с GPT 1» ещё не запускалась, данных нет." in out["text_ru"]
    assert "Нода «Работа с GPT 2» ещё не запускалась, данных нет." in out["text_ru"]
    assert "Кадров разбивки 4" in out["text_ru"]
    assert "download_url" not in out and not (settings.data_dir / "videos" / slug / "reports").exists()
    failed = await _run(env, pid, slug, {}, runs=[{"node_key": "n_excel_gpt_1", "status": "failed"}])
    assert "Нода «Работа с GPT 1» запускалась, но упала с ошибкой, данных нет." in failed["text_ru"]
    done = await _run(env, pid, slug, {}, runs=[{"node_key": "n_excel_gpt_1", "status": "done"}])
    assert "Нода «Работа с GPT 1» (готово) не оставила сохранённого ответа с данными." in done["text_ru"]


async def test_frames_from_project_xlsx_when_db_has_none_read_only(env) -> None:  # noqa: F811
    from sqlalchemy import func, select

    pid, slug = await _seed(env, frames=0)
    folder = settings.data_dir / "videos" / slug
    folder.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "план"
    ws.cell(row=2, column=1, value="id scene")
    ws.cell(row=8, column=1, value="персонажи")
    ws.cell(row=49, column=1, value="закадровый текст")
    ws.cell(row=54, column=1, value="Место")
    for i, (vo, sc, ch, pl) in enumerate(
        [("лиса смотрит", "scene_01", "c01", "лес"), ("лиса прыгает", "scene_01", "c01", "снег")], start=3
    ):
        ws.cell(row=49, column=i, value=vo)
        ws.cell(row=2, column=i, value=sc)
        ws.cell(row=8, column=i, value=ch)
        ws.cell(row=54, column=i, value=pl)
    wb.save(folder / "project.xlsx")
    _reply(slug, "n_excel_gpt_1", '{"ops": [], "characters": [{"id": "c01", "имя": "Лиса"}]}')

    out = await _run(env, pid, slug, {"kind": "json"})
    assert out["summary"]["frames_source"].startswith("из project.xlsx")
    assert [(r["frame"], r["voiceover_text"], r["scene"], r["place"]) for r in out["rows"]] == [
        (1, "лиса смотрит", "scene_01", "лес"),
        (2, "лиса прыгает", "scene_01", "снег"),
    ]
    assert out["rows"][0]["characters"] == "c01 (Лиса)"
    assert out["scenes"][0]["id"] == "scene_01" and out["scenes"][0]["frames"] == "1–2"
    async with env.factory() as s:
        assert await s.scalar(select(func.count(Frame.id)).where(Frame.project_id == pid)) == 0


async def test_characters_fall_back_to_project_entities(env) -> None:  # noqa: F811
    pid, slug = await _seed(env, frames=2, entities=[("c01", "Барсик")], attrs={1: {"персонажи": "c01"}})
    _reply(slug, "n_excel_gpt_1", '{"ops": [{"frame_uuid": "u2", "fields": {"место": "двор"}}]}')
    out = await _run(env, pid, slug, {"kind": "json"})
    assert [(c["id"], c["name"], c["source"], c["frames"]) for c in out["characters"]] == [
        ("c01", "Барсик", "база", "1")
    ]


async def test_characters_without_cards_come_from_frame_codes(env) -> None:  # noqa: F811
    pid, slug = await _seed(env, frames=2, attrs={1: {"персонажи": "c01, c02"}, 2: {"персонажи": "c02"}})
    _reply(slug, "n_excel_gpt_1", '{"ops": [{"frame_uuid": "u1", "fields": {"место": "лес"}}]}')
    out = await _run(env, pid, slug, {"kind": "json"})
    assert [(c["id"], c["frames"], c["source"]) for c in out["characters"]] == [
        ("c01", "1", "кадры"),
        ("c02", "1–2", "кадры"),
    ]


def test_nodes_without_saved_reply_share_one_line() -> None:
    def node(label, status, has):
        return {
            "label": label,
            "status_ru": status,
            "has_data": has,
            "note": rgd.empty_note(label, status),
            "frames_written": 1,
            "fields_ru": "",
            "characters": 0,
            "scenes": 0,
        }

    lines = rgd._node_lines(
        [
            node("Каркас", "готово", False),
            node("Стиль", "готово", False),
            node("Работа с GPT 2", "ждёт", False),
            node("Работа с GPT 1", "готово", True),
        ]
    )
    assert "- Отработали, но сохранённого ответа с данными не оставили: «Каркас», «Стиль»." in lines
    assert "- Нода «Работа с GPT 2» ещё не запускалась, данных нет." in lines
    assert len(lines) == 3


async def test_chat_table_is_clipped_with_count(env) -> None:  # noqa: F811
    pid, slug = await _seed(env, frames=14, attrs={i: {"место": f"место {i}"} for i in range(1, 15)})
    _reply(slug, "n_excel_gpt_1", REPLY_1)
    out = await _run(env, pid, slug, {})
    assert f"Показаны первые {fmt.CHAT_ROWS} из 14 кадров, полный отчёт в файле." in out["text_ru"]
    assert len(load_workbook(out["xlsx_path"])["Кадры"]["A"]) == 15


async def test_foreign_or_missing_project_is_not_found(env) -> None:  # noqa: F811
    with pytest.raises(ActionCallError):
        await rgd.report_gpt_data(FakeCtx({}), {"project_id": 9999})


async def test_member_sees_no_money(env, monkeypatch) -> None:  # noqa: F811
    from app.services.action_layer import plan

    if not hasattr(plan, "member_view"):
        pytest.skip("на этой линии нет участников студии")
    pid, slug = await _seed(env, frames=2)
    _reply(slug, "n_excel_gpt_1", REPLY_1)
    monkeypatch.setattr(plan, "member_view", lambda: True)
    out = await _run(env, pid, slug, {"kind": "json"})
    flat = str(out)
    assert "cost" not in flat and "spent" not in flat and "$" not in flat


# ── подписи нод и node.output ──


def test_duplicate_node_labels_get_their_number() -> None:
    labels = bi._labels(_meta())
    assert labels["n_excel_gpt_1"] == "Работа с GPT 1" and labels["n_excel_gpt_2"] == "Работа с GPT 2"
    assert labels["n_split"] == "Разбивка"


async def test_node_output_names_each_gpt_node_distinctly(env) -> None:  # noqa: F811
    pid, slug = await _seed(env, frames=1)
    ctx = FakeCtx(_routes(pid, slug, _meta(), RUNS))
    one = await bi._node_output(ctx, {"project_id": pid, "node_key": "n_excel_gpt_1"})
    two = await bi._node_output(ctx, {"project_id": pid, "node_key": "n_excel_gpt_2"})
    assert one["title"] == "Работа с GPT 1" and two["title"] == "Работа с GPT 2"
    assert one["text_ru"] != two["text_ru"]
    assert one["text_ru"].startswith("Нода «Работа с GPT 1»")


# ── регистрация ──


def test_registered_read_only_free_and_in_chat_catalog() -> None:
    from app.orchestrator.actions import metadata_of
    from app.orchestrator.agents.action_catalog import BASE_READ_IDS
    from app.services.action_layer.planner_catalog import select_catalog
    from app.services.action_layer.spec import ensure_builtin_actions, registry

    ensure_builtin_actions()
    spec = registry.find("report.gpt_data")
    assert spec is not None and spec.risk == "read" and not spec.spends_money and not spec.destructive
    meta = metadata_of("report.gpt_data")
    assert meta.risk == "none" and meta.read_only and not meta.paid
    assert "report.gpt_data" in BASE_READ_IDS
    picked = select_catalog("какие сцены и персонажи записал гпт", None, registry, 8).ids
    assert "report.gpt_data" in picked


# ── маршрут просьбы ──


@pytest.mark.parametrize(
    ("text", "task_project", "expected"),
    [
        ("отчёт по сценам проекта 7", None, {"project_id": 7, "kind": "chat"}),
        ("какие персонажи в проекте 7", None, {"project_id": 7, "kind": "chat"}),
        (
            "выгрузи данные работы с гпт 1 проекта 5 в xlsx",
            None,
            {"project_id": 5, "kind": "xlsx", "node_key": "n_excel_gpt_1"},
        ),
        (
            "сделай отчёт по данным гпт с промтами картинок",
            6,
            {"project_id": 6, "kind": "chat", "columns": [*fmt.PRESETS["standard"], "image_prompt"]},
        ),
        (
            "сделай отчет по данным из работы с гпт в проекте 7: какие сцены и персонажи",
            None,
            {"project_id": 7, "kind": "chat"},
        ),
        (
            "отчёт второй работы с гпт проекта 4, только сцены и персонажи",
            None,
            {"project_id": 4, "kind": "chat", "node_key": "n_excel_gpt_2", "preset": "scenes_characters"},
        ),
        ("покажи персонажей #12 в html", None, {"project_id": 12, "kind": "html"}),
    ],
)
def test_phrases_route_to_data_report(text, task_project, expected) -> None:
    step = dr.report_step(text, task_project)
    assert step is not None and step["action"] == "report.gpt_data"
    assert step["params"] == expected
    assert step["paid"] is False and step["mutating"] is False


@pytest.mark.parametrize(
    "text",
    [
        "запусти работу с гпт в проекте 7",
        "отчёт по нодам проекта 7",
        "какие поля перезаписала вторая работа с гпт в проекте 7",
        "отчёт по проектам 5, 6, 7",
        "покажи промт ноды n_excel_gpt_1 проекта 7",
        "поменяй модель на работе с гпт 1 в проекте 7",
        "сколько потратили на проект 7",
        "какие персонажи",  # проект не назван и у задачи его нет — решает модель (спросит)
        "отчёт по сценам проектов 5 и 7",
    ],
)
def test_other_requests_are_left_to_the_model(text) -> None:
    assert dr.report_step(text, None) is None


async def test_router_planner_builds_the_plan_without_the_model() -> None:
    from app.orchestrator.agents.planner import RouterPlanner

    class _NoModel:
        def __getattr__(self, name):
            raise AssertionError("модель не должна вызываться")

    planner = RouterPlanner(_NoModel())
    steps = await planner.make_plan(
        "сделай отчет по данным из работы с гпт в проекте 7: какие сцены и персонажи"
    )
    assert [(s.action, s.params) for s in steps] == [("report.gpt_data", {"project_id": 7, "kind": "chat"})]
    assert planner.last_from_code is True and steps[0].risk == "none"


def test_catalog_rule_steers_away_from_node_output() -> None:
    from app.orchestrator.agents.planner import _CATALOG_RULES

    rule = next(r for r in _CATALOG_RULES if "report.gpt_data" in r)
    assert "не node.output" in rule


def test_question_reads_use_data_report() -> None:
    from app.orchestrator.agents.question_context import plan_reads

    reads = plan_reads("какие персонажи в проекте 7?", 7)
    assert ("report.gpt_data", {"project_id": 7, "kind": "chat"}) in reads
    assert not any(a == "report.nodes" for a, _p in reads)
    assert ("report.gpt_data", {"project_id": 7, "kind": "chat"}) in plan_reads(
        "какие сцены в проекте 7", None
    )


# ── ответ чата ──


def _note(text="| № кадра |\n|---|\n| 1 |"):
    from app.orchestrator.agents.summarizer import note_from_result

    body = {
        "text_ru": "Отчёт по данным работы с GPT, проект #7: кадров 1.\n" + text,
        "downloads": [
            {"url": "/api/projects/7/reports/gpt_data_report_7_x.xlsx", "file_format": "xlsx"},
            {"url": "/api/projects/7/reports/gpt_data_report_7_x.html", "file_format": "html"},
        ],
    }
    return note_from_result("report.gpt_data", {"status": "ok", "result": body}, readonly=True)


def test_summary_note_keeps_table_and_links() -> None:
    note = _note()
    assert note.known and "| № кадра |" in note.text
    assert note.links == [
        "[Скачать отчёт (xlsx)](/api/projects/7/reports/gpt_data_report_7_x.xlsx)",
        "[Скачать отчёт (html)](/api/projects/7/reports/gpt_data_report_7_x.html)",
    ]


async def test_summarizer_shows_report_verbatim_without_model() -> None:
    from app.orchestrator.agents.summarizer import TaskFacts, TaskSummarizer

    class _NoModel:
        def __getattr__(self, name):
            raise AssertionError("модель не должна пересказывать отчёт")

    facts = TaskFacts(goal="сделай отчёт по сценам проекта 7", status="done", steps=[_note()])
    text = await TaskSummarizer(_NoModel()).summarize(facts, run_id="r")
    assert text.startswith("Отчёт по данным работы с GPT, проект #7")
    assert "| № кадра |" in text and "Сформируй" not in text
