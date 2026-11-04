/**
 * Прятать ли суммы в $ по ответу /api/me.
 *
 * Нет ответа — прячем (лишний раз скрытая цена безопаснее мелькнувшей).
 * Режим владельца (`accounts_enabled: false`) — показываем: учётных записей нет,
 * бэкенд ничего не прячет. В режиме учётных записей — только админу.
 */
export function shouldHideMoney(me: { role?: string; accounts_enabled?: boolean } | null | undefined): boolean {
  if (me == null) return true;
  if (me.accounts_enabled === false) return false;
  return me.role !== "admin";
}
