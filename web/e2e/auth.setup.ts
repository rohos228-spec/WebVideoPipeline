import { test as setup, expect, request as pwRequest } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Вход перед прогоном. Результат — файл состояния, который подхватывают
 * остальные спеки: и cookie сессии, и токен в `localStorage`.
 *
 * Почему и то и другое. Сервер выдаёт один токен двумя путями: cookie
 * `HttpOnly` для стримов (`EventSource` и WebSocket заголовков не ставят) и
 * тело ответа — для заголовка `Authorization`, который проставляет `lib/api`
 * из `localStorage`. Восстановить надо оба: с одной cookie страница
 * загрузится, но `AuthGate` не найдёт токен и покажет форму входа.
 *
 * Установка может работать и БЕЗ учётных записей (`STUDIO_SESSION_SECRET` не
 * задан) — это режим владельца на одной машине. Тогда шаг записывает пустое
 * состояние и не делает ничего: гонять вход там не только незачем, но и
 * нечем — ручка отвечает 410.
 */

// Проект собирается как ES-модуль, и `__dirname` в нём не существует.
const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATE = path.join(HERE, ".auth", "user.json");

const EMAIL = process.env.STUDIO_EMAIL ?? "";
const PASSWORD = process.env.STUDIO_PASSWORD ?? "";

setup("вход в студию", async ({ baseURL }) => {
  fs.mkdirSync(path.dirname(STATE), { recursive: true });

  const api = await pwRequest.newContext({ baseURL });
  const status = await (await api.get("/api/auth/status")).json();

  if (!status.auth_required) {
    // Режим владельца: замка нет. Пустое состояние — валидное состояние.
    fs.writeFileSync(STATE, JSON.stringify({ cookies: [], origins: [] }));
    await api.dispose();
    return;
  }

  if (!EMAIL || !PASSWORD) {
    throw new Error(
      "студия требует входа, но STUDIO_EMAIL / STUDIO_PASSWORD не заданы — " +
        "прогон без них проверял бы только форму входа",
    );
  }

  const res = await api.post("/api/auth/login", { data: { email: EMAIL, password: PASSWORD } });
  expect(res.ok(), `вход не удался: ${res.status()} ${await res.text()}`).toBeTruthy();
  const { token } = await res.json();

  const state = await api.storageState();
  const origin = new URL(baseURL ?? "http://127.0.0.1:8765").origin;
  state.origins = [{ origin, localStorage: [{ name: "vp.token", value: token }] }];
  fs.writeFileSync(STATE, JSON.stringify(state));
  await api.dispose();
});
