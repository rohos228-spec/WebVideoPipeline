"""Отказ удалять данные: совет подходит и к файлам хранилища, и к сбросу шагов, а не только к кадрам."""

from app.orchestrator.agents.summarizer import _REASON_RU


def test_deletion_blocked_hint_is_generic() -> None:
    reason, hint = _REASON_RU["deletion_blocked"]
    assert "удалял данные" in reason
    assert "вручную в Студии" in hint
    assert not hint.startswith("Кадры")
