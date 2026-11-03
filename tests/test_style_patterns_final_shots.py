"""patterns получает планы после удаления ложных склеек (смены субтитров)."""

from __future__ import annotations

from app.services.style_agent import agents, extract
from app.services.style_agent.job import with_final_shots


def _profile() -> dict:
    # 8 с один план, но детектор поставил склейку на смене субтитра в 3.5 с
    cuts = [
        {"t": 3.5, "score": 0.03, "kind": "cue", "uncertain": True},
        {"t": 8.0, "score": 0.34, "kind": "hard"},
    ]
    shots = extract.shots_from_cuts(cuts, 16.0)
    subs = [
        {"start": 0.0, "end": 3.5, "text": "первая фраза", "conf": 0.99},
        {"start": 3.5, "end": 8.0, "text": "вторая фраза", "conf": 0.99},
        {"start": 8.0, "end": 16.0, "text": "третья фраза", "conf": 0.99},
    ]
    return {"format": {"duration_s": 16.0}, "shots": shots, "subtitles": subs, "motion": {"x": 1}}


def test_patterns_sees_merged_shots_and_relinked_phrases() -> None:
    prof = _profile()
    assert len(prof["shots"]) == 3
    reply = {"false_cuts": [3.5], "shots": [{"i": 1, "what": "сова"}, {"i": 2, "what": "сова ближе"}]}
    final = agents.merge_shot_descriptions(agents.apply_false_cuts(prof["shots"], [3.5]), reply)
    fixed, fixed_reply = with_final_shots(prof, final, reply)
    assert [(s["start"], s["end"]) for s in fixed["shots"]] == [(0.0, 8.0), (8.0, 16.0)]
    assert fixed["motion"]["x"] == 1 and fixed["motion"]["shots"] == 2
    assert [ln["shot_ids"] for ln in fixed["linkage"]["lines"]] == [[1], [1], [2]]
    assert [s["i"] for s in fixed_reply["shots"]] == [1, 2]
    assert fixed_reply["shots"][0]["what"] == "сова"
    assert prof["shots"] is not fixed["shots"] and len(prof["shots"]) == 3  # исходный профиль цел


def test_no_false_cuts_returns_same_objects() -> None:
    prof = _profile()
    reply = {"shots": []}
    same, same_reply = with_final_shots(prof, [dict(s) for s in prof["shots"]], reply)
    assert same is prof and same_reply is reply
