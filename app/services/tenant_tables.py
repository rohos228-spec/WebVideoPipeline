"""Какие таблицы принадлежат арендатору — один список на всю систему.

Список нужен в трёх местах: миграция добавляет колонку, миграция политик
включает RLS, проверка целостности сверяет, что первое и второе не разошлись.
Три копии разошлись бы на первой же новой таблице — и разошлись бы молча:
таблица без политики не ломается, она просто видна всем.

Поэтому список объявлен здесь, а `check_coverage` сверяет его с моделями и
называет забытые таблицы поимённо.
"""

from __future__ import annotations

#: Данные клиента в конвейере. §4.1 плюс дочерние таблицы: RLS проверяет
#: каждую строку сам и наследовать арендатора через проект не умеет.
BASE_TENANT_TABLES: tuple[str, ...] = (
    "projects",
    "frames",
    "scenes",
    "frame_texts",
    "frame_edges",
    "scene_design_cells",
    "entities",
    "artifacts",
    "asr_words",
    "attempts",
    "prompt_versions",
    "master_prompts",
    "hitl_requests",
    "node_runs",
    "llm_calls",
    "media_calls",
    "batch_projects",
    "test_prompt_projects",
    "workflows",
    "workflow_runs",
    "workflow_versions",
    "library_items",
    "library_versions",
    "library_events",
    "library_configs",
    # Промт-библиотека: системные строки идут с `tenant_id IS NULL` и видны
    # в режиме владельца, клиентские — только своему арендатору. Разрешение
    # «системный → арендатор» держит `prompt_store`, изоляцию — политика.
    "prompt_library",
)

#: Таблицы движка оркестратора (перенесён из video-pipeline int/chat-v1),
#: которые принадлежат арендатору. Чат оркестратора открыт каждому клиенту
#: студии (роль `member`), значит всё, что чат пишет и читает от имени
#: клиента, — его данные: планы и шаги, одобрения, журнал действий, снимки
#: для отката, предложения изменений и версии настроек его проектов, журнал
#: наблюдения прогонов, медиа-задачи, решения и разрешения управляемых
#: запусков. Сомнительные случаи (журнал наблюдения, идемпотентность Outsee,
#: медиа-задачи) отнесены сюда же: каждая их строка — про проект клиента, и
#: читают их только в контексте этого проекта.
#:
#: Колонку на свежей базе создаёт baseline (модели уже несут `tenant_id`),
#: политику — 0006 (идёт по `all_isolated()` в момент прогона). Ревизия 0020
#: догоняет базы, где таблицы были созданы раньше.
ENGINE_TENANT_TABLES: tuple[str, ...] = (
    "action_layer_approvals",
    "action_layer_attachments",
    "action_layer_calls",
    "action_layer_history",
    "action_layer_macros",
    "action_layer_plan_context",
    "action_layer_plans",
    "action_layer_snapshots",
    "action_layer_spend",
    "action_layer_steps",
    "action_execution_controls",
    "action_node_recoveries",
    "action_v1_records",
    "change_proposals",
    "config_pointers",
    "config_versions",
    "decision_execution_controls",
    "decision_records",
    "decision_runtime_bindings",
    "decision_turns",
    "managed_launches",
    "managed_run_allowances",
    "node_profile_overlays",
    "observation_commit_uncertainty",
    "observation_degradations",
    "observation_diagnosis",
    "observation_events",
    "observation_executions",
    "observation_incidents",
    "observation_outbox",
    "orchestrator_media_jobs",
    "orchestrator_media_requests",
    "outsee_idempotency",
)

#: Таблицы движка без моделей в `Base.metadata`: их пишет сырой SQL через
#: `app/orchestrator/db` (ревизия 0018). Это история чата целиком — задачи и
#: их шаги, чекпоинты, события шины с нумерацией, очередь сообщений, вызовы
#: LLM, отчёты, уведомления в чат. `check_coverage` их не видит (моделей
#: нет), поэтому живая база сверяется отдельно: `check_database_coverage`.
#:
#: Сырые вставки не перечисляют `tenant_id`: на Postgres колонка получает
#: его значением по умолчанию из `app.tenant_id` транзакции — той же
#: настройки, которую читает политика (ревизия 0020). Забыть проставить
#: нельзя, чужого арендатора вставка не получит: `WITH CHECK` сверяет то же.
ORCH_RAW_TENANT_TABLES: tuple[str, ...] = (
    "orch_tasks",
    "orch_steps",
    "orch_checkpoints",
    "orch_events",
    "orch_event_seq",
    "orch_messages",
    "orch_llm_calls",
    "orch_reports",
    "orch_report_versions",
    "notification_log",
    "chat_notices",
)

#: Всё, что принадлежит арендатору: конвейер, движок, сырые таблицы чата.
TENANT_TABLES: tuple[str, ...] = BASE_TENANT_TABLES + ENGINE_TENANT_TABLES + ORCH_RAW_TENANT_TABLES

#: Касса. Отдельно, потому что колонка арендатора у неё не добавляется —
#: она там с рождения, и в 0005 эти таблицы создаются целиком.
CREDIT_TABLES: tuple[str, ...] = ("credit_accounts", "credit_holds", "credit_entries")

#: Инфраструктура узла, а не данные клиента. Арендатор у лизинга шага был бы
#: бессмыслицей: лизинг принадлежит воркеру, а не заказчику ролика.
#:
#: `studio_users` здесь по другой причине, и её стоит назвать отдельно: это не
#: данные арендатора, а СПИСОК арендаторов. Политика «видно только своё» на
#: этой таблице означала бы, что форма входа не может найти пользователя до
#: того, как узнает, кто он, — то есть войти нельзя вовсе. Читает её слой
#: личности до назначения арендатора (`app/web/identity.py::_assert_live`), и
#: это единственный правильный порядок. Роль базы при этом обычная, не
#: `BYPASSRLS`: политик на этой таблице просто нет, на остальных они в силе.
INFRA_TABLES: frozenset[str] = frozenset(
    {
        "fleet_nodes",
        "work_leases",
        "alembic_version",
        "studio_users",
        "email_verifications",
        "coupons",
        "coupon_redemptions",
    }
)

#: Таблицы движка, которые остаются ОБЩИМИ на студию, — решение, а не
#: забывчивость. Колонки арендатора у них нет.
#:
#: * `spend_ledger`, `provider_task_ledger` — касса провайдеров и сверка
#:   задач у провайдера. Дневной лимит трат и сверка считаются по всей
#:   студии: провайдеру платит владелец, а не клиент. Политика «видно только
#:   своё» молча дала бы каждому арендатору полный дневной лимит владельца.
#: * `supervisor_kill_switch`, `supervisor_kill_switch_history` — аварийный
#:   стоп платных вызовов всей студии. Он один на узел по смыслу.
#:
#: Ручки, которые их показывают и меняют (реестр резервов, аварийный стоп),
#: открыты только админу (`app/web/engine_access.py::require_engine_admin`).
ENGINE_TABLES: frozenset[str] = frozenset(
    {
        "provider_task_ledger",
        "spend_ledger",
        "supervisor_kill_switch",
        "supervisor_kill_switch_history",
    }
)

#: Сырые таблицы движка, общие на студию: глобальный каталог нод и
#: неизменяемая история его промптов (правит только админ), журнал схемы
#: раннера SQL движка.
ORCH_RAW_SHARED_TABLES: frozenset[str] = frozenset(
    {"node_defs", "node_prompt_versions", "orch_schema_migrations"}
)

#: Журнал публикаций (ревизия 0019): относить ли его к данным арендатора,
#: решает заказчик. До решения таблицы общие, и ни одной ручки участнику к
#: ним не открыто. Список нужен `check_database_coverage`, чтобы новая
#: неклассифицированная таблица не пряталась среди этих.
PENDING_DECISION_TABLES: frozenset[str] = frozenset(
    {"publications", "publish_approvals", "publication_manual_actions"}
)

#: Маршрутные таблицы: закрыты политикой, но политика **обратная**. Сессия
#: без арендатора видит всё, сессия с арендатором — только своё.
#:
#: Так сделано ровно для воркера. Он ищет работу сканированием, а сканирование
#: межарендно по природе — под обычной политикой он видел бы проекты владельца
#: и ни одного клиентского, причём молча. Чтобы асимметрия не стоила дорого, в
#: таких таблицах нет содержимого: только номер проекта, арендатор и статус.
#: Разбор — `app/models.py::ProjectRoute`.
ROUTE_TABLES: tuple[str, ...] = ("project_routes",)

#: Имя политики по таблице. Разные имена не косметика: `rls_check` сверяет
#: наличие политики поимённо, и таблица с обычным именем, но обратным
#: предикатом прошла бы проверку молча.
POLICY_BY_TABLE: dict[str, str] = {t: "tenant_routes" for t in ROUTE_TABLES}

#: Имя политики по умолчанию — у всех, кроме маршрутных.
DEFAULT_POLICY = "tenant_isolation"


def policy_for(table: str) -> str:
    """Как называется политика этой таблицы."""
    return POLICY_BY_TABLE.get(table, DEFAULT_POLICY)


def all_isolated() -> tuple[str, ...]:
    """Всё, что закрывается политикой RLS — включая маршрутные таблицы."""
    return TENANT_TABLES + CREDIT_TABLES + ROUTE_TABLES


def check_coverage() -> list[str]:
    """Таблицы моделей, не попавшие ни в один список. Пусто — всё учтено.

    Новая таблица без арендатора не падает — она просто видна всем сразу.
    Отказ бесшумный, поэтому проверка нужна автоматическая
    (`tests/test_tenant_isolation.py`).
    """
    from app.models import Base

    known = set(all_isolated()) | INFRA_TABLES | ENGINE_TABLES
    return sorted(name for name in Base.metadata.tables if name not in known)


def check_database_coverage(table_names) -> list[str]:
    """Таблицы ЖИВОЙ базы, не попавшие ни в один список. Пусто — всё учтено.

    `check_coverage` смотрит в модели, а таблицы чата оркестратора пишет сырой
    SQL — моделей у них нет. Эта проверка получает имена из инспектора базы
    после миграций (`tests/test_orch_tenant_postgres.py`).
    """
    known = (
        set(all_isolated()) | INFRA_TABLES | ENGINE_TABLES | ORCH_RAW_SHARED_TABLES | PENDING_DECISION_TABLES
    )
    return sorted(str(name) for name in table_names if str(name) not in known)
