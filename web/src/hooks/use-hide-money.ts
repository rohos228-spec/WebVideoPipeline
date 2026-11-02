"use client";

/**
 * Прятать ли суммы в $ в чате оркестратора.
 *
 * Участнику студии себестоимость вызовов (оценки шагов в $, траты провайдеров)
 * не показывается — так же, как бэкенд прячет её в отчётах и сводках
 * (`app/services/action_layer/plan.py::member_view`). Админ видит всё. Пока
 * роль неизвестна (ответ /api/me ещё не пришёл) — прячем: лишний раз скрытая
 * цена безопаснее мелькнувшей.
 */

import { useQuery } from "@tanstack/react-query";

import { fetchMe } from "@/lib/identity-api";

export const MEMBER_PRICE_TEXT = "цена видна админу студии";

export function useHideMoney(): boolean {
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: fetchMe, staleTime: 60_000 });
  return me?.role !== "admin";
}
