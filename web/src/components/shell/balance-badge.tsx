"use client";

/**
 * Баланс в шапке: сколько осталось и сколько заморожено под идущим шагом.
 *
 * Резерв показан отдельно не для полноты. Деньги под работающим шагом уже
 * вычтены из остатка, и без второй цифры человек видит, что баланс упал, и не
 * видит, куда — а это первый вопрос, с которым он придёт.
 *
 * Пока действует бесплатный уровень, вместо цифры стоит слово: у человека
 * ещё нет кредитов, и показывать ему ноль значит пугать нулём там, где всё
 * работает. Показываем, сколько подарка осталось, — это ответ на настоящий
 * вопрос «когда попросят деньги».
 *
 * В режиме владельца компонент не рисуется вовсе: кредитов не существует,
 * владелец платит провайдерам напрямую, и любая цифра здесь была бы враньём.
 */

import { Coins, Gift, Loader2 } from "lucide-react";

import { useBalance } from "@/hooks/use-identity";
import { cn } from "@/lib/utils";

export function BalanceBadge({ className }: { className?: string }) {
  const { data, isLoading, isError } = useBalance();

  if (isError) return null;
  if (isLoading) {
    return (
      <span className={cn("flex items-center gap-1.5 text-xs text-muted-foreground", className)}>
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      </span>
    );
  }
  if (!data?.tenant_id) return null;

  const free = data.free_tier;
  if (free?.active) {
    const left = Math.max(0, (free.cap_usd || 0) - (free.granted_usd || 0));
    return (
      <span
        className={cn(
          "flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1 text-xs",
          className,
        )}
        title={
          free.cap_usd
            ? `Раскадровка и кадры бесплатно. Осталось подарка на $${left.toFixed(2)} себестоимости; оплата начнётся с рендера видео.`
            : "Раскадровка и кадры бесплатно. Оплата начнётся с рендера видео."
        }
      >
        <Gift className="h-3.5 w-3.5" />
        бесплатно до видео
      </span>
    );
  }

  const held = data.held_micro > 0;
  return (
    <span
      className={cn(
        "flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1 text-xs tabular-nums",
        className,
      )}
      title={
        held
          ? "В скобках — резерв под идущим шагом. Он уже вычтен из остатка и вернётся, если шаг не состоится."
          : "Остаток кредитов"
      }
    >
      <Coins className="h-3.5 w-3.5" />
      {data.balance_credits}
      {held && (
        <span className="text-muted-foreground">
          (−{(data.held_micro / 1_000_000).toFixed(2)})
        </span>
      )}
    </span>
  );
}
