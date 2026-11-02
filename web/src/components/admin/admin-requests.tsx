"use client";

/**
 * Вкладка «Запросы» админ-панели VIA: журнал вызовов моделей из трекера —
 * сводка, распределение по провайдерам/моделям/пользователям и таблица.
 */
import { useEffect, useState, type ReactNode } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { adminApi, type AdminRequestsSummary } from "@/lib/admin-api";
import {
  Badge,
  Card,
  Empty,
  ErrorBox,
  Field,
  MiniBars,
  Pager,
  Seg,
  Select,
  Skel,
  Stat,
  TextInput,
  fmtNum,
  fmtDateTime,
  fmtUsd,
  tableCls,
  tableWrap,
  tdCls,
  thCls,
} from "@/components/admin/admin-kit";

const LIMIT = 50;

/** Стоимость: до 6 знаков после запятой для копеечных сумм. */
const fmtCost = (n: number) =>
  n > 0 && n < 0.01 ? `$${n.toFixed(6)}` : fmtUsd(n, 2);

type Filters = {
  days: number;
  status: string;
  provider: string;
  model: string;
  user: string;
  /** Аккаунт users.db: фильтр по привязке трекера (account_id). */
  accountId: number | null;
};

type TextFilters = {
  provider: string;
  model: string;
  user: string;
  q: string;
};

const EMPTY_TEXT: TextFilters = { provider: "", model: "", user: "", q: "" };

function UserCell({ ownerName, userName, deviceName }: { ownerName: string; userName: string; deviceName: string }) {
  const second = [userName, deviceName].filter(Boolean).join("@");
  return (
    <div className="max-w-[220px]">
      <div className="truncate text-zinc-200" title={ownerName || userName}>
        {ownerName || userName || "—"}
      </div>
      {second ? (
        <div className="truncate text-[12px] text-zinc-600" title={second}>
          {second}
        </div>
      ) : null}
    </div>
  );
}

function DistributionCard({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <Card title={title} className="min-w-0">
      {children}
    </Card>
  );
}

function ProvidersBlock({ summary }: { summary: AdminRequestsSummary }) {
  const providers = summary.by_provider ?? [];
  if (providers.length === 0) return <Empty text="Нет данных за период." />;
  return (
    <>
      <MiniBars
        points={providers.map((p) => ({ label: p.provider || "—", value: p.calls, hint: fmtCost(p.cost_usd) }))}
        height={100}
      />
      <div className="mt-2 space-y-1 text-[12px]">
        {providers.map((p) => (
          <div key={p.provider || "unknown"} className="flex items-center gap-2">
            <span className="truncate text-zinc-300">{p.provider || "—"}</span>
            <div className="flex-1" />
            <span className="text-zinc-500">{fmtNum(p.calls)} выз.</span>
            <span className="w-[84px] text-right font-mono text-zinc-500">{fmtCost(p.cost_usd)}</span>
          </div>
        ))}
      </div>
    </>
  );
}

export function AdminRequestsTab() {
  const [filters, setFilters] = useState<Filters>({ days: 30, status: "", provider: "", model: "", user: "", accountId: null });
  const [text, setText] = useState<TextFilters>(EMPTY_TEXT);
  const [textDebounced, setTextDebounced] = useState<TextFilters>(EMPTY_TEXT);
  const [offset, setOffset] = useState(0);

  // Варианты для фильтра «по аккаунту»: до 200 пользователей, свежие сверху.
  const accounts = useQuery({
    queryKey: ["admin", "users", "filter-options"],
    queryFn: () => adminApi.users({ limit: 200, sort: "activity" }),
    staleTime: 60_000,
  });

  useEffect(() => {
    const t = setTimeout(() => {
      setTextDebounced({
        provider: text.provider.trim(),
        model: text.model.trim(),
        user: text.user.trim(),
        q: text.q.trim(),
      });
    }, 400);
    return () => clearTimeout(t);
  }, [text]);

  useEffect(() => {
    setOffset(0);
  }, [textDebounced, filters]);

  const patch = (p: Partial<Filters>) => {
    // offset сбрасывает эффект ниже — второй сброс здесь только плодил лишний рендер.
    setFilters((prev) => ({ ...prev, ...p }));
  };

  const {
    data,
    isLoading,
    isFetching,
    error,
    refetch,
  } = useQuery({
    queryKey: ["admin", "requests", filters, textDebounced, offset],
    queryFn: () =>
      adminApi.requests({
        days: filters.days,
        status: filters.status || undefined,
        provider: textDebounced.provider || undefined,
        model: textDebounced.model || undefined,
        user: textDebounced.user || undefined,
        account_id: filters.accountId ?? undefined,
        q: textDebounced.q || undefined,
        limit: LIMIT,
        offset,
      }),
    // При смене фильтров не мигаем пустотой — держим прошлую страницу до ответа.
    placeholderData: keepPreviousData,
  });

  // Сводка приходит в том же ответе /requests — отдельный запрос не нужен.
  const summary = data?.summary;
  const summaryLoading = isLoading || Boolean(error);
  const items = data?.items ?? [];

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <Stat
          label="Всего запросов"
          value={summaryLoading ? "…" : fmtNum(summary?.total)}
          hint={filters.days ? `за ${filters.days} дн` : "за всё время"}
        />
        <Stat
          label="Расход"
          value={summaryLoading ? "…" : fmtUsd(summary?.cost_usd, 2)}
          hint="по трекеру, $"
          tone={(summary?.cost_usd ?? 0) > 0 ? "ok" : undefined}
        />
        <Stat
          label="Ошибки"
          value={summaryLoading ? "…" : fmtNum(summary?.errors)}
          hint={
            summary?.total
              ? `${((summary.errors / summary.total) * 100).toFixed(1)}% от запросов`
              : undefined
          }
          tone={(summary?.errors ?? 0) > 0 ? "bad" : undefined}
        />
        <Stat
          label="Средняя длительность"
          value={summaryLoading ? "…" : `${(summary?.avg_duration ?? 0).toFixed(2)} с`}
          hint="на один запрос"
        />
      </div>

      {error ? null : (
      <div className="grid gap-3 lg:grid-cols-3">
        <DistributionCard title="Провайдеры">
          {summaryLoading ? <Skel h={120} /> : summary ? <ProvidersBlock summary={summary} /> : <Empty text="Нет сводки." />}
        </DistributionCard>

        <DistributionCard title="Модели (топ-10)">
          {summaryLoading ? (
            <Skel h={120} />
          ) : (summary?.by_model?.length ?? 0) === 0 ? (
            <Empty text="Нет данных за период." />
          ) : (
            <div className={tableWrap}>
              <table className={tableCls}>
                <thead>
                  <tr>
                    <th className={thCls}>Модель</th>
                    <th className={thCls}>Провайдер</th>
                    <th className={thCls}>Вызовы</th>
                    <th className={thCls}>$</th>
                  </tr>
                </thead>
                <tbody>
                  {(summary?.by_model ?? []).slice(0, 10).map((m, i) => (
                    <tr key={`${m.provider}-${m.model}-${i}`}>
                      <td className={tdCls}>
                        <div className="max-w-[220px] truncate" title={m.model}>
                          {m.model || "—"}
                        </div>
                      </td>
                      <td className={tdCls}>{m.provider || "—"}</td>
                      <td className={tdCls}>{fmtNum(m.calls)}</td>
                      <td className={tdCls}>
                        <span className="font-mono text-[13px]">{fmtCost(m.cost_usd)}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </DistributionCard>

        <DistributionCard title="Пользователи">
          {summaryLoading ? (
            <Skel h={120} />
          ) : (summary?.by_user?.length ?? 0) === 0 ? (
            <Empty text="Нет данных за период." />
          ) : (
            <div className={tableWrap}>
              <table className={tableCls}>
                <thead>
                  <tr>
                    <th className={thCls}>Пользователь</th>
                    <th className={thCls}>Вызовы</th>
                    <th className={thCls}>$</th>
                  </tr>
                </thead>
                <tbody>
                  {(summary?.by_user ?? []).map((u, i) => (
                    <tr key={`${u.owner_user_id ?? u.user_name}-${i}`}>
                      <td className={tdCls}>
                        <div className="max-w-[200px] truncate" title={u.owner_name || u.user_name}>
                          {u.owner_name || u.user_name || "—"}
                        </div>
                        {u.owner_name && u.user_name && u.owner_name !== u.user_name ? (
                          <div className="truncate text-[12px] text-zinc-600">{u.user_name}</div>
                        ) : null}
                      </td>
                      <td className={tdCls}>{fmtNum(u.calls)}</td>
                      <td className={tdCls}>
                        <span className="font-mono text-[13px]">{fmtCost(u.cost_usd)}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </DistributionCard>
      </div>
      )}

      <Card title="Фильтры">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-6">
          <div className="lg:col-span-2">
            <Field label="Период">
              <Seg
                value={filters.days}
                options={[
                  { v: 7, t: "7 дн" },
                  { v: 30, t: "30 дн" },
                  { v: 90, t: "90 дн" },
                  { v: 0, t: "Всё" },
                ]}
                onChange={(v) => patch({ days: v })}
              />
            </Field>
          </div>
          <div className="sm:col-span-2 lg:col-span-2">
            <Field label="Аккаунт">
              <Select
                value={filters.accountId === null ? "" : String(filters.accountId)}
                onChange={(e) => patch({ accountId: e.target.value ? Number(e.target.value) : null })}
              >
                <option value="">Все аккаунты</option>
                {(accounts.data?.items ?? []).map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name || u.username || `#${u.id}`} · @{u.username || "—"} (#{u.id})
                  </option>
                ))}
              </Select>
            </Field>
            {accounts.isError ? (
              <div className="mt-1 flex items-center gap-2 text-[12px] text-red-300">
                Не удалось загрузить аккаунты
                <button
                  type="button"
                  onClick={() => void accounts.refetch()}
                  className="underline transition-colors hover:text-red-200"
                >
                  Повторить
                </button>
              </div>
            ) : accounts.isLoading ? (
              <div className="mt-1 text-[12px] text-zinc-600">Загружаем аккаунты…</div>
            ) : (
              <div className="mt-1 text-[12px] text-zinc-600">
                {accounts.data && accounts.data.total > 200
                  ? `Показаны первые 200 из ${fmtNum(accounts.data.total)} аккаунтов`
                  : "До 200 аккаунтов по активности"}
              </div>
            )}
          </div>
          <Field label="Статус">
            <Select value={filters.status} onChange={(e) => patch({ status: e.target.value })}>
              <option value="">Все</option>
              <option value="ok">ok</option>
              <option value="error">error</option>
              <option value="blocked">Отказы (401/403)</option>
            </Select>
          </Field>
          <Field label="Провайдер">
            <TextInput
              value={text.provider}
              onChange={(e) => setText((p) => ({ ...p, provider: e.target.value }))}
              placeholder="openrouter"
            />
          </Field>
          <Field label="Модель">
            <TextInput
              value={text.model}
              onChange={(e) => setText((p) => ({ ...p, model: e.target.value }))}
              placeholder="gpt-..."
            />
          </Field>
          <Field label="Пользователь" hint="identity в трекере">
            <TextInput
              value={text.user}
              onChange={(e) => setText((p) => ({ ...p, user: e.target.value }))}
              placeholder="user@device"
            />
          </Field>
          <div className="sm:col-span-2 lg:col-span-6">
            <Field label="Поиск">
              <TextInput
                value={text.q}
                onChange={(e) => setText((p) => ({ ...p, q: e.target.value }))}
                placeholder="Модель, тип вызова, текст ошибки…"
              />
            </Field>
          </div>
        </div>
      </Card>

      <Card
        title="Запросы"
        right={
          <span className="text-[13px] text-zinc-500">
            Всего {fmtNum(data?.total)}
            {isFetching && !isLoading ? " · обновляем…" : ""}
          </span>
        }
      >
        {isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skel key={i} h={34} />
            ))}
          </div>
        ) : error ? (
          <ErrorBox err={error} retry={() => void refetch()} />
        ) : items.length === 0 ? (
          <Empty text="Запросов не найдено. Попробуй изменить фильтры." />
        ) : (
          <div className={tableWrap}>
            <table className={tableCls}>
              <thead>
                <tr>
                  <th className={thCls}>Время</th>
                  <th className={thCls}>Пользователь</th>
                  <th className={thCls}>Провайдер</th>
                  <th className={thCls}>Модель</th>
                  <th className={thCls}>Тип</th>
                  <th className={thCls}>Токены</th>
                  <th className={thCls}>Сек</th>
                  <th className={thCls}>Стоимость</th>
                  <th className={thCls}>Статус</th>
                  <th className={thCls}>Источник</th>
                  <th className={thCls}>Ошибка</th>
                </tr>
              </thead>
              <tbody>
                {items.map((r) => (
                  <tr key={r.id}>
                    <td className={tdCls}>
                      <div className="whitespace-nowrap">{fmtDateTime(r.ts)}</div>
                    </td>
                    <td className={tdCls}>
                      <UserCell ownerName={r.owner_name} userName={r.user_name} deviceName={r.device_name} />
                    </td>
                    <td className={tdCls}>
                      <div className="max-w-[130px] truncate" title={r.provider}>
                        {r.provider || "—"}
                      </div>
                    </td>
                    <td className={tdCls}>
                      <div className="max-w-[200px] truncate" title={r.model}>
                        {r.model || "—"}
                      </div>
                    </td>
                    <td className={tdCls}>
                      <div className="max-w-[140px] truncate" title={r.call_type}>
                        {r.call_type || "—"}
                      </div>
                    </td>
                    <td className={tdCls}>
                      <span className="whitespace-nowrap">{fmtNum(r.total_tokens)}</span>
                    </td>
                    <td className={tdCls}>
                      <span className="whitespace-nowrap">{(r.duration_sec ?? 0).toFixed(2)}</span>
                    </td>
                    <td className={tdCls}>
                      <span className="whitespace-nowrap font-mono text-[13px]">{fmtCost(r.cost_usd)}</span>
                    </td>
                    <td className={tdCls}>
                      <span title={r.status_code === 0 ? "Нет ответа от провайдера" : `HTTP ${r.status_code}`}>
                        <Badge tone={r.status_code === 0 || r.status_code >= 400 ? "bad" : "ok"}>
                          {r.status_code === 0 ? "нет ответа" : r.status_code}
                        </Badge>
                      </span>
                    </td>
                    <td className={tdCls}>
                      <div className="max-w-[120px] truncate" title={r.project_source}>
                        {r.project_source || "—"}
                      </div>
                    </td>
                    <td className={tdCls}>
                      <div
                        className="max-w-[220px] truncate text-[12px] text-red-300/80"
                        title={r.error || undefined}
                      >
                        {r.error || "—"}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {data ? (
          <Pager total={data.total} limit={data.limit || LIMIT} offset={data.offset} onChange={setOffset} />
        ) : null}
      </Card>
    </div>
  );
}
