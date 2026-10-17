"use client";
import { useSyncExternalStore } from "react";
import { fmtRub, yearlyMonthly, yearlyTotal, YEARLY_DISCOUNT } from "@/lib/pricing";

export type Period = "month" | "year";
let period: Period = "month";
const subs = new Set<() => void>();
export function setPeriod(p: Period) { period = p; subs.forEach((f) => f()); }
export function usePeriod(): Period {
  return useSyncExternalStore((f) => { subs.add(f); return () => { subs.delete(f); }; }, () => period, () => "month");
}

export function PeriodToggle({ compact = false }: { compact?: boolean }) {
  const p = usePeriod();
  const pct = Math.round(YEARLY_DISCOUNT * 100);
  return (
    <div role="radiogroup" aria-label="Период оплаты" data-testid="period-toggle"
      className={"relative inline-flex select-none items-center rounded-full border border-teal-400/15 bg-black/40 p-0.5 " + (compact ? "text-[12px]" : "text-sm")}>
      <span aria-hidden className="absolute inset-y-0.5 left-0.5 w-[calc(50%-2px)] rounded-full bg-teal-500/20 ring-1 ring-teal-300/25 transition-transform duration-300 ease-out"
        style={{ transform: p === "year" ? "translateX(100%)" : "translateX(0)" }} />
      {(["month", "year"] as const).map((v) => (
        <button key={v} type="button" role="radio" aria-checked={p === v} onClick={() => setPeriod(v)}
          className={"relative z-10 flex flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded-full transition-colors duration-200 " + (compact ? "px-3 py-1" : "px-5 py-1.5") + " " + (p === v ? "text-teal-50" : "text-zinc-400 hover:text-zinc-200")}>
          {v === "month" ? "Месяц" : "Год"}
          {v === "year" && <span className="rounded-full bg-emerald-400/15 px-1.5 text-[10px] font-semibold text-emerald-300">−{pct}%</span>}
        </button>
      ))}
    </div>
  );
}

/** Цена тарифа с учётом выбранного периода. size: "lg" для карточек главной, "sm" для мини-карточек. */
export function PriceTag({ price, size = "lg", accent }: { price: number; size?: "lg" | "sm"; accent?: string }) {
  const p = usePeriod();
  const year = p === "year";
  const shown = year ? yearlyMonthly(price) : price;
  if (size === "sm") {
    return (
      <span className="inline-flex flex-wrap items-baseline gap-x-1">
        {year && <s className="text-[11px] text-zinc-600">{fmtRub(price)}</s>}
        <span style={accent ? { color: accent } : undefined}>{fmtRub(shown)}</span>
        <span className="text-zinc-500">/мес</span>
      </span>
    );
  }
  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="text-3xl font-bold text-white transition-all duration-300">{fmtRub(shown)}</span>
        <span className="text-base font-normal text-white/40">/ месяц</span>
        {year && <span className="rounded-full bg-emerald-400/15 px-2 py-0.5 text-xs font-semibold text-emerald-300">−{Math.round(YEARLY_DISCOUNT * 100)}%</span>}
      </div>
      <div className="mt-1 h-4 text-xs text-white/40">
        {year ? <><s className="mr-1.5">{fmtRub(price)}</s>при оплате за год: {fmtRub(yearlyTotal(price))}</> : "оплата помесячно"}
      </div>
    </div>
  );
}

export function PricingNote() {
  return <div className="mt-2 text-center text-xs text-white/40">Пакеты кредитов не сгорают · 1 кредит = 1 ₽ · при оплате за год скидка {Math.round(YEARLY_DISCOUNT * 100)}%</div>;
}
