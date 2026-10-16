"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { billingClient, formatCredits, formatRub, redirectToCheckout } from "@/lib/billing-client";
import type { BillingPeriod, BillingPlan } from "@/lib/types.billing";

const STREAMS: [keyof BillingPlan["streams"], string][] = [
  ["llm", "Потоки LLM"],
  ["agents", "Агентов"],
  ["images", "Потоки картинок"],
  ["video", "Потоки видео"],
];

function PlanCard({ plan, period }: { plan: BillingPlan; period: BillingPeriod }) {
  const m = useMutation({
    mutationFn: () => billingClient.checkout({ plan: plan.id, period }),
    onSuccess: redirectToCheckout,
  });
  const price = period === "year" ? plan.price_year : plan.price_month;
  const credits = period === "year" ? plan.credits_year : plan.credits_month;
  const factory = plan.group === "factory";
  return (
    <article
      data-testid={`plan-${plan.id}`}
      className={`flex flex-col rounded-xl border p-4 ${factory ? "border-teal-800/70 bg-gradient-to-b from-teal-950/40 to-zinc-950" : "border-zinc-800 bg-zinc-950/70"}`}
    >
      <h4 className="text-lg font-semibold text-zinc-50">{plan.name}</h4>
      <div className="mt-1 text-2xl font-bold text-teal-200">
        {formatRub(price)}
        <span className="text-sm font-normal text-zinc-500"> / {period === "year" ? "год" : "мес"}</span>
      </div>
      <div className="text-xs text-zinc-400">{formatCredits(credits)} · {plan.storage_gb >= 1024 ? `${plan.storage_gb / 1024} ТБ` : `${plan.storage_gb} ГБ`}</div>
      <dl className="mt-3 grid grid-cols-2 gap-1.5 text-xs">
        {STREAMS.map(([k, label]) => (
          <div key={k} className="rounded bg-zinc-900/80 px-2 py-1">
            <dt className="text-zinc-500">{label}</dt>
            <dd className="font-semibold text-zinc-100">{plan.streams[k]}</dd>
          </div>
        ))}
      </dl>
      <ul className="mt-3 space-y-1 text-xs text-zinc-300">
        {plan.features.map((f) => (
          <li key={f}>• {f}</li>
        ))}
        {plan.own_mechanics && <li>• Интеграция собственных механик</li>}
        {plan.priority_support && <li>• Приоритетная поддержка</li>}
        {plan.own_api && <li>• Интеграция собственных API</li>}
      </ul>
      <button
        type="button"
        disabled={m.isPending}
        onClick={() => m.mutate()}
        className="mt-4 rounded-md bg-teal-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
      >
        {m.isPending ? "Переход к оплате…" : "Оформить"}
      </button>
      {m.isError && <p className="mt-1 text-xs text-red-400">{(m.error as Error).message}</p>}
    </article>
  );
}

export function PlanCheckout() {
  const [period, setPeriod] = useState<BillingPeriod>("month");
  const q = useQuery({ queryKey: ["billing", "plans"], queryFn: billingClient.getPlans });
  const groups: [BillingPlan["group"], string][] = [
    ["trial", "Попробовать"],
    ["factory", "Заводы"],
  ];
  return (
    <section data-testid="plans">
      <div className="flex items-center gap-3">
        <div className="inline-flex rounded-lg border border-zinc-700 p-0.5" role="group" aria-label="Период оплаты">
          {(["month", "year"] as const).map((p) => (
            <button
              key={p}
              type="button"
              aria-pressed={period === p}
              onClick={() => setPeriod(p)}
              className={`rounded-md px-3 py-1 text-sm ${period === p ? "bg-teal-700 text-white" : "text-zinc-300"}`}
            >
              {p === "month" ? "Месяц" : "Год"}
            </button>
          ))}
        </div>
        <span className="rounded-full bg-teal-900/60 px-2 py-0.5 text-xs text-teal-200">−25% при оплате за год</span>
      </div>
      {q.isError && <p className="mt-2 text-xs text-red-400">{(q.error as Error).message}</p>}
      {groups.map(([g, title]) => (
        <div key={g} className="mt-5">
          <h3 className="mb-2 text-base font-semibold text-zinc-100">{title}</h3>
          <div className={`grid gap-3 ${g === "trial" ? "sm:grid-cols-3" : "sm:grid-cols-2 xl:grid-cols-4"}`}>
            {q.data?.plans
              .filter((p) => p.group === g)
              .map((p) => <PlanCard key={p.id} plan={p} period={period} />)}
          </div>
        </div>
      ))}
    </section>
  );
}
