"use client";

/**
 * Цена шага на карточке канваса.
 *
 * Цену видно в чате, а нажимают шаги здесь. Правило §7.3 — «человек видит
 * цену ДО нажатия» — до сих пор выполнялось только в одном из двух мест, где
 * шаг запускают, а это то же самое, что не выполнялось.
 *
 * **Дорогое выглядит дорого.** Перерисовать кадр стоит копейки, переделать
 * раскадровку — в тысячу раз дороже. Одинаковая серая цифра на обеих
 * карточках учит не читать цену вообще, поэтому дороже порога подтверждения
 * бейдж меняет вид, а не только число.
 *
 * В режиме владельца бейдж не рисуется: кредитов не существует, владелец
 * платит провайдерам напрямую.
 */

import { useQuery } from "@tanstack/react-query";
import { Coins } from "lucide-react";

import { useAuthStatus } from "@/hooks/use-identity";
import { authHeaders } from "@/lib/identity-api";
import { cn } from "@/lib/utils";

interface StepPrice {
  step_code: string;
  price_micro: number;
  price_credits: string;
  basis: string;
  exact: boolean;
  note: string;
}

interface Quotes {
  prices: Record<string, StepPrice>;
  by_node_type: Record<string, string>;
}

/** Порог обязательного подтверждения (§7.3), микрокредиты. */
const CONFIRM_THRESHOLD_MICRO = 1_000_000;

/**
 * Цены всех шагов одним запросом на проект.
 *
 * По запросу на узел это два десятка обходов истории вызовов на каждое
 * открытие проекта — цена, которую платит сервер за то, чтобы нарисовать
 * цифру.
 */
export function useStepPrices(projectId: number | null) {
  const { data: status } = useAuthStatus();
  return useQuery<Quotes>({
    queryKey: ["step-prices", projectId],
    queryFn: async () => {
      const res = await fetch(`/api/projects/${projectId}/steps/quotes`, {
        headers: authHeaders(),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as Quotes;
    },
    enabled: Boolean(projectId) && Boolean(status?.sso),
    // Смета меняется от числа кадров и истории прогонов — не ежесекундно.
    staleTime: 60_000,
    retry: false,
  });
}

export function NodePriceBadge({
  projectId,
  nodeType,
  className,
}: {
  projectId: number | null;
  nodeType: string;
  className?: string;
}) {
  const { data } = useStepPrices(projectId);
  if (!data) return null;

  const code = data.by_node_type[nodeType];
  const price = code ? data.prices[code] : undefined;
  if (!price) return null;

  const heavy = price.price_micro > CONFIRM_THRESHOLD_MICRO;
  return (
    <span
      className={cn(
        "flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] tabular-nums",
        heavy
          ? "border-warning/40 bg-warning/10 text-warning"
          : "border-white/10 bg-white/[0.03] text-muted-foreground",
        className,
      )}
      title={
        price.exact
          ? `Цена точна: ${price.note || price.basis}`
          : `Оценка по истории прогонов: ${price.note || price.basis}`
      }
    >
      <Coins className="h-2.5 w-2.5" />
      {price.price_credits}
      {!price.exact && "~"}
    </span>
  );
}
