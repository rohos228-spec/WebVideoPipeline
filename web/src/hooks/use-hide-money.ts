"use client";

/**
 * Прятать ли суммы в $ в чате оркестратора.
 *
 * Участнику студии себестоимость вызовов (оценки шагов в $, траты провайдеров)
 * не показывается — так же, как бэкенд прячет её в отчётах и сводках
 * (`app/services/action_layer/plan.py::member_view`). Режим владельца и админ
 * видят всё. Пока роль неизвестна (ответ /api/me ещё не пришёл) — прячем:
 * лишний раз скрытая цена безопаснее мелькнувшей.
 */

import { useDevRole } from "@/hooks/use-dev-role";
import { useMe, useOwnerMode } from "@/hooks/use-identity";

export const MEMBER_PRICE_TEXT = "цена видна админу студии";

export function useHideMoney(): boolean {
  const owner = useOwnerMode();
  const { data: me } = useMe();
  // Режим владельца с dev-переключателем «вид пользователя» — показываем, как увидит участник.
  const { isMemberPreview } = useDevRole();
  if (owner) return isMemberPreview;
  return me?.role !== "admin";
}
