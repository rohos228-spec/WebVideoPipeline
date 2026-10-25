"""Строки ``orch_tasks`` / ``orch_steps``. Статус меняет только Supervisor.

SQL общий для SQLite и Postgres: именованные параметры, новый id — через
``RETURNING id`` (``lastrowid`` у Postgres нет).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from app.orchestrator.contracts import PlanStep, SpendSnapshot
from app.orchestrator.db.migrations import connect, utc_now


def _id(value: Any) -> int | None:
    """Ключ строки как int: Postgres не сравнивает BIGINT со строкой «5»."""
    return None if value is None else int(value)


class TaskStore:
    def insert(
        self,
        session_id: str,
        goal: str,
        status: str,
        project_id: int | None = None,
    ) -> int:
        stamp = utc_now()
        conn = connect()
        try:
            new_id = conn.execute(
                text(
                    """
                    INSERT INTO orch_tasks (
                        session_id, status, goal, position,
                        spend_internal, spend_actions,
                        cancel_requested, pause_requested,
                        project_id, created_at, updated_at
                    )
                    VALUES (:sid, :status, :goal, 0, 0, 0, 0, 0, :project_id, :stamp, :stamp)
                    RETURNING id
                    """
                ),
                {"sid": session_id, "status": status, "goal": goal, "project_id": project_id, "stamp": stamp},
            ).scalar()
            conn.commit()
            return int(new_id or 0)
        finally:
            conn.close()

    def get(self, task_id: int) -> dict[str, Any] | None:
        conn = connect()
        try:
            row = (
                conn.execute(text("SELECT * FROM orch_tasks WHERE id = :id"), {"id": _id(task_id)})
                .mappings()
                .fetchone()
            )
        finally:
            conn.close()
        if row is None:
            return None
        return dict(row)

    def set_project_id(self, task_id: int, project_id: int) -> None:
        conn = connect()
        try:
            conn.execute(
                text(
                    """
                    UPDATE orch_tasks
                    SET project_id = :project_id, updated_at = :stamp
                    WHERE id = :id AND project_id IS NULL
                    """
                ),
                {"project_id": int(project_id), "stamp": utc_now(), "id": _id(task_id)},
            )
            conn.commit()
        finally:
            conn.close()

    def set_status(self, task_id: int, status: str, error: str | None = None) -> None:
        conn = connect()
        try:
            conn.execute(
                text(
                    """
                    UPDATE orch_tasks
                    SET status = :status, error = :error, updated_at = :stamp
                    WHERE id = :id
                    """
                ),
                {"status": status, "error": error, "stamp": utc_now(), "id": _id(task_id)},
            )
            conn.commit()
        finally:
            conn.close()

    def set_flag(
        self,
        task_id: int,
        *,
        cancel_requested: int | None = None,
        pause_requested: int | None = None,
        steer_text: str | None = None,
    ) -> None:
        sets: list[str] = []
        args: dict[str, Any] = {}
        if cancel_requested is not None:
            sets.append("cancel_requested = :cancel_requested")
            args["cancel_requested"] = int(cancel_requested)
        if pause_requested is not None:
            sets.append("pause_requested = :pause_requested")
            args["pause_requested"] = int(pause_requested)
        if steer_text is not None:
            sets.append("steer_text = :steer_text")
            args["steer_text"] = steer_text
        if not sets:
            return
        sets.append("updated_at = :stamp")
        args["stamp"] = utc_now()
        args["id"] = _id(task_id)
        conn = connect()
        try:
            conn.execute(text(f"UPDATE orch_tasks SET {', '.join(sets)} WHERE id = :id"), args)
            conn.commit()
        finally:
            conn.close()

    def save_plan(self, task_id: int, plan: list[PlanStep], position: int | None = None) -> None:
        payload = json.dumps([step.model_dump() for step in plan], ensure_ascii=False)
        conn = connect()
        try:
            if position is None:
                conn.execute(
                    text("UPDATE orch_tasks SET plan_json = :plan, updated_at = :stamp WHERE id = :id"),
                    {"plan": payload, "stamp": utc_now(), "id": _id(task_id)},
                )
            else:
                conn.execute(
                    text(
                        """
                        UPDATE orch_tasks
                        SET plan_json = :plan, position = :position, updated_at = :stamp
                        WHERE id = :id
                        """
                    ),
                    {"plan": payload, "position": int(position), "stamp": utc_now(), "id": _id(task_id)},
                )
            conn.commit()
        finally:
            conn.close()

    def mirror_progress(
        self,
        task_id: int,
        plan: list[PlanStep],
        position: int,
        spend: SpendSnapshot,
    ) -> None:
        conn = connect()
        try:
            conn.execute(
                text(
                    """
                    UPDATE orch_tasks
                    SET plan_json = :plan, position = :position, spend_internal = :internal,
                        spend_actions = :actions, updated_at = :stamp
                    WHERE id = :id
                    """
                ),
                {
                    "plan": json.dumps([step.model_dump() for step in plan], ensure_ascii=False),
                    "position": int(position),
                    "internal": float(spend.internal),
                    "actions": float(spend.actions),
                    "stamp": utc_now(),
                    "id": _id(task_id),
                },
            )
            conn.commit()
        finally:
            conn.close()

    def add_spend(self, task_id: int, *, internal: float = 0.0, actions: float = 0.0) -> SpendSnapshot:
        conn = connect()
        try:
            row = (
                conn.execute(
                    text(
                        """
                        UPDATE orch_tasks
                        SET spend_internal = spend_internal + :internal,
                            spend_actions = spend_actions + :actions,
                            updated_at = :stamp
                        WHERE id = :id
                        RETURNING spend_internal, spend_actions
                        """
                    ),
                    {
                        "internal": float(internal),
                        "actions": float(actions),
                        "stamp": utc_now(),
                        "id": _id(task_id),
                    },
                )
                .mappings()
                .fetchone()
            )
            if row is None:
                raise LookupError(task_id)
            conn.commit()
        finally:
            conn.close()
        return SpendSnapshot(
            internal=float(row["spend_internal"] or 0),
            actions=float(row["spend_actions"] or 0),
        )

    def insert_step(self, task_id: int, position: int, step: PlanStep) -> int:
        conn = connect()
        try:
            new_id = conn.execute(
                text(
                    """
                    INSERT INTO orch_steps (
                        task_id, position, action_name, params_json, expected,
                        paid, mutating, status, started_at
                    )
                    VALUES (:task_id, :position, :action, :params, :expected, :paid, :mutating, 'running', :stamp)
                    RETURNING id
                    """
                ),
                {
                    "task_id": _id(task_id),
                    "position": int(position),
                    "action": step.action,
                    "params": json.dumps(step.params, ensure_ascii=False),
                    "expected": step.expected,
                    "paid": 1 if step.paid else 0,
                    "mutating": 1 if step.mutating else 0,
                    "stamp": utc_now(),
                },
            ).scalar()
            conn.commit()
            return int(new_id or 0)
        finally:
            conn.close()

    def note_running_outcome(self, step_id: int, change_id: str | None, cost: float) -> None:
        """Записать сдачу платного шага, не закрывая строку ``running``."""
        conn = connect()
        try:
            conn.execute(
                text(
                    """
                    UPDATE orch_steps
                    SET change_id = :change_id, cost = :cost
                    WHERE id = :id AND status = 'running'
                    """
                ),
                {"change_id": change_id, "cost": float(cost), "id": _id(step_id)},
            )
            conn.commit()
        finally:
            conn.close()

    def finish_step(
        self,
        step_id: int,
        status: str,
        change_id: str | None,
        cost: float,
    ) -> None:
        conn = connect()
        try:
            conn.execute(
                text(
                    """
                    UPDATE orch_steps
                    SET status = :status, change_id = :change_id, cost = :cost, finished_at = :stamp
                    WHERE id = :id
                    """
                ),
                {
                    "status": status,
                    "change_id": change_id,
                    "cost": float(cost),
                    "stamp": utc_now(),
                    "id": _id(step_id),
                },
            )
            conn.commit()
        finally:
            conn.close()

    def list_open(self) -> list[dict[str, Any]]:
        """Нетерминальные задачи по возрастанию id. Для подъёма после рестарта."""
        conn = connect()
        try:
            rows = (
                conn.execute(
                    text(
                        """
                        SELECT * FROM orch_tasks
                        WHERE status NOT IN ('done', 'failed', 'cancelled')
                        ORDER BY id ASC
                        """
                    )
                )
                .mappings()
                .fetchall()
            )
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def session_steps(self, session_id: str, action_name: str) -> list[dict[str, Any]]:
        """Шаги ``action_name`` всех задач этой сессии чата (и закрытых), по порядку.

        Внутреннее имя сессии несёт арендатора (``orch_tenancy.scoped_session``), а
        строки закрыты RLS: читать под арендатором этой сессии.
        """
        conn = connect()
        try:
            rows = (
                conn.execute(
                    text(
                        """
                        SELECT s.* FROM orch_steps AS s
                        JOIN orch_tasks AS t ON t.id = s.task_id
                        WHERE t.session_id = :session_id AND s.action_name = :action
                        ORDER BY s.task_id ASC, s.position ASC
                        """
                    ),
                    {"session_id": session_id, "action": action_name},
                )
                .mappings()
                .fetchall()
            )
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def list_steps(self, task_id: int) -> list[dict[str, Any]]:
        conn = connect()
        try:
            rows = (
                conn.execute(
                    text("SELECT * FROM orch_steps WHERE task_id = :task_id ORDER BY position ASC"),
                    {"task_id": _id(task_id)},
                )
                .mappings()
                .fetchall()
            )
        finally:
            conn.close()
        return [dict(row) for row in rows]
