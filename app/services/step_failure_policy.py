"""Политика отказов шага.

Схема (на один running-шаг):
  - каждый fail → soft retry (файлы на диске не трогаем);
  - глобальный счётчик total_fails: 1…9;
  - каждые 3 fail (3, 6) → сон 30 мин, затем новый цикл;
  - на 9-м fail (3 цикла × 3 попытки) → paused + следующий в gen_queue.

Явный сброс шага — только через UI (reset_step), не при ошибках.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.orchestrator.pipeline_steps import step_by_running_status
from app.services.chrome_recovery import (
    clear_chrome_recovery,
    handle_chrome_step_failure,
    is_chrome_infra_error,
)

FAILS_PER_CYCLE = 3
MAX_CYCLES = 3
MAX_TOTAL_FAILS = FAILS_PER_CYCLE * MAX_CYCLES  # 9
SLEEP_MINUTES = 30
# Лишние листы GPT / hotfix normalize — не морозить на полчаса.
XLSX_SHEET_FORMAT_SLEEP_MINUTES = 2

WAIT_PROFILE_ENV = "STEP_FAILURE_WAIT_PROFILE"
WAIT_PROFILE_SAFE = "safe"
WAIT_PROFILE_FAST = "fast"
WAIT_PROFILE_NAMES = (WAIT_PROFILE_SAFE, WAIT_PROFILE_FAST)
FAST_FAILS_PER_CYCLE = 2
FAST_MAX_CYCLES = 2
FAST_SLEEP_MINUTES = 3
FAST_QUICK_SLEEP_MINUTES = 1


WAIT_PROFILE_ENV = "STEP_FAILURE_WAIT_PROFILE"
WAIT_PROFILE_SAFE = "safe"
WAIT_PROFILE_FAST = "fast"
WAIT_PROFILE_NAMES = (WAIT_PROFILE_SAFE, WAIT_PROFILE_FAST)
FAST_FAILS_PER_CYCLE = 2
FAST_MAX_CYCLES = 2
FAST_SLEEP_MINUTES = 3
FAST_QUICK_SLEEP_MINUTES = 1
# Профили ожидания и порог «ретраи исчерпаны» — из video-pipeline int/chat-v1
# (нужны decision_v1, actions_recovery, node_edit).
FATAL_CONFIG_ERROR_CODES = frozenset(
    {
        "gpt_no_key",
        "gpt_no_base",
        "gpt_auth",
        "gpt_model_unauthorized",
        "gpt_model_unsupported",
        "media_no_key",
        "media_auth",
    }
)


@dataclass(frozen=True)
class WaitProfile:
    name: str
    fails_per_cycle: int
    max_cycles: int
    sleep_minutes: int
    quick_sleep_minutes: int
    abandon_on_fatal_config: bool

    @property
    def max_total_fails(self) -> int:
        return self.fails_per_cycle * self.max_cycles


def wait_profile(project: Project | None = None) -> WaitProfile:
    name = ""
    if project is not None:
        meta = project.meta if isinstance(project.meta, dict) else {}
        name = str(meta.get("step_failure_profile") or "").strip().lower()
    if name not in WAIT_PROFILE_NAMES:
        name = (os.environ.get(WAIT_PROFILE_ENV) or "").strip().lower()
    if name == WAIT_PROFILE_FAST:
        return WaitProfile(
            WAIT_PROFILE_FAST,
            FAST_FAILS_PER_CYCLE,
            FAST_MAX_CYCLES,
            FAST_SLEEP_MINUTES,
            FAST_QUICK_SLEEP_MINUTES,
            True,
        )
    return WaitProfile(
        WAIT_PROFILE_SAFE, FAILS_PER_CYCLE, MAX_CYCLES, SLEEP_MINUTES, XLSX_SHEET_FORMAT_SLEEP_MINUTES, False
    )


def abandon_threshold(project: Project | None = None) -> int:
    return wait_profile(project).max_total_fails


def exhausted_threshold(project: Project | None) -> int:
    if project is not None:
        recorded = _failure_state(project).get("abandoned_threshold")
        if isinstance(recorded, int) and not isinstance(recorded, bool) and recorded > 0:
            return recorded
    return abandon_threshold(project)


def is_retry_exhausted(project: Project | None, running_key: str | None = None) -> bool:
    if project is None:
        return False
    fs = _failure_state(project)
    if not fs.get("abandoned_at") or fs.get("abandoned_reason") == "fatal_config":
        return False
    totals = fs.get("total_fails") or {}
    if running_key is not None:
        counts = [int(totals.get(running_key) or 0)]
    else:
        counts = [int(v or 0) for v in totals.values()]
    return bool(counts) and max(counts) >= exhausted_threshold(project)


def sleep_minutes_for_error(error: Exception) -> int:
    msg = str(error)
    if "скачанный xlsx невалиден" in msg and "листы" in msg:
        return XLSX_SHEET_FORMAT_SLEEP_MINUTES
    # Неполный TSV / prose без TSV — книга не тронута; не морозить 30 мин.
    if (
        "всё ещё неполный" in msg
        or "CONTINUE_XLSX" in msg
        or "не вернула TSV" in msg
        or "project.xlsx не изменён" in msg
        or "без writeback" in msg
    ):
        return XLSX_SHEET_FORMAT_SLEEP_MINUTES
    conn_markers = (
        "Cannot connect to host",
        "ClientConnectorError",
        "CDP не доступен",
        "29229",
        "connect call failed",
    )
    if any(m in msg for m in conn_markers):
        return XLSX_SHEET_FORMAT_SLEEP_MINUTES
    return SLEEP_MINUTES


def _meta(project: Project) -> dict[str, Any]:
    m = project.meta
    return dict(m) if isinstance(m, dict) else {}


def _failure_state(project: Project) -> dict[str, Any]:
    meta = _meta(project)
    fs = meta.get("step_failure")
    return dict(fs) if isinstance(fs, dict) else {}


def _save_failure_state(project: Project, fs: dict[str, Any]) -> None:
    meta = _meta(project)
    meta["step_failure"] = fs
    project.meta = meta


def failure_sleep_until(project: Project) -> str | None:
    until = _failure_state(project).get("sleep_until")
    return str(until) if until else None


def is_sleeping(project: Project) -> bool:
    fs = _failure_state(project)
    until = fs.get("sleep_until")
    if not until:
        return False
    try:
        dt = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
    except ValueError:
        return False
    return not datetime.now(UTC) >= dt


def clear_failure_backoff_for_manual_start(project: Project, *, running_key: str) -> bool:
    """Ручной запуск шага — снять sleep и счётчик fails для этого running-статуса."""
    fs = _failure_state(project)
    if not fs:
        return False
    changed = False
    if fs.pop("sleep_until", None) is not None:
        changed = True
    # Явный ручной запуск шага — решение человека: снять стоп «нет исходных данных».
    if running_key == ProjectStatus.generating_image_prompts.value and fs.pop("img_pr_no_source", None) is not None:
        changed = True
    totals: dict[str, int] = dict(fs.get("total_fails") or {})
    if totals.pop(running_key, None) is not None:
        changed = True
        fs["total_fails"] = totals
    if not changed:
        return False
    _save_failure_state(project, fs)
    return True


def clear_failure_sleep(project: Project) -> bool:
    """Снять sleep_until (⏹ STOP) — без сброса счётчиков fails."""
    fs = _failure_state(project)
    if fs.pop("sleep_until", None) is None:
        return False
    _save_failure_state(project, fs)
    return True


def clear_sleep_if_expired(project: Project) -> bool:
    """Снять sleep_until если время вышло. True если только что проснулись."""
    if is_sleeping(project):
        return False
    fs = _failure_state(project)
    if not fs.get("sleep_until"):
        return False
    fs.pop("sleep_until", None)
    _save_failure_state(project, fs)
    return True


async def _soft_retry_without_wipe(
    session: AsyncSession,
    project: Project,
    step_code: str,
) -> None:
    """Повтор шага без reset_step / wipe — сохраняем файлы на диске."""
    if step_code == "audio":
        from app.services.artifact_recovery import recover_before_assemble

        await recover_before_assemble(session, project)
        logger.info(
            "[#{}] soft retry audio: подхват озвучки/whisper с диска (без wipe)",
            project.id,
        )
    elif step_code == "music":
        from app.services.artifact_recovery import recover_music_from_disk

        if await recover_music_from_disk(session, project):
            logger.info(
                "[#{}] soft retry music: music артефакт восстановлен с диска",
                project.id,
            )
        else:
            logger.info(
                "[#{}] soft retry music: без wipe, повтор генерации",
                project.id,
            )
    elif step_code == "assemble":
        from app.services.artifact_recovery import recover_before_assemble

        await recover_before_assemble(session, project)
        logger.info(
            "[#{}] soft retry assemble: подхват артефактов с диска (без wipe)",
            project.id,
        )
    elif step_code == "anim_pr":
        logger.info(
            "[#{}] soft retry {}: без wipe, без Excel sync (DB SoT)",
            project.id,
            step_code,
        )
    elif step_code == "img":
        from app.services.scan_frames import sync_frames_with_disk_images

        synced = await sync_frames_with_disk_images(session, project)
        logger.info(
            "[#{}] soft retry img: {} кадров уже на диске (без wipe)",
            project.id,
            synced,
        )
    elif step_code == "video":
        from app.services.artifact_recovery import recover_scene_videos_from_disk

        recovered = await recover_scene_videos_from_disk(session, project)
        videos_dir = project.data_dir / "videos"
        on_disk = len(list(videos_dir.glob("clip_*.mp4"))) if videos_dir.is_dir() else 0
        logger.info(
            "[#{}] soft retry video: {} clip на диске, {} newly linked (без wipe)",
            project.id,
            on_disk,
            len(recovered),
        )
    else:
        logger.info(
            "[#{}] soft retry {}: без wipe, только restart",
            project.id,
            step_code,
        )


def _no_source_error(error: BaseException | None) -> Any:
    """ImgPrNoSourceData в самой ошибке или в цепочке ``raise … from`` (__cause__)."""
    from app.services.img_pr_batches import ImgPrNoSourceData

    seen: set[int] = set()
    cur = error
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, ImgPrNoSourceData):
            return cur
        seen.add(id(cur))
        cur = cur.__cause__
    return None


async def _pause_img_pr_no_source(session: AsyncSession, project: Project, error: Exception, err_code: str) -> str:
    """img_pr: модель ответила «нет исходных данных» — пауза, без soft retry.

    Повтор того же входа даёт тот же ответ, каждый повтор — платный вызов.
    Хорошие опы смешанного батча уже записаны шагом. Человек чинит данные сцен
    (шаг «Работа с GPT») и продолжает проект; пока вход сцен тот же, шаг
    модель не зовёт (generate_image_prompts._raise_if_no_source_unchanged).
    """
    from app.services import img_pr_batches as ipb
    from app.services.project_control import pause_project as pause_project_svc
    from app.services.run_sync import mark_running_node_failed

    running = project.status
    src = _no_source_error(error)
    frames = list(getattr(src, "frame_numbers", None) or [])
    ctx = getattr(src, "context", None)
    fingerprint = ctx.get("scene_fingerprint") if isinstance(ctx, dict) else None
    reason = ipb.no_scene_data_pause_text(frames, ipb.upstream_scene_step_ru(project.meta))

    fs = _failure_state(project)
    fs["last_error"] = reason
    fs["last_error_code"] = err_code
    fs["last_running"] = running.value
    fs[ipb.NO_SOURCE_GUARD_KEY] = {
        "frame_numbers": frames,
        "fingerprint": fingerprint,
        "at": datetime.now(UTC).isoformat(),
    }
    fs.pop("sleep_until", None)
    _save_failure_state(project, fs)
    await mark_running_node_failed(
        session,
        project,
        error,
        initiator="worker",
        error_code=err_code,
    )
    await pause_project_svc(session, project)
    await session.flush()
    logger.warning(
        "[#{}] img_pr: нет исходных данных у кадров {} — пауза, без повтора модели",
        project.id,
        frames[:20],
    )
    return "pause_infra"


async def record_step_failure(
    session: AsyncSession,
    project: Project,
    *,
    error: Exception,
) -> str:
    """Обработать ошибку advance_project.

    Returns: retry | sleep | abandon | pause_infra | pause_budget | stopped
    """
    if is_chrome_infra_error(error):
        return await handle_chrome_step_failure(session, project, error)

    from app.services.error_catalog import describe_error
    from app.services.gen_queue_run import is_user_stopped

    # Этап 3 (E.3): бюджет прогона исчерпан — сразу paused с машиночитаемой
    # причиной (паттерн vision-паузы этапа 4), БЕЗ sleep-циклов и счёта
    # фейлов: ретраи ничего не починят, первый же вызов снова упрётся.
    from app.services.llm_ledger import BUDGET_CODE, BudgetExhausted
    from app.services.project_state import is_running_status
    from app.services.step_cancel import is_stop_requested

    if isinstance(error, BudgetExhausted):
        from app.services.project_control import pause_project as pause_project_svc
        from app.services.run_sync import mark_running_node_failed

        running = project.status
        meta = dict(project.meta or {})
        meta["pause_reason"] = {
            "code": BUDGET_CODE,
            "spent_usd": round(float(error.spent_usd), 4),
            "budget_usd": round(float(error.budget_usd), 4),
            "kind": str(getattr(error, "kind", "project")),
            "node": running.value,
        }
        project.meta = meta
        fs = _failure_state(project)
        fs["last_error"] = str(error)
        fs["last_error_code"] = BUDGET_CODE
        fs["last_running"] = running.value
        fs.pop("sleep_until", None)
        _save_failure_state(project, fs)
        await mark_running_node_failed(
            session,
            project,
            error,
            initiator="worker",
            error_code=BUDGET_CODE,
        )
        await pause_project_svc(session, project)
        await session.flush()
        logger.error(
            "[#{}] LLM budget exhausted — pause, no retry: {}",
            project.id,
            error,
        )
        return "pause_budget"

    # SQLite busy / PendingRollback при parallel projects — не копить к 30-мин sleep.
    err_code_early, err_msg_early = describe_error(error)
    # 402 / нет кредитов — не soft-retry и не sleep 30 мин (только жечь баланс).
    try:
        from app.services.scene_design.agent_chunks import is_credits_failure as _is_credits_failure
    except Exception:  # noqa: BLE001

        def _is_credits_failure(exc: BaseException) -> bool:
            return False

    if _is_credits_failure(error) or "credits insufficient" in (err_msg_early or "").lower():
        from app.services.project_control import pause_project as pause_project_svc
        from app.services.run_sync import mark_running_node_failed

        running = project.status
        step = step_by_running_status(running)
        fs = _failure_state(project)
        fs["last_error"] = err_msg_early
        fs["last_error_code"] = err_code_early or "gpt_credits"
        fs["last_running"] = running.value
        fs.pop("sleep_until", None)
        _save_failure_state(project, fs)
        await mark_running_node_failed(
            session,
            project,
            error,
            initiator="worker",
            error_code=err_code_early or "gpt_credits",
        )
        try:
            from app.services.scene_design import runner as sd_runner

            sd_runner.mark_failed(project, err_msg_early)
        except Exception:  # noqa: BLE001
            pass
        await pause_project_svc(session, project)
        await session.flush()
        logger.error(
            "[#{}] GPT credits exhausted (402) — pause, no soft-retry: {}",
            project.id,
            err_msg_early[:200],
        )
        return "pause_infra"

    if err_code_early == "infra_db_locked":
        running = project.status
        step = step_by_running_status(running)
        step_code = step.code if step else running.value
        await _soft_retry_without_wipe(session, project, step_code)
        fs = _failure_state(project)
        fs["last_error"] = err_msg_early
        fs["last_error_code"] = err_code_early
        fs["last_running"] = running.value
        fs.pop("sleep_until", None)
        _save_failure_state(project, fs)
        if step is not None:
            project.status = step.running_status
        from app.services.run_sync import update_active_node_progress_text

        await update_active_node_progress_text(
            session,
            project,
            f"БД занята — повтор без счётчика: {err_msg_early[:80]}",
        )
        await session.flush()
        logger.warning(
            "[#{}] infra_db_locked: soft retry без fail-счётчика (не sleep)",
            project.id,
        )
        return "retry"

    # ⏹ уже нажат — не поднимать planning снова soft-retry'ем (иначе крутилка вечная)
    if is_user_stopped(project) or is_stop_requested(project.id):
        from app.services.run_sync import mark_running_node_failed

        running = project.status
        step = step_by_running_status(running)
        err_code, err_msg = describe_error(error)
        fs = _failure_state(project)
        fs["last_error"] = err_msg
        fs["last_error_code"] = err_code
        if step is not None:
            fs["last_running"] = step.running_status.value
        fs.pop("sleep_until", None)
        _save_failure_state(project, fs)
        await mark_running_node_failed(
            session,
            project,
            error,
            initiator="worker",
            error_code=err_code,
        )
        if is_running_status(project.status):
            rollback = step.requires if step is not None and step.requires is not None else ProjectStatus.new
            project.status = rollback
        await session.flush()
        logger.warning(
            "[#{}] step failure ignored soft-retry (user_stop) → {}",
            project.id,
            project.status.value,
        )
        return "stopped"

    # «Нет исходных данных» у img_pr: повтор платный и даст тот же ответ — пауза.
    if err_code_early == "img_pr_no_scene_data" or _no_source_error(error) is not None:
        return await _pause_img_pr_no_source(session, project, error, "img_pr_no_scene_data")

    running = project.status
    step = step_by_running_status(running)
    step_code = step.code if step else running.value
    fs = _failure_state(project)
    key = running.value

    totals: dict[str, int] = dict(fs.get("total_fails") or {})
    total = totals.get(key, 0) + 1
    totals[key] = total
    fs["total_fails"] = totals

    err_code, err_msg = describe_error(error)
    fs["last_error"] = err_msg
    fs["last_error_code"] = err_code
    fs["last_running"] = key
    cycle = (total - 1) // FAILS_PER_CYCLE + 1
    fail_in_cycle = ((total - 1) % FAILS_PER_CYCLE) + 1

    await _soft_retry_without_wipe(session, project, step_code)

    if total >= MAX_TOTAL_FAILS:
        fs["abandoned_at"] = datetime.now(UTC).isoformat()
        fs["recovery_cycles"] = MAX_CYCLES
        _remember_place_before_failed_step(project, running)
        project.status = ProjectStatus.paused
        _save_failure_state(project, fs)
        from app.services.run_sync import mark_running_node_failed

        await mark_running_node_failed(
            session,
            project,
            error,
            initiator="worker",
            error_code=err_code,
        )
        await session.flush()
        logger.error(
            "[#{}] abandoned after {} fails ({} cycles) on {}",
            project.id,
            total,
            MAX_CYCLES,
            key,
        )
        return "abandon"

    if total % FAILS_PER_CYCLE == 0:
        sleep_min = sleep_minutes_for_error(error)
        until = datetime.now(UTC) + timedelta(minutes=sleep_min)
        fs["sleep_until"] = until.isoformat()
        fs["recovery_cycles"] = total // FAILS_PER_CYCLE
        _save_failure_state(project, fs)
        from app.services.run_sync import mark_running_node_failed

        # Сначала failed по NodeRun (пока status ещё running-шаг), потом уходим
        # из planning/… — иначе UI крутит «выполняется» все 30 мин сна.
        await mark_running_node_failed(
            session,
            project,
            error,
            initiator="worker",
            error_code=err_code,
        )
        # Не откатывать в step.requires (часто enrich_N_ready): при auto_mode
        # auto_advance сразу уводит проект «назад» (например img_pr → enrich_3_ready → scripting).
        # Во время 30-мин сна держим paused; ручной run_step снимает sleep.
        project.status = ProjectStatus.paused
        await session.flush()
        logger.warning(
            "[#{}] sleep {} min after fail {}/{} on {} (cycle {}/{}) → status={}",
            project.id,
            sleep_min,
            total,
            MAX_TOTAL_FAILS,
            key,
            cycle,
            MAX_CYCLES,
            project.status.value,
        )
        return "sleep"

    _save_failure_state(project, fs)
    # Не вызываем start_step / reset_step — они удаляют файлы на диске.
    if step is not None:
        project.status = step.running_status
    short_err = err_msg.replace("\n", " ")[:120]
    from app.services.run_sync import update_active_node_progress_text

    await update_active_node_progress_text(
        session,
        project,
        f"Повтор {fail_in_cycle} из {FAILS_PER_CYCLE}: {short_err}",
    )
    await session.flush()
    logger.warning(
        "[#{}] fail {}/{} on {} (cycle {} fail {}/{}), soft retry без wipe: {}",
        project.id,
        total,
        MAX_TOTAL_FAILS,
        key,
        cycle,
        fail_in_cycle,
        FAILS_PER_CYCLE,
        fs["last_error"],
    )
    return "retry"


def ready_status_before(project: Project | None, running: ProjectStatus) -> ProjectStatus | None:
    """Готовность перед running-шагом (с учётом числа enrich-слотов проекта).

    ``new`` - у шага нет требования (сценарий). None - статус не running-шаг.
    """
    step = step_by_running_status(running)
    if step is None:
        return None
    requires = step.requires
    try:
        from app.orchestrator.pipeline_steps import steps_for

        for sd in steps_for(project):
            if sd.code == step.code:
                requires = sd.requires
                break
    except Exception:  # noqa: BLE001
        pass
    return requires if requires is not None else ProjectStatus.new


def _remember_place_before_failed_step(project: Project, running: ProjectStatus) -> None:
    """Сдача после ошибок: «снять паузу» вернёт проект на готовность перед упавшим
    шагом, а не на сам шаг (тот сразу запустился бы снова и тратил бы деньги) и не в new.
    """
    from sqlalchemy.orm.attributes import flag_modified

    place = ready_status_before(project, running)
    if place is None:
        return
    meta = _meta(project)
    meta["paused_from_status"] = place.value
    project.meta = meta
    flag_modified(project, "meta")


_NOT_A_PLACE = frozenset({ProjectStatus.paused, ProjectStatus.failed, ProjectStatus.new})


def resume_place_without_origin(project: Project) -> ProjectStatus | None:
    """Куда снять паузу, если ``paused_from_status`` не записан (старые сдачи шага).

    Порядок: готовность перед шагом, на котором остановили ошибки (есть след сбоя), затем
    готовность, на которой проект ждал ▶ после перезапуска, затем готовность перед
    последним упавшим шагом без следа сбоя. None - места не знаем.
    """
    meta = project.meta if isinstance(project.meta, dict) else {}
    fs = raw_fs if isinstance(raw_fs := meta.get("step_failure"), dict) else {}
    totals = raw_totals if isinstance(raw_totals := fs.get("total_fails"), dict) else {}

    def _running(raw: Any) -> ProjectStatus | None:
        try:
            st = ProjectStatus(str(raw))
        except ValueError:
            return None
        return None if st in _NOT_A_PLACE else st

    last = _running(fs.get("last_running")) if fs.get("last_running") else None
    if last is None:
        last = _running_status_to_resume(fs)
    evidence = bool(
        fs.get("abandoned_at")
        or fs.get("sleep_until")
        or (last is not None and int(totals.get(last.value) or 0) > 0)
    )
    if last is not None and evidence:
        place = ready_status_before(project, last)
        if place is not None:
            return place
    blocked = meta.get("startup_blocked_ready_status")
    if blocked:
        st = _running(blocked)
        if st is not None and step_by_running_status(st) is None:
            return st
    if last is not None:
        return ready_status_before(project, last)
    return None


def clear_failure_on_success(project: Project, running: ProjectStatus) -> None:
    clear_chrome_recovery(project)
    fs = _failure_state(project)
    totals: dict[str, int] = dict(fs.get("total_fails") or {})
    totals.pop(running.value, None)
    fs["total_fails"] = totals
    fs.pop("sleep_until", None)
    fs.pop("recovery_cycles", None)
    fs.pop("last_error", None)
    fs.pop("img_pr_no_source", None)
    _save_failure_state(project, fs)


def _running_status_to_resume(fs: dict[str, Any]) -> ProjectStatus | None:
    """Pick a real running status: last_running, or total_fails keys (not paused)."""
    candidates: list[str] = []
    last = fs.get("last_running")
    if last:
        candidates.append(str(last))
    totals = fs.get("total_fails") or {}
    if isinstance(totals, dict):
        for key in sorted(totals, key=lambda k: int(totals.get(k) or 0), reverse=True):
            if key not in candidates:
                candidates.append(str(key))
    skip = {
        ProjectStatus.paused,
        ProjectStatus.failed,
        ProjectStatus.new,
    }
    for key in candidates:
        try:
            running = ProjectStatus(key)
        except ValueError:
            continue
        if running in skip:
            continue
        if step_by_running_status(running) is not None:
            return running
    return None


async def maybe_resume_after_sleep(
    session: AsyncSession,
    project: Project,
) -> bool:
    """После сна — снова запустить тот же шаг (auto retry)."""
    if not clear_sleep_if_expired(project):
        return False
    from app.services.gen_queue_run import is_user_stopped

    if is_user_stopped(project):
        return False
    # Error-sleep parks as paused (not generating_*). Skipping paused here
    # left projects stuck after sleep_until with the worker never seeing them.
    fs = _failure_state(project)
    running = _running_status_to_resume(fs)
    if running is None:
        return False
    step = step_by_running_status(running)
    if step is None:
        return False
    await _soft_retry_without_wipe(session, project, step.code)
    project.status = step.running_status
    await session.flush()
    logger.info(
        "[#{}] авто-резюм после паузы ошибок → {} (без wipe)",
        project.id,
        step.code,
    )
    return True


async def resume_expired_error_sleeps(session: AsyncSession) -> list[int]:
    """paused + expired sleep_until → restore last_running (worker tick)."""
    from sqlalchemy import select

    rows = (
        (await session.execute(select(Project).where(Project.status == ProjectStatus.paused))).scalars().all()
    )
    ids: list[int] = []
    for p in rows:
        if await maybe_resume_after_sleep(session, p):
            ids.append(p.id)
    return ids
