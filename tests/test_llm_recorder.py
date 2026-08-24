"""Рекордер ответов моделей: пишет по флагу и не роняет шаг.

Модуль маленький, но у него два свойства, которые дороже его размера. Первое —
он выключен по умолчанию: ответы моделей это содержимое роликов заказчика и
десятки килобайт на вызов, и включённая по недосмотру запись удваивает диск
под каждым проектом. Второе — он не имеет права быть причиной падения: его
зовут из середины шага конвейера, и потеря случая для корпуса дешевле
потерянного прогона.

Оба проверяются здесь, потому что оба проявляются не сразу: первое — счётом за
диск через месяц, второе — упавшим прогоном ролика.
"""

from __future__ import annotations

import json

from app.services import llm_recorder


def test_recording_is_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv(llm_recorder.ENV_DIR, raising=False)
    assert llm_recorder.enabled() is False
    assert llm_recorder.record_dir() is None
    assert llm_recorder.record(contract="vp_sd_world", reply="что-то") is None


def test_blank_env_is_the_same_as_unset(monkeypatch) -> None:
    """`LLM_RECORD_DIR=` в `.env` не должен означать «пиши в текущий каталог»."""
    monkeypatch.setenv(llm_recorder.ENV_DIR, "   ")
    assert llm_recorder.enabled() is False


def test_record_writes_the_exchange(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(llm_recorder.ENV_DIR, str(tmp_path))
    path = llm_recorder.record(
        contract="vp_sd_world",
        reply='{"мир": "метро"}',
        prompt="опиши мир",
        model="gpt-5.6",
        node_key="n_scene_d_1",
        verdict="ok",
    )
    assert path is not None and path.is_file()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["contract"] == "vp_sd_world"
    assert saved["reply"] == '{"мир": "метро"}'
    assert saved["prompt"] == "опиши мир"
    assert saved["node_key"] == "n_scene_d_1"
    assert saved["verdict"] == "ok"
    assert saved["truncated"] is False


def test_directory_is_created_if_missing(tmp_path, monkeypatch) -> None:
    """Каталог задают руками и обычно несуществующий — создавать его наша забота."""
    target = tmp_path / "нет" / "такого"
    monkeypatch.setenv(llm_recorder.ENV_DIR, str(target))
    assert llm_recorder.record(contract="vp_sd_world", reply="ответ") is not None
    assert target.is_dir()


def test_same_reply_is_not_written_twice(tmp_path, monkeypatch) -> None:
    """Ретраи повторяют один и тот же вызов; корпус не должен от этого пухнуть.

    Имя файла несёт хеш ответа, поэтому повтор попадает в тот же файл. Секунда
    в метке времени различает РАЗНЫЕ ответы, а не повторы одного.
    """
    monkeypatch.setenv(llm_recorder.ENV_DIR, str(tmp_path))
    first = llm_recorder.record(contract="vp_sd_world", reply="один и тот же ответ")
    second = llm_recorder.record(contract="vp_sd_world", reply="один и тот же ответ")
    assert first == second
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_different_replies_get_different_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(llm_recorder.ENV_DIR, str(tmp_path))
    llm_recorder.record(contract="vp_sd_world", reply="первый")
    llm_recorder.record(contract="vp_sd_world", reply="второй")
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_huge_reply_is_truncated_and_marked(tmp_path, monkeypatch) -> None:
    """Голден из мегабайтного ответа никто не станет читать, а место он займёт.

    Пометка обязательна: `scripts/goldens.py add` предупреждает по ней, потому
    что голден из обрезанного текста проверяет обработку обрезка.
    """
    monkeypatch.setenv(llm_recorder.ENV_DIR, str(tmp_path))
    path = llm_recorder.record(contract="vp_sd_skeleton", reply="я" * (llm_recorder.MAX_CHARS + 500))
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["truncated"] is True
    assert "обрезано рекордером" in saved["reply"]
    assert len(saved["reply"]) < llm_recorder.MAX_CHARS + 100


def test_contract_name_is_sanitized_into_the_filename(tmp_path, monkeypatch) -> None:
    """Имя контракта уезжает в путь — значит `../` там быть не должно."""
    monkeypatch.setenv(llm_recorder.ENV_DIR, str(tmp_path))
    path = llm_recorder.record(contract="../../и слэши/тоже", reply="ответ")
    assert path is not None
    assert path.parent == tmp_path
    assert "/" not in path.name and ".." not in path.name


def test_write_failure_is_swallowed(tmp_path, monkeypatch) -> None:
    """Отказ файловой системы не имеет права уронить шаг конвейера.

    Рекордер зовут из середины генерации ролика; исключение отсюда стоило бы
    прогона, а потерянный случай для корпуса не стоит ничего.
    """
    monkeypatch.setenv(llm_recorder.ENV_DIR, str(tmp_path))
    monkeypatch.setattr(
        llm_recorder.Path,
        "mkdir",
        lambda *a, **k: (_ for _ in ()).throw(PermissionError("нет прав")),
    )
    assert llm_recorder.record(contract="vp_sd_world", reply="ответ") is None


def test_error_without_verdict_is_marked_as_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(llm_recorder.ENV_DIR, str(tmp_path))
    path = llm_recorder.record(contract="vp_apply_ops", reply="брак", error="схема нарушена")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["verdict"] == "error"
    assert saved["error"] == "схема нарушена"


def test_policy_records_only_when_enabled(tmp_path, monkeypatch) -> None:
    """Врезка в `contracts/policy` не должна писать при выключенной записи."""
    from app.contracts.policy import _record

    monkeypatch.delenv(llm_recorder.ENV_DIR, raising=False)
    _record("vp_sd_world", "ответ", label="n1", verdict="ok")
    assert not list(tmp_path.glob("*.json"))

    monkeypatch.setenv(llm_recorder.ENV_DIR, str(tmp_path))
    _record("vp_sd_world", "ответ", label="n1", verdict="ok")
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8"))["node_key"] == "n1"
