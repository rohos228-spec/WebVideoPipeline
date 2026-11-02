"use client";

/**
 * Вкладка «Расходы» админ-панели VIA: сводка по факту и списаниям, разрез по
 * дням, видам, моделям и проектам, прайс-лист с покрытием, сверка факт/списания
 * и кнопка «Пересчитать» (recount с dry_run и подтверждением).
 *
 * Контракт ручек — docs/cost-accounting.md, раздел «Контракт API v1».
 * Участнику ручки отдают 404: раздел не показываем и ошибок не сыплем.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Calculator, CircleDollarSign, Coins, Gauge, Scale } from "lucide-react";
import {
  adminApi,
  isNotFound,
  type AdminCostDay,
  type AdminCostRecount,
} from "@/lib/admin-api";
import {
  Badge,
  Btn,
  Card,
  Drawer,
  Empty,
  ErrorBox,
  MiniBars,
  Seg,
  Skel,
  Spinner,
  Stat,
  fmtNum,
  tableCls,
  tableWrap,
  tdCls,
  thCls,
} from "./admin-kit";
import {
  KIND_OPTIONS,
  costPeriod,
  fmtCredits,
  fmtMicro,
  fmtUsdCost,
  kindLabel,
  shortDay,
} from "./admin-costs-format";
import { ModelsCard, PricesCard, ProjectsCard } from "./admin-costs-tables";

const DAY_OPTIONS = [
  { v: 7, t: "7 дн" },
  { v: 30, t: "30 дн" },
  { v: 90, t: "90 дн" },
];

/** Подписанная сумма: дельта может быть и в плюс, и в минус. */
const signedUsd = (n: number): string => `${n > 0 ? "+" : n < 0 ? "−" : ""}${fmtUsdCost(Math.abs(n))}`;

type DayPoint = {
  day: string;
  calls: number;
  actual_usd: number;
  credits_charged: number;
  unpriced_calls: number;
};

const aggregateDays = (rows: AdminCostDay[]): DayPoint[] => {
  const map = new Map<string, DayPoint>();
  for (const r of rows) {
    const point = map.get(r.day) ?? {
      day: r.day,
      calls: 0,
      actual_usd: 0,
      credits_charged: 0,
      unpriced_calls: 0,
    };
    point.calls += r.calls ?? 0;
    point.actual_usd += r.actual_usd ?? 0;
    point.credits_charged += r.credits_charged ?? 0;
    point.unpriced_calls += r.unpriced_calls ?? 0;
    map.set(r.day, point);
  }
  return [...map.values()].sort((a, b) => a.day.localeCompare(b.day));
};

export function AdminCostsTab() {
  const qc = useQueryClient();
  const [days, setDays] = useState(30);
  const [kind, setKind] = useState("");
  const [recountOpen, setRecountOpen] = useState(false);
  const period = useMemo(() => costPeriod(days), [days]);
  const kindParam = kind || undefined;

  const overview = useQuery({
    queryKey: ["admin", "costs", "overview", period.from, period.to],
    queryFn: () => adminApi.costsOverview(period),
    retry: false,
    staleTime: 30_000,
  });
  // Участнику ручки отдают 404 — раздел не показываем и красной ошибкой не шумим.
  const notFound = isNotFound(overview.error);
  const enabled = !notFound;

  const byDay = useQuery({
    queryKey: ["admin", "costs", "by-day", period.from, period.to, kind],
    queryFn: () => adminApi.costsByDay({ ...period, kind: kindParam }),
    enabled,
    retry: false,
  });

  const byModel = useQuery({
    queryKey: ["admin", "costs", "by-model", period.from, period.to, kind],
    queryFn: () => adminApi.costsByModel({ ...period, kind: kindParam }),
    enabled,
    retry: false,
  });

  const byProject = useQuery({
    queryKey: ["admin", "costs", "by-project", period.from, period.to],
    queryFn: () => adminApi.costsByProject(period),
    enabled,
    retry: false,
  });

  const prices = useQuery({
    queryKey: ["admin", "costs", "prices", kind],
    queryFn: () => adminApi.costsPrices({ kind: kindParam }),
    enabled,
    retry: false,
  });

  const reconcile = useQuery({
    queryKey: ["admin", "costs", "reconcile", period.from, period.to],
    queryFn: () => adminApi.costsReconcile(period),
    enabled,
    retry: false,
  });

  const refreshCosts = useCallback(() => {
    void qc.invalidateQueries({
      predicate: (q) => Array.isArray(q.queryKey) && q.queryKey[0] === "admin" && q.queryKey[1] === "costs",
    });
  }, [qc]);

  /* ── нет доступа: раздел не показываем, ошибку не шумим ── */
  if (notFound) {
    return (
      <Card title="Расходы">
        <Empty text="Раздел недоступен: подробности себестоимости видит только администратор студии." />
      </Card>
    );
  }

  const totals = overview.data?.totals;
  const byKind = overview.data?.by_kind ?? [];
  const series = aggregateDays(byDay.data ?? []);
  const seriesUsd = series.reduce((acc, p) => acc + p.actual_usd, 0);
  const seriesCalls = series.reduce((acc, p) => acc + p.calls, 0);
  const rec = reconcile.data;
  const refRows = rec?.by_ref_table ?? [];
  const ratio = rec && rec.actual_usd > 0 ? rec.charged_usd / rec.actual_usd : null;
  const deltaTone = rec ? (Math.abs(rec.delta_usd) < 0.01 ? "ok" : "warn") : undefined;
  const coverageTone =
    !totals || totals.calls === 0
      ? undefined
      : totals.price_coverage_pct >= 99
        ? "ok"
        : totals.price_coverage_pct >= 90
          ? "warn"
          : "bad";

  return (
    <div className="flex flex-col gap-3">
      {/* период, вид, пересчёт */}
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-[13px] text-zinc-500">Период</span>
        <div className="w-[240px]">
          <Seg value={days} options={DAY_OPTIONS} onChange={setDays} />
        </div>
        <span
          className="text-[13px] text-zinc-500"
          title="Применяется к графику, моделям и прайсу; сводка и сверка — по всем видам"
        >
          Вид
        </span>
        <div className="w-[360px]">
          <Seg value={kind} options={KIND_OPTIONS} onChange={setKind} />
        </div>
        <div className="flex-1" />
        {overview.data ? (
          <span className="text-[12px] text-zinc-600">
            {overview.data.from ?? period.from} — {overview.data.to ?? period.to}
          </span>
        ) : null}
        <Btn accent disabled={overview.isLoading} onClick={() => setRecountOpen(true)} title="Пересчитать себестоимость из прайсов">
          <Calculator className="h-4 w-4" />
          Пересчитать
        </Btn>
      </div>

      {overview.isError ? <ErrorBox err={overview.error} retry={() => void overview.refetch()} /> : null}

      {overview.isLoading ? (
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skel key={i} h={92} />
            ))}
          </div>
          <Skel h={220} />
          <Skel h={300} />
        </div>
      ) : totals ? (
        <>
          {/* сводка */}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <Stat
              label="Факт (себестоимость)"
              value={fmtUsdCost(totals.actual_usd)}
              hint="все виды за период"
              tone={totals.actual_usd > 0 ? "ok" : undefined}
            />
            <Stat
              label="Списано кредитов"
              value={fmtCredits(totals.credits_charged)}
              hint={`${fmtMicro(totals.credits_micro)} микрокредитов`}
            />
            <Stat
              label="Вызовов"
              value={fmtNum(totals.calls)}
              hint={`нерасценённых: ${fmtNum(totals.unpriced_calls)}`}
            />
            <Stat
              label="Покрытие прайса"
              value={totals.calls > 0 ? `${(totals.price_coverage_pct ?? 0).toFixed(1)}%` : "—"}
              hint="доля вызовов с ценой"
              tone={coverageTone}
            />
            <Stat
              label="Нерасценено"
              value={fmtNum(totals.unpriced_calls)}
              hint="цена неизвестна — в ноль не идёт"
              tone={totals.unpriced_calls > 0 ? "bad" : undefined}
            />
          </div>

          {totals.unpriced_calls > 0 ? (
            <div className="rounded-[12px] border border-amber-500/20 bg-amber-500/[0.06] px-4 py-2.5 text-[13px] text-amber-200/90">
              У {fmtNum(totals.unpriced_calls)} вызовов нет цены в прайсе — в факте они не учтены (это не ноль).
              Покрытие закрывается строкой «Прайс-лист и покрытие» и кнопкой «Пересчитать».
            </div>
          ) : null}

          {/* дни + виды */}
          <div className="grid gap-3 xl:grid-cols-[1.4fr_1fr]">
            <Card
              title={`По дням${kind ? " · " + kindLabel(kind) : ""} · ${days} дн`}
              icon={<Gauge className="h-4 w-4" />}
              className="min-w-0"
              right={
                <span className="text-[12px] text-zinc-500">
                  {fmtUsdCost(seriesUsd)} · {fmtNum(seriesCalls)} выз.
                  {byDay.isFetching && !byDay.isLoading ? " · обновляем…" : ""}
                </span>
              }
            >
              {byDay.isLoading ? (
                <Skel h={180} />
              ) : byDay.isError ? (
                <ErrorBox err={byDay.error} retry={() => void byDay.refetch()} />
              ) : series.length === 0 ? (
                <Empty text="За период расходов нет." />
              ) : (
                <>
                  <MiniBars
                    points={series.map((p) => ({
                      label: shortDay(p.day),
                      value: p.actual_usd,
                      hint: `${p.day} · ${fmtNum(p.calls)} выз.`,
                    }))}
                    height={140}
                    format={fmtUsdCost}
                    accent="#fbbf24"
                  />
                  <div className="mt-3 max-h-[240px] overflow-y-auto">
                    <table className={tableCls}>
                      <thead>
                        <tr>
                          <th className={thCls}>День</th>
                          <th className={thCls}>Вызовы</th>
                          <th className={thCls}>Факт</th>
                          <th className={thCls}>Кредиты</th>
                          <th className={thCls}>Без цены</th>
                        </tr>
                      </thead>
                      <tbody>
                        {series.map((p) => (
                          <tr key={p.day}>
                            <td className={tdCls}>
                              <span className="whitespace-nowrap text-zinc-400">{p.day}</span>
                            </td>
                            <td className={tdCls}>{fmtNum(p.calls)}</td>
                            <td className={tdCls}>
                              <span className="whitespace-nowrap font-mono text-[13px]">{fmtUsdCost(p.actual_usd)}</span>
                            </td>
                            <td className={tdCls}>
                              <span className="whitespace-nowrap">{fmtCredits(p.credits_charged)}</span>
                            </td>
                            <td className={tdCls}>
                              {p.unpriced_calls > 0 ? (
                                <span className="text-red-300/90">{fmtNum(p.unpriced_calls)}</span>
                              ) : (
                                <span className="text-zinc-600">0</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </Card>

            <Card title="По видам" icon={<CircleDollarSign className="h-4 w-4" />} className="min-w-0">
              {byKind.length === 0 ? (
                <Empty text="За период расходов нет." />
              ) : (
                <div className={tableWrap}>
                  <table className={tableCls}>
                    <thead>
                      <tr>
                        <th className={thCls}>Вид</th>
                        <th className={thCls}>Вызовы</th>
                        <th className={thCls}>Единицы</th>
                        <th className={thCls}>Факт</th>
                        <th className={thCls}>Кредиты</th>
                        <th className={thCls}>Без цены</th>
                      </tr>
                    </thead>
                    <tbody>
                      {byKind.map((k) => (
                        <tr key={k.kind}>
                          <td className={tdCls}>
                            <Badge tone={k.kind === "llm" ? "info" : k.kind === "video" ? "ok" : "neutral"}>
                              {kindLabel(k.kind)}
                            </Badge>
                          </td>
                          <td className={tdCls}>{fmtNum(k.calls)}</td>
                          <td className={tdCls}>{k.units > 0 ? fmtNum(k.units) : "—"}</td>
                          <td className={tdCls}>
                            <span className="whitespace-nowrap font-mono text-[13px]">{fmtUsdCost(k.actual_usd)}</span>
                          </td>
                          <td className={tdCls}>
                            <span className="whitespace-nowrap">{fmtCredits(k.credits_charged)}</span>
                          </td>
                          <td className={tdCls}>
                            {k.unpriced_calls > 0 ? (
                              <span className="text-red-300/90">{fmtNum(k.unpriced_calls)}</span>
                            ) : (
                              <span className="text-zinc-600">0</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>

          {/* модели */}
          <ModelsCard
            rows={byModel.data ?? []}
            loading={byModel.isLoading}
            error={byModel.error}
            onRetry={() => void byModel.refetch()}
          />

          {/* проекты */}
          <ProjectsCard
            rows={byProject.data ?? []}
            loading={byProject.isLoading}
            error={byProject.error}
            onRetry={() => void byProject.refetch()}
          />

          {/* прайс-лист */}
          <PricesCard
            rows={prices.data ?? []}
            loading={prices.isLoading}
            error={prices.error}
            onRetry={() => void prices.refetch()}
          />

          {/* сверка */}
          <Card title="Сверка: факт против списаний" icon={<Scale className="h-4 w-4" />}>
            {reconcile.isLoading ? (
              <Skel h={160} />
            ) : reconcile.isError ? (
              <ErrorBox err={reconcile.error} retry={() => void reconcile.refetch()} />
            ) : !rec ? (
              <Empty text="Нет данных для сверки." />
            ) : (
              <>
                <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
                  <Stat label="Факт" value={fmtUsdCost(rec.actual_usd)} hint="себестоимость вызовов" />
                  <Stat label="Списано" value={fmtUsdCost(rec.charged_usd)} hint="credit_entries.settle, $" />
                  <Stat
                    label="Дельта"
                    value={signedUsd(rec.delta_usd ?? 0)}
                    hint="факт − списания"
                    tone={deltaTone}
                  />
                  <Stat
                    label="Списано на $1 факта"
                    value={ratio !== null ? `×${ratio.toFixed(2)}` : "—"}
                    hint="маржа в списаниях"
                  />
                </div>

                <div className="mt-3">
                  {refRows.length === 0 ? (
                    <Empty text="Списаний за период нет." />
                  ) : (
                    <div className={tableWrap}>
                      <table className={tableCls}>
                        <thead>
                          <tr>
                            <th className={thCls}>Источник списания</th>
                            <th className={thCls}>Записей</th>
                            <th className={thCls}>Списано</th>
                            <th className={thCls}>Факт</th>
                            <th className={thCls}>Дельта</th>
                          </tr>
                        </thead>
                        <tbody>
                          {refRows.map((row) => {
                            const rowDelta = (row.actual_usd ?? 0) - (row.charged_usd ?? 0);
                            return (
                              <tr key={row.ref_table}>
                                <td className={tdCls}>
                                  <span className="font-mono text-[13px] text-white">{row.ref_table || "—"}</span>
                                </td>
                                <td className={tdCls}>{fmtNum(row.entries)}</td>
                                <td className={tdCls}>
                                  <span className="whitespace-nowrap font-mono text-[13px]">
                                    {fmtUsdCost(row.charged_usd)}
                                  </span>
                                </td>
                                <td className={tdCls}>
                                  <span className="whitespace-nowrap font-mono text-[13px]">
                                    {fmtUsdCost(row.actual_usd)}
                                  </span>
                                </td>
                                <td className={tdCls}>
                                  <span
                                    className={
                                      "whitespace-nowrap font-mono text-[13px] " +
                                      (Math.abs(rowDelta) < 0.01 ? "text-zinc-500" : "text-amber-300/90")
                                    }
                                  >
                                    {signedUsd(rowDelta)}
                                  </span>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                <div className="mt-2 text-[12px] text-zinc-600">
                  Списания — из леджера кредитов с маржой; расхождение объясняется округлением, маржой и
                  вызовами без привязки к проекту.
                </div>
              </>
            )}
          </Card>
        </>
      ) : null}

      <RecountDrawer
        open={recountOpen}
        from={period.from}
        to={period.to}
        onClose={() => setRecountOpen(false)}
        onApplied={refreshCosts}
      />
    </div>
  );
}

/* ───────────────────────── пересчёт: dry_run → запись ───────────────────────── */

function RecountDrawer({
  open,
  from,
  to,
  onClose,
  onApplied,
}: {
  open: boolean;
  from: string;
  to: string;
  onClose: () => void;
  onApplied: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<AdminCostRecount | null>(null);
  const [applied, setApplied] = useState<AdminCostRecount | null>(null);
  const [error, setError] = useState<unknown>(null);

  const dryRun = useCallback(async () => {
    setBusy(true);
    setError(null);
    setPreview(null);
    setApplied(null);
    try {
      setPreview(await adminApi.costsRecount({ from, to, dry_run: true }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }, [from, to]);

  useEffect(() => {
    if (!open) {
      setPreview(null);
      setApplied(null);
      setError(null);
      return;
    }
    if (isNotFound(error)) return; // раздел недоступен — не долбим ручку
    void dryRun();
    // dryRun меняется только вместе с периодом; повторный запуск на закрытие не нужен
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, from, to]);

  const apply = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await adminApi.costsRecount({ from, to, dry_run: false });
      setApplied(r);
      toast.success(`Пересчёт записан: ${fmtNum(r.priced)} вызов(ов), +${fmtUsdCost(r.added_usd)}`);
      onApplied();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const close = () => {
    if (busy) return;
    onClose();
  };

  const nothingToWrite = preview !== null && preview.priced === 0 && (preview.added_usd ?? 0) <= 0;

  return (
    <Drawer
      open={open}
      width={520}
      title="Пересчёт расходов"
      subtitle={`${from} — ${to}${applied ? " · записано" : " · предварительный расчёт"}`}
      onClose={close}
    >
      {busy && !preview && !applied ? (
        <div className="flex items-center gap-3 text-[14px] text-zinc-500">
          <Spinner className="h-4 w-4" />
          {applied ? "Записываем…" : "Считаем, что изменится…"}
        </div>
      ) : error ? (
        <ErrorBox err={error} retry={() => void dryRun()} />
      ) : applied ? (
        <>
          <RecountResult r={applied} />
          <div className="mt-4 flex gap-2">
            <Btn accent onClick={close}>
              Готово
            </Btn>
          </div>
        </>
      ) : preview ? (
        <>
          <RecountResult r={preview} />
          <div className="mt-3 rounded-[10px] bg-black/25 px-3 py-2.5 text-[13px] leading-relaxed text-zinc-400">
            Пересчёт заполняет себестоимость у вызовов без цены по текущему прайсу. Уже оценённые вызовы
            не меняются, кредиты и леджер не трогаются.
          </div>
          {nothingToWrite ? (
            <div className="mt-2 text-[13px] text-amber-300/90">
              Новых цен не нашлось — записывать нечего.
            </div>
          ) : null}
          <div className="mt-4 flex flex-wrap gap-2">
            <Btn accent disabled={busy || nothingToWrite} onClick={() => void apply()}>
              {busy ? "Записываем…" : "Пересчитать и записать"}
            </Btn>
            <Btn disabled={busy} onClick={close}>
              Отмена
            </Btn>
          </div>
        </>
      ) : (
        <div className="flex items-center gap-3 text-[14px] text-zinc-500">
          <Spinner className="h-4 w-4" />
          Считаем…
        </div>
      )}
    </Drawer>
  );
}

function RecountResult({ r }: { r: AdminCostRecount }) {
  return (
    <div className="grid grid-cols-2 gap-2">
      <Stat label="Просмотрено" value={fmtNum(r.scanned)} hint="вызовов в периоде" />
      <Stat label="Получат цену" value={fmtNum(r.priced)} tone={r.priced > 0 ? "ok" : undefined} />
      <Stat
        label="Останутся без цены"
        value={fmtNum(r.still_unpriced)}
        tone={r.still_unpriced > 0 ? "warn" : undefined}
      />
      <Stat label="Добавится к факту" value={fmtUsdCost(r.added_usd)} hint="USD" />
    </div>
  );
}
