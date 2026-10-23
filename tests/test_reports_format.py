"""Отчёты report.nodes / report.projects в формате отчёта «Кадры»: все пачки, без архивных кадров,
стоимость по нодам только по связи с тратами, HTML рядом с xlsx."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from openpyxl import load_workbook

from app.models import Frame, Project, ProjectStatus
from app.orchestrator.agents.summarizer import download_links
from app.services import spend_guard
from app.services.action_layer import builtin_insight as bi
from app.services.action_layer import report_costs as rc
from app.services.action_layer import report_gpt as rg
from app.services.action_layer import report_html
from app.services.action_layer.context import ActionCallError
from app.services.action_layer.spec import ActionRegistry
from app.settings import settings
from tests.action_layer_support import env  # noqa: F401

T0 = datetime(2026, 10, 21, 14, 0, 0)


class FakeCtx:
    def __init__(self, routes):
        self.routes = routes

    async def call(self, method, path, *, params=None, json=None, timeout=0):
        val = self.routes.get((method, path))
        if val is None:
            raise ActionCallError(404, "нет")
        return val


def _reply(ops: list[dict], **extra) -> str:
    return "```json\n" + json.dumps({"ops": ops, **extra}, ensure_ascii=False) + "\n```"


def _call(run_dir, name, reply, ops_final=None, error=""):
    d = run_dir / name
    d.mkdir(parents=True)
    (d / "reply.txt").write_text(reply, encoding="utf-8")
    if ops_final is not None:
        (d / "ops_final.json").write_text(json.dumps(ops_final, ensure_ascii=False), encoding="utf-8")
    if error:
        (d / "error.txt").write_text(error, encoding="utf-8")


def test_split_costs_uses_only_real_links() -> None:
    runs = [{"node_key": "n_a", "started_at": T0.isoformat(), "finished_at": (T0 + timedelta(minutes=5)).isoformat()},
            {"node_key": "n_b", "started_at": (T0 + timedelta(minutes=4)).isoformat(),
             "finished_at": (T0 + timedelta(minutes=9)).isoformat()}]
    rows = [
        {"created_at": T0 + timedelta(minutes=1), "source": "native_call", "run_id": "native:7:x", "request_id": "req-c",
         "usd": 0.1},
        {"created_at": T0 + timedelta(minutes=2), "source": "native_call", "run_id": "native:7:x", "request_id": "",
         "usd": 0.2},
        # Окна n_a и n_b пересекаются: какая нода потратила — неизвестно, запись не разносим.
        {"created_at": T0 + timedelta(minutes=4, seconds=30), "source": "native_call", "run_id": "native:7:x",
         "request_id": "", "usd": 0.4},
        {"created_at": T0 - timedelta(hours=1), "source": "native_call", "run_id": "native:7:verdict-plan",
         "request_id": "queue-1", "usd": 0.05},
        # Траты чата нодам не приписываются даже во время прогона.
        {"created_at": T0 + timedelta(minutes=2), "source": "orch_llm", "run_id": "orch-task-1", "request_id": "",
         "usd": 0.3},
        {"created_at": "2026-10-21 14:07:00.123456", "source": "native_call", "run_id": "", "request_id": "",
         "usd": 0.01},
    ]
    split = rc.split_costs(rows, {"req-c": {"n_c"}}, runs, {"plan": ["n_plan"], "excel_gpt": ["n_a", "n_b"]}, 7)
    assert round(split.by_node["n_c"], 4) == 0.1 and split.how["n_c"] == [rc.HOW_REQUEST]
    assert round(split.by_node["n_a"], 4) == 0.2 and split.how["n_a"] == [rc.HOW_WINDOW]
    assert round(split.by_node["n_plan"], 4) == 0.05 and split.how["n_plan"] == [rc.HOW_VERDICT]
    assert round(split.by_node["n_b"], 4) == 0.01
    assert round(split.total, 4) == 1.06 and round(split.attributed, 4) == 0.36
    assert round(split.by_source["orch_llm"], 4) == 0.3
    assert rc.cost_text(None) == "нет данных" and rc.cost_text(0.0) == "0.0"


def test_writes_from_batches_reads_every_call_and_skips_archived(tmp_path) -> None:
    run = tmp_path / "node_trace" / "n_excel_gpt_1" / "20261021-140000-000001-1"
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"status": "ok", "calls": 3, "counts": {"fix": 2},
                                              "skipped": [{"uuids": ["u9"], "reason": "гейт"}]}), encoding="utf-8")
    long_text = "длинный закадр " * 300
    _call(run, "call_01_L1", _reply([{"frame_uuid": "u1", "fields": {"закадр": "сырой"}}],
                                    characters=[{"id": "c01", "имя": "Сова"}]),
          ops_final=[{"frame_uuid": "u1", "fields": {"закадр": long_text}}])
    _call(run, "call_02_L1", _reply([{"frame_uuid": "u2", "fields": {"место": "лес", "кадры": [{"id": "1-K1"}]}},
                                     {"frame_uuid": "u-old", "fields": {"место": "архив"}}]))
    _call(run, "call_10_L2", "не json", error="таймаут")
    # Последний ответ (gpt_reply.txt) — только одна пачка; отчёт берёт все из node_trace.
    up = tmp_path / "excel_gpt_uploads" / "n_excel_gpt_1"
    up.mkdir(parents=True)
    (up / "gpt_reply.txt").write_text(_reply([{"frame_uuid": "u2", "fields": {"место": "лес"}}]), encoding="utf-8")
    index = rg.FrameIndex(num_by_uuid={"u1": 1, "u2": 2}, archived_uuids={"u-old"}, archived_numbers={3})
    w = rg.node_writes(tmp_path, "n_excel_gpt_1", index)
    assert w["frames"] == {1: ["voiceover_text"], 2: ["place", "кадры"]}
    assert w["values"][1]["voiceover_text"] == long_text
    assert w["values"][2]["кадры"] == '[{"id": "1-K1"}]'
    assert w["archived_skipped"] == 1 and w["batches"] == 3 and w["batches_unparsed"] == 1
    assert w["batch_errors"] == 1 and w["characters"] == ["c01 — Сова"] and w["source"] == "пачек прогона: 3"
    note = rg._writes_note(w, "готово")
    assert "пачек без разбора: 1" in note and "пачек не записано: 1" in note
    assert "записей в архивные кадры (не показаны): 1" in note
    assert index.is_archived("3") and not index.is_archived("2") and index.is_archived("1000004")
    assert rg.read_batches(tmp_path, "..")["calls"] == []
    # Без дневника прогона — сохранённый ответ, как раньше.
    only = rg.node_writes(tmp_path, "n_other", index)
    assert only["frames"] == {} and only["source"] == ""


def test_xlsx_keeps_value_longer_than_excel_cell(tmp_path) -> None:
    big = "я" * (rg.XLSX_CELL_MAX * 2 + 5)
    path = tmp_path / "r.xlsx"
    rg.write_workbook(path, [("Лист", (("a", "Нода"), ("v", "Значение")), [{"a": "Работа с GPT 1", "v": big}])],
                      [("Итог", 1)])
    ws = load_workbook(path)["Лист"]
    rows = [[c.value for c in r] for r in ws.iter_rows(min_row=2)]
    assert len(rows) == 3 and "".join(r[1] for r in rows) == big
    assert rows[1][0] == "Работа с GPT 1 (продолжение)"


def test_nodes_html_escapes_and_keeps_full_values() -> None:
    value = "<script>alert(1)</script>\n" + "слово " * 500
    gpt = [{"node_key": "n_excel_gpt_1", "label": "Работа с GPT 1", "prompt_title": "Разметка", "model": "m",
            "status_ru": "готово", "frames_written": 1, "fields": ["place"], "fields_ru": "место",
            "frames": {"1": ["place"]}, "values": {"1": {"place": value}}, "characters": [], "scenes": [],
            "note": "", "source": "пачек прогона: 2", "cost_usd": None, "cost_how": ""},
           {"node_key": "n_excel_gpt_2", "label": "Работа с GPT 2", "prompt_title": "", "model": "",
            "status_ru": "готово", "frames_written": 1, "fields": ["place"], "fields_ru": "место",
            "frames": {"1": ["place"]}, "values": {"1": {"place": "поле"}}, "characters": [], "scenes": [],
            "note": "", "source": "", "cost_usd": 0.25, "cost_how": rc.HOW_WINDOW}]
    rows = [{"node_key": "n_excel_gpt_1", "title": "Работа с GPT", "type_ru": "GPT", "status_ru": "готово",
             "attempts": 1, "model": "m", "prompt": "", "checks": "", "result": "", "cost_usd": None,
             "cost_how": "", "errors": ""}]
    page = report_html.render_nodes_html({
        "project_id": 4, "title": "Совы", "slug": "sovy", "stamp": "21.10.2026 14:00", "rows": rows, "gpt": gpt,
        "frames": [{"number": 1, "uuid": "u1", "voiceover_text": "закадр", "place": "лес"}],
        "archived_frames": 2, "runs": [{"node_key": "n_excel_gpt_1", "finished": "2026-10-21T14:05:00",
                                        "status": "ok", "calls": 2, "counts": {"fix": 1}, "dir": "x"}],
        "diary": [], "with_rules": False, "costs": {"total": 1.0, "attributed": 0.25, "unattributed": 0.75,
                                                    "by_source": {"native_call": 1.0}}})
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert ("слово " * 500).strip() in page
    assert "Нода</th><th>Когда</th><th>Итог</th><th>Вызовов GPT</th>" in page
    assert "Работа с GPT 1 → Работа с GPT 2" in page and "да, потом «Работа с GPT 2»" in page
    assert "нет данных" in page and "архивных не показано 2" in page and "временн" not in page


def test_download_links_lists_every_report_file() -> None:
    body = {"download_url": "/api/projects/4/reports/a.html", "file_format": "html",
            "downloads": [{"url": "/api/projects/4/reports/a.html", "file_format": "html"},
                          {"url": "/api/projects/4/reports/a.xlsx", "file_format": "xlsx"},
                          {"url": "https://evil/x.xlsx", "file_format": "xlsx"}]}
    assert download_links(body) == ["[Скачать отчёт (html)](/api/projects/4/reports/a.html)",
                                    "[Скачать отчёт (xlsx)](/api/projects/4/reports/a.xlsx)"]


async def test_report_nodes_reads_batches_skips_archived_and_costs_by_link(env, monkeypatch) -> None:
    meta = {"canvas_graph": {"nodes": [
        {"id": "n_plan", "type": "plan", "data": {"label": "План"}},
        {"id": "n_excel_gpt_1", "type": "excel_gpt", "data": {"label": "Работа с GPT", "slotIndex": 1}},
        {"id": "n_excel_gpt_2", "type": "excel_gpt", "data": {"label": "Работа с GPT", "slotIndex": 2}},
    ], "edges": []}}
    async with env.factory() as s:
        p = Project(topic="совы", title="Совы", slug="sovy-fmt", status=ProjectStatus("images_ready"), meta=meta)
        s.add(p)
        await s.flush()
        s.add(Frame(project_id=p.id, number=1, uuid="u1", voiceover_text="Совы видят ночью", attrs={"место": "лес"}))
        s.add(Frame(project_id=p.id, number=2, uuid="u2", voiceover_text="Совы молчат", attrs={}))
        s.add(Frame(project_id=p.id, number=3, uuid="u3", voiceover_text="старое", attrs={"archived": True}))
        for minute, usd in ((1, 0.2), (2, 0.3)):
            s.add(spend_guard.SpendLedger(created_at=T0 + timedelta(minutes=minute), day="2026-10-21",
                                          kind="estimated", provider="vibecode", project_id=p.id,
                                          run_id=f"native:{p.id}:t", source="native_call", usd=usd, credits=0.0,
                                          note="", request_id="tick-1", ref=""))
        s.add(spend_guard.SpendLedger(created_at=T0, day="2026-10-21", kind="estimated", provider="kie",
                                      project_id=p.id, run_id="orch-task-1", source="orch_llm", usd=0.05,
                                      credits=0.0, note="", request_id="", ref=""))
        await s.commit()
        pid, slug = p.id, p.slug
    root = settings.data_dir / "videos" / slug
    run = root / "node_trace" / "n_excel_gpt_1" / "20261021-140000-000001-1"
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"node_key": "n_excel_gpt_1", "status": "ok", "calls": 2,
                                              "counts": {"fix": 1}, "finished": "2026-10-21T14:05:00"}),
                                  encoding="utf-8")
    _call(run, "call_01_L1", _reply([{"frame_uuid": "u1", "fields": {"место": "дупло"}}]))
    _call(run, "call_02_L1", _reply([{"frame_uuid": "u2", "fields": {"место": "поляна"}},
                                     {"frame_uuid": "u3", "fields": {"место": "архив"}}]))
    node_runs = [{"node_key": "n_excel_gpt_1", "node_type": "excel_gpt", "status": "done", "attempts": 1,
                  "started_at": T0.isoformat(), "finished_at": (T0 + timedelta(minutes=5)).isoformat()},
                 {"node_key": "n_plan", "node_type": "plan", "status": "done", "attempts": 1,
                  "started_at": (T0 - timedelta(hours=2)).isoformat(),
                  "finished_at": (T0 - timedelta(hours=1)).isoformat()}]
    detail = {"id": pid, "slug": slug, "title": "Совы", "topic": "совы", "status": "images_ready", "meta": meta,
              "prompt_overrides": {}}
    ctx = FakeCtx({("GET", f"/projects/{pid}"): detail,
                   ("GET", f"/projects/{pid}/run"): {"id": 1, "node_runs": node_runs}})
    reg = ActionRegistry()
    bi.register_insight(reg)
    report = reg.get("report.nodes").executor

    data = await report(ctx, {"project_id": pid, "kind": "json"})
    one = {g["node_key"]: g for g in data["gpt"]}["n_excel_gpt_1"]
    assert one["frames"] == {"1": ["place"], "2": ["place"]} and one["archived_skipped"] == 1
    assert one["values"]["2"]["place"] == "поляна" and one["cost_usd"] == 0.5
    rows = {r["node_key"]: r for r in data["rows"]}
    assert rows["n_excel_gpt_1"]["cost_usd"] == 0.5 and rows["n_excel_gpt_1"]["cost_how"] == rc.HOW_WINDOW
    assert rows["n_plan"]["cost_usd"] is None
    assert data["summary"]["frames"] == 2 and data["summary"]["archived_frames"] == 1
    assert data["summary"]["spent_usd_estimate"] == 0.55 and data["summary"]["attributed_usd_estimate"] == 0.5

    chat = await report(ctx, {"project_id": pid})
    page = (await env.client.get(chat["download_url"])).text
    assert "поляна" in page and "дупло" in page and "архив</td>" not in page
    assert "Ячейка 3" not in page and "Ячейка 2" in page and "нет данных" in page
    assert "чат (планировщик и критик)" in page
    ws = load_workbook(chat["xlsx_path"])["Ноды"]
    header = [c.value for c in ws[1]]
    costs = {r[0].value: r[header.index("Стоимость, $ (оценка)")].value for r in ws.iter_rows(min_row=2)}
    assert costs["n_plan"] == "нет данных" and costs["n_excel_gpt_1"] == 0.5
