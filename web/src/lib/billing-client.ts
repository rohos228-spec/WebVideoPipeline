import type {
  BalanceResponse,
  CheckoutRequest,
  CheckoutResponse,
  LedgerEntry,
  PlansResponse,
  SubscriptionResponse,
} from "./types.billing";

const BASE = "/api/billing";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let msg = `Ошибка ${res.status}`;
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (typeof j.detail === "string") msg = j.detail;
    } catch {
      /* тело не JSON */
    }
    throw new Error(msg);
  }
  return (await res.json()) as T;
}

export const billingClient = {
  getPlans: () => call<PlansResponse>("/plans"),
  getBalance: () => call<BalanceResponse>("/balance"),
  getLedger: (limit = 100) =>
    call<{ entries: LedgerEntry[] }>(`/ledger?limit=${limit}`).then((r) => r.entries),
  getSubscription: () => call<SubscriptionResponse>("/subscription"),
  checkout: (body: CheckoutRequest) =>
    call<CheckoutResponse>("/checkout", { method: "POST", body: JSON.stringify(body) }),
};

export function redirectToCheckout(r: CheckoutResponse): void {
  window.location.href = r.confirmation_url;
}

export const formatRub = (v: number): string => `${v.toLocaleString("ru-RU")} ₽`;
export const formatCredits = (v: number): string => `${v.toLocaleString("ru-RU")} кр`;
