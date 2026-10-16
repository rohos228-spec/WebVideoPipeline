import type { ModelHealth, ModelHealthResponse } from "./types.health";

// Тексты уже очищены бэкендом (app/brand.py).
const clean = (s?: string) => s;

type Raw = Record<string, unknown>;
const num = (...v: unknown[]) => {
  for (const x of v) if (typeof x === "number" && Number.isFinite(x)) return x;
  return 0;
};

function normalize(r: Raw): ModelHealth {
  const success = num(r.success, r.ok);
  const errors = num(r.errors, r.failed);
  const rate = typeof r.success_rate === "number" ? (r.success_rate > 1 ? r.success_rate / 100 : r.success_rate) : undefined;
  return {
    model: String(r.model ?? ""),
    title: clean((r.title as string) ?? (r.name as string) ?? undefined),
    kind: r.kind as string | undefined,
    success,
    errors,
    success_rate: rate ?? (success + errors > 0 ? success / (success + errors) : undefined),
    avg_latency_s: typeof r.avg_latency_s === "number" ? r.avg_latency_s : undefined,
    last_error: clean(r.last_error as string | undefined),
    updated_at: (r.updated_at ?? r.last_run ?? r.ts) as string | undefined,
    series: Array.isArray(r.series) ? (r.series as ModelHealth["series"]) : undefined,
  };
}

export class HealthUnauthorized extends Error {}

export async function fetchModelHealth(window: "24h" | "7d" = "24h"): Promise<ModelHealthResponse> {
  const res = await fetch(`/api/models/health?window=${window}`, { credentials: "include" });
  if (res.status === 401 || res.status === 403) throw new HealthUnauthorized("auth");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = (await res.json()) as unknown;
  const list = Array.isArray(data) ? data : ((data as Raw).models as unknown[]) ?? [];
  const meta = Array.isArray(data) ? {} : (data as Raw);
  return {
    models: (list as Raw[]).map(normalize).filter((m) => m.model),
    window_hours: typeof meta.window_hours === "number" ? meta.window_hours : undefined,
    updated_at: meta.updated_at as string | undefined,
  };
}
