// Создаёт подтверждённого QA-пользователя перед e2e (бэкенд с VIA_TEST_HOOKS=1).
// Использование: node e2e/scripts/seed-qa-user.mjs
const BASE = process.env.BASE_URL ?? 'http://127.0.0.1:8765';
const EMAIL = process.env.QA_EMAIL ?? 'qa@test.local';
const PASSWORD = process.env.QA_PASSWORD ?? 'Str0ng!pass';

const res = await fetch(`${BASE}/api/_test/seed-user`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email: EMAIL, password: PASSWORD, verified: true, credits: 100 }),
});
if (!res.ok) {
  console.error('seed failed', res.status, await res.text());
  process.exit(1);
}
console.log('QA user seeded:', EMAIL);
