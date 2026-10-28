"use client";

/**
 * Вкладка «Пользователи» админ-панели VIA: таблица с поиском и фильтрами,
 * выдвижная карточка пользователя (подписка, кредиты, настройки, история,
 * проекты, аудит) и все мутации.
 */
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Ban,
  Coins,
  CreditCard,
  ExternalLink,
  History,
  Monitor,
  RefreshCw,
  ScrollText,
  Search,
  Settings2,
  ShieldAlert,
  Trash2,
  UserPlus,
  X,
} from "lucide-react";
import { toast } from "sonner";
import {
  LEDGER_KIND_RU,
  adminApi,
  type AdminLedgerEntry,
  type AdminPlan,
  type AdminSubscription,
  type AdminUserDetail,
  type AdminUserProject,
  type AdminUserRow,
} from "@/lib/admin-api";
import {
  A,
  Badge,
  Btn,
  Card,
  Drawer,
  Empty,
  ErrorBox,
  Field,
  Pager,
  Seg,
  Select,
  Skel,
  Stat,
  TextInput,
  Toggle,
  fmtDate,
  fmtDateTime,
  fmtNum,
  fmtUsd,
  tableCls,
  tableWrap,
  tdCls,
  thCls,
  timeAgo,
} from "./admin-kit";

const LIMIT = 25;
const DRAWER_WIDTH = 660;
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
/** Как на бэке (регистрация): латиница, цифры, _ . -; 3–32 символа. */
const USERNAME_RE = /^[a-zA-Z0-9_.-]{3,32}$/;

type StatusFilter = "all" | "active" | "blocked";
type SortKey = "activity" | "balance" | "generations" | "spend";

const STATUS_OPTS: { v: StatusFilter; t: string }[] = [
  { v: "all", t: "Все" },
  { v: "active", t: "Активные" },
  { v: "blocked", t: "Блок" },
];

const SORT_OPTS: { v: SortKey; t: string }[] = [
  { v: "activity", t: "Активность" },
  { v: "balance", t: "Баланс" },
  { v: "generations", t: "Генерации" },
  { v: "spend", t: "Расход" },
];

type UserPatch = Partial<{
  plan: string | null;
  role: string;
  status: string;
  limits: Record<string, number | string>;
  tracker_identity: string;
  device_name: string;
  note: string;
}>;

const SUB_STATUS: Record<string, { t: string; tone: "ok" | "warn" | "bad" | "info" | "neutral" }> = {
  active: { t: "активна", tone: "ok" },
  replaced: { t: "заменена", tone: "info" },
  cancelled: { t: "отменена", tone: "neutral" },
  expired: { t: "истекла", tone: "bad" },
};

/** Ключ формы настроек: при смене серверных полей форма пересоздаётся с чистым draft. */
const settingsSyncKey = (u: AdminUserDetail) =>
  [
    u.id,
    u.plan ?? "",
    u.role,
    u.status,
    u.tracker_identity ?? "",
    u.device_name ?? "",
    u.note ?? "",
    JSON.stringify(u.limits ?? {}),
    u.subscription?.id ?? 0,
  ].join("|");

const PROJECT_STATUS_RU: Record<string, string> = {
  new: "Новый",
  in_progress: "В работе",
  done: "Готов",
  failed: "Ошибка",
  published: "Опубликован",
};

const projectStatus = (s: string) => PROJECT_STATUS_RU[s] ?? s.replace(/_/g, " ");

/** unix-время в ISO: бэкенд может отдавать и секунды, и миллисекунды. */
const tsIso = (n: number | null | undefined): string | null => {
  if (!n) return null;
  const ms = n > 1e12 ? n : n * 1000;
  const d = new Date(ms);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
};

const planLabel = (p: AdminPlan) => `${p.name} · ${fmtNum(p.credits)} cr`;

const adminPredicate = (q: { queryKey: readonly unknown[] }) =>
  Array.isArray(q.queryKey) && q.queryKey[0] === "admin";

function openProject(id: number) {
  try {
    localStorage.setItem("vp-studio-selected-project-id", String(id));
  } catch {
    /* localStorage может быть недоступен */
  }
  window.location.href = "/";
}

/* ─────────────────────────── вкладка ─────────────────────────── */

export function AdminUsersTab() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [sort, setSort] = useState<SortKey>("activity");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<number | string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const list = useQuery({
    queryKey: ["admin", "users", { q: search.trim(), status, sort, limit: LIMIT, offset }],
    queryFn: () =>
      adminApi.users({
        q: search.trim() || undefined,
        status: status === "all" ? undefined : status,
        sort,
        limit: LIMIT,
        offset,
      }),
    placeholderData: keepPreviousData,
  });

  const rows = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  const filtered = Boolean(search.trim()) || status !== "all";

  const refresh = () => {
    // invalidateQueries перезапрашивает активные запросы — и карточку, и список.
    void qc.invalidateQueries({ predicate: adminPredicate });
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[260px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-600" />
          <TextInput
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setOffset(0);
            }}
            placeholder="Поиск: имя, @username, email"
            className="pl-9"
          />
        </div>
        <div className="w-[300px]">
          <Seg
            value={status}
            options={STATUS_OPTS}
            onChange={(v) => {
              setStatus(v);
              setOffset(0);
            }}
          />
        </div>
        <div className="w-[440px]">
          <Seg
            value={sort}
            options={SORT_OPTS}
            onChange={(v) => {
              setSort(v);
              setOffset(0);
            }}
          />
        </div>
        <div className="ml-auto">
          <Btn accent onClick={() => setCreateOpen(true)}>
            <UserPlus className="h-4 w-4" />
            Создать пользователя
          </Btn>
        </div>
      </div>

      <Card
        title="Пользователи"
        icon={<Search className="h-4 w-4" />}
        right={
          <span className="text-[12px] text-zinc-500">
            {list.isLoading ? "загрузка…" : `${fmtNum(total)} всего`}
          </span>
        }
      >
        {list.isError ? (
          <ErrorBox err={list.error} retry={() => void list.refetch()} />
        ) : list.isLoading ? (
          <div className="flex flex-col gap-1.5">
            {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
              <Skel key={i} h={52} />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <Empty
            text={filtered ? "Ничего не найдено — измени поиск или фильтр" : "Пользователей пока нет"}
            action={
              filtered ? (
                <Btn
                  onClick={() => {
                    setSearch("");
                    setStatus("all");
                    setOffset(0);
                  }}
                >
                  Сбросить фильтры
                </Btn>
              ) : undefined
            }
          />
        ) : (
          <>
            <div className={tableWrap} style={{ opacity: list.isPlaceholderData ? 0.55 : 1 }}>
              <table className={tableCls}>
                <thead>
                  <tr>
                    <th className={thCls}>Пользователь</th>
                    <th className={thCls}>План / подписка</th>
                    <th className={thCls}>Баланс</th>
                    <th className={thCls}>Генерации</th>
                    <th className={thCls}>Проекты</th>
                    <th className={thCls}>Запросы</th>
                    <th className={thCls}>Расход</th>
                    <th className={thCls}>Активность</th>
                    <th className={thCls} />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((u) => (
                    <UserRow key={u.id} u={u} onOpen={() => setSelected(u.id)} />
                  ))}
                </tbody>
              </table>
            </div>
            <Pager total={total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        )}
      </Card>

      <UserDrawer userId={selected} onClose={() => setSelected(null)} onChanged={refresh} />

      <CreateUserModal open={createOpen} onClose={() => setCreateOpen(false)} onCreated={refresh} />
    </div>
  );
}

/* ─────────────────────────── строка таблицы ─────────────────────────── */

function UserRow({ u, onOpen }: { u: AdminUserRow; onOpen: () => void }) {
  const planName = u.subscription?.plan_name ?? u.plan ?? "—";
  const expired = !u.plan_active && Boolean(u.plan_expires_at);

  return (
    <tr
      onClick={onOpen}
      title="Открыть карточку пользователя"
      className="cursor-pointer transition-colors hover:bg-white/[0.035]"
    >
      <td className={tdCls}>
        <div className="text-white">{u.name || u.username || `#${u.id}`}</div>
        <div className="text-[12px] text-zinc-500">
          @{u.username || "—"} · {u.email || "—"}
        </div>
        <div className="mt-1 flex flex-wrap gap-1">
          <Badge tone={u.email_verified ? "ok" : "warn"}>{u.email_verified ? "email ✓" : "email ?"}</Badge>
          <Badge tone={u.role === "admin" ? "info" : "neutral"}>{u.role}</Badge>
          {u.status === "blocked" ? <Badge tone="bad">blocked</Badge> : null}
        </div>
      </td>
      <td className={tdCls}>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-zinc-200">{planName}</span>
          {u.plan_active ? (
            <Badge tone="ok">active</Badge>
          ) : expired ? (
            <Badge tone="bad">expired</Badge>
          ) : (
            <Badge tone="neutral">нет</Badge>
          )}
        </div>
        <div className="text-[12px] text-zinc-500">
          {u.plan_expires_at ? `до ${fmtDate(u.plan_expires_at)}` : "без подписки"}
        </div>
      </td>
      <td className={tdCls + " whitespace-nowrap " + (u.balance_credits < 0 ? "text-red-400" : "text-white")}>
        {fmtNum(u.balance_credits)}
      </td>
      <td className={tdCls}>{fmtNum(u.generations)}</td>
      <td className={tdCls}>{fmtNum(u.projects)}</td>
      <td className={tdCls}>{fmtNum(u.requests)}</td>
      <td className={tdCls}>{fmtUsd(u.cost_usd)}</td>
      <td className={tdCls}>
        <div className="whitespace-nowrap text-zinc-300" title={fmtDateTime(u.last_activity)}>
          {timeAgo(u.last_activity)}
        </div>
        <div className="text-[12px] text-zinc-600">создан {fmtDate(tsIso(u.created_at))}</div>
      </td>
      <td className={tdCls}>
        <span className="whitespace-nowrap text-[13px]" style={{ color: A }}>
          Открыть →
        </span>
      </td>
    </tr>
  );
}

/* ─────────────────────────── drawer ─────────────────────────── */

function UserDrawer({
  userId,
  onClose,
  onChanged,
}: {
  userId: number | string | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const detail = useQuery({
    queryKey: ["admin", "user", userId],
    queryFn: () => adminApi.user(userId!),
    enabled: userId !== null,
  });
  const plans = useQuery({
    queryKey: ["admin", "plans"],
    queryFn: () => adminApi.plans(),
    enabled: userId !== null,
    staleTime: 60_000,
  });
  // Свой id и число админов — для защиты от блокировки/разжалования себя и последнего админа.
  const access = useQuery({
    queryKey: ["admin", "access"],
    queryFn: () => adminApi.access(),
    enabled: userId !== null,
    staleTime: 30_000,
  });
  const overview = useQuery({
    queryKey: ["admin", "overview", 30],
    queryFn: () => adminApi.overview(30),
    enabled: userId !== null,
    staleTime: 30_000,
  });

  if (userId === null) return null;

  const u = detail.data;
  const meId = access.data?.user?.id ?? null;
  const adminsCount = overview.data?.users.admins ?? null;

  return (
    <Drawer
      open
      width={DRAWER_WIDTH}
      title={u ? u.name || u.username || `#${u.id}` : `Пользователь #${userId}`}
      subtitle={u ? `@${u.username || "—"} · ${u.email || "—"}` : "загрузка…"}
      onClose={onClose}
    >
      {detail.isLoading ? (
        <div className="flex flex-col gap-3">
          <Skel h={96} />
          <Skel h={92} />
          <Skel h={150} />
          <Skel h={120} />
        </div>
      ) : detail.isError ? (
        <ErrorBox err={detail.error} retry={() => void detail.refetch()} />
      ) : !u ? (
        <Empty text="Не удалось загрузить пользователя" action={<Btn onClick={() => void detail.refetch()}>Повторить</Btn>} />
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-end">
            <Btn onClick={onChanged} title="Обновить карточку">
              <RefreshCw className="h-4 w-4" />
              Обновить
            </Btn>
          </div>

          <IdentityBlock u={u} />
          <StatsBlock u={u} />
          <SubscriptionBlock u={u} onChanged={onChanged} />
          <SubscriptionHistoryBlock items={u.subscriptions_history} />
          <AccrualBlock userId={u.id} onChanged={onChanged} />
          <IssueBlock
            userId={u.id}
            plans={plans.data}
            plansLoading={plans.isLoading}
            currentSub={u.subscription}
            onChanged={onChanged}
          />
          <SettingsBlock
            key={settingsSyncKey(u)}
            u={u}
            plans={plans.data}
            meId={meId}
            adminsCount={adminsCount}
            adminsPending={overview.isLoading}
            onChanged={onChanged}
          />
          <LedgerBlock userId={u.id} />
          <SessionsBlock userId={u.id} isSelf={meId !== null && meId === u.id} />
          <Card title="Проекты" icon={<ExternalLink className="h-4 w-4" />}>
            <ProjectsList items={u.projects_list} />
          </Card>
          <Card title="Аудит пользователя" icon={<ScrollText className="h-4 w-4" />}>
            <AuditList items={u.audit} />
          </Card>
          <DangerZone
            u={u}
            meId={meId}
            adminsCount={adminsCount}
            onClose={onClose}
            onChanged={onChanged}
          />
        </div>
      )}
    </Drawer>
  );
}

function IdentityBlock({ u }: { u: AdminUserDetail }) {
  return (
    <div className="rounded-[12px] bg-black/25 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[17px] text-white">{u.name || u.username || `#${u.id}`}</span>
        <span className="text-[13px] text-zinc-500">@{u.username || "—"}</span>
        <Badge tone={u.role === "admin" ? "info" : "neutral"}>{u.role}</Badge>
        <Badge tone={u.status === "active" ? "ok" : "bad"}>
          {u.status === "active" ? "активен" : u.status === "blocked" ? "заблокирован" : u.status}
        </Badge>
        {u.plan ? <Badge tone="ok">{u.plan}</Badge> : <Badge tone="neutral">без тарифа</Badge>}
        <Badge tone={u.email_verified ? "ok" : "warn"}>
          {u.email_verified ? "email подтверждён" : "email не подтверждён"}
        </Badge>
      </div>
      <div className="mt-1 text-[13px] text-zinc-400">{u.email || "—"}</div>
      <div className="mt-1 text-[12px] text-zinc-600">
        создан {fmtDate(tsIso(u.created_at))} · последняя активность {timeAgo(u.last_activity)}
      </div>
      {u.note ? (
        <div className="mt-2 whitespace-pre-wrap rounded-[10px] bg-black/30 px-3 py-2 text-[13px] text-zinc-400">
          {u.note}
        </div>
      ) : null}
    </div>
  );
}

function StatsBlock({ u }: { u: AdminUserDetail }) {
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
      <Stat
        label="Баланс кредитов"
        value={fmtNum(u.balance_credits)}
        tone={u.balance_credits < 0 ? "bad" : undefined}
      />
      <Stat label="Потрачено кредитов" value={fmtNum(u.spent_credits)} />
      <Stat label="Генерации" value={fmtNum(u.generations)} />
      <Stat label="Проекты" value={fmtNum(u.projects)} />
      <Stat label="Запросы" value={fmtNum(u.requests)} />
      <Stat label="Расход" value={fmtUsd(u.cost_usd)} />
    </div>
  );
}

/* ─────────────────────────── подтверждение действия ─────────────────────────── */

function ConfirmAction({
  label,
  confirmText,
  confirmLabel = "Подтвердить",
  busy,
  icon,
  onConfirm,
}: {
  label: string;
  confirmText: string;
  confirmLabel?: string;
  busy: boolean;
  icon?: ReactNode;
  onConfirm: () => void;
}) {
  const [armed, setArmed] = useState(false);
  if (!armed) {
    return (
      <Btn danger disabled={busy} onClick={() => setArmed(true)}>
        {icon}
        {label}
      </Btn>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-[10px] border border-red-500/25 bg-red-500/[0.06] px-3 py-2">
      <ShieldAlert className="h-4 w-4 shrink-0 text-red-300" />
      <span className="min-w-0 flex-1 text-[13px] text-red-300">{confirmText}</span>
      <Btn
        danger
        disabled={busy}
        onClick={() => {
          setArmed(false);
          onConfirm();
        }}
      >
        {busy ? "…" : confirmLabel}
      </Btn>
      <Btn disabled={busy} onClick={() => setArmed(false)}>
        Нет
      </Btn>
    </div>
  );
}

/* ─────────────────────────── подписка ─────────────────────────── */

function SubscriptionBlock({ u, onChanged }: { u: AdminUserDetail; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const sub = u.subscription;
  const expiresTs = sub?.expires_at ? new Date(sub.expires_at).getTime() : null;
  const validTs = expiresTs !== null && !Number.isNaN(expiresTs);
  const expired = validTs && expiresTs < Date.now();
  const expiring = validTs && !expired && expiresTs - Date.now() < 7 * 24 * 60 * 60 * 1000;
  const isActive = sub?.status === "active" && !expired;

  const cancel = async () => {
    setBusy(true);
    try {
      await adminApi.cancelSubscription(u.id);
      toast.success("Подписка отменена");
      onChanged();
    } catch (err) {
      toast.error((err as Error).message || "Не удалось отменить подписку");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Подписка" icon={<CreditCard className="h-4 w-4" />}>
      {!sub ? (
        <Empty text="Подписка не выдана" />
      ) : (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[15px] text-white">{sub.plan_name}</span>
            <Badge tone={isActive ? "ok" : sub.status === "cancelled" ? "neutral" : "bad"}>
              {isActive ? "активна" : sub.status === "cancelled" ? "отменена" : "истекла"}
            </Badge>
            {sub.auto_renew ? <Badge tone="info">автопродление</Badge> : <Badge tone="neutral">без автопродления</Badge>}
          </div>
          <div className="grid grid-cols-2 gap-2 text-[13px]">
            <div className="rounded-[10px] bg-black/25 px-3 py-2">
              <div className="text-zinc-500">Действует до</div>
              <div
                className={
                  expired
                    ? "text-red-400"
                    : expiring
                      ? "text-amber-400"
                      : "text-zinc-200"
                }
              >
                {sub.expires_at ? fmtDateTime(sub.expires_at) : "бессрочно"}
                {expiring ? <span className="ml-1 text-[12px] text-amber-400">истекает скоро</span> : null}
              </div>
            </div>
            <div className="rounded-[10px] bg-black/25 px-3 py-2">
              <div className="text-zinc-500">Начислено кредитов</div>
              <div className="text-zinc-200">{fmtNum(sub.credits_granted)}</div>
            </div>
          </div>
          <div className="text-[12px] text-zinc-600">
            выдана {fmtDate(sub.started_at)} · автор {sub.created_by || "—"}
            {sub.note ? ` · ${sub.note}` : ""}
          </div>
          {isActive ? (
            <div>
              <ConfirmAction
                label="Отменить подписку"
                confirmText="Точно отменить текущую подписку? Доступ к тарифу закроется."
                confirmLabel="Да, отменить"
                busy={busy}
                icon={<Ban className="h-4 w-4" />}
                onConfirm={() => void cancel()}
              />
            </div>
          ) : null}
        </div>
      )}
    </Card>
  );
}

/* ─────────────────────────── история подписок ─────────────────────────── */

function SubscriptionHistoryBlock({ items }: { items: AdminSubscription[] }) {
  return (
    <Card
      title="История подписок"
      icon={<History className="h-4 w-4" />}
      right={<span className="text-[12px] text-zinc-500">{fmtNum(items.length)} записей</span>}
    >
      {items.length === 0 ? (
        <Empty text="Подписок ещё не выдавали" />
      ) : (
        <div className={tableWrap}>
          <table className={tableCls}>
            <thead>
              <tr>
                <th className={thCls}>Тариф</th>
                <th className={thCls}>Статус</th>
                <th className={thCls}>Начало</th>
                <th className={thCls}>Действует до</th>
                <th className={thCls}>Кредиты</th>
                <th className={thCls}>Автопродление</th>
                <th className={thCls}>Комментарий</th>
              </tr>
            </thead>
            <tbody>
              {items.map((s) => {
                const st = SUB_STATUS[s.status] ?? { t: s.status || "—", tone: "neutral" as const };
                return (
                  <tr key={s.id}>
                    <td className={tdCls}>
                      <div className="text-white">{s.plan_name || s.plan_id}</div>
                      <div className="text-[12px] text-zinc-500">#{s.id}</div>
                    </td>
                    <td className={tdCls}>
                      <Badge tone={st.tone}>{st.t}</Badge>
                    </td>
                    <td className={tdCls + " whitespace-nowrap"}>{fmtDate(s.started_at)}</td>
                    <td className={tdCls + " whitespace-nowrap"}>
                      {s.expires_at ? fmtDate(s.expires_at) : "бессрочно"}
                    </td>
                    <td className={tdCls}>+{fmtNum(s.credits_granted)}</td>
                    <td className={tdCls}>{s.auto_renew ? "да" : "нет"}</td>
                    <td className={tdCls}>{s.note || "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/* ─────────────────────────── начисление кредитов ─────────────────────────── */

function AccrualBlock({ userId, onChanged }: { userId: number | string; onChanged: () => void }) {
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return; // Enter во время запроса не должен начислять второй раз
    const value = Number(amount);
    if (!amount.trim() || !Number.isFinite(value) || value === 0) {
      toast.error("Укажи сумму начисления (минус — списание)");
      return;
    }
    if (!reason.trim()) {
      toast.error("Причина обязательна");
      return;
    }
    setBusy(true);
    try {
      const r = await adminApi.accrue(userId, { amount: value, reason: reason.trim() });
      toast.success(`Готово. Баланс: ${fmtNum(r.balance_credits)} кредитов`);
      setAmount("");
      setReason("");
      onChanged();
    } catch (err) {
      toast.error((err as Error).message || "Не удалось начислить кредиты");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Начислить кредиты" icon={<Coins className="h-4 w-4" />}>
      <form className="flex flex-col gap-2.5" onSubmit={(e) => void submit(e)}>
        <div className="grid gap-2.5 md:grid-cols-[160px_1fr]">
          <Field label="Сумма">
            <TextInput
              type="number"
              step="1"
              inputMode="numeric"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="500 или -100"
              disabled={busy}
            />
          </Field>
          <Field label="Причина" hint="Обязательно — попадает в историю начислений и аудит">
            <TextInput
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Например: компенсация за сбой генерации"
              maxLength={400}
              disabled={busy}
            />
          </Field>
        </div>
        <div className="flex items-center gap-2">
          <Btn type="submit" accent disabled={busy}>
            <Coins className="h-4 w-4" />
            {busy ? "Начисляем…" : "Начислить"}
          </Btn>
          <span className="text-[12px] text-zinc-600">Минусовое значение спишет кредиты</span>
        </div>
      </form>
    </Card>
  );
}

/* ─────────────────────────── выдача подписки ─────────────────────────── */

function IssueBlock({
  userId,
  plans,
  plansLoading,
  currentSub,
  onChanged,
}: {
  userId: number | string;
  plans: AdminPlan[] | undefined;
  plansLoading: boolean;
  currentSub: AdminSubscription | null;
  onChanged: () => void;
}) {
  const activePlans = (plans ?? []).filter((p) => p.is_active);
  const [planId, setPlanId] = useState("");
  const [months, setMonths] = useState(1);
  const [credits, setCredits] = useState("");
  const [autoRenew, setAutoRenew] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (planId) return;
    const first = (plans ?? []).find((p) => p.is_active);
    if (first) setPlanId(first.id);
  }, [plans, planId]);

  // Смена тарифа/срока отменяет незавершённое подтверждение замены.
  useEffect(() => setConfirming(false), [planId, months]);

  const picked = activePlans.find((p) => p.id === planId);

  const doIssue = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await adminApi.issueSubscription(userId, {
        plan_id: planId,
        months,
        credits: credits.trim() ? Number(credits) : undefined,
        auto_renew: autoRenew,
        note: note.trim() || undefined,
      });
      toast.success("Подписка выдана");
      setCredits("");
      setNote("");
      setConfirming(false);
      onChanged();
    } catch (err) {
      toast.error((err as Error).message || "Не удалось выдать подписку");
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (busy) return; // Enter во время запроса не должен выдавать вторую подписку
    if (!planId) {
      toast.error("Выбери тариф");
      return;
    }
    const creditValue = credits.trim() ? Number(credits) : undefined;
    if (creditValue !== undefined && (!Number.isFinite(creditValue) || creditValue < 0)) {
      toast.error("Кредиты: нужно неотрицательное число");
      return;
    }
    if (currentSub && !confirming) {
      setConfirming(true);
      return;
    }
    void doIssue();
  };

  return (
    <Card title="Выдать подписку" icon={<CreditCard className="h-4 w-4" />}>
      {plansLoading ? (
        <Skel h={80} />
      ) : activePlans.length === 0 ? (
        <Empty text="Нет активных тарифов — включи их во вкладке «Тарифы»" />
      ) : (
        <form className="flex flex-col gap-2.5" onSubmit={submit}>
          <div className="grid gap-2.5 md:grid-cols-2">
            <Field label="Тариф">
              <Select value={planId} onChange={(e) => setPlanId(e.target.value)} disabled={busy}>
                {activePlans.map((p) => (
                  <option key={p.id} value={p.id}>
                    {planLabel(p)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Срок">
              <Seg
                value={months}
                options={[
                  { v: 1, t: "1 мес" },
                  { v: 3, t: "3 мес" },
                  { v: 6, t: "6 мес" },
                  { v: 12, t: "12 мес" },
                ]}
                onChange={setMonths}
              />
            </Field>
          </div>
          <div className="grid gap-2.5 md:grid-cols-2">
            <Field label="Кредиты" hint={picked ? `Пусто = из тарифа: ${fmtNum(picked.credits)}` : "Пусто = из тарифа"}>
              <TextInput
                type="number"
                step="1"
                min={0}
                inputMode="numeric"
                value={credits}
                onChange={(e) => setCredits(e.target.value)}
                placeholder={picked ? String(picked.credits) : "из тарифа"}
                disabled={busy}
              />
            </Field>
            <Field label="Комментарий">
              <TextInput
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Например: оплата по счёту №123"
                maxLength={400}
                disabled={busy}
              />
            </Field>
          </div>
          <div className="flex items-center gap-3 rounded-[10px] bg-black/25 px-3 py-2.5">
            <div className="min-w-0 flex-1">
              <div className="text-[14px] text-zinc-300">Автопродление</div>
              <div className="text-[12px] text-zinc-600">Продлевать подписку по окончании срока</div>
            </div>
            <Toggle on={autoRenew} onChange={setAutoRenew} />
          </div>
          {confirming && currentSub ? (
            <div className="rounded-[10px] border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2.5">
              <div className="text-[13px] text-amber-300">
                Заменяем активную подписку «{currentSub.plan_name || currentSub.plan_id}»: она станет
                replaced, остаток кредитов и срок не переносятся.
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                <Btn danger disabled={busy} onClick={() => void doIssue()}>
                  {busy ? "Выдаём…" : "Да, заменить"}
                </Btn>
                <Btn disabled={busy} onClick={() => setConfirming(false)}>
                  Отмена
                </Btn>
              </div>
            </div>
          ) : null}

          <div>
            <Btn type="submit" accent disabled={busy}>
              <CreditCard className="h-4 w-4" />
              {busy ? "Выдаём…" : "Выдать подписку"}
            </Btn>
          </div>
        </form>
      )}
    </Card>
  );
}

/* ─────────────────────────── настройки пользователя ─────────────────────────── */

function SettingsBlock({
  u,
  plans,
  meId,
  adminsCount,
  adminsPending,
  onChanged,
}: {
  u: AdminUserDetail;
  plans: AdminPlan[] | undefined;
  meId: number | string | null;
  adminsCount: number | null;
  adminsPending: boolean;
  onChanged: () => void;
}) {
  const [plan, setPlan] = useState(u.plan ?? "");
  const [role, setRole] = useState(u.role);
  const [status, setStatus] = useState(u.status);
  const [tracker, setTracker] = useState(u.tracker_identity ?? "");
  const [device, setDevice] = useState(u.device_name ?? "");
  const [note, setNote] = useState(u.note ?? "");
  const [limits, setLimits] = useState(() => JSON.stringify(u.limits ?? {}, null, 2));
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => setConfirming(false), [role, status]);

  const planIds = new Set((plans ?? []).map((p) => p.id));
  const knownPlan = plan === "" || planIds.has(plan);

  const isMe = meId !== null && meId === u.id;
  const blocking = status === "blocked" && u.status !== "blocked";
  const demoting = u.role === "admin" && role !== "admin";
  const lastAdmin = adminsCount !== null && adminsCount <= 1 && u.role === "admin";
  const guardError =
    isMe && blocking
      ? "Себя заблокировать нельзя"
      : isMe && demoting
        ? "Нельзя снять роль admin с самого себя"
        : (demoting || blocking) && lastAdmin
          ? "Это единственный администратор — блокировать или разжаловать его нельзя"
          : (demoting || (blocking && u.role === "admin")) && adminsPending
            ? "Проверяем число администраторов — подожди пару секунд и обнови карточку"
            : null;

  /** PUT только с реально изменёнными полями — пустой plan больше не улетает. */
  const computePatch = (): UserPatch | null => {
    const patch: UserPatch = {};
    if (plan !== (u.plan ?? "")) patch.plan = plan || null;
    if (role !== u.role) patch.role = role;
    if (status !== u.status) patch.status = status;

    const trackerValue = tracker.trim();
    if (trackerValue !== (u.tracker_identity ?? "")) patch.tracker_identity = trackerValue;
    const deviceValue = device.trim();
    if (deviceValue !== (u.device_name ?? "")) patch.device_name = deviceValue;
    const noteValue = note.trim();
    if (noteValue !== (u.note ?? "")) patch.note = noteValue;

    const serverLimits = u.limits ?? {};
    const text = limits.trim();
    if (text !== JSON.stringify(serverLimits) && text !== JSON.stringify(serverLimits, null, 2)) {
      try {
        const parsed: unknown = text ? JSON.parse(text) : {};
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          throw new Error("not an object");
        }
        const record = parsed as Record<string, number | string>;
        if (JSON.stringify(record) !== JSON.stringify(serverLimits)) patch.limits = record;
      } catch {
        toast.error('Лимиты: нужен JSON-объект, например {"images_per_day": 50}');
        return null;
      }
    }
    return patch;
  };

  const doSave = async (known?: UserPatch) => {
    if (busy) return; // повторный Enter/клик во время сохранения ничего не шлёт
    const patch = known ?? computePatch();
    if (patch === null) return;
    if (Object.keys(patch).length === 0) {
      toast.error("Изменений нет");
      setConfirming(false);
      return;
    }
    setBusy(true);
    try {
      await adminApi.updateUser(u.id, patch);
      toast.success("Настройки пользователя сохранены");
      setConfirming(false);
      onChanged();
    } catch (err) {
      toast.error((err as Error).message || "Не удалось сохранить настройки");
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (busy) return; // Enter во время запроса не должен сохранять дважды
    if (guardError) {
      toast.error(guardError);
      return;
    }
    const patch = computePatch();
    if (patch === null) return;
    if (Object.keys(patch).length === 0) {
      toast.error("Изменений нет");
      return;
    }
    const needsConfirm = patch.role !== undefined || patch.status === "blocked";
    if (needsConfirm && !confirming) {
      setConfirming(true);
      return;
    }
    void doSave(patch);
  };

  const confirmText =
    role !== u.role && blocking
      ? `Заблокировать пользователя и сменить роль на «${role}»?`
      : role !== u.role
        ? `Сменить роль: ${u.role} → ${role}?`
        : "Заблокировать пользователя? Он потеряет доступ к студии.";

  return (
    <Card title="Настройки пользователя" icon={<Settings2 className="h-4 w-4" />}>
      <form className="flex flex-col gap-2.5" onSubmit={submit}>
        <div className="grid gap-2.5 md:grid-cols-3">
          <Field label="Тариф">
            <Select value={plan} onChange={(e) => setPlan(e.target.value)}>
              <option value="">без тарифа</option>
              {(plans ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
              {!knownPlan ? <option value={plan}>{plan} (нет в списке)</option> : null}
            </Select>
          </Field>
          <Field label="Роль">
            <Select value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="user">user</option>
              <option value="admin">admin</option>
            </Select>
          </Field>
          <Field label="Статус">
            <Select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="active">active</option>
              <option value="blocked">blocked</option>
            </Select>
          </Field>
        </div>
        <div className="grid gap-2.5 md:grid-cols-2">
          <Field label="Имя в API Tracker" hint="Так расходы привязываются к аккаунту">
            <TextInput value={tracker} onChange={(e) => setTracker(e.target.value)} placeholder="например: ivan/studio-pc" maxLength={200} />
          </Field>
          <Field label="Устройство">
            <TextInput value={device} onChange={(e) => setDevice(e.target.value)} placeholder="например: домашний ПК" maxLength={200} />
          </Field>
        </div>
        <Field label="Заметка">
          <TextInput value={note} onChange={(e) => setNote(e.target.value)} placeholder="Внутренний комментарий для админов" maxLength={400} />
        </Field>
        <Field label="Лимиты (JSON)" hint="Объект вида {«images_per_day»: 50}. Пустое поле — сбросить лимиты.">
          <textarea
            value={limits}
            onChange={(e) => setLimits(e.target.value)}
            spellCheck={false}
            className="min-h-[96px] w-full rounded-[9px] border border-white/[0.08] bg-black/35 px-3 py-2 font-mono text-[13px] text-white outline-none transition-colors placeholder:text-zinc-600 focus:border-[#2dd4bf]/60"
          />
        </Field>

        {guardError ? (
          <div className="flex items-center gap-2 rounded-[10px] border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2 text-[13px] text-amber-300">
            <ShieldAlert className="h-4 w-4 shrink-0" />
            {guardError}
          </div>
        ) : null}

        {confirming ? (
          <div className="rounded-[10px] border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2.5">
            <div className="text-[13px] text-amber-300">Подтверди изменение: {confirmText}</div>
            <div className="mt-2 flex flex-wrap gap-2">
              <Btn danger disabled={busy} onClick={() => void doSave()}>
                {busy ? "Сохраняем…" : "Подтвердить"}
              </Btn>
              <Btn disabled={busy} onClick={() => setConfirming(false)}>
                Отмена
              </Btn>
            </div>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          <Btn type="submit" accent disabled={busy || Boolean(guardError)}>
            <Settings2 className="h-4 w-4" />
            {busy ? "Сохраняем…" : "Сохранить настройки"}
          </Btn>
          <span className="text-[12px] text-zinc-600">Отправляются только изменённые поля</span>
        </div>
      </form>
    </Card>
  );
}

/* ─────────────────────────── история начислений ─────────────────────────── */

function LedgerBlock({ userId }: { userId: number | string }) {
  const q = useQuery({
    queryKey: ["admin", "user", userId, "ledger"],
    queryFn: () => adminApi.userLedger(userId, 100),
  });
  const items = q.data?.items ?? [];

  return (
    <Card
      title="История начислений"
      icon={<History className="h-4 w-4" />}
      right={<span className="text-[12px] text-zinc-500">{q.isLoading ? "загрузка…" : `${fmtNum(q.data?.total ?? 0)} записей`}</span>}
    >
      {q.isLoading ? (
        <Skel h={120} />
      ) : q.isError ? (
        <ErrorBox err={q.error} retry={() => void q.refetch()} />
      ) : items.length === 0 ? (
        <Empty text="Начислений ещё не было" />
      ) : (
        <div className={tableWrap}>
          <table className={tableCls}>
            <thead>
              <tr>
                <th className={thCls}>Дата</th>
                <th className={thCls}>Тип</th>
                <th className={thCls}>Сумма</th>
                <th className={thCls}>Причина</th>
                <th className={thCls}>Админ</th>
                <th className={thCls}>Баланс</th>
              </tr>
            </thead>
            <tbody>
              {items.map((e: AdminLedgerEntry) => (
                <tr key={e.id}>
                  <td className={tdCls + " whitespace-nowrap"}>{fmtDateTime(e.ts)}</td>
                  <td className={tdCls}>{LEDGER_KIND_RU[e.kind] ?? e.kind}</td>
                  <td
                    className={tdCls + " whitespace-nowrap font-medium"}
                    style={{ color: e.amount > 0 ? A : e.amount < 0 ? "#f87171" : "#a1a1aa" }}
                  >
                    {e.amount > 0 ? "+" : ""}
                    {fmtNum(e.amount)}
                  </td>
                  <td className={tdCls}>{e.reason || "—"}</td>
                  <td className={tdCls}>{e.admin || "—"}</td>
                  <td className={tdCls}>{fmtNum(e.balance_after)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/* ─────────────────────────── проекты и аудит ─────────────────────────── */

function ProjectsList({ items }: { items: AdminUserProject[] }) {
  if (!items.length) return <Empty text="Проектов у пользователя пока нет" />;
  return (
    <div className="flex flex-col gap-1.5">
      {items.map((p) => (
        <a
          key={p.id}
          href="/"
          onClick={() => openProject(p.id)}
          className="flex items-center gap-3 rounded-[10px] bg-black/20 px-3 py-2 transition-colors hover:bg-white/[0.05]"
        >
          <div className="min-w-0 flex-1">
            <div className="truncate text-[14px] text-white">{p.title || p.slug || `#${p.id}`}</div>
            <div className="text-[12px] text-zinc-500">
              {projectStatus(p.status)} · {fmtNum(p.generations)} ген · {fmtDate(p.created_at)}
            </div>
          </div>
          <ExternalLink className="h-4 w-4 shrink-0 text-zinc-600" />
        </a>
      ))}
    </div>
  );
}

function AuditList({ items }: { items: AdminUserDetail["audit"] }) {
  if (!items.length) return <Empty text="Записей аудита нет" />;
  return (
    <div className="flex flex-col gap-1.5">
      {items.slice(0, 6).map((a) => (
        <div key={a.id} className="flex items-start gap-3 rounded-[10px] bg-black/20 px-3 py-2">
          <ScrollText className="mt-0.5 h-4 w-4 shrink-0 text-zinc-600" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] text-zinc-300">
              {a.action}
              {a.target ? <span className="ml-1.5 text-zinc-500">{a.target}</span> : null}
            </div>
            <div className="text-[12px] text-zinc-600">
              {fmtDateTime(a.ts)} · {a.admin || "—"}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

/* ─────────────────────────── сессии аккаунта ─────────────────────────── */

function SessionsBlock({ userId, isSelf }: { userId: number | string; isSelf: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["admin", "user", userId, "sessions"],
    queryFn: () => adminApi.userSessions(userId),
  });
  const [busy, setBusy] = useState(false);
  const items = q.data?.items ?? [];

  const revoke = async () => {
    if (busy || isSelf) return;
    setBusy(true);
    try {
      const r = await adminApi.revokeSessions(userId);
      toast.success(`Сессии отозваны: ${fmtNum(r.revoked)}`);
      await qc.invalidateQueries({ queryKey: ["admin"] });
    } catch (err) {
      toast.error((err as Error).message || "Не удалось отозвать сессии");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="Сессии"
      icon={<Monitor className="h-4 w-4" />}
      right={
        <span className="text-[12px] text-zinc-500">
          {q.isLoading ? "загрузка…" : `${fmtNum(items.length)} всего`}
        </span>
      }
    >
      {q.isLoading ? (
        <Skel h={80} />
      ) : q.isError ? (
        <ErrorBox err={q.error} retry={() => void q.refetch()} />
      ) : items.length === 0 ? (
        <Empty text="Сессий нет — аккаунт нигде не залогинен" />
      ) : (
        <>
          <div className={tableWrap}>
            <table className={tableCls}>
              <thead>
                <tr>
                  <th className={thCls}>Выдана</th>
                  <th className={thCls}>Истекает</th>
                  <th className={thCls}>Браузер</th>
                  <th className={thCls}>Статус</th>
                </tr>
              </thead>
              <tbody>
                {items.map((s, i) => (
                  <tr key={`${s.created_at}-${i}`}>
                    <td className={tdCls + " whitespace-nowrap"}>{fmtDateTime(s.created_at)}</td>
                    <td className={tdCls + " whitespace-nowrap"}>{fmtDateTime(s.expires_at)}</td>
                    <td className={tdCls}>
                      <div className="max-w-[240px] truncate text-[12px] text-zinc-400" title={s.user_agent || undefined}>
                        {s.user_agent || "—"}
                      </div>
                    </td>
                    <td className={tdCls}>
                      <Badge tone={s.active ? "ok" : "neutral"}>{s.active ? "активна" : "истекла"}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {isSelf ? (
              <span className="rounded-[10px] border border-white/[0.08] bg-black/20 px-3 py-2 text-[13px] text-zinc-400">
                Это твой аккаунт — свои сессии здесь не отзываем
              </span>
            ) : (
              <ConfirmAction
                label="Отозвать все"
                confirmText="Отозвать все сессии? Пользователю придётся войти заново."
                confirmLabel="Да, отозвать"
                busy={busy}
                icon={<Ban className="h-4 w-4" />}
                onConfirm={() => void revoke()}
              />
            )}
            <span className="text-[12px] text-zinc-600">Блокировка аккаунта тоже отзывает сессии</span>
          </div>
        </>
      )}
    </Card>
  );
}

/* ─────────────────────────── удаление аккаунта ─────────────────────────── */

function DangerZone({
  u,
  meId,
  adminsCount,
  onClose,
  onChanged,
}: {
  u: AdminUserDetail;
  meId: number | string | null;
  adminsCount: number | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const isMe = meId !== null && meId === u.id;
  const lastAdmin = adminsCount !== null && adminsCount <= 1 && u.role === "admin";
  const blocked = isMe
    ? "Нельзя удалить свой аккаунт."
    : lastAdmin
      ? "Нельзя удалить последнего администратора."
      : null;

  const remove = async () => {
    setBusy(true);
    try {
      const r = await adminApi.deleteUser(u.id);
      toast.success(
        `Аккаунт @${u.username || u.id} удалён${r.sessions_revoked ? `, сессий отозвано: ${fmtNum(r.sessions_revoked)}` : ""}`,
      );
      onClose();
      onChanged();
    } catch (err) {
      toast.error((err as Error).message || "Не удалось удалить аккаунт");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Удаление аккаунта" icon={<Trash2 className="h-4 w-4" />}>
      <div className="text-[13px] text-zinc-500">
        Удалятся аккаунт, его сессии и подписки. История начислений остаётся.
      </div>
      {blocked ? (
        <div className="mt-2 rounded-[10px] border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2 text-[13px] text-amber-300">
          {blocked}
        </div>
      ) : (
        <div className="mt-2">
          <ConfirmAction
            label="Удалить пользователя"
            confirmText={`Удалить @${u.username || u.id}? Действие необратимо.`}
            confirmLabel="Да, удалить"
            busy={busy}
            icon={<Trash2 className="h-4 w-4" />}
            onConfirm={() => void remove()}
          />
        </div>
      )}
    </Card>
  );
}

/* ─────────────────────────── создание пользователя ─────────────────────────── */

function CreateUserModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const plansQ = useQuery({
    queryKey: ["admin", "plans"],
    queryFn: () => adminApi.plans(),
    enabled: open,
    staleTime: 60_000,
  });
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("user");
  const [plan, setPlan] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setEmail("");
    setUsername("");
    setName("");
    setPassword("");
    setRole("user");
    setPlan("");
    setBusy(false);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      // Пока идёт создание, Esc не закрывает модалку — запрос уже улетел.
      if (e.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onClose]);

  if (!open) return null;

  const activePlans = (plansQ.data ?? []).filter((p) => p.is_active);
  const emailBad = !EMAIL_RE.test(email.trim());
  const userBad = !USERNAME_RE.test(username.trim());
  const pwBad = password.length < 8 || !/\p{L}/u.test(password) || !/\d/.test(password);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return; // Enter во время запроса не должен создавать дубль
    if (emailBad) {
      toast.error("Укажи корректный email");
      return;
    }
    if (userBad) {
      toast.error("username: 3–32 символа, только латиница, цифры, _ . -");
      return;
    }
    if (pwBad) {
      toast.error("Пароль: минимум 8 символов, хотя бы одна буква и цифра");
      return;
    }
    setBusy(true);
    try {
      const created = await adminApi.createUser({
        email: email.trim(),
        username: username.trim(),
        name: name.trim() || username.trim(),
        password,
        role,
        plan: plan || null,
      });
      toast.success(`Аккаунт @${created.username} создан`);
      onCreated();
      onClose();
    } catch (err) {
      toast.error((err as Error).message || "Не удалось создать пользователя");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[90] flex items-start justify-center overflow-y-auto bg-black/70 p-4 backdrop-blur-[2px]"
      onMouseDown={(e) => {
        // Клик по подложке не закрывает модалку, пока идёт запрос.
        if (!busy && e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-user-title"
        className="mb-8 mt-16 w-full max-w-[560px] rounded-[18px] border border-white/[0.08] bg-[#1b1d20] shadow-2xl"
      >
        <div className="flex items-start gap-3 border-b border-white/[0.07] px-5 py-4">
          <span
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px]"
            style={{ background: A + "1f", color: A }}
          >
            <UserPlus className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <div id="create-user-title" className="text-[16px] font-medium text-white">Новый пользователь</div>
            <div className="text-[12px] text-zinc-500">Email и пароль — для входа в студию; роль и тариф — сразу</div>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label="Закрыть"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[8px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-white disabled:opacity-40"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <form className="flex flex-col gap-2.5 px-5 py-4" onSubmit={submit}>
          <div className="grid gap-2.5 md:grid-cols-2">
            <Field label="Email">
              <TextInput
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="user@example.com"
                autoComplete="off"
                autoFocus
                disabled={busy}
              />
            </Field>
            <Field label="Username">
              <TextInput
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="ivan"
                autoComplete="off"
              />
            </Field>
          </div>
          <Field label="Имя">
            <TextInput
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Иван Петров"
              autoComplete="off"
            />
          </Field>
          <Field label="Пароль" hint="Минимум 8 символов, буква и цифра">
            <TextInput
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Например: via-2026-pass"
              autoComplete="new-password"
            />
          </Field>
          <div className="grid gap-2.5 md:grid-cols-2">
            <Field label="Роль">
              <Select value={role} onChange={(e) => setRole(e.target.value)}>
                <option value="user">user</option>
                <option value="admin">admin</option>
              </Select>
            </Field>
            <Field label="Тариф">
              <Select value={plan} onChange={(e) => setPlan(e.target.value)}>
                <option value="">без тарифа</option>
                {activePlans.map((p) => (
                  <option key={p.id} value={p.id}>
                    {planLabel(p)}
                  </option>
                ))}
              </Select>
            </Field>
          </div>

          <div className="mt-1 flex flex-wrap items-center gap-2">
            <Btn type="submit" accent disabled={busy}>
              <UserPlus className="h-4 w-4" />
              {busy ? "Создаём…" : "Создать"}
            </Btn>
            <Btn onClick={onClose} disabled={busy}>
              Отмена
            </Btn>
            {plansQ.isLoading ? <span className="text-[12px] text-zinc-600">загружаем тарифы…</span> : null}
          </div>
        </form>
      </div>
    </div>
  );
}
