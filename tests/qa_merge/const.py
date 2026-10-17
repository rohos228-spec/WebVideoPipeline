"""Константы QA-набора. Если реальный путь в репо отличается — правим ТОЛЬКО здесь."""

# ---- Эндпоинты API ----
AUTH = {
    "register":        "/api/auth/register",
    "verify":          "/api/auth/verify-email",        # POST {token}
    "login":           "/api/auth/login",
    "logout":          "/api/auth/logout",
    "forgot":          "/api/auth/forgot-password",     # POST {email}
    "reset":           "/api/auth/reset-password",      # POST {token, password}
    "sessions":        "/api/auth/sessions",            # GET / DELETE {id}
    "oauth_link":      "/api/auth/oauth/{provider}/link",      # POST, provider: telegram|google|vk
    "oauth_callback":  "/api/auth/oauth/{provider}/callback",
    "me":              "/api/auth/me",
}
BILLING = {
    "balance":       "/api/billing/balance",
    "ledger":        "/api/billing/ledger",
    "plans":         "/api/billing/plans",
    "checkout":      "/api/billing/checkout",
    "subscription":  "/api/billing/subscription",
    "webhook":       "/api/billing/webhook/yookassa",
}
PROFILE = {
    "me":       "/api/profile/me",
    "roles":    "/api/profile/roles",          # owner/admin управление ролями
    "set_role": "/api/profile/roles/{user_id}",
}
MODELS_HEALTH = "/api/models/health"
ADMIN_USERS   = "/api/admin/users"             # доступен только admin/owner

# Тестовые хуки бэкенда (включаются VIA_TEST_HOOKS=1, в проде недоступны)
TEST_HOOKS = {
    "run_generation": "/api/_test/generation/run",      # POST {model, fail: bool, user_id}
    "queue_state":    "/api/_test/queue/state",         # GET — активные/ожидающие задачи
    "enqueue":        "/api/_test/queue/enqueue",       # POST {count}
    "notify":         "/api/_test/notify/last",         # GET — последнее уведомление пользователю
    "report":         "/api/_test/report/last",         # GET — последний отчёт пользователю
}

# ---- Пути модулей для monkeypatch (правим при расхождении) ----
MAIL_SEND_FUNC   = "app.services.user_accounts.mailer.send_mail"
OAUTH_VERIFY = {
    "telegram": "app.web.routers.oauth.verify_telegram_payload",
    "google":   "app.web.routers.oauth.verify_google_token",
    "vk":       "app.web.routers.oauth.verify_vk_token",
}
APP_FACTORY = "app.web.api:create_app"

# ---- Запрещённые имена провайдеров (ТЗ п.22) ----
PROVIDER_WORDS = [
    "52pay", "kie", "outsee", "vibecode",
    "wavespeed", "elevenlabs", "anthropic", "openai",
]
import re
PROVIDER_RE = re.compile(r"(?i)\b(" + "|".join(PROVIDER_WORDS) + r")\b")

YOOKASSA_WEBHOOK_SECRET = "qa-sandbox-webhook-secret"
