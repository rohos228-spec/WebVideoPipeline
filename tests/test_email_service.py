"""Unit tests for email_service (Resend API & SMTP transports)."""

from __future__ import annotations

import httpx
import pytest

from app.services import email_service
from app.settings import settings


@pytest.mark.asyncio
async def test_email_service_dev_mode(monkeypatch):
    """When neither Resend nor SMTP is configured, it falls back to dev mode and returns True."""
    monkeypatch.setattr(settings, "resend_api_key", "")
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_user", "")

    ok = await email_service.send_verification_code("test@example.com", "123456", purpose="register")
    assert ok is True


@pytest.mark.asyncio
async def test_email_service_resend_success(monkeypatch):
    """When Resend is configured, send_verification_code sends via Resend HTTPS API."""
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key_12345")
    monkeypatch.setattr(settings, "resend_from", "Видеостудия <noreply@zukiemi.space>")

    captured_requests = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"id": "msg_123456789"})

    transport = httpx.MockTransport(mock_handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: orig_client(transport=transport))

    ok = await email_service.send_verification_code("recipient@example.com", "654321", purpose="register")
    assert ok is True
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert str(req.url) == "https://api.resend.com/emails"
    assert req.headers["authorization"] == "Bearer re_test_key_12345"


@pytest.mark.asyncio
async def test_email_service_resend_error(monkeypatch):
    """When Resend returns an error status code, it logs and returns False."""
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key_12345")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"statusCode": 400, "message": "Domain not verified"})

    transport = httpx.MockTransport(mock_handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: orig_client(transport=transport))

    ok = await email_service.send_verification_code("recipient@example.com", "654321", purpose="reset_password")
    assert ok is False
