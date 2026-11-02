"use client";

/**
 * Вкладка «Обзор» админ-панели VIA: сводные цифры, мини-графики за период,
 * топ пользователей и моделей, состояние источников данных.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  BarChart3,
  Coins,
  CreditCard,
  Database,
  Images,
  Users,
  UserX,
} from "lucide-react";
import { adminApi, type AdminOverview } from "@/lib/admin-api";
import {
  Badge,
  Btn,
  Card,
  Empty,
  ErrorBox,
  MiniBars,
  Seg,
  Skel,
  Stat,
  fmtDateTime,
  fmtNum,
  fmtRub,
  fmtUsd,
  tableCls,
  tableWrap,
  tdCls,
  thCls,
} from "./admin-kit";

const PERIODS: { v: number; t: string }[] = [
  { v: 7, t: "7 дней" },
  { v: 30, t: "30 дней" },
  { v: 90, t: "90 дней" },
];

const SOURCES: { key: keyof AdminOverview["sources"]; label: string }[] = [
  { key: "users_db", label: "База пользователей" },
  { key: "tracker_db", label: "API Tracker" },
  { key: "state_db", label: "Снапшоты студии" },
  { key: "cloud", label: "Облако (CDN)" },
];

const shortDate = (s: string) => {
  if (s.length >= 10) {
    const parts = s.slice(0, 10).split("-");
    if (parts.length === 3) return `${parts[2]}.${parts[1]}`;
    return s.slice(5);
  }
  return s;
};

export function AdminOverviewTab() {
  const [days, setDays] = useState(30);
  const q = useQuery({
    queryKey: ["admin", "overview", days],
    queryFn: () => adminApi.overview(days),
  });
  const o = q.data;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-[13px] text-zinc-500">Период</span>
        <div className="w-[300px]">
          <Seg value={days} options={PERIODS} onChange={setDays} />
        </div>
        <div className="flex-1" />
        {o ? (
          <span className="text-[12px] text-zinc-600">обновлено {fmtDateTime(o.generated_at)}</span>
        ) : null}
      </div>

      {q.isError ? <ErrorBox err={q.error} retry={() => void q.refetch()} /> : null}

      {q.isLoading ? (
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
              <Skel key={i} h={92} />
            ))}
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            {[0, 1, 2, 3].map((i) => (
              <Skel key={i} h={190} />
            ))}
          </div>
          <Skel h={240} />
        </div>
      ) : !o ? (
        <Empty text="Нет данных обзора" action={<Btn onClick={() => void q.refetch()}>Обновить</Btn>} />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat
              label="Пользователи"
              value={fmtNum(o.users.total)}
              hint={`+${fmtNum(o.users.new_period)} новых за ${fmtNum(o.period_days)} дн`}
            />
            <Stat
              label="Активных за 7 дней"
              value={fmtNum(o.users.active_7d)}
              hint={`админов: ${fmtNum(o.users.admins)} · блокировано: ${fmtNum(o.users.blocked)}`}
              tone="ok"
            />
            <Stat
              label="Проекты"
              value={fmtNum(o.projects.total)}
              hint={`${fmtNum(o.projects.active)} в работе · ${fmtNum(o.projects.done)} готово`}
            />
            <Stat
              label="Генераций за период"
              value={fmtNum(o.generations.period)}
              hint={`всего ${fmtNum(o.generations.total)} · финалов ${fmtNum(o.generations.finals)}`}
            />
            <Stat
              label="Запросов за период"
              value={fmtNum(o.requests.period_calls)}
              hint={o.sources.tracker_db ? `всего ${fmtNum(o.requests.total)}` : "API Tracker недоступен"}
              tone={o.sources.tracker_db ? undefined : "warn"}
            />
            <Stat
              label="Расход за период"
              value={fmtUsd(o.requests.period_cost_usd)}
              hint={`ошибок: ${fmtNum(o.requests.period_errors)}`}
              tone={o.requests.period_errors > 0 ? "warn" : undefined}
            />
            <Stat
              label="Баланс кредитов у клиентов"
              value={fmtNum(o.credits.balance_total)}
              hint={`выдано ${fmtNum(o.credits.issued)} · списано ${fmtNum(o.credits.spent)}`}
            />
            <Stat
              label="Доход по подпискам (MRR)"
              value={fmtRub(o.subscriptions.mrr_monthly_rub)}
              hint={`в месяц · ${fmtNum(o.subscriptions.active)} активных · ${fmtNum(o.subscriptions.expiring_7d)} истекают`}
              extra={
                o.subscriptions.mrr_rub > o.subscriptions.mrr_monthly_rub
                  ? `Сумма по активным подпискам: ${fmtRub(o.subscriptions.mrr_rub)}`
                  : undefined
              }
            />
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <Card title={`Генерации · ${days} дн`} icon={<Images className="h-4 w-4" />}>
              {o.series.some((p) => p.generations > 0) ? (
                <MiniBars
                  points={o.series.map((p) => ({
                    label: shortDate(p.date),
                    value: p.generations,
                    hint: p.date,
                  }))}
                  height={120}
                  format={fmtNum}
                />
              ) : (
                <Empty text="Нет генераций за период" />
              )}
            </Card>

            <Card title={`Запросы · ${days} дн`} icon={<Activity className="h-4 w-4" />}>
              {o.sources.tracker_db ? (
                o.series.some((p) => p.requests > 0) ? (
                  <MiniBars
                    points={o.series.map((p) => ({
                      label: shortDate(p.date),
                      value: p.requests,
                      hint: p.date,
                    }))}
                    height={120}
                    format={fmtNum}
                    accent="#38bdf8"
                  />
                ) : (
                  <Empty text="Нет запросов за период" />
                )
              ) : (
                <Empty text="API Tracker недоступен — статистика запросов не собирается" />
              )}
            </Card>

            <Card title={`Расход · ${days} дн`} icon={<CreditCard className="h-4 w-4" />}>
              {o.series.some((p) => p.cost_usd > 0) ? (
                <MiniBars
                  points={o.series.map((p) => ({
                    label: shortDate(p.date),
                    value: p.cost_usd,
                    hint: p.date,
                  }))}
                  height={120}
                  format={(n) => fmtUsd(n)}
                  accent="#fbbf24"
                />
              ) : (
                <Empty text="Нет расходов за период" />
              )}
            </Card>

            <Card title={`Списания кредитов · ${days} дн`} icon={<Coins className="h-4 w-4" />}>
              {o.series.some((p) => p.credits_spent > 0) ? (
                <MiniBars
                  points={o.series.map((p) => ({
                    label: shortDate(p.date),
                    value: p.credits_spent,
                    hint: p.date,
                  }))}
                  height={120}
                  format={fmtNum}
                  accent="#a78bfa"
                />
              ) : (
                <Empty text="Нет списаний за период" />
              )}
            </Card>
          </div>

          <div className="grid gap-3 xl:grid-cols-2">
            <Card
              title="Топ пользователей"
              icon={<Users className="h-4 w-4" />}
              right={<span className="text-[12px] text-zinc-500">{o.top_users.length} в списке</span>}
            >
              {o.top_users.length ? (
                <div className={tableWrap}>
                  <table className={tableCls}>
                    <thead>
                      <tr>
                        <th className={thCls}>Пользователь</th>
                        <th className={thCls}>Генерации</th>
                        <th className={thCls}>Запросы</th>
                        <th className={thCls}>Расход</th>
                        <th className={thCls}>Баланс</th>
                      </tr>
                    </thead>
                    <tbody>
                      {o.top_users.map((u) => (
                        <tr key={u.user_id}>
                          <td className={tdCls}>
                            <div className="text-white">{u.name || u.username || `#${u.user_id}`}</div>
                            <div className="text-[12px] text-zinc-500">@{u.username || "—"}</div>
                          </td>
                          <td className={tdCls}>{fmtNum(u.generations)}</td>
                          <td className={tdCls}>{fmtNum(u.requests)}</td>
                          <td className={tdCls}>{fmtUsd(u.cost_usd)}</td>
                          <td className={tdCls + (u.balance_credits < 0 ? " text-red-400" : "")}>
                            {fmtNum(u.balance_credits)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <Empty text="Пока нет активности пользователей" />
              )}
            </Card>

            <Card
              title="Топ моделей"
              icon={<BarChart3 className="h-4 w-4" />}
              right={<span className="text-[12px] text-zinc-500">{o.models.length} в списке</span>}
            >
              {o.models.length ? (
                <div className={tableWrap}>
                  <table className={tableCls}>
                    <thead>
                      <tr>
                        <th className={thCls}>Провайдер</th>
                        <th className={thCls}>Модель</th>
                        <th className={thCls}>Вызовы</th>
                        <th className={thCls}>Расход</th>
                      </tr>
                    </thead>
                    <tbody>
                      {o.models.map((m) => (
                        <tr key={`${m.provider}-${m.model}`}>
                          <td className={tdCls}>{m.provider || "—"}</td>
                          <td className={tdCls}>
                            <span className="font-mono text-[13px] text-white">{m.model}</span>
                          </td>
                          <td className={tdCls}>{fmtNum(m.calls)}</td>
                          <td className={tdCls}>{fmtUsd(m.cost_usd)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <Empty text="Нет вызовов моделей за период" />
              )}
            </Card>
          </div>

          <div className="grid gap-3 xl:grid-cols-2">
            <Card
              title="Без владельца"
              icon={<UserX className="h-4 w-4" />}
              right={<span className="text-[12px] text-zinc-500">не привязано к аккаунтам</span>}
            >
              <div className="grid grid-cols-2 gap-2">
                <Stat label="Проекты" value={fmtNum(o.unattributed.projects)} />
                <Stat label="Генерации" value={fmtNum(o.unattributed.generations)} />
                <Stat label="Запросы" value={fmtNum(o.unattributed.requests)} />
                <Stat label="Расход" value={fmtUsd(o.unattributed.cost_usd)} />
              </div>
              <div className="mt-2 text-[12px] text-zinc-600">
                Старые проекты и запросы без owner_id — их расходы не попадают в карточки пользователей.
              </div>
            </Card>

            <Card title="Источники данных" icon={<Database className="h-4 w-4" />}>
              <div className="flex flex-wrap gap-2">
                {SOURCES.map((s) => {
                  const ok = o.sources[s.key];
                  return (
                    <div key={s.key} className="flex items-center gap-2 rounded-[10px] bg-black/25 px-3 py-2">
                      <span className="text-[13px] text-zinc-300">{s.label}</span>
                      <Badge tone={ok ? "ok" : "neutral"}>{ok ? "доступно" : "нет данных"}</Badge>
                    </div>
                  );
                })}
              </div>
              {!o.sources.tracker_db ? (
                <div className="mt-2 text-[12px] text-amber-400/90">
                  API Tracker не отвечает — данные запросов, расходов и моделей могут быть неполными. Проверь путь к
                  tracker-базе во вкладке «Настройки».
                </div>
              ) : null}
              {!o.sources.cloud ? (
                <div className="mt-2 text-[12px] text-amber-400/90">
                  Облачная синхронизация не подключена — расходы с других ПК не видны.
                </div>
              ) : null}
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
