/**
 * Каким токеном чат оркестратора ходит к серверу.
 *
 * На этой линии движок пускает по входу в студию (IdentityMiddleware): токен входа
 * (`vp.token`) — главный. Отдельный токен чата (`vp-orch-api-token`) остаётся только
 * для режима без входа (владелец, мок e2e). Если вошли — отдельный токен не
 * используется: иначе он, сохранённый прошлым человеком в общем браузере, побеждал бы
 * вход нынешнего, и тот писал бы в чат от чужого имени.
 */

export const ORCH_TOKEN_STORAGE_KEY = "vp-orch-api-token";

export function chooseChatToken(loginToken: string | null | undefined, chatToken: string | null | undefined): string {
  const login = (loginToken ?? "").trim();
  if (login) return login;
  return (chatToken ?? "").trim();
}
