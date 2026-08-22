"""Единая точка согласования состояния (п.8 плана техдолга).

## Что было

Реконсайлеров пять, и они звались из четырёх разных мест:

* ``run_sync.reconcile_stale_node_runs_on_startup`` — из ``main._startup_maintenance``;
* ``run_sync.background_node_run_reconcile_loop`` — отдельным ``create_task``
  в ``main.main()``;
* ``montage_board_job_state.reconcile_stale_montage_jobs_on_startup`` — и из
  ``main``, и из lifespan FastAPI (в разном порядке относительно остальных);
* ``gen_queue.gen_queue_reconcile`` — из воркер-цикла и изнутри
  ``gen_queue_busy_projects``;
* ``startup_guard.block_pipeline_autorun_on_startup`` — ещё раньше по коду.

Порядок между ними нигде не был записан, хотя он важен: снять осиротевшие
NodeRun имеет смысл ПОСЛЕ того, как startup_guard откатил running-проекты,
а не до. Критерий «работа живая» был продублирован: ``is_generation_active``
в одном месте, проверка step-lease — в другом, внутри цикла по нодам.

## Что здесь

Фасад, а не переписывание. Доменная логика остаётся в своих модулях: они
согласуют РАЗНЫЕ вещи (очередь проектов, meta монтажной доски, NodeRun,
политику автозапуска), и слить их в один проход нельзя, не потеряв смысл.
Консолидируется управление:

* один вход — :func:`reconcile`, с явным перечнем проходов и их порядком;
* один критерий живости — :func:`is_work_live` (in-process задача ИЛИ живой
  межпроцессный lease), вместо двух копий;
* один фоновый цикл вместо ``create_task`` по месту;
* один отчёт :class:`ReconcileReport` — видно, какой проход что сделал.

## Чего здесь намеренно НЕТ

П.8 в плане сформулирован как «4 реконсайлера → 1 (из журнала)»: вывести
состояние из журнала событий, а не сверять таблицы попарно. Это отдельная
работа, и она зависит от п.9/10 (lease на кадры, межпроцессный lock) —
журнала событий в схеме нет, ``logs/status.log`` текстовый и не
запрашивается. Здесь сделана безопасная половина: порядок, критерий и
наблюдаемость. Переход на журнал — следующий шаг, и он должен идти после
живого прогона на этой конструкции.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from loguru import logger

# Порядок важен: сначала политика автозапуска откатывает running-проекты,
# только потом снимаются осиротевшие NodeRun — иначе второй проход красит
# в failed то, что первый через секунду откатит сам.
STARTUP_PASSES: tuple[str, ...] = ("montage_jobs", "node_runs")
BACKGROUND_PASSES: tuple[str, ...] = ("node_runs",)

_DEFAULT_INTERVAL_S = 60.0


@dataclass
class ReconcileReport:
    """Что сделал каждый проход. Пустой отчёт = согласовывать было нечего."""

    scope: str
    passes: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.passes.values())

    def as_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "total": self.total,
            "passes": dict(self.passes),
            "errors": dict(self.errors),
        }


async def is_work_live(project_id: int, *, step_code: str = "") -> bool:
    """Идёт ли по проекту работа прямо сейчас.

    Два независимых признака, раньше проверявшиеся в разных местах:

    * задача в ЭТОМ процессе (``step_cancel.is_generation_active`` — advance,
      montage-джоба, xlsx-flow, выставленный стоп-флаг);
    * живой межпроцессный lease на шаг (этап 2) — работу мог держать другой
      процесс, и для него наши in-process структуры пусты.

    Без второго признака реконсайлер в двухпроцессной конфигурации красил
    чужую живую работу в ``failed``.
    """
    from app.services.step_cancel import is_generation_active

    if is_generation_active(project_id):
        return True
    if not step_code:
        return False
    try:
        from app.services.work_lease import is_held

        return await is_held(project_id, f"step:{step_code}")
    except Exception:  # noqa: BLE001 — недоступность lease не делает работу живой
        logger.debug("[#{}] проверка step-lease не удалась", project_id, exc_info=True)
        return False


async def _run_pass(name: str, report: ReconcileReport, *, background: bool) -> None:
    """Один проход. Падение прохода не должно ронять остальные."""
    try:
        if name == "montage_jobs":
            from app.services.montage_board_job_state import (
                reconcile_stale_montage_jobs_on_startup,
            )

            report.passes[name] = await reconcile_stale_montage_jobs_on_startup()
            return

        if name == "node_runs":
            from app.services.run_sync import _reconcile_stale_node_runs

            report.passes[name] = await _reconcile_stale_node_runs(
                initiator="background_reconcile" if background else "startup_reconcile",
                require_no_live_task=background,
            )
            return

        raise ValueError(f"неизвестный проход реконсайлера: {name!r}")
    except Exception as e:  # noqa: BLE001
        report.errors[name] = f"{type(e).__name__}: {e}"
        logger.exception("reconcile: проход {} упал", name)


async def reconcile(*, scope: str = "startup") -> ReconcileReport:
    """Согласовать состояние. ``scope``: ``startup`` | ``background``.

    ``startup`` — после перезапуска процесса: живых задач нет по
    определению, осиротевшее снимается сразу.
    ``background`` — периодически на работающем процессе: снимается только
    то, у чего нет ни живой задачи, ни lease, и что провисело дольше grace.
    """
    background = scope == "background"
    names = BACKGROUND_PASSES if background else STARTUP_PASSES
    report = ReconcileReport(scope=scope)
    for name in names:
        await _run_pass(name, report, background=background)
    if report.total or report.errors:
        logger.info("reconcile ({}): {}", scope, report.as_dict())
    return report


async def background_reconcile_loop(*, interval_sec: float = _DEFAULT_INTERVAL_S) -> None:
    """Фоновый цикл. Единственный — раньше их заводили ``create_task`` по месту."""
    while True:
        try:
            await reconcile(scope="background")
        except Exception:  # noqa: BLE001
            logger.exception("background_reconcile_loop: итерация упала")
        await asyncio.sleep(interval_sec)
