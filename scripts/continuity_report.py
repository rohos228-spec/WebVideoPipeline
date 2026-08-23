"""Отчёт по непрерывности проекта: что реально стоит на кадрах.

Инструмент измерения, а не часть пайплайна. Печатает раскадровку и то, чем
кадры связаны: сцена, кто в кадре, действие, посчитанная расстановка и
предметы. Ниже — сводка: сколько кадров получили строку непрерывности,
какие нарушения нашёл реестр, есть ли сцены, где ось не определилась.

    python3 scripts/continuity_report.py <project_id>
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from sqlalchemy import select

from app.db import session_scope
from app.models import Frame, Project
from app.services.scene_design.continuity import axis_for_scene, parse_character_ids, resolve_axis
from app.services.scene_design.continuity_apply import build_continuity


def _attrs(fr: Frame) -> dict[str, Any]:
    return fr.attrs if isinstance(fr.attrs, dict) else {}


def _clip(text: Any, n: int) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


async def main(project_id: int) -> int:
    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            print(f"проекта #{project_id} нет")
            return 1
        rows = (await session.execute(select(Frame).where(Frame.project_id == project_id))).scalars().all()
        frames = sorted(rows, key=lambda f: (f.sort_key is None, f.sort_key or 0.0, f.number or 0))

        print(f"# {project.title} (#{project_id}) — {project.status.value}")
        print(f"кадров: {len(frames)}\n")

        # Считаем тем же кодом, что и шаг сборки сцен, — иначе отчёт мерил бы себя.
        lines, violations, context = build_continuity(project, frames)
        film_axis = resolve_axis([{"кто_в_кадре": _attrs(f).get("characters") or ""} for f in frames])
        sides = context["side"]

        by_scene: dict[str, list[Frame]] = {}
        for fr in frames:
            a0 = _attrs(fr)
            sid = str(a0.get("shot01_id_scene") or a0.get("id_scene") or "—")
            by_scene.setdefault(sid, []).append(fr)

        for sid, group in by_scene.items():
            shots = [{"кто_в_кадре": _attrs(f).get("characters") or ""} for f in group]
            axis = axis_for_scene(shots, film_axis)
            axis_txt = f"{axis.left} слева / {axis.right} справа" if axis else "оси нет (один субъект)"
            print(f"## {sid} — кадров {len(group)}, ось: {axis_txt}")
            for fr in group:
                a = _attrs(fr)
                print(
                    f"  [{fr.number:>3}] {_clip(a.get('characters'), 24):<24} {_clip(a.get('shot01_action'), 62)}"
                )
                cont = a.get("continuity") or lines.get(str(fr.uuid or ""))
                if cont:
                    side = sides.get(str(fr.uuid or ""), "?")
                    print(f"        ↳ [{side}] {_clip(cont, 150)}")
            print()

        with_line = sum(1 for fr in frames if _attrs(fr).get("continuity"))
        no_axis = [
            sid
            for sid, g in by_scene.items()
            if axis_for_scene([{"кто_в_кадре": _attrs(f).get("characters") or ""} for f in g], film_axis)
            is None
        ]
        multi = [sid for sid, g in by_scene.items() if len(g) > 1]
        people = {cid for fr in frames for cid in parse_character_ids(_attrs(fr).get("characters") or "")}

        # Приёмка §3.1: строка есть на кадре — половина дела; вопрос в том,
        # доехала ли она до промта картинки. На прогоне #2 было 1 из 24.
        from app.services.img_pr_continuity import has_continuity_line

        with_prompt = [
            fr for fr in frames if (fr.image_prompt or "").strip() and _attrs(fr).get("continuity")
        ]
        in_prompt = sum(
            1 for fr in with_prompt if has_continuity_line(fr.image_prompt, _attrs(fr)["continuity"])
        )

        # Приёмка §3.2: доля кадров с двумя людьми в сценах, где двое есть.
        pair_total = pair_together = 0
        lonely: list[str] = []
        for sid, g in by_scene.items():
            casts = [parse_character_ids(_attrs(f).get("characters") or "") for f in g]
            if len({cid for cast in casts for cid in cast}) < 2:
                continue
            both = sum(1 for cast in casts if len(cast) >= 2)
            pair_total += len(casts)
            pair_together += both
            if both < max(1, int(0.4 * len(casts))):
                lonely.append(f"{sid}: {both}/{len(casts)}")

        print("## Сводка")
        print(f"сцен: {len(by_scene)}; из них многокадровых: {len(multi)}")
        print(f"персонажей на кадрах: {len(people)} ({', '.join(sorted(people)) or '—'})")
        print(f"строка непрерывности записана на кадрах: {with_line}/{len(frames)}")
        print(f"строк посчитано сейчас: {sum(1 for v in lines.values() if v)}/{len(frames)}")
        print(f"расстановка доехала до промта картинки: {in_prompt}/{len(with_prompt)}")
        share = f" ({pair_together / pair_total:.0%})" if pair_total else ""
        print(f"двое в кадре в сценах с двумя людьми: {pair_together}/{pair_total}{share}")
        if lonely:
            print("  сцены ниже 40%: " + "; ".join(lonely))
        print(f"сцен без оси действия: {len(no_axis)}" + (f" ({', '.join(no_axis)})" if no_axis else ""))
        print(f"нарушений реестра предметов: {len(violations)}")
        for v in violations:
            print(f"  · {v}")
    return 0


if __name__ == "__main__":
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    raise SystemExit(asyncio.run(main(pid)))
