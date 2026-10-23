"""Живой реестр для ядра: execute / metadata_of поверх ActionSpec.

Пакет схем — ``app.orchestrator.action_contracts``. Отсюда не экспортируется
``ACTION_IDS`` и ``AUDIT_ACTION_IDS``: белый список собирает адаптер из реестра.
"""

from app.services.action_layer.spec import ensure_builtin_actions, registry

__all__ = [
    "ensure_builtin_actions",
    "execute",
    "launch_blockers",
    "metadata_of",
    "plan_defaults",
    "plan_required",
    "plan_resolve",
    "prepare_card",
    "project_required",
    "registry",
    "settings_diff",
    "whitelist",
]

_LAZY = frozenset(
    {
        "execute",
        "metadata_of",
        "whitelist",
        "prepare_card",
        "plan_required",
        "project_required",
        "plan_defaults",
        "plan_resolve",
        "settings_diff",
        "launch_blockers",
    }
)


def __getattr__(name: str):
    """Исполнитель не импортируется вместе с contracts: иначе цикл через builtin_group."""

    if name not in _LAZY:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from app.orchestrator.actions import executor as executor_mod

    value = getattr(executor_mod, name)
    globals()[name] = value
    return value


try:
    ensure_builtin_actions()
except ImportError as exc:
    if "partially initialized" not in str(exc):
        raise
