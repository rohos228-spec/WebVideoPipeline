"use client";

/**
 * Вкладка «Тарифы» админ-панели VIA: список планов, создание, правка и удаление.
 * Лимиты хранятся JSON-объектом, в форме редактируются как JSON-текст.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, Plus } from "lucide-react";
import { adminApi, type AdminPlan } from "@/lib/admin-api";
import {
  Badge,
  Btn,
  Card,
  Empty,
  ErrorBox,
  Field,
  Select,
  Skel,
  TextArea,
  TextInput,
  Toggle,
  fmtNum,
  fmtRub,
} from "@/components/admin/admin-kit";

const PLANS_KEY = ["admin", "plans"] as const;

type PlanDraft = {
  name: string;
  price_rub: string;
  group: string;
  credits: string;
  note: string;
  hot: boolean;
  is_active: boolean;
  limits: string;
  perks: string;
  sort: string;
};

type AddDraft = {
  id: string;
  name: string;
  price_rub: string;
  group: string;
  credits: string;
  note: string;
};

const EMPTY_ADD: AddDraft = {
  id: "",
  name: "",
  price_rub: "0",
  group: "trial",
  credits: "0",
  note: "",
};

const numOr = (s: string): number | null => {
  const raw = String(s).trim();
  if (raw === "") return null;
  const v = Number(raw.replace(",", "."));
  return Number.isFinite(v) ? v : null;
};

const draftFromPlan = (p: AdminPlan): PlanDraft => ({
  name: p.name ?? "",
  price_rub: String(p.price_rub ?? 0),
  group: p.group || "trial",
  credits: String(p.credits ?? 0),
  note: p.note ?? "",
  hot: Boolean(p.hot),
  is_active: Boolean(p.is_active),
  limits: JSON.stringify(p.limits ?? {}, null, 2),
  perks: (p.perks ?? []).join("\n"),
  sort: String(p.sort ?? 0),
});

const GROUP_OPTIONS = [
  { v: "trial", t: "trial" },
  { v: "factory", t: "factory" },
];

/** JSON-текст лимитов → объект; null, если JSON невалиден. */
function parseLimits(text: string): Record<string, number | string> | null {
  try {
    const parsed: unknown = JSON.parse(text.trim() || "{}");
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    return parsed as Record<string, number | string>;
  } catch {
    return null;
  }
}

function LimitsList({ limits }: { limits: Record<string, number | string> }) {
  const entries = Object.entries(limits ?? {});
  if (entries.length === 0) return <div className="text-[13px] text-zinc-600">—</div>;
  return (
    <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
      {entries.map(([k, v]) => (
        <div key={k} className="flex items-baseline justify-between gap-3 text-[13px]">
          <dt className="truncate text-zinc-500" title={k}>
            {k}
          </dt>
          <dd className="shrink-0 text-zinc-300">{String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function AdminPlansTab() {
  const qc = useQueryClient();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: PLANS_KEY,
    queryFn: () => adminApi.plans(),
  });

  const [editId, setEditId] = useState<string | null>(null);
  const [draft, setDraft] = useState<PlanDraft | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [addDraft, setAddDraft] = useState<AddDraft>(EMPTY_ADD);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: PLANS_KEY });
    void refetch();
  };

  const save = useMutation({
    mutationFn: (body: Partial<AdminPlan> & { id: string }) => adminApi.savePlan(body),
    onSuccess: (p) => {
      toast.success(`Тариф «${p.name || p.id}» сохранён`);
      setEditId(null);
      setDraft(null);
      setAddOpen(false);
      setAddDraft(EMPTY_ADD);
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message || "Не удалось сохранить тариф"),
  });

  const remove = useMutation({
    mutationFn: (id: string) => adminApi.deletePlan(id),
    onSuccess: (res, id) => {
      if (res.deactivated) {
        toast.info("Тариф деактивирован — на него есть ссылки");
      } else {
        toast.success("Тариф удалён");
      }
      if (editId === id) {
        setEditId(null);
        setDraft(null);
      }
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message || "Не удалось удалить тариф"),
  });

  const plans = [...(data ?? [])].sort(
    (a, b) => (a.sort ?? 0) - (b.sort ?? 0) || a.price_rub - b.price_rub,
  );

  const startEdit = (p: AdminPlan) => {
    setAddOpen(false);
    setEditId(p.id);
    setDraft(draftFromPlan(p));
  };

  const submitEdit = () => {
    if (!editId || !draft) return;
    if (!draft.name.trim()) {
      toast.error("Укажи название тарифа");
      return;
    }
    const price = numOr(draft.price_rub);
    if (price === null) {
      toast.error("Цена: нужно число");
      return;
    }
    const credits = numOr(draft.credits);
    if (credits === null) {
      toast.error("Кредиты: нужно число");
      return;
    }
    const sort = numOr(draft.sort);
    if (sort === null) {
      toast.error("Сортировка: нужно число");
      return;
    }
    const limits = parseLimits(draft.limits);
    if (!limits) {
      toast.error("limits: нужен корректный JSON-объект");
      return;
    }
    const perks = draft.perks
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    save.mutate({
      id: editId,
      name: draft.name.trim(),
      price_rub: price,
      group: draft.group,
      credits,
      note: draft.note,
      hot: draft.hot,
      is_active: draft.is_active,
      limits,
      perks,
      sort,
    });
  };

  const submitAdd = () => {
    const id = addDraft.id.trim().toLowerCase();
    if (!/^[a-z0-9][a-z0-9_-]{0,39}$/.test(id)) {
      toast.error("id: латиница, цифры, «-» и «_», до 40 символов");
      return;
    }
    if (!addDraft.name.trim()) {
      toast.error("Укажи название тарифа");
      return;
    }
    const price = numOr(addDraft.price_rub);
    if (price === null) {
      toast.error("Цена: нужно число");
      return;
    }
    const credits = numOr(addDraft.credits);
    if (credits === null) {
      toast.error("Кредиты: нужно число");
      return;
    }
    save.mutate({
      id,
      name: addDraft.name.trim(),
      price_rub: price,
      group: addDraft.group,
      credits,
      note: addDraft.note,
      limits: {},
      perks: [],
      is_active: true,
      hot: false,
      sort: plans.length,
    });
  };

  const removePlan = (p: AdminPlan) => {
    const ok = window.confirm(
      `Удалить тариф «${p.name}» (${p.id})?\n\nЕсли на тариф есть ссылки или история назначений, он не удалится, а деактивируется.`,
    );
    if (!ok) return;
    remove.mutate(p.id);
  };

  const editForm = (p: AdminPlan) =>
    draft ? (
      <div className="mt-3 space-y-2 rounded-[12px] border border-white/[0.06] bg-black/25 p-3">
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="Название">
            <TextInput value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          </Field>
          <Field label="Цена, ₽">
            <TextInput
              type="number"
              value={draft.price_rub}
              onChange={(e) => setDraft({ ...draft, price_rub: e.target.value })}
            />
          </Field>
          <Field label="Группа">
            <Select value={draft.group} onChange={(e) => setDraft({ ...draft, group: e.target.value })}>
              {GROUP_OPTIONS.map((g) => (
                <option key={g.v} value={g.v}>
                  {g.t}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Кредиты">
            <TextInput
              type="number"
              value={draft.credits}
              onChange={(e) => setDraft({ ...draft, credits: e.target.value })}
            />
          </Field>
          <Field label="Сортировка">
            <TextInput
              type="number"
              value={draft.sort}
              onChange={(e) => setDraft({ ...draft, sort: e.target.value })}
            />
          </Field>
          <div className="flex items-end gap-5 pb-1">
            <label className="flex items-center gap-2 text-[13px] text-zinc-400">
              <Toggle on={draft.is_active} onChange={(v) => setDraft({ ...draft, is_active: v })} />
              активен
            </label>
            <label className="flex items-center gap-2 text-[13px] text-zinc-400">
              <Toggle on={draft.hot} onChange={(v) => setDraft({ ...draft, hot: v })} />
              хит
            </label>
          </div>
        </div>
        <Field label="Примечание">
          <TextInput value={draft.note} onChange={(e) => setDraft({ ...draft, note: e.target.value })} />
        </Field>
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="limits (JSON)" hint={'Объект «ключ: значение», например {"images": 100}'}>
            <TextArea
              value={draft.limits}
              onChange={(e) => setDraft({ ...draft, limits: e.target.value })}
              className="min-h-[110px] font-mono text-[12px]"
              spellCheck={false}
            />
          </Field>
          <Field label="perks" hint="По одному пункту на строку">
            <TextArea
              value={draft.perks}
              onChange={(e) => setDraft({ ...draft, perks: e.target.value })}
              className="min-h-[110px]"
            />
          </Field>
        </div>
        <div className="flex items-center gap-2">
          <Btn accent onClick={submitEdit} disabled={save.isPending}>
            {save.isPending ? "Сохраняю…" : "Сохранить"}
          </Btn>
          <Btn
            onClick={() => {
              setEditId(null);
              setDraft(null);
            }}
          >
            Отмена
          </Btn>
          <span className="text-[12px] text-zinc-600">id: {p.id} (не меняется)</span>
        </div>
      </div>
    ) : null;

  return (
    <div className="flex flex-col gap-3">
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <Btn
            accent
            onClick={() => {
              setEditId(null);
              setDraft(null);
              setAddOpen((v) => !v);
            }}
          >
            <Plus className="h-4 w-4" />
            Добавить тариф
          </Btn>
          <div className="flex-1" />
          <span className="text-[13px] text-zinc-500">Всего {fmtNum(plans.length)}</span>
        </div>

        {addOpen ? (
          <div className="mt-3 space-y-2 rounded-[12px] border border-white/[0.06] bg-black/25 p-3">
            <div className="text-[13px] font-medium uppercase tracking-[0.08em] text-zinc-400">Новый тариф</div>
            <div className="grid gap-2 sm:grid-cols-2">
              <Field label="id" hint="slug: латиница, цифры, «-» и «_»">
                <TextInput
                  value={addDraft.id}
                  onChange={(e) => setAddDraft({ ...addDraft, id: e.target.value })}
                  placeholder="factory-pro"
                  spellCheck={false}
                />
              </Field>
              <Field label="Название">
                <TextInput
                  value={addDraft.name}
                  onChange={(e) => setAddDraft({ ...addDraft, name: e.target.value })}
                  placeholder="Factory Pro"
                />
              </Field>
              <Field label="Цена, ₽">
                <TextInput
                  type="number"
                  value={addDraft.price_rub}
                  onChange={(e) => setAddDraft({ ...addDraft, price_rub: e.target.value })}
                />
              </Field>
              <Field label="Группа">
                <Select
                  value={addDraft.group}
                  onChange={(e) => setAddDraft({ ...addDraft, group: e.target.value })}
                >
                  {GROUP_OPTIONS.map((g) => (
                    <option key={g.v} value={g.v}>
                      {g.t}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Кредиты">
                <TextInput
                  type="number"
                  value={addDraft.credits}
                  onChange={(e) => setAddDraft({ ...addDraft, credits: e.target.value })}
                />
              </Field>
              <Field label="Примечание">
                <TextInput
                  value={addDraft.note}
                  onChange={(e) => setAddDraft({ ...addDraft, note: e.target.value })}
                />
              </Field>
            </div>
            <div className="flex items-center gap-2">
              <Btn accent onClick={submitAdd} disabled={save.isPending}>
                {save.isPending ? "Создаю…" : "Создать тариф"}
              </Btn>
              <Btn onClick={() => setAddOpen(false)}>Отмена</Btn>
            </div>
          </div>
        ) : null}
      </Card>

      {isLoading ? (
        <div className="grid gap-3 lg:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skel key={i} h={210} />
          ))}
        </div>
      ) : error ? (
        <ErrorBox err={error} retry={() => void refetch()} />
      ) : plans.length === 0 ? (
        <Empty text="Тарифов пока нет. Добавь первый." />
      ) : (
        <div className="grid items-start gap-3 lg:grid-cols-2">
          {plans.map((p) => (
            <article
              key={p.id}
              className="rounded-[16px] border border-white/[0.07] bg-white/[0.025] p-4"
            >
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="truncate text-[16px] font-medium text-white">{p.name || p.id}</span>
                    <Badge tone={p.is_active ? "ok" : "neutral"}>
                      {p.is_active ? "is_active" : "выключен"}
                    </Badge>
                    {p.hot ? <Badge tone="warn">hot</Badge> : null}
                    <Badge tone="neutral">{p.group || "—"}</Badge>
                  </div>
                  <div className="mt-0.5 font-mono text-[12px] text-zinc-600">{p.id}</div>
                </div>
                <div className="text-right">
                  <div className="text-[20px] font-medium text-white">{fmtRub(p.price_rub)}</div>
                  <div className="text-[12px] text-zinc-500">{fmtNum(p.credits)} кредитов</div>
                </div>
              </div>

              {p.note ? <div className="mt-2 text-[13px] text-zinc-400">{p.note}</div> : null}

              <div className="mt-3">
                <div className="mb-1 text-[12px] uppercase tracking-[0.06em] text-zinc-600">Лимиты</div>
                <LimitsList limits={p.limits} />
              </div>

              {(p.perks ?? []).length > 0 ? (
                <ul className="mt-3 space-y-1">
                  {(p.perks ?? []).map((perk, i) => (
                    <li key={`${perk}-${i}`} className="flex items-start gap-1.5 text-[13px] text-zinc-300">
                      <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#2dd4bf]" />
                      <span>{perk}</span>
                    </li>
                  ))}
                </ul>
              ) : null}

              <div className="mt-3 flex items-center gap-2">
                <Btn onClick={() => startEdit(p)}>Изменить</Btn>
                <Btn danger onClick={() => removePlan(p)} disabled={remove.isPending}>
                  Удалить
                </Btn>
              </div>

              {editId === p.id ? editForm(p) : null}
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
