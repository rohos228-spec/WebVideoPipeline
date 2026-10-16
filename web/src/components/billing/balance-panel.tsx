"use client";

import { useQuery } from "@tanstack/react-query";
import { billingClient, formatCredits } from "@/lib/billing-client";

const STREAM_LABELS: [keyof import("@/lib/types.billing").StreamLimits, string][] = [
  ["llm", "Потоки LLM"],
  ["agents", "Агентов"],
  ["images", "Потоки картинок"],
  ["video", "Потоки видео"],
];

export function BalancePanel() {
  const bal = useQuery({ queryKey: ["billing", "balance"], queryFn: billingClient.getBalance });
  const sub = useQuery({ queryKey: ["billing", "subscription"], queryFn: billingClient.getSubscription });
  return (
    <section className="rounded-xl border border-teal-900/60 bg-zinc-950/70 p-5" data-testid="balance-panel">
      <h2 className="text-sm font-medium uppercase tracking-wide text-teal-300">Баланс VIA</h2>
      <div className="mt-2 text-3xl font-semibold text-zinc-50">
        {bal.isLoading ? "…" : bal.isError ? "—" : formatCredits(bal.data?.balance ?? 0)}
      </div>
      <p className="mt-1 text-xs text-zinc-400">1 кредит = 1 ₽ · кредиты не сгорают</p>
      {bal.isError && <p className="mt-2 text-xs text-red-400">{(bal.error as Error).message}</p>}
      <div className="mt-4 text-sm text-zinc-300">
        Тариф: <b>{sub.data?.active ? sub.data.plan_name : "без тарифа"}</b>
        {sub.data?.expires_at && (
          <span className="text-zinc-500"> · до {new Date(sub.data.expires_at).toLocaleDateString("ru-RU")}</span>
        )}
      </div>
      {sub.data && (
        <dl className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {STREAM_LABELS.map(([k, label]) => (
            <div key={k} className="rounded-lg bg-zinc-900 px-3 py-2">
              <dt className="text-[11px] text-zinc-500">{label}</dt>
              <dd className="text-lg font-semibold text-zinc-100">{sub.data.streams[k]}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}
