"use client";

import { BalancePanel } from "./balance-panel";
import { InDevelopment } from "./in-development";
import { LedgerHistory } from "./ledger-history";
import { PlanCheckout } from "./plan-checkout";
import { TopupDialog } from "./topup-dialog";

export function BillingPage() {
  return (
    <main className="mx-auto max-w-6xl space-y-6 p-6 text-zinc-100">
      <h1 className="text-2xl font-semibold">Баланс и тарифы</h1>
      <div className="grid gap-4 md:grid-cols-2">
        <BalancePanel />
        <TopupDialog />
      </div>
      <PlanCheckout />
      <LedgerHistory />
      <InDevelopment />
      <p className="text-xs text-zinc-600">Продавец: ИП Крохмаль М. В. Оплата в тестовом режиме.</p>
    </main>
  );
}
