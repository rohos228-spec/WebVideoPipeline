"""Досинхронизировать библиотеку промтов в базе с каталогом на диске.

    python scripts/prompts_resync.py [--apply]

Без `--apply` — только отчёт. Что делает с `--apply`:

1. Перечитывает `prompts/` в СИСТЕМНЫЙ уровень с перезаписью. На сервере это
   безопасно, пока системные строки никто не правил руками — а правки людей
   живут на уровне арендатора и здесь не трогаются.
2. Удаляет с системного уровня то, чего на диске больше нет (отставленные
   агенты): импорт сам ничего не удаляет, и без этого шага они остались бы в
   базе и в редакторе навсегда.
3. Печатает, какие проекты ссылаются на удалённые имена в `prompt_overrides`.

После — перезапуск приложения: кэш промтов живёт в процессе.
"""

from __future__ import annotations

import asyncio
import sys


async def main(apply: bool) -> int:
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Project, PromptLibraryEntry
    from app.services import prompt_store
    from app.services.prompt_library import (
        is_excel_gpt_prompt_step,
        prompt_path,
        resolve_excel_gpt_prompt_path,
    )
    from app.services.prompt_store import PromptScope

    def on_disk(step: str, name: str) -> bool:
        p = resolve_excel_gpt_prompt_path(name) if is_excel_gpt_prompt_step(step) else prompt_path(step, name)
        return p.is_file()

    async with session_scope() as s:
        await prompt_store.refresh(s)
        rows = (
            (await s.execute(select(PromptLibraryEntry).where(PromptLibraryEntry.tenant_id.is_(None))))
            .scalars()
            .all()
        )
        stale = [(r.step_code, r.name) for r in rows if not on_disk(r.step_code, r.name)]
        print(f"системных строк: {len(rows)}; без файла на диске: {len(stale)}")
        for step, name in stale:
            print(f"   {step:10} {name}")

        projects = (await s.execute(select(Project))).scalars().all()
        stale_names = {n for _, n in stale}
        for p in projects:
            ov = p.prompt_overrides if isinstance(p.prompt_overrides, dict) else {}
            hits = {k: v for k, v in ov.items() if isinstance(v, str) and v in stale_names}
            if hits:
                print(f"   проект #{p.id} ссылается на удаляемое: {hits}")

        if not apply:
            print("режим отчёта — ничего не изменено; для применения добавьте --apply")
            return 0

        stats = await prompt_store.import_from_disk(s, overwrite=True)
        print(f"импорт с перезаписью: {stats}")
        dropped = 0
        for step, name in stale:
            if await prompt_store.drop(s, step, name, scope=PromptScope()):
                dropped += 1
        print(f"удалено с системного уровня: {dropped}")
        await s.commit()
    print("готово; перезапустите приложение, чтобы обновился кэш")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(apply="--apply" in sys.argv[1:])))
