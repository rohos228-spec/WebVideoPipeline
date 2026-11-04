"""Откуда брать транскрипт: вшитые субтитры (OCR) или ASR.

Раньше хватало трёх строк OCR, и ASR даже не запускался. На настоящих рилсах
это ломалось двумя способами (замер 03.11 на двух роликах по 91 с):

* в полосу субтитров попадают водяной знак («@adgerce TikTok»), надпись на
  футболке и интерфейс из записи экрана — транскрипт становился кашей, хотя
  whisper small распознал речь почти без ошибок;
* субтитры по одному слову меняются чаще, чем кадры OCR (2 в секунду):
  из 161 сказанного слова в субтитры попало 75.

Теперь ASR идёт всегда, когда есть звук, и OCR становится транскриптом только
если его слова почти все есть в речи (precision) и покрывают почти всю речь
(recall). Иначе фразы для связки с планами собираются из слов ASR.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

SUBS_MIN_PRECISION = 0.7  # доля слов OCR, найденных в речи
SUBS_MIN_RECALL = 0.7  # доля слов речи, найденных в субтитрах
_MATCH_RATIO = 0.75  # «рорбуши» ~ «горбуши», «звенигорода» ~ «венигорода»

PHRASE_MAX_GAP_S = 0.6
PHRASE_MAX_LEN_S = 6.0
_END_RE = re.compile(r"[.!?…]$")

# латинские двойники кириллицы: OCR пишет «MockBy», «BOT», «oHa»
_HOMO = str.maketrans("abcehkmoptxyABCEHKMOPTXY", "абсенкмортхуАВСЕНКМОРТХУ")
_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    t = str(text or "").translate(_HOMO).lower().replace("ё", "е")
    return [w for w in _TOKEN_RE.findall(t) if len(w) >= 2]


def _found(tok: str, pool: set[str]) -> bool:
    if tok in pool:
        return True
    return any(difflib.SequenceMatcher(None, tok, p).ratio() >= _MATCH_RATIO for p in pool)


def agreement(subs: list[dict[str, Any]], asr_words: list[Any]) -> dict[str, Any]:
    """Сходство слов OCR-субтитров и ASR: precision (OCR→речь) и recall (речь→OCR)."""
    ocr = [w for ln in subs for w in _tokens(str(ln.get("text") or ""))]
    asr = [w for x in asr_words for w in _tokens(str(getattr(x, "word", x)))]
    if not ocr or not asr:
        return {"precision": None, "recall": None, "ocr_words": len(ocr), "asr_words": len(asr)}
    asr_set, ocr_set = set(asr), set(ocr)
    prec = sum(1 for w in ocr if _found(w, asr_set)) / len(ocr)
    rec = sum(1 for w in asr if _found(w, ocr_set)) / len(asr)
    return {
        "precision": round(prec, 3),
        "recall": round(rec, 3),
        "ocr_words": len(ocr),
        "asr_words": len(asr),
    }


def choose(subs: list[dict[str, Any]], asr_words: list[Any]) -> dict[str, Any]:
    """→ {"source": "subtitles"|"asr"|"none", "agreement": {...}, "reason": str}."""
    if not asr_words:
        src = "subtitles" if len(subs) >= 3 else "none"
        return {"source": src, "agreement": None, "reason": "нет ASR"}
    if len(subs) < 3:
        return {"source": "asr", "agreement": None, "reason": "субтитров нет или меньше 3 строк"}
    ag = agreement(subs, asr_words)
    prec, rec = ag["precision"] or 0.0, ag["recall"] or 0.0
    if prec >= SUBS_MIN_PRECISION and rec >= SUBS_MIN_RECALL:
        return {"source": "subtitles", "agreement": ag, "reason": "субтитры совпадают с речью"}
    why = []
    if prec < SUBS_MIN_PRECISION:
        why.append(f"в субтитрах чужой текст (совпало с речью {prec:.0%})")
    if rec < SUBS_MIN_RECALL:
        why.append(f"субтитры покрывают {rec:.0%} речи")
    return {"source": "asr", "agreement": ag, "reason": "; ".join(why)}


def phrases_from_words(words: list[Any]) -> list[dict[str, Any]]:
    """Слова ASR → фразы {start, end, text, conf}: конец предложения, пауза или длина."""
    out: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    prev_end = None
    for w in words:
        text = str(getattr(w, "word", "")).strip()
        if not text:
            continue
        start, end = float(getattr(w, "start", 0.0)), float(getattr(w, "end", 0.0))
        if cur is not None and (
            (prev_end is not None and start - prev_end >= PHRASE_MAX_GAP_S)
            or end - float(cur["start"]) > PHRASE_MAX_LEN_S
        ):
            out.append(cur)
            cur = None
        if cur is None:
            cur = {"start": round(start, 3), "end": round(end, 3), "text": text, "conf": 1.0}
        else:
            cur["end"] = round(end, 3)
            cur["text"] = f"{cur['text']} {text}"
        prev_end = end
        if _END_RE.search(text):
            out.append(cur)
            cur = None
    if cur is not None:
        out.append(cur)
    return out
