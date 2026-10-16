"use client";

import { useQuery } from "@tanstack/react-query";
import { billingClient } from "@/lib/billing-client";
import type { LedgerType } from "@/lib/types.billing";

const TYPE_LABEL: Record<LedgerType, string> = {
  topup: "Пополнение",
  charge: "Списание",
  refund: "Возврат",
  adjust: "Корректировка",
};

export function LedgerHistory() {
  const q = useQuery({ queryKey: ["billing", "ledger"], queryFn: () => billingClient.getLedger(100) });
  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-950/70 p-5" data-testid="ledger">
      <h3 className="text-base font-semibold text-zinc-100">История операций</h3>
      {q.isError && <p className="mt-2 text-xs text-red-400">{(q.error as Error).message}</p>}
      {q.data && q.data.length === 0 && <p className="mt-2 text-sm text-zinc-500">Операций пока нет</p>}
      {q.data && q.data.length > 0 && (
        <table className="mt-3 w-full text-sm">
          <thead className="text-left text-xs text-zinc-500">
            <tr>
              <th className="py-1">Дата</th>
              <th>Операция</th>
              <th className="text-right">Кредиты</th>
            </tr>
          </thead>
          <tbody>
            {q.data.map((e) => (
              <tr key={e.id} className="border-t border-zinc-900">
                <td className="py-1.5 text-zinc-400">{new Date(e.created_at).toLocaleString("ru-RU")}</td>
                <td className="text-zinc-200">{TYPE_LABEL[e.type] ?? e.type}</td>
                <td className={`text-right font-medium ${e.amount >= 0 ? "text-teal-300" : "text-zinc-300"}`}>
                  {e.amount > 0 ? "+" : ""}
                  {e.amount.toLocaleString("ru-RU")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
