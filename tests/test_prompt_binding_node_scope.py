"""Привязка промта к узлу: свой слот, любой слот, и запрет брать чужой.

Находка 14: узел без своей привязки подхватывал чужую — «Проверка кадров»,
встав в слот 2, получила промт сборщика сцен. Спросили про конкретный узел —
отвечаем только про него.
"""

from __future__ import annotations

from app.services.prompt_library import resolve_project_prompt_with_source as resolve


def test_own_main_slot_wins() -> None:
    meta = {"prompt_slot_variants": {"n_a": {"main": "sd_action"}}}
    assert resolve({}, "excel_gpt", meta=meta, node_key="n_a") == ("sd_action", "slot")


def test_any_slot_used_when_main_is_empty() -> None:
    """main пуст, но у узла есть другой gpt-слот — берём его, а не дефолт."""
    meta = {"prompt_slot_variants": {"n_a": {"main": "", "gpt": "sd_camera"}}}
    assert resolve({}, "excel_gpt", meta=meta, node_key="n_a") == ("sd_camera", "slot")


def test_nonexistent_variant_is_not_used() -> None:
    """Привязка есть, файла нет — молча подставлять нельзя."""
    meta = {"prompt_slot_variants": {"n_a": {"main": "такого-файла-нет"}}}
    assert resolve({}, "excel_gpt", meta=meta, node_key="n_a")[0] != "такого-файла-нет"


def test_foreign_binding_is_not_borrowed() -> None:
    meta = {"prompt_slot_variants": {"n_other": {"main": "sd_action"}}}
    assert resolve({}, "excel_gpt", meta=meta, node_key="n_a") == ("default", "default")


def test_without_node_key_project_binding_still_works() -> None:
    """Поведение старого пути не изменилось: узел не назван — берём слот проекта."""
    meta = {"prompt_slot_variants": {"n_other": {"main": "sd_action"}}}
    assert resolve({}, "excel_gpt", meta=meta) == ("sd_action", "slot")


def test_override_wins_over_slots_for_plain_step(tmp_path, monkeypatch) -> None:
    # Вариант кладём в подменённую библиотеку: настоящая prompts/ вне git,
    # и на чистом клоне (CI) файла vlog.md не существует — тест падал бы
    # на отсутствии ДАННЫХ, а не кода.
    (tmp_path / "01_plan").mkdir()
    (tmp_path / "01_plan" / "vlog.md").write_text("# план-влог", encoding="utf-8")
    monkeypatch.setattr("app.services.prompt_library.PROMPTS_ROOT", tmp_path)
    meta = {"prompt_slot_variants": {"n_a": {"main": "vlog"}}}
    assert resolve({"plan": "vlog"}, "plan", meta=meta, node_key="n_a")[1] in ("slot", "override")
