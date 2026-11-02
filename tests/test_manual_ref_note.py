from types import SimpleNamespace

from app.services.montage_frame_refs import append_manual_ref_note, manual_ref_prompt_note


def _frame(refs):
    return SimpleNamespace(attrs={"ref_manual": refs})


def test_ref_names_are_marked_as_file_names_not_text_to_draw():
    fr = _frame([{"id": "a", "kind": "item", "имя": "ветка"}, {"id": "b", "kind": "item", "имя": "ветка2"}])
    note = manual_ref_prompt_note(fr)
    assert "ветка" in note and "ветка2" in note
    assert "не писать" in note and "надписей" in note
    # Повторная вставка не дублирует заметку.
    once = append_manual_ref_note("Кадр.", fr)
    assert append_manual_ref_note(once, fr) == once


def test_no_refs_no_note():
    assert manual_ref_prompt_note(_frame([])) == ""
