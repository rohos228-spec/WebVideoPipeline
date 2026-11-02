"use client";

/**
 * Вкладка «Настройки» админ-панели VIA: валюта, курс, кредиты, регистрация,
 * тариф по умолчанию, путь к базе трекера и состояние источников данных.
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Database } from "lucide-react";
import {
  adminApi,
  type AdminOverview,
  type AdminSettings,
} from "@/lib/admin-api";
import {
  A,
  Badge,
  Btn,
  Card,
  ErrorBox,
  Field,
  Select,
  Skel,
  TextInput,
  Toggle,
  fmtDateTime,
  fmtNum,
} from "@/components/admin/admin-kit";

const SETTINGS_KEY = ["admin", "settings"] as const;
const PLANS_KEY = ["admin", "plans"] as const;
const OVERVIEW_KEY = ["admin", "overview", 7] as const;

type SettingsForm = {
  currency: string;
  usd_rub: string;
  credits_per_usd: string;
  spend_markup: string;
  low_balance_threshold: string;
  registration_open: boolean;
  default_plan: string;
};

const formFrom = (s: AdminSettings): SettingsForm => ({
  currency: s.currency ?? "RUB",
  usd_rub: String(s.usd_rub ?? 0),
  credits_per_usd: String(s.credits_per_usd ?? 0),
  spend_markup: String(s.spend_markup ?? 1),
  low_balance_threshold: String(s.low_balance_threshold ?? 0),
  registration_open: Boolean(s.registration_open),
  default_plan: s.default_plan ?? "",
});

const numOr = (s: string): number | null => {
  const raw = String(s).trim();
  if (raw === "") return null;
  const v = Number(raw.replace(",", "."));
  return Number.isFinite(v) ? v : null;
};

const SOURCES: { key: keyof AdminOverview["sources"]; title: string; text: string }[] = [
  {
    key: "users_db",
    title: "users_db",
    text: "Основная база: пользователи, роли, балансы, подписки.",
  },
  {
    key: "tracker_db",
    title: "tracker_db",
    text: "Локальная база запросов к моделям: токены, время, стоимость.",
  },
  {
    key: "state_db",
    title: "state_db",
    text: "Состояние студии: проекты, генерации, журнал действий.",
  },
  {
    key: "cloud",
    title: "cloud",
    text: "Облачный биллинг и внешние провайдеры моделей.",
  },
];

export function AdminSettingsTab() {
  const qc = useQueryClient();
  const [form, setForm] = useState<SettingsForm | null>(null);

  const {
    data: settings,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: SETTINGS_KEY,
    queryFn: () => adminApi.settings(),
  });

  const { data: plans } = useQuery({
    queryKey: PLANS_KEY,
    queryFn: () => adminApi.plans(),
  });

  const { data: overview } = useQuery({
    queryKey: OVERVIEW_KEY,
    queryFn: () => adminApi.overview(7),
  });

  useEffect(() => {
    if (settings) setForm(formFrom(settings));
  }, [settings]);

  const save = useMutation({
    mutationFn: (patch: Partial<AdminSettings>) => adminApi.saveSettings(patch),
    onSuccess: (saved) => {
      setForm(formFrom(saved));
      toast.success("Настройки сохранены");
      void qc.invalidateQueries({ queryKey: ["admin", "settings"] });
      void qc.invalidateQueries({ queryKey: ["admin", "overview"] });
      void qc.invalidateQueries({ queryKey: ["admin"] });
      void refetch();
    },
    onError: (e: Error) => toast.error(e.message || "Не удалось сохранить настройки"),
  });

  const patchForm = (p: Partial<SettingsForm>) => {
    setForm((prev) => (prev ? { ...prev, ...p } : prev));
  };

  const submit = () => {
    if (!form) return;
    const usdRub = numOr(form.usd_rub);
    const creditsPerUsd = numOr(form.credits_per_usd);
    const spendMarkup = numOr(form.spend_markup);
    const lowBalance = numOr(form.low_balance_threshold);
    if (usdRub === null || creditsPerUsd === null || spendMarkup === null || lowBalance === null) {
      toast.error("Числовые поля должны содержать число");
      return;
    }
    save.mutate({
      currency: form.currency.trim() || "RUB",
      usd_rub: usdRub,
      credits_per_usd: creditsPerUsd,
      spend_markup: spendMarkup,
      low_balance_threshold: lowBalance,
      registration_open: form.registration_open,
      default_plan: form.default_plan,
    });
  };

  const planOptions = (plans ?? []).map((p) => ({
    id: p.id,
    label: p.name ? `${p.name} (${p.id})` : p.id,
  }));
  if (form?.default_plan && !planOptions.some((o) => o.id === form.default_plan)) {
    planOptions.unshift({ id: form.default_plan, label: form.default_plan });
  }

  const sources = overview?.sources;

  return (
    <div className="flex flex-col gap-3">
      {error ? (
        <ErrorBox err={error} retry={() => void refetch()} />
      ) : isLoading || !settings || !form ? (
        <>
          <Skel h={260} />
          <Skel h={140} />
        </>
      ) : (
        <>
          <div
            className="rounded-[12px] border px-3.5 py-2.5 text-[13px] leading-relaxed text-zinc-300"
            style={{ borderColor: A + "33", background: A + "0f" }}
          >
            Кредиты и лимиты здесь — учёт и ручные операции; автосписания с генераций пока нет.
            registration_open теперь реально закрывает /register, default_plan применяется при
            регистрации.
          </div>
          <Card
            title="Параметры студии"
            icon={<Database className="h-4 w-4" />}
            right={
              <span className="text-[12px] text-zinc-500">
                обновлено {fmtDateTime(settings.updated_at)}
                {settings.updated_by ? ` · ${settings.updated_by}` : ""}
              </span>
            }
          >
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <Field label="Валюта" hint="код отображения, например RUB">
                <TextInput
                  value={form.currency}
                  onChange={(e) => patchForm({ currency: e.target.value })}
                  placeholder="RUB"
                />
              </Field>
              <Field label="Курс USD → RUB" hint="сколько рублей в одном долларе">
                <TextInput
                  type="number"
                  value={form.usd_rub}
                  onChange={(e) => patchForm({ usd_rub: e.target.value })}
                />
              </Field>
              <Field label="Кредитов за $1" hint="курс начисления кредитов">
                <TextInput
                  type="number"
                  value={form.credits_per_usd}
                  onChange={(e) => patchForm({ credits_per_usd: e.target.value })}
                />
              </Field>
              <Field label="Наценка расхода" hint="множитель отображения расхода">
                <TextInput
                  type="number"
                  value={form.spend_markup}
                  onChange={(e) => patchForm({ spend_markup: e.target.value })}
                />
              </Field>
              <Field label="Порог низкого баланса" hint="в кредитах, для предупреждений">
                <TextInput
                  type="number"
                  value={form.low_balance_threshold}
                  onChange={(e) => patchForm({ low_balance_threshold: e.target.value })}
                />
              </Field>
              <Field label="Регистрация" hint="открыт ли приём новых пользователей">
                <div className="flex h-9 items-center gap-2">
                  <Toggle
                    on={form.registration_open}
                    onChange={(v) => patchForm({ registration_open: v })}
                  />
                  <span className="text-[13px] text-zinc-400">
                    {form.registration_open ? "открыта" : "закрыта"}
                  </span>
                </div>
              </Field>
              <Field label="Тариф по умолчанию">
                <Select
                  value={form.default_plan}
                  onChange={(e) => patchForm({ default_plan: e.target.value })}
                >
                  <option value="">— не выбран —</option>
                  {planOptions.map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </Field>
              <div className="sm:col-span-2">
                <Field label="Путь к базе запросов" hint="путь к локальной базе запросов, только чтение">
                  <TextInput
                    value={settings.tracker_db_path}
                    readOnly
                    className="cursor-not-allowed font-mono text-[12px] text-zinc-500"
                  />
                </Field>
              </div>
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Btn accent onClick={submit} disabled={save.isPending}>
                {save.isPending ? "Сохраняю…" : "Сохранить"}
              </Btn>
              <Btn
                onClick={() => setForm(formFrom(settings))}
                disabled={save.isPending}
                title="Вернуть значения с сервера"
              >
                Сбросить
              </Btn>
              <div className="flex-1" />
              <span className="text-[12px] text-zinc-600">
                {settings.updated_by ? `последняя правка: ${settings.updated_by}` : "правок ещё не было"}
              </span>
            </div>
          </Card>

          <Card title="Источники данных">
            <div className="grid gap-2 sm:grid-cols-2">
              {SOURCES.map((s) => {
                const on = Boolean(sources?.[s.key]);
                return (
                  <div
                    key={s.key}
                    className="flex items-start gap-2 rounded-[12px] bg-black/25 px-3 py-2.5"
                  >
                    <span
                      className="mt-1.5 h-2 w-2 shrink-0 rounded-full"
                      style={{ background: on ? A : "#52525b" }}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-[13px] text-zinc-200">{s.title}</span>
                        <Badge tone={on ? "ok" : "neutral"}>{on ? "доступен" : "нет данных"}</Badge>
                      </div>
                      <div className="mt-0.5 text-[12px] text-zinc-500">{s.text}</div>
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="mt-2 text-[12px] text-zinc-600">
              Данные по источникам — из обзора за 7 дней
              {overview
                ? `, срез на ${fmtDateTime(overview.generated_at)}`
                : ""}
              . Всего записей в трекере: {fmtNum(overview?.requests.total)}.
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
