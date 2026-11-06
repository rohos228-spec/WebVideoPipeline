"""Мост окна «ИИ-агент» к движку оркестратора (без сервера и модели)."""

import asyncio
from types import SimpleNamespace

from app.orchestrator import ai_agent_bridge as bridge


def test_clean_text_strips_auto_prefix_and_keeps_legacy_only_requests():
    text, ok = bridge.clean_text("[РЕЖИМ: АВТОНОМНЫЙ. Выполняй сразу]\n\nзапусти видео")
    assert ok and text == "запусти видео"
    for legacy in (
        "поправь в коде студии функцию формата времени",
        "закоммить и запушь",
        "Брифинг проекта",
        "Покажи доступные действия по проекту",
        "глянь файл\n\n[Вложенные файлы:\n- a.txt]",
    ):
        assert bridge.clean_text(legacy)[1] is False, legacy
    assert bridge.clean_text("перерисуй кадр 5")[1] is True
    assert bridge.clean_text("брифинг проекта 10 и что дальше")[1] is True


def test_handled_falls_back_on_not_understood():
    assert bridge.handled({"reply": "Проект #10 на паузе.", "card": None})
    assert bridge.handled({"reply": "", "card": {"change_id": "x"}})
    assert not bridge.handled({"reply": "Не понял, что сделать. Уточните", "card": None})
    assert not bridge.handled({"reply": "Этого чат пока не умеет.", "card": None})
    # С пояснением, чего нет и что можно вместо, — ответ оркестратора, не старый путь.
    assert bridge.handled({"reply": "Этого чат пока не умеет. Скорость речи не настраивается.", "card": None})
    assert not bridge.handled({"reply": "", "card": None})
    assert bridge.handled({"reply": "Сценарий верный. Совы не умеют видеть в полной темноте.", "card": None})
    assert not bridge.handled(None)


def test_card_becomes_window_action_with_orch_token():
    card = {
        "change_id": "orch-appr-1-1",
        "title": "Поставить правки в очередь",
        "risk": "medium",
        "cost_text": "бесплатно",
        "steps": [{"text": "картинка кадра 5 заново; проект #10", "paid": True, "params_text": "кадр 5"}],
    }
    out = bridge.done_payload({"reply": "", "card": card})
    item = out["pending_confirm"][0]
    assert item["kind"] == "action" and item["token"] == "orch:orch-appr-1-1"
    assert "картинка кадра 5" in item["title"] and "(платно)" in item["title"] and "риск: средний" in item["title"]
    assert out["reply"] and out["engine"] == "orchestrator"


def test_collect_returns_card_and_reply():
    async def run():
        q: asyncio.Queue = asyncio.Queue()
        ev = lambda seq, typ, **pl: SimpleNamespace(seq=seq, type=typ, payload=pl)  # noqa: E731
        await q.put(ev(1, "message", text="старое"))
        await q.put(ev(5, "message", text="Смотрю проект"))
        await q.put(ev(6, "approval_required", change_id="c1", title="Шаг"))
        return await bridge._collect(q, 4, 5)

    res = asyncio.run(run())
    assert res["reply"] == "Смотрю проект" and res["card"]["change_id"] == "c1"


def test_disabled_flag_goes_legacy(monkeypatch):
    monkeypatch.setenv("AI_AGENT_ORCHESTRATOR", "0")

    async def run():
        return [x async for x in bridge.stream_turn(10, "что с проектом", None)]

    assert asyncio.run(run()) == [("result", None)]


def test_summary_drops_same_request_in_other_words_but_keeps_other_project():
    from app.orchestrator.agents.summarizer import drop_goal_quote

    goal = "покажи ошибки в логах по проекту 4"
    text = "В журнале проекта #4 ошибок не найдено.\n\n«Покажи ошибки в журнале проекта 4»"
    assert drop_goal_quote(text, goal) == "В журнале проекта #4 ошибок не найдено."
    other = "Ошибок нет. «покажи ошибки в логах по проекту 7»"
    assert "проекту 7" in drop_goal_quote(other, goal)


def test_node_failure_diagnosis_reads_project_errors_too():
    from app.orchestrator.agents.failure_scope import logs_with_diagnosis
    from app.orchestrator.contracts import PlanStep

    plan = [PlanStep(action="diag.node_failure", params={"project_id": 11}, expected="x")]
    out = logs_with_diagnosis(plan)
    assert [s.action for s in out] == ["diag.node_failure", "logs.tail"]
    assert out[1].params == {"project_id": 11, "kind": "errors"}
    assert len(logs_with_diagnosis(out)) == 2


def test_summary_drops_quote_that_only_adds_window_project():
    from app.orchestrator.agents.summarizer import drop_goal_quote

    text = "Переходы пока не добавлены. В проекте #4 — 13 кадров.\n\n«Добавь переходы между сценами в проекте 4»"
    assert drop_goal_quote(text, "добавь переходы между сценами") == "Переходы пока не добавлены. В проекте #4 — 13 кадров."


def test_voice_speed_gap_is_explained():
    from app.orchestrator.agents.summarizer import known_gap_text

    assert "Скорость речи" in known_gap_text("сделай голос медленнее")
    assert "Скорость речи" in known_gap_text("ускорь озвучку")
    assert known_gap_text("поменяй голос на мужской") == ""


def test_old_project_without_own_canvas_sees_run_graph_nodes():
    from app.services.canvas_graph import with_effective_graph

    graph = {"nodes": [{"id": "n_videos", "type": "videos"}], "edges": [], "workflow_id": 1}
    meta = with_effective_graph({"step_failure": {}}, graph)
    assert [n["id"] for n in meta["canvas_graph"]["nodes"]] == ["n_videos"]
    own = {"canvas_graph": {"nodes": [{"id": "n_a", "type": "plan"}], "edges": []}}
    assert with_effective_graph(own, graph)["canvas_graph"]["nodes"][0]["id"] == "n_a"


def test_trailing_verb_moves_only_when_alone():
    from app.orchestrator.queue import fix_typos

    assert fix_typos("все проекты до монтажа доведи") == "запусти все проекты до монтажа"
    for keep in (
        "разберись почему упал ИИ-редактор сцен, почини и перезапусти",
        "проверь что закадр совпадает с текстом сценария, и если нет — исправь",
    ):
        assert fix_typos(keep) == keep


def test_card_hides_reads_ids_and_repeated_project():
    card = {
        "change_id": "c", "title": "Изменить закадр ячейки", "risk": "medium",
        "steps": [
            {"text": "Результат ноды — нода «Сценарий»; проект #10", "risk": "none"},
            {"text": "Изменить закадр — parent_uuid 4a8a1c4a233d4dd3a1b3e795; voiceover Вот факт.; проект #10", "risk": "medium"},
            {"text": "Изменить закадр — parent_uuid 7ef52126e98740298ecc0f19; voiceover Первый.; проект #10", "risk": "medium"},
        ],
    }
    title = bridge.card_title(card)
    assert "Результат ноды" not in title and "parent_uuid" not in title and "voiceover" not in title
    assert title.count("проект #10") == 1 and "закадр: Вот факт." in title


def test_new_video_request_skips_open_project_rules():
    from app.orchestrator.agents.supervisor import _NEW_VIDEO_RE

    assert _NEW_VIDEO_RE.search("сделай ролик про пчёл в стиле аниме с женским голосом")
    assert not _NEW_VIDEO_RE.search("поменяй голос на женский")
    assert not _NEW_VIDEO_RE.search("сделай стиль аниме для всех кадров")


def test_open_card_words_and_plain_errors():
    for word in ("нет", "нет, не надо", "отмена", "не надо", "передумал", "Отмени."):
        assert bridge._NO_RE.match(word), word
    for other in ("нет картинки у кадра 5", "отмени кадр 5", "перерисуй кадр 6"):
        assert not bridge._NO_RE.match(other), other
    assert bridge._is_question("что с проектом") and not bridge._is_question("перерисуй кадр 6")

    raw = (
        "Не получилось.\nШаг «Обмен»: шаг не выполнен: ActionCallError: HTTP 400: оба слота пустые.\n"
        "Что можно сделать: Устраните причину, указанную выше, и повторите."
    )
    assert bridge.plain_errors(raw) == "Не получилось.\nШаг «Обмен»: оба слота пустые."
    wrapped = "ActionCallError: HTTP 404: {'code': 'node_not_found', 'message': 'Ноды нет на холсте.'}."
    assert bridge.plain_errors(wrapped) == "Ноды нет на холсте.."


def test_one_step_card_title_is_the_step():
    card = {"change_id": "c", "title": "", "risk": "medium",
            "steps": [{"text": "Сменить модель ноды «Видео» на Hailuo —; проект #10", "risk": "medium"}]}
    assert bridge.card_title(card).startswith("Сменить модель ноды «Видео» на Hailuo (проект #10) · риск")


def test_next_free_step_of_approved_plan_needs_no_second_button():
    steps = [
        {"action": "montage.set_assemble_params", "paid": False, "risk": "medium", "text": "Музыка 10; проект #11"},
        {"action": "audio.set_voice", "paid": False, "risk": "medium", "text": "Голос женский; проект #11"},
    ]
    first = {"task_id": 5, "action": steps[0]["action"], "risk": "medium", "steps": steps}
    second = {"task_id": 5, "action": steps[1]["action"], "risk": "medium", "steps": steps}
    assert bridge.same_plan_free_step(first, second)
    assert "Музыка" not in bridge.card_title({**second, "title": "Голос"})
    assert not bridge.same_plan_free_step(first, {**second, "task_id": 6})
    assert not bridge.same_plan_free_step(first, {**second, "risk": "high"})
    paid = [steps[0], {**steps[1], "paid": True}]
    assert not bridge.same_plan_free_step({**first, "steps": paid}, {**second, "steps": paid})
    assert not bridge.same_plan_free_step(None, second)
