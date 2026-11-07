from __future__ import annotations

import re

_CARDINALS: dict[str, int] = {
    "один": 1, "одна": 1, "одно": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
    "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12,
    "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
    "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20,
}
_WORD_RE = re.compile(r"(?<![а-яё])(" + "|".join(sorted(_CARDINALS, key=len, reverse=True)) + r")(?![а-яё])", re.I)


def words_to_digits(text: str) -> str:
    return _WORD_RE.sub(lambda m: str(_CARDINALS[m.group(1).lower()]), str(text or ""))
