"""report.projects у участника: без ссылок на общие файлы отчётов (их отдают только админу),
и имена файлов отчётов уникальны на вызов."""

from __future__ import annotations

from app.models import Project, ProjectStatus
from app.services.action_layer import report_gpt as rg
from app.services.action_layer.context import ActionCallError
from app.settings import settings
from tests.action_layer_support import env  # noqa: F401


class FakeCtx:
    def __init__(self, routes):
        self.routes = routes

    async def call(self, method, path, *, params=None, json=None, timeout=0):
        val = self.routes.get((method, path))
        if val is None:
            raise ActionCallError(404, "нет")
        return val


async def _project(env) -> tuple[int, FakeCtx]:
    async with env.factory() as s:
        p = Project(topic="совы", title="Совы", slug="sovy-member", status=ProjectStatus("images_ready"), meta={})
        s.add(p)
        await s.commit()
        pid = p.id
    detail = {"id": pid, "slug": "sovy-member", "title": "Совы", "topic": "совы", "status": "images_ready",
              "meta": {}, "prompt_overrides": {}}
    return pid, FakeCtx({("GET", f"/projects/{pid}"): detail,
                         ("GET", f"/projects/{pid}/run"): {"id": 1, "node_runs": []}})


async def test_member_gets_no_links_to_admin_only_report_files(env, monkeypatch) -> None:
    import app.services.action_layer.plan as plan

    monkeypatch.setattr(plan, "member_view", lambda: True)
    pid, ctx = await _project(env)
    reports = settings.data_dir / "reports"
    before = set(reports.glob("*")) if reports.exists() else set()
    out = await rg.report_projects(ctx, {"project_ids": [pid], "kind": "chat"})
    assert "download_url" not in out and "downloads" not in out
    assert "/api/projects/reports/" not in out["text_ru"] + out["markdown"]
    assert "скачивает админ" in out["text_ru"]
    assert f"#{pid}" in out["markdown"] or "Совы" in out["markdown"]
    after = set(reports.glob("*")) if reports.exists() else set()
    assert after == before, "участнику файл общего отчёта не пишется"


async def test_admin_still_gets_both_files(env, monkeypatch) -> None:
    import app.services.action_layer.plan as plan

    monkeypatch.setattr(plan, "member_view", lambda: False)
    pid, ctx = await _project(env)
    out = await rg.report_projects(ctx, {"project_ids": [pid], "kind": "chat"})
    assert [d["file_format"] for d in out["downloads"]] == ["html", "xlsx"]
    assert out["download_url"].startswith("/api/projects/reports/")


def test_report_stamp_is_unique_within_one_second() -> None:
    stamps = {rg._stamp() for _ in range(50)}
    assert len(stamps) == 50
    name = f"projects_report_{rg._stamp()}.html"
    from app.web.routers.project_reports import _NAME

    assert _NAME.fullmatch(name)
