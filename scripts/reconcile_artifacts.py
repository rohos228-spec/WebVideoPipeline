"""CLI утилита для сверки и восстановления артефактов на диске и в БД.

Запуск:
    python scripts/reconcile_artifacts.py --dry-run
    python scripts/reconcile_artifacts.py
    python scripts/reconcile_artifacts.py --project-id 5
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Обеспечиваем импорт app.*
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import session_scope
from app.services.artifact_reconciler import reconcile_all


async def main() -> None:
    parser = argparse.ArgumentParser(description="Сверка артефактов на диске с записями в БД")
    parser.add_argument("--dry-run", action="store_true", help="Только анализ без изменения БД")
    parser.add_argument("--project-id", type=int, default=None, help="ID конкретного проекта (по умолчанию все)")
    parser.add_argument("--no-thumbs", action="store_true", help="Не генерировать .webp миниатюры")
    args = parser.parse_args()

    mode_label = "DRY-RUN (безопасный просмотр)" if args.dry_run else "БОЕВОЙ РЕЖИМ (восстановление в БД)"
    print(f"\n=== Запуск сверки артефактов: {mode_label} ===")
    if args.project_id:
        print(f"Фильтр по проекту: #{args.project_id}")

    async with session_scope() as session:
        stats = await reconcile_all(
            session,
            project_id=args.project_id,
            generate_thumbs=not args.no_thumbs,
            dry_run=args.dry_run,
        )

    print("\n=== Результаты сверки ===")
    print(f"Проектов просканировано:          {stats.projects_scanned}")
    print(f"Файлов обнаружено на диске:       {stats.artifacts_found_on_disk}")
    print(f"Уже присутствовали в БД:          {stats.artifacts_already_in_db}")
    print(f"Новых Artifact создано:           {stats.artifacts_created}")
    print(f"Починено tenant_id у Artifact:    {stats.artifacts_tenants_repaired}")
    print(f"Обновлено статусов кадров:        {stats.frames_status_updated}")
    print(f"Миниатюр (.webp) сгенерировано:   {stats.thumbnails_generated}")
    print(f"Починено tenant_id в MediaCall:   {stats.media_calls_repaired}")
    print(f"Починено tenant_id в LlmCall:     {stats.llm_calls_repaired}")

    if stats.errors:
        print(f"\nОшибок ({len(stats.errors)}):")
        for err in stats.errors[:10]:
            print(f"  - {err}")
    print("\nГотово.")


if __name__ == "__main__":
    asyncio.run(main())
