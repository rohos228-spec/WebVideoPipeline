"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { fetchModelHealth, HealthUnauthorized } from "@/lib/health-client";
import type { ModelHealth } from "@/lib/types.health";

const KIND_LABEL: Record<string, string> = { video: "Видео", image: "Картинки", llm: "Текст", audio: "Звук" };

function rateColor(r?: number) {
  if (r == null) return "text-zinc-500";
  if (r >= 0.95) return "text-emerald-300";
  if (r >= 0.8) return "text-teal-300";
  if (r >= 0.5) return "text-amber-300";
  return "text-rose-300";
}
function barColor(r?: number) {
  if (r == null) return "bg-zinc-700";
  if (r >= 0.95) return "bg-emerald-400/80";
  if (r >= 0.8) return "bg-teal-400/80";
  if (r >= 0.5) return "bg-amber-400/80";
  return "bg-rose-400/80";
}
const fmtTime = (s?: string) => {
  if (!s) return "—";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
};

function Spark({ m }: { m: ModelHealth }) {
  const pts = m.series ?? [];
  if (!pts.length) return <div className="h-6 text-[11px] text-zinc-600">нет истории</div>;
  const max = Math.max(1, ...pts.map((p) => p.success + p.errors));
  return (
    <div className="flex h-6 items-end gap-[2px]">
      {pts.slice(-24).map((p, i) => {
        const tot = p.success + p.errors;
        const h = Math.max(2, Math.round((tot / max) * 24));
        const errH = tot ? Math.round((p.errors / tot) * h) : 0;
        return (
          <div key={i} title={`${fmtTime(p.ts)}: ${p.success} ок / ${p.errors} ошибок`} className="flex w-[5px] flex-col justify-end overflow-hidden rounded-[1px] bg-zinc-800" style={{ height: 24 }}>
            <div className="bg-rose-400/80" style={{ height: errH }} />
            <div className="bg-emerald-400/70" style={{ height: h - errH }} />
          </div>
        );
      })}
    </div>
  );
}

export function ModelHealthDashboard() {
  const [win, setWin] = useState<"24h" | "7d">("24h");
  const q = useQuery({
    queryKey: ["model-health", win],
    queryFn: () => fetchModelHealth(win),
    refetchInterval: 15000,
    retry: (n, e) => !(e instanceof HealthUnauthorized) && n < 2,
  });
  const models = [...(q.data?.models ?? [])].sort((a, b) => b.success + b.errors - (a.success + a.errors));
  const tot = models.reduce((a, m) => ({ s: a.s + m.success, e: a.e + m.errors }), { s: 0, e: 0 });
  const overall = tot.s + tot.e ? tot.s / (tot.s + tot.e) : undefined;

  return (
    <section className="rounded-2xl border border-teal-500/15 bg-[#0b1413]/80 p-4" data-testid="model-health">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <div className="text-[15px] font-medium text-white">Здоровье моделей</div>
          <div className="text-[12px] text-zinc-500">
            Реальные успехи и ошибки генераций · обновлено {fmtTime(q.data?.updated_at ?? new Date(q.dataUpdatedAt || Date.now()).toISOString())}
            <span className="ml-1.5 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400 align-middle" />
          </div>
        </div>
        <div className="relative flex rounded-full border border-white/10 bg-black/40 p-0.5 text-[12px]">
          {(["24h", "7d"] as const).map((w) => (
            <button key={w} type="button" onClick={() => setWin(w)} className={"relative z-10 rounded-full px-3 py-1 transition-colors duration-200 " + (win === w ? "bg-teal-500/20 text-teal-100" : "text-zinc-400 hover:text-zinc-200")}>
              {w === "24h" ? "24 часа" : "7 дней"}
            </button>
          ))}
        </div>
      </div>

      {q.error instanceof HealthUnauthorized ? (
        <div className="rounded-lg border border-white/5 bg-black/30 px-3 py-6 text-center text-[13px] text-zinc-400">Войдите, чтобы видеть статистику</div>
      ) : q.isError ? (
        <div className="rounded-lg border border-white/5 bg-black/30 px-3 py-6 text-center text-[13px] text-zinc-400">Статистика временно недоступна</div>
      ) : q.isLoading ? (
        <div className="space-y-2">{[0, 1, 2].map((i) => <div key={i} className="h-12 animate-pulse rounded-lg bg-white/[0.03]" />)}</div>
      ) : !models.length ? (
        <div className="rounded-lg border border-white/5 bg-black/30 px-3 py-6 text-center text-[13px] text-zinc-400">За выбранный период генераций не было</div>
      ) : (
        <>
          <div className="mb-3 grid grid-cols-3 gap-2 text-center">
            <div className="rounded-lg bg-black/30 py-2"><div className={"text-[18px] font-semibold " + rateColor(overall)}>{overall == null ? "—" : `${Math.round(overall * 100)}%`}</div><div className="text-[11px] text-zinc-500">успешность</div></div>
            <div className="rounded-lg bg-black/30 py-2"><div className="text-[18px] font-semibold text-emerald-300">{tot.s}</div><div className="text-[11px] text-zinc-500">успехов</div></div>
            <div className="rounded-lg bg-black/30 py-2"><div className="text-[18px] font-semibold text-rose-300">{tot.e}</div><div className="text-[11px] text-zinc-500">ошибок</div></div>
          </div>
          <div className="space-y-1.5">
            {models.map((m) => (
              <div key={m.model} className="grid grid-cols-[1fr_auto_auto] items-center gap-3 rounded-lg border border-white/[0.04] bg-black/25 px-3 py-2 transition-colors hover:border-teal-500/20" title={m.last_error ? `Последняя ошибка: ${m.last_error}` : undefined}>
                <div className="min-w-0">
                  <div className="truncate text-[13px] text-zinc-100">{m.title || m.model}</div>
                  <div className="mt-1 h-1 overflow-hidden rounded-full bg-zinc-800">
                    <div className={"h-full transition-all duration-700 " + barColor(m.success_rate)} style={{ width: `${Math.round((m.success_rate ?? 0) * 100)}%` }} />
                  </div>
                  <div className="mt-1 text-[11px] text-zinc-500">
                    {m.kind ? (KIND_LABEL[m.kind] ?? m.kind) + " · " : ""}{m.success} ок · {m.errors} ошиб.{m.avg_latency_s != null ? ` · ~${Math.round(m.avg_latency_s)} с` : ""} · {fmtTime(m.updated_at)}
                  </div>
                </div>
                <Spark m={m} />
                <div className={"w-12 text-right text-[14px] font-semibold " + rateColor(m.success_rate)}>{m.success_rate == null ? "—" : `${Math.round(m.success_rate * 100)}%`}</div>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
