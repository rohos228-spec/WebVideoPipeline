export type BillingPeriod = "month" | "year";

export interface StreamLimits {
  llm: number;
  agents: number;
  images: number;
  video: number;
}

export interface BillingPlan {
  id: string;
  name: string;
  group: "trial" | "factory";
  currency: "RUB";
  price_month: number;
  price_year: number;
  credits_month: number;
  credits_year: number;
  streams: StreamLimits;
  storage_gb: number;
  max_videos: number | null;
  features: string[];
  own_api: boolean;
  own_mechanics: boolean;
  priority_support: boolean;
  yearly_discount: number;
  thread_limit: number;
  credits_expire: boolean;
}

export interface PlansResponse {
  currency: "RUB";
  rub_per_credit: number;
  yearly_discount: number;
  seller: string;
  plans: BillingPlan[];
  in_development: string[];
}

export interface BalanceResponse {
  balance: number;
  currency: "credits";
  rub_per_credit: number;
  label: string;
}

export type LedgerType = "topup" | "charge" | "refund" | "adjust";

export interface LedgerEntry {
  id: number;
  user_id: string;
  type: LedgerType;
  purpose: string | null;
  amount: number;
  ref: string | null;
  meta: Record<string, unknown>;
  expires_at: string | null;
  created_at: string;
}

export interface SubscriptionResponse {
  active: boolean;
  plan: string | null;
  plan_name: string | null;
  period: BillingPeriod | null;
  expires_at: string | null;
  streams: StreamLimits;
  thread_limit: number;
}

export interface CheckoutResponse {
  payment_id: string;
  confirmation_url: string;
  amount: string;
  currency: "RUB";
  test: boolean;
  mode: "sandbox";
}

export type CheckoutRequest = { plan: string; period: BillingPeriod } | { amount: number };
