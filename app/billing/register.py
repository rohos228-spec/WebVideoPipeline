"""Подключение роутеров биллинга к приложению: register(app, prefix="/api")."""

from __future__ import annotations

from fastapi import FastAPI

from app import settings_billing


def register(app: FastAPI, prefix: str = "/api") -> None:
    from app.billing.router import router

    app.include_router(router, prefix=prefix)
    if settings_billing.load().test_hooks:
        from app.billing.test_hooks import router as hooks

        app.include_router(hooks, prefix=prefix)
