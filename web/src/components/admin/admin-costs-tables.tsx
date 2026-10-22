"use client";

/**
 * Блоки раздела «Расходы»: таблицы по моделям и проектам, прайс-лист с
 * покрытием. Данные приходят из /api/admin/costs/*, поиск и сортировка —
 * на клиенте (контракт ручек не расширяем).
 */
import { useMemo, useState } from "react";
import { CircleDollarSign, FolderKanban, ListChecks } from "lucide-react";
import type { AdminCostModel, AdminCostPrice, AdminCostProject } from "@/lib/admin-api";
import {
  Badge,
  Card,
  Empty,
  ErrorBox,
  Skel,
  TextInput,
  Toggle,
  fmtNum,
  tableCls,
  tableWrap,
  tdCls,
  thCls,
} from "./admin-kit";
import { fmtCredits, fmtPrice, fmtUnits, fmtUsdCost, kindLabel } from "./admin-costs-format";

type SortDir = "asc" | "desc";
type SortState<K extends string> = { key: K; dir: SortDir };

const cmp = (a: unknown, b: unknown): number => {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a ?? "").localeCompare(String(b ?? ""), "ru");
};

function SortTh<K extends string>({
  label,
  k,
  sort,
  onSort,
}: {
  label: string;
  k: K;
  sort: SortState<K>;
  onSort: (k: K) => void;
}) {
  const active = sort.key === k;
  return (
    <th className={thCls}>
      <button
        type="button"
        onClick={() => onSort(k)}
        className="inline-flex items-center gap-1 uppercase tracking-[0.06em] transition-colors hover:text-zinc-300"
        style={active ? { color: "#2dd4bf" } : undefined}
        title="Сортировать"
      >
        {label}
        {active ? (sort.dir === "asc" ? " ↑" : " ↓") : ""}
      </button>
    </th>
  );
}

type ModelSortKey =
  | "kind"
  | "provider"
  | "model"
  | "calls"
  | "units"
  | "actual_usd"
  | "credits_charged"
  | "avg_usd"
  | "unpriced_calls";

/* ───────────────────────────── по моделям ───────────────────────────── */

export function ModelsCard({
  rows,
  loading,
  error,
  onRetry,
}: {
  rows: AdminCostModel[];
  loading: boolean;
  error: unknown;
  onRetry: () => void;
}) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<SortState<ModelSortKey>>({ key: "actual_usd", dir: "desc" });

  const onSort = (k: ModelSortKey) =>
    setSort((prev) => (prev.key === k ? { key: k, dir: prev.dir === "asc" ? "desc" : "asc" } : { key: k, dir: k === "model" || k === "provider" || k === "kind" ? "asc" : "desc" }));

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const list = needle
      ? rows.filter((m) => `${m.model} ${m.provider} ${kindLabel(m.kind)}`.toLowerCase().includes(needle))
      : rows;
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...list].sort((a, b) => cmp(a[sort.key], b[sort.key]) * dir);
  }, [rows, q, sort]);

  const sumUsd = filtered.reduce((acc, m) => acc + (m.actual_usd ?? 0), 0);
  const sumCalls = filtered.reduce((acc, m) => acc + (m.calls ?? 0), 0);

  return (
    <Card
      title="По моделям"
      icon={<ListChecks className="h-4 w-4" />}
      right={
        <span className="text-[12px] text-zinc-500">
          {fmtNum(filtered.length)} из {fmtNum(rows.length)} · {fmtNum(sumCalls)} выз. · {fmtUsdCost(sumUsd)}
        </span>
      }
    >
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <div className="w-[280px]">
          <TextInput
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Поиск по модели или провайдеру"
            aria-label="Поиск по модели"
          />
        </div>
        <span className="text-[12px] text-zinc-600">Клик по заголовку — сортировка</span>
      </div>

      {loading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skel key={i} h={32} />
          ))}
        </div>
      ) : error ? (
        <ErrorBox err={error} retry={onRetry} />
      ) : rows.length === 0 ? (
        <Empty text="За период не было вызовов моделей." />
      ) : filtered.length === 0 ? (
        <Empty text="Ничего не найдено. Измени поиск." />
      ) : (
        <div className={tableWrap}>
          <table className={tableCls}>
            <thead>
              <tr>
                <SortTh label="Вид" k="kind" sort={sort} onSort={onSort} />
                <SortTh label="Провайдер" k="provider" sort={sort} onSort={onSort} />
                <SortTh label="Модель" k="model" sort={sort} onSort={onSort} />
                <SortTh label="Вызовы" k="calls" sort={sort} onSort={onSort} />
                <SortTh label="Единицы" k="units" sort={sort} onSort={onSort} />
                <SortTh label="Факт" k="actual_usd" sort={sort} onSort={onSort} />
                <SortTh label="Кредиты" k="credits_charged" sort={sort} onSort={onSort} />
                <SortTh label="Средняя" k="avg_usd" sort={sort} onSort={onSort} />
                <SortTh label="Цена" k="unpriced_calls" sort={sort} onSort={onSort} />
              </tr>
            </thead>
            <tbody>
              {filtered.map((m, i) => (
                <tr key={`${m.kind}-${m.provider}-${m.model}-${i}`}>
                  <td className={tdCls}>
                    <Badge tone={m.kind === "llm" ? "info" : m.kind === "video" ? "ok" : "neutral"}>
                      {kindLabel(m.kind)}
                    </Badge>
                  </td>
                  <td className={tdCls}>
                    <div className="max-w-[140px] truncate" title={m.provider}>
                      {m.provider || "—"}
                    </div>
                  </td>
                  <td className={tdCls}>
                    <div className="max-w-[260px] truncate font-mono text-[13px] text-white" title={m.model}>
                      {m.model || "—"}
                    </div>
                  </td>
                  <td className={tdCls}>{fmtNum(m.calls)}</td>
                  <td className={tdCls}>
                    <span className="whitespace-nowrap">{fmtUnits(m.units, m.unit)}</span>
                  </td>
                  <td className={tdCls}>
                    <span className="whitespace-nowrap font-mono text-[13px]">{fmtUsdCost(m.actual_usd)}</span>
                  </td>
                  <td className={tdCls}>
                    <span className="whitespace-nowrap">{fmtCredits(m.credits_charged)}</span>
                  </td>
                  <td className={tdCls}>
                    <span className="whitespace-nowrap font-mono text-[13px] text-zinc-400">
                      {fmtUsdCost(m.avg_usd)}
                    </span>
                  </td>
                  <td className={tdCls}>
                    {m.has_price ? (
                      <Badge tone="ok">есть</Badge>
                    ) : (
                      <Badge tone="bad">нет</Badge>
                    )}
                    {m.unpriced_calls > 0 ? (
                      <div className="mt-0.5 whitespace-nowrap text-[12px] text-red-300/80">
                        {fmtNum(m.unpriced_calls)} без цены
                      </div>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/* ───────────────────────────── по проектам ───────────────────────────── */

export function ProjectsCard({
  rows,
  loading,
  error,
  onRetry,
}: {
  rows: AdminCostProject[];
  loading: boolean;
  error: unknown;
  onRetry: () => void;
}) {
  const sorted = useMemo(
    () => [...rows].sort((a, b) => (b.actual_usd ?? 0) - (a.actual_usd ?? 0)),
    [rows],
  );
  const total = sorted.reduce((acc, p) => acc + (p.actual_usd ?? 0), 0);

  return (
    <Card
      title="По проектам"
      icon={<FolderKanban className="h-4 w-4" />}
      right={
        <span className="text-[12px] text-zinc-500">
          {fmtNum(rows.length)} проект(ов) · {fmtUsdCost(total)}
        </span>
      }
    >
      {loading ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skel key={i} h={32} />
          ))}
        </div>
      ) : error ? (
        <ErrorBox err={error} retry={onRetry} />
      ) : sorted.length === 0 ? (
        <Empty text="Нет расходов, привязанных к проектам." />
      ) : (
        <div className={tableWrap}>
          <table className={tableCls}>
            <thead>
              <tr>
                <th className={thCls}>Проект</th>
                <th className={thCls}>Вызовы</th>
                <th className={thCls}>Нерасценено</th>
                <th className={thCls}>Факт</th>
                <th className={thCls}>Кредиты</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((p) => (
                <tr key={p.project_id}>
                  <td className={tdCls}>
                    <div className="max-w-[320px] truncate text-white" title={p.project_title}>
                      {p.project_title || "Без названия"}
                    </div>
                    <div className="text-[12px] text-zinc-600">#{p.project_id}</div>
                  </td>
                  <td className={tdCls}>{fmtNum(p.calls)}</td>
                  <td className={tdCls}>
                    {p.unpriced_calls > 0 ? (
                      <span className="text-red-300/90">{fmtNum(p.unpriced_calls)}</span>
                    ) : (
                      <span className="text-zinc-600">0</span>
                    )}
                  </td>
                  <td className={tdCls}>
                    <span className="whitespace-nowrap font-mono text-[13px]">{fmtUsdCost(p.actual_usd)}</span>
                  </td>
                  <td className={tdCls}>
                    <span className="whitespace-nowrap">{fmtCredits(p.credits_charged)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/* ───────────────────────────── прайс-лист ───────────────────────────── */

export function PricesCard({
  rows,
  loading,
  error,
  onRetry,
}: {
  rows: AdminCostPrice[];
  loading: boolean;
  error: unknown;
  onRetry: () => void;
}) {
  const [q, setQ] = useState("");
  const [onlyGaps, setOnlyGaps] = useState(false);

  const withPrice = rows.filter((p) => p.has_price).length;
  const coverage = rows.length ? (withPrice / rows.length) * 100 : 0;
  const tone = rows.length === 0 ? "neutral" : coverage >= 95 ? "ok" : coverage >= 70 ? "warn" : "bad";

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return rows
      .filter((p) => {
        if (onlyGaps && p.has_price && (p.unpriced_30d ?? 0) === 0) return false;
        if (!needle) return true;
        return `${p.model} ${p.provider} ${kindLabel(p.kind)}`.toLowerCase().includes(needle);
      })
      .sort(
        (a, b) =>
          Number(a.has_price) - Number(b.has_price) ||
          (b.calls_30d ?? 0) - (a.calls_30d ?? 0) ||
          `${a.provider}-${a.model}`.localeCompare(`${b.provider}-${b.model}`, "ru"),
      );
  }, [rows, q, onlyGaps]);

  return (
    <Card
      title="Прайс-лист и покрытие"
      icon={<CircleDollarSign className="h-4 w-4" />}
      right={
        <span className="flex items-center gap-2 text-[12px] text-zinc-500">
          покрытие <Badge tone={tone}>{rows.length ? `${coverage.toFixed(0)}%` : "—"}</Badge>
          {fmtNum(withPrice)} из {fmtNum(rows.length)} моделей
        </span>
      }
    >
      <div className="mb-2 flex flex-wrap items-center gap-3">
        <div className="w-[280px]">
          <TextInput
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Поиск модели или провайдера"
            aria-label="Поиск по прайсу"
          />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[13px] text-zinc-400">Только без цены</span>
          <Toggle on={onlyGaps} onChange={setOnlyGaps} />
        </div>
        <span className="text-[12px] text-zinc-600">Вызовы — за последние 30 дней</span>
      </div>

      {loading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skel key={i} h={32} />
          ))}
        </div>
      ) : error ? (
        <ErrorBox err={error} retry={onRetry} />
      ) : rows.length === 0 ? (
        <Empty text="Прайсы пусты — цен нет ни у одной модели." />
      ) : filtered.length === 0 ? (
        <Empty text="Под фильтр ничего не подходит." />
      ) : (
        <div className={tableWrap}>
          <table className={tableCls}>
            <thead>
              <tr>
                <th className={thCls}>Вид</th>
                <th className={thCls}>Провайдер</th>
                <th className={thCls}>Модель</th>
                <th className={thCls}>Единица</th>
                <th className={thCls}>$ / ед.</th>
                <th className={thCls}>$ / вызов</th>
                <th className={thCls}>Цена</th>
                <th className={thCls}>Вызовов 30д</th>
                <th className={thCls}>Без цены 30д</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((p, i) => (
                <tr key={`${p.kind}-${p.provider}-${p.model}-${i}`}>
                  <td className={tdCls}>
                    <Badge tone={p.kind === "llm" ? "info" : p.kind === "video" ? "ok" : "neutral"}>
                      {kindLabel(p.kind)}
                    </Badge>
                  </td>
                  <td className={tdCls}>
                    <div className="max-w-[130px] truncate" title={p.provider}>
                      {p.provider || "—"}
                    </div>
                  </td>
                  <td className={tdCls}>
                    <div className="max-w-[260px] truncate font-mono text-[13px] text-white" title={p.model}>
                      {p.model || "—"}
                    </div>
                  </td>
                  <td className={tdCls}>
                    <span className="text-zinc-400">{p.unit || "—"}</span>
                  </td>
                  <td className={tdCls}>
                    <span className="whitespace-nowrap font-mono text-[13px]">{fmtPrice(p.usd_per_unit)}</span>
                  </td>
                  <td className={tdCls}>
                    <span className="whitespace-nowrap font-mono text-[13px]">{fmtPrice(p.usd_per_call)}</span>
                  </td>
                  <td className={tdCls}>{p.has_price ? <Badge tone="ok">есть</Badge> : <Badge tone="bad">нет</Badge>}</td>
                  <td className={tdCls}>{fmtNum(p.calls_30d)}</td>
                  <td className={tdCls}>
                    {(p.unpriced_30d ?? 0) > 0 ? (
                      <span className="text-red-300/90">{fmtNum(p.unpriced_30d)}</span>
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
  );
}
