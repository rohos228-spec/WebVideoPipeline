"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { billingClient, redirectToCheckout } from "@/lib/billing-client";

const PRESETS = [890, 1990, 4990];
const MIN = 100;
const MAX = 500_000;

export function TopupDialog() {
  const [amount, setAmount] = useState(1990);
  const m = useMutation({ mutationFn: () => billingClient.checkout({ amount }), onSuccess: redirectToCheckout });
  const valid = Number.isInteger(amount) && amount >= MIN && amount <= MAX;
  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-950/70 p-5" data-testid="topup">
      <h3 className="text-base font-semibold text-zinc-100">Пополнить кредиты</h3>
      <p className="text-xs text-zinc-500">Пакет кредитов не сгорает. {MIN}–{MAX.toLocaleString("ru-RU")} ₽</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {PRESETS.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => setAmount(p)}
            className={`rounded-md border px-3 py-1 text-sm ${amount === p ? "border-teal-400 text-teal-200" : "border-zinc-700 text-zinc-300"}`}
          >
            {p.toLocaleString("ru-RU")} ₽
          </button>
        ))}
      </div>
      <div className="mt-3 flex gap-2">
        <input
          type="number"
          min={MIN}
          max={MAX}
          value={amount}
          aria-label="Сумма, ₽"
          onChange={(e) => setAmount(Math.floor(Number(e.target.value)))}
          className="w-40 rounded-md border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-zinc-100"
        />
        <button
          type="button"
          disabled={!valid || m.isPending}
          onClick={() => m.mutate()}
          className="rounded-md bg-teal-600 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {m.isPending ? "Переход к оплате…" : "Пополнить"}
        </button>
      </div>
      {m.isError && <p className="mt-2 text-xs text-red-400">{(m.error as Error).message}</p>}
    </section>
  );
}
