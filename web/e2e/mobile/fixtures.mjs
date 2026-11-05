// Read-only fixtures match studio-stubs.ts and the UI's Profile/Stats contracts.
// Deliberately no generation, login, payment or mutation handlers.
export const profile = {
  name: "Тестовый пользователь",
  handle: "mobile-review",
  bio: "Изолированная проверка мобильного интерфейса",
  plan: "Pro",
  avatar_url: null,
  created_at: "2026-01-01T12:00:00Z",
};

export const textLlm = { active_label: "Тестовая модель", active_provider: "fixture", models: [] };

export const readFixtures = {
  "/api/auth/status": { auth_required: false },
  "/api/auth/operator-token": { token: null },
  "/api/admin/access": { is_admin: false },
  "/api/projects": [],
  "/api/sidebar-layout": { folders: [], project_layout: {}, gen_queue: [], gen_queue_positions: {} },
  "/api/chat-notices": { notices: [], count: 0, last_id: 0 },
  "/api/action-layer/catalog": { count: 0, actions: [] },
  "/api/action-layer/plans": { items: [] },
  "/api/action-layer/macros": { macros: [] },
  "/api/action-layer/history": { items: [] },
  "/api/profile": profile,
  "/api/profile/stats": {
    projects: 0, completed_videos: 0, published: 0, generations_total: 0,
    totals_by_kind: {}, per_project: {}, daily: [], status_counts: {},
  },
  "/api/profile/keys": [],
  "/api/outsee/status": { configured: false, balance: null },
  "/api/text-llm": textLlm,
  "/api/studio-version": { ui_stale: false },
  "/api/workflows": [],
  "/api/gpt-workspace/sessions": { sessions: [] },
  "/api/outsee-create/settings": {},
  "/api/outsee-create/history": [],
  "/api/create/queue": { max_parallel: 1, running_count: 0, waiting_count: 0, total_active: 0, running: [], waiting: [], jobs: [] },
  "/api/kie-create/catalog": { credit_usd: 0.005, categories: [], models: [], configured: false },
  "/api/kie-create/credits": { configured: false, credits: null, usd: null },
};

export const chatText = [
  "Мобильная проверка: это локальный ответ, генерации не запускались.",
  "",
  `Длинная строка: https://example.invalid/${"mobile-regression-".repeat(14)}`,
  "",
  "| Этап | Описание | Статус |",
  "| --- | --- | --- |",
  "| Проверка | Широкая таблица должна прокручиваться внутри чата, не растягивая страницу | Только mock |",
  "",
  "```text",
  "unbroken_fixture_".repeat(25),
  "```",
].join("\n");

// Event envelope/payload taken from orchestrator/mock-server.mjs (message event).
export function chatEvent(session) {
  return {
    type: "message", task_id: null, seq: 1, session_id: session,
    payload: { role: "assistant", text: chatText },
  };
}
