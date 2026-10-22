"use client";
import { useEffect, useMemo, useState } from "react";
import { PLANS, PLAN_GROUPS, fmtRub, limitRows } from "@/lib/pricing";
import { FactoryMini, FactoryStyles } from "@/components/home/factory-cards";
import { PanelShell, Section, Btn, Chips, StatusPill, Empty, card, ACCENT } from "./ui";

/** Ответ /api/billing/balance — остаток и последние проводки. */
type Balance = {
  unlimited: boolean;
  balance_credits: string;
  balance_micro: number;
  held_micro: number;
  free_tier: {
    active: boolean;
    granted_usd: number;
    cap_usd: number;
    projects: number;
    max_projects: number;
    reason: string;
  };
  entries: {
    kind: string;
    delta_micro: number;
    delta_credits: string;
    cost_usd: number | null;
    memo: string | null;
    created_at: string | null;
  }[];
};

type KindItem = { calls: number; credits: string; credits_micro: number; cost_usd: number | null };

/** Ответ /api/billing/usage-history — расходы по моделям и последние вызовы. */
type Usage = {
  summary: {
    total_spent_credits: string;
    total_spent_micro: number;
    total_cost_usd: number | null;
    by_kind: Record<string, KindItem>;
  };
  models: {
    model: string;
    display_name: string;
    kind: string;
    provider: string;
    calls: number;
    units: number;
    unit_label: string;
    credits_spent: string;
    cost_usd: number | null;
  }[];
  recent: {
    id: string;
    created_at: string;
    kind: string;
    model: string;
    provider: string;
    description: string;
    units_label: string;
    credits_delta: string;
    credits_delta_micro: number;
    cost_usd: number | null;
    status: string;
  }[];
  is_admin: boolean;
};

type ProjectCost = {
  project_id: number;
  title: string;
  calls: number;
  cost_usd: number;
};

const KIND_LABEL: Record<string, string> = {
  image: "Картинки",
  video: "Видео",
  audio: "Озвучка",
  llm: "Текст",
  topup: "Пополнение",
  settle: "Списание",
  promo: "Бонус",
  coupon: "Купон",
};

const CURRENT_PLAN = "start";

const usd = (v: number | null | undefined) =>
  v == null ? "—" : `$${v.toFixed(v < 0.1 ? 4 : 2)}`;

const dayOf = (iso: string | null) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}`;
};

const pill = (status: string): "успешно" | "ожидает" | "ошибка" => {
  const s = (status || "").toLowerCase();
  if (s.includes("err") || s.includes("fail") || s.includes("ошиб") || s.includes("отмен")) return "ошибка";
  if (s.includes("pend") || s.includes("wait") || s.includes("ожид") || s.includes("queue")) return "ожидает";
  return "успешно";
};

/** Данные о деньгах: то, что реально посчитал сервер, без выдуманных чисел. */
function useMoneyData() {
  const [balance, setBalance] = useState<Balance | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [projects, setProjects] = useState<ProjectCost[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    const get = async <T,>(url: string): Promise<T | null> => {
      try {
        const r = await fetch(url, { credentials: "same-origin" });
        if (!r.ok) return null;
        return (await r.json()) as T;
      } catch {
        return null;
      }
    };
    (async () => {
      const [b, u, p] = await Promise.all([
        get<Balance>("/api/billing/balance?limit=20"),
        get<Usage>("/api/billing/usage-history?limit=100"),
        get<{ projects: ProjectCost[] }>("/api/llm-costs/projects"),
      ]);
      if (!alive) return;
      setBalance(b);
      setUsage(u);
      setProjects(p?.projects ?? []);
      if (!b && !u) setError("Биллинг не ответил — показывать нечего");
      setLoading(false);
    })();
    return () => {
      alive = false;
    };
  }, []);

  return { balance, usage, projects, error, loading };
}

export function PaymentsPanel({ onClose }: { onClose: () => void }) {
  const { balance, usage, projects, error, loading } = useMoneyData();
  const [f, setF] = useState<"Все" | "Пополнения" | "Списания">("Все");

  const rows = useMemo(() => {
    const list = (usage?.recent ?? []).map((r) => ({
      key: r.id,
      d: dayOf(r.created_at),
      t: r.description || r.model || KIND_LABEL[r.kind] || r.kind,
      title: `${KIND_LABEL[r.kind] ?? r.kind}${r.model ? ` · ${r.model}` : ""}`,
      a: Number(r.credits_delta.replace(/\s/g, "").replace(",", ".")) || 0,
      units: r.units_label,
      s: pill(r.status),
    }));
    return list.filter((r) => (f === "Все" ? true : f === "Пополнения" ? r.a > 0 : r.a < 0));
  }, [usage, f]);

  const models = useMemo(
    () =>
      [...(usage?.models ?? [])]
        .sort((a, b) => (b.cost_usd ?? 0) - (a.cost_usd ?? 0) || b.calls - a.calls)
        .slice(0, 8),
    [usage],
  );

  const projectRows = useMemo(
    () => [...projects].sort((a, b) => (b.cost_usd ?? 0) - (a.cost_usd ?? 0)).slice(0, 8),
    [projects],
  );

  const spent = usage?.summary.total_spent_credits ?? "0";
  const costUsd = usage?.summary.total_cost_usd ?? null;
  const free = balance?.free_tier;

  return (
    <PanelShell title="Оплата" onClose={onClose}>
      <div className={card + " p-4"}>
        <div className="text-sm text-zinc-500">Баланс</div>
        {loading ? (
          <div className="mt-1 text-[15px] text-zinc-500">считаю…</div>
        ) : error ? (
          <div className="mt-1 text-[15px] text-zinc-500">{error}</div>
        ) : balance?.unlimited ? (
          <>
            <div className="mt-1 text-[28px] font-medium text-white">∞</div>
            <div className="mt-1.5 text-sm text-zinc-500">Касса не ведётся: у тебя права владельца</div>
          </>
        ) : (
          <>
            <div className="mt-1 text-[28px] font-medium text-white">
              {balance?.balance_credits ?? "0"} <span className="text-[15px] text-zinc-500">кредитов</span>
            </div>
            {balance && balance.held_micro > 0 ? (
              <div className="mt-1.5 text-sm text-zinc-500">Заморожено под задачи: {(balance.held_micro / 1_000_000).toFixed(2)} кр.</div>
            ) : null}
          </>
        )}
        {balance && !balance.unlimited ? (
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.min(
                  100,
                  Math.max(
                    2,
                    balance.balance_micro + (usage?.summary.total_spent_micro ?? 0) > 0
                      ? (balance.balance_micro / (balance.balance_micro + (usage?.summary.total_spent_micro ?? 0))) * 100
                      : 0,
                  ),
                )}%`,
                background: ACCENT,
              }}
            />
          </div>
        ) : null}
        <div className="mt-1.5 text-sm text-zinc-500">
          Израсходовано всего: {spent} кр.
          {costUsd != null ? ` · себестоимость ${usd(costUsd)}` : ""}
        </div>
        {free?.active ? (
          <div className="mt-2 rounded-[10px] border border-teal-400/20 bg-teal-400/[0.06] px-3 py-2 text-sm text-teal-200/80">
            Подарочный режим: потрачено {usd(free.granted_usd)} из {usd(free.cap_usd)}, проектов {free.projects} из {free.max_projects}
            {free.reason ? ` · ${free.reason}` : ""}
          </div>
        ) : null}
        <div className="mt-3 flex gap-2" title="Оплата в студии не подключена">
          <Btn primary disabled title="оплата не подключена">Пополнить</Btn>
          {["500", "2000", "10000"].map((v) => (
            <Btn key={v} disabled title="оплата не подключена">+{v}</Btn>
          ))}
        </div>
        <div className="mt-1.5 text-[12px] text-zinc-600">Пополнение отключено: студия пока не принимает платежи.</div>
      </div>

      {usage && Object.keys(usage.summary.by_kind).length > 0 ? (
        <Section title="Расход по типам">
          <div className="space-y-1">
            {Object.entries(usage.summary.by_kind).map(([kind, it]) => (
              <div key={kind} className="flex items-center gap-2 rounded-[10px] px-2 py-1.5 text-sm hover:bg-white/[0.04]">
                <span className="flex-1 text-zinc-300">{KIND_LABEL[kind] ?? kind}</span>
                <span className="text-zinc-500">{it.calls} выз.</span>
                <span className="w-20 text-right text-zinc-200">{it.credits} кр.</span>
                <span className="w-20 text-right text-zinc-500">{usd(it.cost_usd)}</span>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      <Section title="Тариф">
        <FactoryStyles />
        {(["trial", "factory"] as const).map((g) => (
          <div key={g} className="mb-3">
            <div className="mb-1.5 text-sm text-zinc-400">{PLAN_GROUPS[g].title} <span className="text-zinc-600">· {g === "trial" ? "разово" : "в месяц"}</span></div>
            <div className="grid grid-cols-2 gap-2">
              {PLANS.filter((p) => p.group === g).map((p) => {
                if (g === "factory") return <FactoryMini key={p.id} p={p} current={p.id === CURRENT_PLAN} />;
                const cur = p.id === CURRENT_PLAN;
                return (
                  <a key={p.id} href={`/register?plan=${p.id}`} className={card + " block p-2.5 transition-colors duration-200 hover:bg-white/[0.05]"} style={cur ? { borderColor: "rgba(45,212,191,0.5)" } : undefined}>
                    <div className="flex items-baseline justify-between"><span className="text-[14px] text-white">{p.name}</span><span className="text-sm" style={{ color: cur ? ACCENT : "#a1a1aa" }}>{fmtRub(p.price)}</span></div>
                    <ul className="mt-1.5 space-y-0.5 text-[12px] text-zinc-500">{limitRows(p.limits).map(([k, v]) => <li key={k} className="flex justify-between gap-2"><span>{k}</span><span className="text-zinc-300">{v}</span></li>)}</ul>
                    {p.perks ? <div className="mt-1.5 text-[12px] text-zinc-500">{p.base ? `Всё из ${p.base} + ` : ""}{p.perks.map((x) => x[0].toLowerCase() + x.slice(1)).join(", ")}</div> : null}
                    {cur ? <div className="mt-1.5 text-sm" style={{ color: ACCENT }}>Текущий</div> : null}
                  </a>
                );
              })}
            </div>
          </div>
        ))}
      </Section>

      <Section title="История" right={<Chips value={f} options={["Все", "Пополнения", "Списания"]} onChange={setF} />}>
        {loading ? <Empty text="Считаю…" /> : rows.length === 0 ? <Empty text="Списаний пока нет" /> : (
          <div className="space-y-1">
            {rows.map((r) => (
              <div key={r.key} className="flex items-center gap-2 rounded-[10px] px-2 py-2 transition-colors hover:bg-white/[0.04]">
                <span className="w-10 text-sm text-zinc-500">{r.d}</span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-zinc-300">{r.t}</span>
                  <span className="block truncate text-[11px] text-zinc-600">{r.title}{r.units ? ` · ${r.units}` : ""}</span>
                </span>
                <span className="text-sm" style={{ color: r.a > 0 ? ACCENT : "#e4e4e7" }}>{r.a > 0 ? "+" : r.a < 0 ? "−" : ""}{Math.abs(r.a)}</span>
                <StatusPill s={r.s} />
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title="Расход по моделям">
        {models.length === 0 ? <Empty text="Вызовов пока не было" /> : (
          <div className="space-y-1">
            {models.map((m) => (
              <div key={`${m.provider}:${m.model}`} className="flex items-center gap-2 rounded-[10px] px-2 py-1.5 text-sm hover:bg-white/[0.04]">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-zinc-300">{m.display_name || m.model}</span>
                  <span className="block truncate text-[11px] text-zinc-600">{KIND_LABEL[m.kind] ?? m.kind} · {m.provider} · {m.calls} выз.{m.units ? ` · ${m.units.toFixed(m.units < 10 ? 2 : 0)} ${m.unit_label}` : ""}</span>
                </span>
                <span className="w-20 text-right text-zinc-200">{m.credits_spent} кр.</span>
                <span className="w-16 text-right text-zinc-500">{usd(m.cost_usd)}</span>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title="Расход по проектам">
        {projectRows.length === 0 ? <Empty text="Текстовых вызовов по проектам нет" /> : (
          <div className="space-y-1">
            {projectRows.map((p) => (
              <div key={p.project_id} className="flex items-center gap-2 px-2 py-1.5 text-sm">
                <span className="min-w-0 flex-1 truncate text-zinc-300">{p.title || `Проект ${p.project_id}`}</span>
                <span className="text-sm text-zinc-500">{p.calls} выз.</span>
                <span className="w-16 text-right text-zinc-400">{usd(p.cost_usd)}</span>
              </div>
            ))}
            <div className="px-2 text-sm text-zinc-600">Только текстовые модели — картинки и видео считаются в «Расход по моделям».</div>
          </div>
        )}
      </Section>
    </PanelShell>
  );
}
