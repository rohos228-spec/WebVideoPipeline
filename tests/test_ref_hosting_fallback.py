"""Публикация ref-кадра для Outsee: Yandex-only по умолчанию, публичные хосты — по опт-ину.

До этого файл утверждал обратное («без Yandex откатываемся на litterbox»)
и прямо противоречил `test_outsee_veo_settings.py::
test_ensure_public_requires_yandex_no_public_hosts`, который висел красным.
Политика зафиксирована: анонимные файлохостинги (litterbox/catbox/uguu/0x0)
кладут кадр по публичному URL без авторизации, поэтому выключены, а включаются
осознанно через `OUTSEE_ALLOW_PUBLIC_HOSTS`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.bots.outsee_http import OutseeApiError, ensure_public_image_url

_PNG_DATA_URL = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
    "DUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _unconfigure_yandex(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "yandex_storage_bucket", "")
    monkeypatch.setattr(settings, "yandex_storage_access_key", "")
    monkeypatch.setattr(settings, "yandex_storage_secret_key", "")


@pytest.mark.asyncio
async def test_no_yandex_and_no_opt_in_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """S3 не настроен, опт-ина нет → громкая ошибка, ни один хост не тронут."""
    from app.settings import settings

    _unconfigure_yandex(monkeypatch)
    monkeypatch.setattr(settings, "outsee_allow_public_hosts", False)

    with patch("app.bots.outsee_http._host_via_litterbox", new_callable=AsyncMock) as mock_litterbox:
        with pytest.raises(OutseeApiError, match="только через Yandex"):
            await ensure_public_image_url(_PNG_DATA_URL)
    assert not mock_litterbox.called


@pytest.mark.asyncio
async def test_no_yandex_with_opt_in_uses_public_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """OUTSEE_ALLOW_PUBLIC_HOSTS=true — старое поведение доступно осознанно."""
    from app.settings import settings

    _unconfigure_yandex(monkeypatch)
    monkeypatch.setattr(settings, "outsee_allow_public_hosts", True)

    with patch("app.bots.outsee_http._host_via_litterbox", new_callable=AsyncMock) as mock_litterbox:
        mock_litterbox.return_value = "https://litterbox.catbox.moe/files/test_frame.png"

        hosted = await ensure_public_image_url(_PNG_DATA_URL)

    assert hosted == "https://litterbox.catbox.moe/files/test_frame.png"
    assert mock_litterbox.called


@pytest.mark.asyncio
async def test_yandex_configured_does_not_touch_public_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    """S3 настроен и опт-ина нет → пробуем только Yandex, без запасных хостов."""
    from app.settings import settings

    monkeypatch.setattr(settings, "outsee_allow_public_hosts", False)

    async def fake_yandex(_client, _raw, _mime, _filename):
        return "https://storage.yandexcloud.net/b/vp-frames/ok.jpg"

    with (
        patch("app.bots.yandex_storage.yandex_storage_configured", return_value=True),
        patch("app.bots.outsee_http._host_via_yandex", side_effect=fake_yandex),
        patch("app.bots.outsee_http._host_via_litterbox", new_callable=AsyncMock) as mock_litterbox,
        patch("app.bots.outsee_http._host_via_catbox", new_callable=AsyncMock) as mock_catbox,
    ):
        hosted = await ensure_public_image_url(_PNG_DATA_URL)

    assert hosted == "https://storage.yandexcloud.net/b/vp-frames/ok.jpg"
    assert not mock_litterbox.called
    assert not mock_catbox.called
