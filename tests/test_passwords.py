"""Пароли: хеш, проверка, требования, невыдача сведений временем ответа.

До 2026-08-24 пароль в проекте был один — `WEB_AUTH_PASSWORD`, открытым
текстом в окружении, сравнивался оператором `!=` и никакими тестами не был
покрыт вовсе. Этот файл — первая проверка того слоя, который его заменил.
"""

from __future__ import annotations

import pytest

from app.services import passwords

GOOD = "gT7k-mQ2p-Xw9d-Rn4s"


def test_hash_is_argon2id_and_carries_its_parameters() -> None:
    """Параметры внутри строки: смена профиля не ломает старые хеши."""
    digest = passwords.hash_password(GOOD)
    assert digest.startswith("$argon2id$")
    assert "m=65536" in digest


def test_same_password_hashes_differently_every_time() -> None:
    """Соль случайна. Одинаковые хеши означали бы её отсутствие."""
    assert passwords.hash_password(GOOD) != passwords.hash_password(GOOD)


def test_password_verifies_against_its_own_hash() -> None:
    assert passwords.verify_password(GOOD, passwords.hash_password(GOOD)) is True


def test_wrong_password_does_not_verify() -> None:
    assert passwords.verify_password("совсем не тот", passwords.hash_password(GOOD)) is False


def test_empty_stored_hash_never_verifies() -> None:
    """Учётка без пароля не пускает никого, включая пустую строку."""
    assert passwords.verify_password("", "") is False
    assert passwords.verify_password(GOOD, "") is False


def test_broken_hash_is_a_refusal_and_not_a_crash() -> None:
    """Испорченная строка в базе — отказ, а не 500 на форме входа."""
    assert passwords.verify_password(GOOD, "не хеш вовсе") is False


def test_long_password_is_not_truncated() -> None:
    """Ловушка bcrypt: он молча режет на 72 байтах, argon2 — нет.

    Парольная фраза из русских слов в UTF-8 набирает 72 байта примерно на
    тридцати шести символах. На bcrypt два разных длинных пароля с общим
    началом совпали бы — и никто бы об этом не узнал.
    """
    base = "длинная-парольная-фраза-из-русских-слов-которая-заведомо-длиннее-семидесяти-двух-байт"
    digest = passwords.hash_password(base + "-конец-один")
    assert passwords.verify_password(base + "-конец-два", digest) is False
    assert passwords.verify_password(base + "-конец-один", digest) is True


def test_unicode_normalization_makes_one_password_one_hash() -> None:
    """«é» набирается одним кодом и двумя. Для человека это один символ.

    Без NFKC пользователь, сменивший клавиатуру, получил бы «неверный пароль»
    на верном пароле — и не смог бы понять, почему.
    """
    composed = "café-parol-nadezhnyy"  # e + комбинирующий акут
    precomposed = "café-parol-nadezhnyy"  # é одним кодом
    assert composed != precomposed
    assert passwords.verify_password(composed, passwords.hash_password(precomposed)) is True


def test_short_password_is_refused() -> None:
    with pytest.raises(passwords.WeakPasswordError, match="короче"):
        passwords.assert_strong("korotkiy1")


def test_absurdly_long_password_is_refused() -> None:
    """Верхняя граница — не про стойкость, а про отказ в обслуживании."""
    with pytest.raises(passwords.WeakPasswordError, match="длиннее"):
        passwords.assert_strong("a" * (passwords.MAX_LENGTH + 1))


def test_low_variety_password_is_refused() -> None:
    """Двенадцать символов и один бит энтропии — всё ещё один бит."""
    with pytest.raises(passwords.WeakPasswordError, match="разных"):
        passwords.assert_strong("aaaaaaaaaaaaaa")


def test_banned_password_is_refused_despite_length() -> None:
    with pytest.raises(passwords.WeakPasswordError, match="подбора"):
        passwords.assert_strong("changeme1234")


def test_padded_password_is_refused() -> None:
    """Пробел по краям теряется при копировании и ломает вход позже."""
    with pytest.raises(passwords.WeakPasswordError, match="пробел"):
        passwords.assert_strong(f" {GOOD} ")


def test_good_password_passes() -> None:
    passwords.assert_strong(GOOD)


def test_needs_rehash_is_false_for_a_fresh_hash() -> None:
    assert passwords.needs_rehash(passwords.hash_password(GOOD)) is False


def test_needs_rehash_is_true_for_a_weaker_profile() -> None:
    """Хеш по старому профилю обязан помечаться к пересчёту на входе."""
    from argon2 import PasswordHasher

    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(GOOD)
    assert passwords.needs_rehash(weak) is True


def test_needs_rehash_survives_garbage() -> None:
    assert passwords.needs_rehash("не хеш") is False
    assert passwords.needs_rehash("") is False


def test_dummy_verify_costs_the_same_as_a_real_check() -> None:
    """Вход по несуществующему адресу обязан стоить столько же, сколько по
    существующему: иначе форма входа становится справочником аккаунтов.

    Порог намеренно грубый — в десять раз, — потому что тест меряет время на
    машине с соседями по CPU. Он ловит то, ради чего написан: не «отличие в
    полтора раза», а «мгновенный отказ против десятков миллисекунд».
    """
    import time

    digest = passwords.hash_password(GOOD)

    start = time.perf_counter()
    passwords.verify_password("не тот пароль совсем", digest)
    real = time.perf_counter() - start

    start = time.perf_counter()
    passwords.dummy_verify()
    fake = time.perf_counter() - start

    assert fake > real / 10, f"фиктивная проверка {fake:.4f}s против настоящей {real:.4f}s"
