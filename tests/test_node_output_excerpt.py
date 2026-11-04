"""node.output: начало текстового файла результата, чтобы чат показал сам текст озвучки."""

from types import SimpleNamespace

from app.services.action_layer import builtin_insight as bi


def test_excerpt_reads_first_text_file(tmp_path) -> None:
    (tmp_path / "project.xlsx").write_bytes(b"PK")
    (tmp_path / "voiceover.txt").write_text("Муравьи умеют считать шаги.", encoding="utf-8")
    got = bi._text_excerpt(SimpleNamespace(data_dir=tmp_path), ["project.xlsx", "voiceover.txt"])
    assert got == {"file": "voiceover.txt", "text": "Муравьи умеют считать шаги.", "cut": False}


def test_excerpt_cuts_long_text_and_skips_binary(tmp_path) -> None:
    (tmp_path / "long.md").write_text("а" * 5000, encoding="utf-8")
    got = bi._text_excerpt(SimpleNamespace(data_dir=tmp_path), ["missing.txt", "long.md"])
    assert got is not None and got["cut"] and len(got["text"]) == bi._EXCERPT_CHARS
    assert bi._text_excerpt(SimpleNamespace(data_dir=tmp_path), ["project.xlsx"]) is None


def test_voiceover_text_means_script_node() -> None:
    from app.orchestrator.agents.project_nodes import match_nodes

    rows = [
        {"key": "n_script", "type": "script", "type_ru": "Закадровый текст", "label": "Закадровый текст"},
        {"key": "n_voice", "type": "voice", "type_ru": "Озвучка", "label": "Озвучка"},
    ]
    assert match_nodes(rows, "какой текст озвучки написан в проекте 12") == ["n_script"]
    assert match_nodes(rows, "что с озвучкой в проекте 12") == ["n_voice"]
    assert match_nodes(rows, "что написано для озвучки в проекте 12") == ["n_script"]
