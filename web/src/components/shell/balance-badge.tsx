"use client";

/**
 * Баланс в шапке: сколько осталось и сколько заморожено под идущим шагом.
 *
 * Резерв показан отдельно не для полноты. Деньги под работающим шагом уже
 * вычтены из остатка, и без второй цифры человек видит, что баланс упал, и не
 * видит, куда — а это первый вопрос, с которым он придёт.
 *
 * При клике открывает диалог ввода купона / пополнения баланса.
 */

import { useState } from "react";
import { Coins, Gift, Loader2, Plus } from "lucide-react";

import { useBalance } from "@/hooks/use-identity";
import { cn } from "@/lib/utils";
import { CouponDialog } from "@/components/billing/coupon-dialog";

export function BalanceBadge({ className }: { className?: string }) {
  const { data, isLoading, isError } = useBalance();
  const [couponOpen, setCouponOpen] = useState(false);

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
  const held = data.held_micro > 0;

  return (
    <>
      <button
        type="button"
        onClick={() => setCouponOpen(true)}
        className={cn(
          "group flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] px-2.5 py-1 text-xs tabular-nums transition-all hover:border-cyan-500/40 hover:bg-white/[0.08] hover:shadow-[0_0_12px_rgba(6,182,212,0.15)]",
          className,
        )}
        title="Нажмите, чтобы ввести промокод или купон для пополнения"
      >
        {free?.active ? (
          <>
            <Gift className="h-3.5 w-3.5 text-amber-400" />
            <span className="text-zinc-300">бесплатно до видео</span>
          </>
        ) : (
          <>
            <Coins className="h-3.5 w-3.5 text-amber-400 group-hover:text-amber-300 transition-colors" />
            <span className="font-semibold text-zinc-100">{data.balance_credits}</span>
            {held && (
              <span className="text-muted-foreground">
                (−{(data.held_micro / 1_000_000).toFixed(2)})
              </span>
            )}
          </>
        )}
        <span className="ml-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-cyan-500/20 text-[10px] font-bold text-cyan-300 group-hover:bg-cyan-500/30 group-hover:scale-105 transition-all">
          <Plus className="h-2.5 w-2.5" />
        </span>
      </button>

      <CouponDialog open={couponOpen} onOpenChange={setCouponOpen} />
    </>
  );
}
