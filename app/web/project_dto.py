"""Сборка ProjectDetail / ProjectSummary с live-полями."""

from __future__ import annotations

from app.models import Project
from app.services.mass_factory import is_mass_factory_parent, mass_parent_id
from app.services.step_cancel import is_generation_active
from app.web.schemas import ProjectDetail, ProjectSummary


def project_to_summary(
    project: Project,
    *,
    sidebar_folder_id: str | None = None,
    sidebar_order: int | None = None,
    gen_queue_position: int | None = None,
) -> ProjectSummary:
    meta = project.meta if isinstance(project.meta, dict) else {}
    lane_raw = meta.get("mass_lane_position")
    lane_pos: int | None
    try:
        lane_pos = int(lane_raw) if lane_raw is not None else None
    except (TypeError, ValueError):
        lane_pos = None
    return ProjectSummary(
        id=project.id,
        slug=project.slug,
        title=project.title,
        topic=project.topic,
        status=project.status.value,
        hero_mode=project.hero_mode,
        auto_mode=bool(project.auto_mode),
        created_at=project.created_at,
        updated_at=project.updated_at,
        mass_parent_id=mass_parent_id(project),
        mass_factory=is_mass_factory_parent(project),
        mass_lane_position=lane_pos,
        sidebar_folder_id=sidebar_folder_id,
        sidebar_order=sidebar_order,
        gen_queue_position=gen_queue_position,
    )


def project_to_detail(project: Project) -> ProjectDetail:
    detail = ProjectDetail.model_validate(project)
    meta = dict(project.meta) if isinstance(project.meta, dict) else {}
    detail.mass_parent_id = mass_parent_id(project)
    detail.mass_factory = is_mass_factory_parent(project)
    lane_raw = meta.get("mass_lane_position")
    try:
        detail.mass_lane_position = int(lane_raw) if lane_raw is not None else None
    except (TypeError, ValueError):
        detail.mass_lane_position = None
    detail.generation_active = is_generation_active(project.id)

    # Авто-подтяжка результатов операторов/excel_gpt с диска (защита от stale PATCH / пустой meta)
    try:
        from app.services.gpt_operator import hydrate_check_result_from_disk
        gpt_results = dict(meta.get("gpt_operator_results") or {})
        excel_nodes = dict(meta.get("excel_gpt_nodes") or {})
        changed = False
        for nk, cfg in list(excel_nodes.items()):
            cur = gpt_results.get(nk)
            if not cur or not cur.get("replyPreview"):
                hydrated = hydrate_check_result_from_disk(project, nk, cur)
                if hydrated.get("replyPreview") or hydrated.get("outputPaths"):
                    gpt_results[nk] = hydrated
                    changed = True
                    if hydrated.get("outputPaths") and isinstance(cfg, dict):
                        cfg_copy = dict(cfg)
                        cfg_copy["lastReplyPath"] = str(hydrated["outputPaths"][0])
                        excel_nodes[nk] = cfg_copy
        if changed:
            meta["gpt_operator_results"] = gpt_results
            meta["excel_gpt_nodes"] = excel_nodes
            detail.meta = meta
    except Exception:
        pass

    return detail
