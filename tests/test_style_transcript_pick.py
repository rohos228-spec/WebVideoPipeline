"""Выбор транскрипта (субтитры или ASR) и фильтр водяных знаков в OCR."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.style_agent import ocr
from app.services.style_agent import transcript_pick as tp


@dataclass
class _W:
    word: str
    start: float
    end: float


def _words(text: str, t0: float = 0.0, step: float = 0.4) -> list[_W]:
    return [_W(w, round(t0 + i * step, 2), round(t0 + i * step + 0.3, 2)) for i, w in enumerate(text.split())]


SPEECH = "сейчас я подробно расскажу как оплатить подписку через прокси выбираем регион турция"


def test_matching_subtitles_stay_transcript() -> None:
    subs = [
        {"start": 0.0, "end": 1.6, "text": "Сейчас я подробно расскажу", "conf": 0.9},
        {"start": 1.6, "end": 3.2, "text": "как оплатить подписку", "conf": 0.9},
        {"start": 3.2, "end": 5.0, "text": "через прокси выбираем регион Турция", "conf": 0.9},
    ]
    pick = tp.choose(subs, _words(SPEECH))
    assert pick["source"] == "subtitles"
    assert pick["agreement"]["precision"] == 1.0 and pick["agreement"]["recall"] == 1.0


def test_watermark_and_ui_text_send_transcript_to_asr() -> None:
    subs = [
        {"start": 0.0, "end": 1.0, "text": "сейчас Aronatine of adgerce TikTok", "conf": 0.9},
        {"start": 1.0, "end": 2.0, "text": "Выгода 10% Выгода 15% TikT iger", "conf": 0.9},
        {"start": 2.0, "end": 3.0, "text": "Оператор (Необя Выбери 0/1 Bce", "conf": 0.9},
    ]
    pick = tp.choose(subs, _words(SPEECH))
    assert pick["source"] == "asr" and "чужой текст" in pick["reason"]


def test_one_word_subtitles_missing_half_go_to_asr() -> None:
    subs = [
        {"start": i, "end": i + 0.5, "text": w, "conf": 0.9}
        for i, w in enumerate(["сейчас", "расскажу", "прокси", "турция"])
    ]
    pick = tp.choose(subs, _words(SPEECH))
    assert pick["source"] == "asr" and "покрывают" in pick["reason"]
    assert pick["agreement"]["precision"] == 1.0


def test_latin_lookalikes_match_cyrillic() -> None:
    ag = tp.agreement([{"text": "MockBy BOT oHa"}], _words("москву вот она"))
    assert ag["precision"] == 1.0


def test_no_asr_keeps_subtitles() -> None:
    subs = [{"start": i, "end": i + 1, "text": f"строка {i}", "conf": 0.9} for i in range(3)]
    assert tp.choose(subs, [])["source"] == "subtitles"
    assert tp.choose([], [])["source"] == "none"


def test_phrases_split_on_sentence_end_and_pause() -> None:
    words = [_W("Привет.", 0.0, 0.4), _W("Мы", 0.5, 0.7), _W("едем", 0.8, 1.0), _W("домой", 2.0, 2.3)]
    ph = tp.phrases_from_words(words)
    assert [p["text"] for p in ph] == ["Привет.", "Мы едем", "домой"]
    assert ph[1]["start"] == 0.5 and ph[1]["end"] == 1.0


def test_static_overlays_dropped_but_subtitles_kept() -> None:
    raw = []
    for i in range(20):
        boxes = [["@adgerce", 0.9, 0.85, 0.9], ["Aronatine" if i % 2 else "Aronotine", 0.8, 0.7, 0.5]]
        boxes.append([f"слово{i // 4}", 0.95, 0.8, 0.5])  # субтитр держится 4 кадра
        raw.append({"t": i * 0.5, "boxes": boxes})
    out, static = ocr.drop_static_overlays(raw)
    assert set(static) == {"@adgerce", "aronatine", "aronotine"}
    assert all(len(fr["boxes"]) == 1 and fr["boxes"][0][0].startswith("слово") for fr in out)


def test_short_clip_no_overlay_filter() -> None:
    raw = [{"t": i * 0.5, "boxes": [["Title", 0.9, 0.8, 0.5]]} for i in range(5)]
    out, static = ocr.drop_static_overlays(raw)
    assert out is raw and static == []
