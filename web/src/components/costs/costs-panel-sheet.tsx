"use client";

// Этап 3 (E.6): единственный дашборд стоимости LLM в смете — таблица
// «нода → вызовы / токены / $ / неуспешные», сумма прогона, разбивка по
// моделям, динамика по прогонам (= проектам), поле бюджета.
// Все цифры — агрегаты по llm_calls (GET /api/projects/{id}/llm-costs,
// GET /api/llm-costs/projects); графики — CSS-бары, без chart-библиотек.

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

interface Agg {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  failed: number;
  contract_rejected: number;
  unbilled: number;
}

interface ProjectCosts {
  project_id: number;
  nodes: Array<Agg & { node_key: string }>;
  models: Array<Agg & { model: string }>;
  total: Agg;
  budget: {
    budget_usd: number;
    spent_usd: number;
    enabled: boolean;
    exhausted: boolean;
    override: boolean;
    paused_for_budget: boolean;
  };
  failed_inserts: number;
  unpersisted_spent_usd: number;
}

interface ProjectsCosts {
  projects: Array<Agg & { project_id: number; title: string }>;
  adhoc: Agg;
  failed_inserts: number;
}

const usd = (v: number) => `$${(v ?? 0).toFixed(v >= 1 ? 2 : 4)}`;
const tok = (v: number) => (v ?? 0).toLocaleString("ru-RU");

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return (await res.json()) as T;
}

function Bar({ value, max, className }: { value: number; max: number; className?: string }) {
  const pct = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
  return (
    <div className="h-2 w-full rounded-sm bg-white/[0.06]">
      <div className={cn("h-2 rounded-sm bg-primary/70", className)} style={{ width: `${pct}%` }} />
    </div>
  );
}

function AggCells({ a }: { a: Agg }) {
  const bad = a.failed > 0;
  return (
    <>
      <td className="px-2 py-1 text-right tabular-nums">{a.calls}</td>
      <td className="px-2 py-1 text-right tabular-nums text-muted-foreground">
        {tok(a.prompt_tokens)} / {tok(a.completion_tokens)}
      </td>
      <td className="px-2 py-1 text-right font-semibold tabular-nums">{usd(a.cost_usd)}</td>
      <td className={cn("px-2 py-1 text-right tabular-nums", bad && "text-amber-400")}>
        {a.failed}
        {a.contract_rejected > 0 ? (
          <span className="text-muted-foreground"> (контракт {a.contract_rejected})</span>
        ) : null}
      </td>
      <td className="px-2 py-1 text-right tabular-nums text-muted-foreground">{a.unbilled}</td>
    </>
  );
}

export function CostsPanelSheet({
  open,
  onOpenChange,
  selectedProjectId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selectedProjectId: number | null;
}) {
  const [projects, setProjects] = useState<ProjectsCosts | null>(null);
  const [projectId, setProjectId] = useState<number | null>(selectedProjectId);
  const [detail, setDetail] = useState<ProjectCosts | null>(null);
  const [budgetInput, setBudgetInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open && selectedProjectId != null) setProjectId(selectedProjectId);
  }, [open, selectedProjectId]);

  const loadProjects = useCallback(async () => {
    try {
      setProjects(await getJson<ProjectsCosts>("/api/llm-costs/projects"));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const loadDetail = useCallback(async (pid: number) => {
    try {
      const d = await getJson<ProjectCosts>(`/api/projects/${pid}/llm-costs`);
      setDetail(d);
      setBudgetInput(String(d.budget.budget_usd));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    void loadProjects();
  }, [open, loadProjects]);

  useEffect(() => {
    if (!open || projectId == null) return;
    void loadDetail(projectId);
  }, [open, projectId, loadDetail]);

  const saveBudget = async () => {
    if (projectId == null) return;
    const v = Number(budgetInput.replace(",", "."));
    if (!Number.isFinite(v) || v < 0) {
      toast.error("Бюджет — число ≥ 0 (0 = выключить для проекта)");
      return;
    }
    setBusy(true);
    try {
      const res = await fetch(`/api/projects/${projectId}/llm-budget`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ budget_usd: v }),
      });
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      const j = (await res.json()) as { pause_reason_cleared: boolean };
      toast.success(
        j.pause_reason_cleared
          ? "Бюджет поднят, причина паузы снята — нажми ▶ на проекте"
          : "Бюджет проекта сохранён",
      );
      await loadDetail(projectId);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const maxNode = detail ? Math.max(0, ...detail.nodes.map((n) => n.cost_usd)) : 0;
  const maxProj = projects ? Math.max(0, ...projects.projects.map((p) => p.cost_usd)) : 0;
  const budgetPct =
    detail && detail.budget.enabled && detail.budget.budget_usd > 0
      ? Math.min(100, Math.round((detail.budget.spent_usd / detail.budget.budget_usd) * 100))
      : null;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-full max-w-[min(96vw,1100px)] flex-col gap-0 p-0 sm:max-w-[min(96vw,1100px)]"
      >
        <SheetHeader className="border-b border-border px-4 py-3">
          <SheetTitle>Стоимость LLM</SheetTitle>
          <SheetDescription>
            Строка на каждый HTTP-вызов (включая ретраи и неуспешные); суммы — по нодам, моделям и
            прогонам. unbilled — вызовы без usage (обрыв без тела), показаны количеством.
          </SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 text-sm">
          {error ? <div className="mb-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs">{error}</div> : null}
          {(projects?.failed_inserts ?? 0) > 0 || (detail?.failed_inserts ?? 0) > 0 ? (
            <div className="mb-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs">
              Учёт неполный: {projects?.failed_inserts ?? detail?.failed_inserts} записей не доехали до БД
              (INSERT падал) — цифры занижены, оценка незаписанного учтена в бюджете.
            </div>
          ) : null}

          <section className="mb-5">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Прогоны
            </h3>
            {projects == null ? (
              <div className="text-xs text-muted-foreground">Загрузка…</div>
            ) : projects.projects.length === 0 ? (
              <div className="text-xs text-muted-foreground">Вызовов LLM ещё не было.</div>
            ) : (
              <table className="w-full border-collapse text-xs">
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="px-2 py-1 text-left">Проект</th>
                    <th className="px-2 py-1 text-left">Динамика</th>
                    <th className="px-2 py-1 text-right">Вызовы</th>
                    <th className="px-2 py-1 text-right">Токены in / out</th>
                    <th className="px-2 py-1 text-right">$</th>
                    <th className="px-2 py-1 text-right">Неуспешные</th>
                    <th className="px-2 py-1 text-right">unbilled</th>
                  </tr>
                </thead>
                <tbody>
                  {projects.projects.map((p) => (
                    <tr
                      key={p.project_id}
                      onClick={() => setProjectId(p.project_id)}
                      className={cn(
                        "cursor-pointer border-t border-white/[0.06] hover:bg-white/[0.04]",
                        p.project_id === projectId && "bg-primary/10",
                      )}
                    >
                      <td className="px-2 py-1">
                        #{p.project_id} {p.title ? <span className="text-muted-foreground">{p.title}</span> : null}
                      </td>
                      <td className="w-40 px-2 py-1"><Bar value={p.cost_usd} max={maxProj} /></td>
                      <AggCells a={p} />
                    </tr>
                  ))}
                  {projects.adhoc.calls > 0 ? (
                    <tr className="border-t border-white/[0.06] text-muted-foreground">
                      <td className="px-2 py-1">adhoc (workspace / чат)</td>
                      <td className="px-2 py-1" />
                      <AggCells a={projects.adhoc} />
                    </tr>
                  ) : null}
                </tbody>
              </table>
            )}
          </section>

          {projectId == null ? (
            <div className="text-xs text-muted-foreground">Выбери прогон выше — покажу разбивку по нодам.</div>
          ) : detail == null ? (
            <div className="text-xs text-muted-foreground">Загрузка прогона #{projectId}…</div>
          ) : (
            <>
              <section className="mb-5 rounded-md border border-white/[0.08] bg-white/[0.02] p-3">
                <div className="flex flex-wrap items-end justify-between gap-3">
                  <div>
                    <div className="text-xs uppercase tracking-wider text-muted-foreground">Прогон #{detail.project_id}</div>
                    <div className="text-xl font-semibold tabular-nums">{usd(detail.total.cost_usd)}</div>
                    <div className="text-xs text-muted-foreground">
                      {detail.total.calls} вызовов · неуспешных {detail.total.failed} · unbilled {detail.total.unbilled}
                    </div>
                  </div>
                  <div className="flex items-end gap-2">
                    <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                      Бюджет прогона, $ {detail.budget.override ? "(override проекта)" : "(default из конфига)"}
                      <Input
                        value={budgetInput}
                        onChange={(e) => setBudgetInput(e.target.value)}
                        className="h-8 w-32 text-sm"
                        inputMode="decimal"
                      />
                    </label>
                    <Button size="sm" onClick={saveBudget} disabled={busy}>
                      {detail.budget.paused_for_budget ? "Поднять и снять причину паузы" : "Сохранить"}
                    </Button>
                  </div>
                </div>
                {budgetPct != null ? (
                  <div className="mt-3">
                    <Bar
                      value={detail.budget.spent_usd}
                      max={detail.budget.budget_usd}
                      className={cn(detail.budget.exhausted ? "bg-destructive" : budgetPct > 80 ? "bg-amber-500" : "")}
                    />
                    <div className="mt-1 text-xs text-muted-foreground">
                      {usd(detail.budget.spent_usd)} из {usd(detail.budget.budget_usd)} ({budgetPct}%)
                      {detail.budget.paused_for_budget ? " — проект в паузе по бюджету" : ""}
                    </div>
                  </div>
                ) : (
                  <div className="mt-2 text-xs text-muted-foreground">Бюджет выключен (0).</div>
                )}
              </section>

              <section className="mb-5">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  По нодам
                </h3>
                <table className="w-full border-collapse text-xs">
                  <thead className="text-muted-foreground">
                    <tr>
                      <th className="px-2 py-1 text-left">Нода</th>
                      <th className="px-2 py-1 text-left">Доля</th>
                      <th className="px-2 py-1 text-right">Вызовы</th>
                      <th className="px-2 py-1 text-right">Токены in / out</th>
                      <th className="px-2 py-1 text-right">$</th>
                      <th className="px-2 py-1 text-right">Неуспешные</th>
                      <th className="px-2 py-1 text-right">unbilled</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.nodes.map((n) => (
                      <tr key={n.node_key} className="border-t border-white/[0.06]">
                        <td className="px-2 py-1 font-mono">{n.node_key}</td>
                        <td className="w-40 px-2 py-1">
                          <Bar value={n.cost_usd} max={maxNode} />
                          <span className="text-[10px] text-muted-foreground">
                            {detail.total.cost_usd > 0 ? Math.round((n.cost_usd / detail.total.cost_usd) * 100) : 0}%
                          </span>
                        </td>
                        <AggCells a={n} />
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>

              <section>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  По моделям
                </h3>
                <table className="w-full border-collapse text-xs">
                  <thead className="text-muted-foreground">
                    <tr>
                      <th className="px-2 py-1 text-left">Модель</th>
                      <th className="px-2 py-1 text-right">Вызовы</th>
                      <th className="px-2 py-1 text-right">Токены in / out</th>
                      <th className="px-2 py-1 text-right">$</th>
                      <th className="px-2 py-1 text-right">Неуспешные</th>
                      <th className="px-2 py-1 text-right">unbilled</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.models.map((m) => (
                      <tr key={m.model} className="border-t border-white/[0.06]">
                        <td className="px-2 py-1 font-mono">{m.model}</td>
                        <AggCells a={m} />
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
