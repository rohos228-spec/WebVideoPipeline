"use client";

/**
 * «Автономия» во вкладке «Регистратор»: что оркестратор делает сам при сбое ноды.
 * Ручки `GET/PUT /api/orchestrator/chat/autonomy` (app/web/routers/orchestrator_chat.py,
 * правило — app/services/orchestrator_autonomy.py). По умолчанию всё «с подтверждением».
 */

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  getOrchAutonomy,
  isAbortError,
  OrchApiError,
  putOrchAutonomy,
  type OrchAutonomySettings,
  type OrchAutonomyView,
} from "@/lib/orchestratorApi";

type Switch = "auto_fix_on_failure" | "auto_retry" | "auto_model_switch";

const SWITCHES: { id: Switch; title: string; hint: string }[] = [
  {
    id: "auto_fix_on_failure",
    title: "Автоисправление после сбоя",
    hint: "Разобрав сбой, оркестратор сам начинает чинить. Выключено — пишет разбор и присылает карточку с предложением.",
  },
  {
    id: "auto_retry",
    title: "Автоповтор после сбоя",
    hint: "Сам перезапускает упавшую ноду. Это новый платный вызов модели. Работает вместе с автоисправлением.",
  },
  {
    id: "auto_model_switch",
    title: "Автосмена модели на не дороже",
    hint: "Сам ставит на ноду другую модель, только если она не дороже текущей. Дороже или цена неизвестна — карточка.",
  },
];

const FALLBACK_LIMITS = { retries: [0, 5] as [number, number], spend: [0, 20] as [number, number] };

function pickSettings(view: OrchAutonomyView): OrchAutonomySettings {
  return {
    auto_retry: view.auto_retry === true,
    auto_model_switch: view.auto_model_switch === true,
    auto_fix_on_failure: view.auto_fix_on_failure === true,
    max_auto_retries_per_node: view.max_auto_retries_per_node,
    auto_spend_limit_usd: view.auto_spend_limit_usd,
  };
}

function saveErrorText(error: unknown): string {
  if (error instanceof OrchApiError) {
    if (error.status === 401) return "Вход в студию закончился. Обновите страницу и войдите снова.";
    if (error.status === 403) return "Менять автономию может только человек из интерфейса студии.";
    if (error.status === 422) return "Сервер не принял значения: проверьте пределы чисел.";
  }
  return "Не удалось сохранить. Проверьте связь и попробуйте ещё раз.";
}

function formatWhen(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
}

export function AutonomySection() {
  const [view, setView] = useState<OrchAutonomyView | null>(null);
  const [draft, setDraft] = useState<OrchAutonomySettings | null>(null);
  const [retriesText, setRetriesText] = useState("");
  const [spendText, setSpendText] = useState("");
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  const absorb = (next: OrchAutonomyView) => {
    setView(next);
    const settings = pickSettings(next);
    setDraft(settings);
    setRetriesText(String(settings.max_auto_retries_per_node));
    setSpendText(String(settings.auto_spend_limit_usd));
  };

  useEffect(() => {
    const ac = new AbortController();
    getOrchAutonomy(ac.signal)
      .then((next) => {
        absorb(next);
        setLoadError("");
      })
      .catch((error: unknown) => {
        if (isAbortError(error)) return;
        setLoadError(
          error instanceof OrchApiError && error.status === 401
            ? "Вход в студию закончился. Обновите страницу и войдите снова."
            : "Настройки автономии не загрузились. Обновите страницу или попробуйте позже.",
        );
      });
    return () => ac.abort();
    // absorb трогает только сеттеры состояния: загрузка один раз при открытии блока.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const retryLimits = view?.limits?.max_auto_retries_per_node ?? FALLBACK_LIMITS.retries;
  const spendLimits = view?.limits?.auto_spend_limit_usd ?? FALLBACK_LIMITS.spend;
  const retries = Number(retriesText);
  const spend = Number(spendText.replace(",", "."));
  const retriesOk = retriesText.trim() !== "" && Number.isInteger(retries) && retries >= retryLimits[0] && retries <= retryLimits[1];
  const spendOk = spendText.trim() !== "" && Number.isFinite(spend) && spend >= spendLimits[0] && spend <= spendLimits[1];

  const dirty =
    view != null &&
    draft != null &&
    (SWITCHES.some((s) => draft[s.id] !== view[s.id]) ||
      (retriesOk && retries !== view.max_auto_retries_per_node) ||
      (spendOk && spend !== view.auto_spend_limit_usd));

  async function save() {
    if (!draft || !retriesOk || !spendOk) return;
    setBusy(true);
    setResult(null);
    try {
      const next = await putOrchAutonomy({
        auto_fix_on_failure: draft.auto_fix_on_failure,
        auto_retry: draft.auto_retry,
        auto_model_switch: draft.auto_model_switch,
        max_auto_retries_per_node: retries,
        auto_spend_limit_usd: spend,
      });
      absorb(next);
      setResult({ ok: true, text: "Сохранено. Новые правила действуют со следующего сбоя." });
    } catch (error) {
      setResult({ ok: false, text: saveErrorText(error) });
    } finally {
      setBusy(false);
    }
  }

  if (loadError) {
    return (
      <section data-testid="autonomy-panel" className="mt-3 rounded border border-white/10 p-2 text-xs text-white/80">
        <p role="alert" className="text-amber-200">
          {loadError}
        </p>
      </section>
    );
  }

  if (!view || !draft) {
    return (
      <section data-testid="autonomy-panel" className="mt-3 rounded border border-white/10 p-2 text-xs text-white/50">
        Загружаю настройки автономии…
      </section>
    );
  }

  const fixOff = !draft.auto_fix_on_failure;
  const when = formatWhen(view.updated_at);

  return (
    <section data-testid="autonomy-panel" className="mt-3 space-y-2 rounded border border-white/10 p-2 text-xs text-white/80">
      <p className="text-white/60">
        Что оркестратор делает сам, когда нода падает. Разбор сбоя пишется в чат всегда. Правка промтов и удаление —
        всегда через карточку подтверждения.
      </p>
      <div className="space-y-1.5">
        {SWITCHES.map((s) => {
          const on = draft[s.id];
          const idle = s.id !== "auto_fix_on_failure" && fixOff && on;
          return (
            <label key={s.id} className="flex cursor-pointer items-start gap-2 rounded px-1 py-1 hover:bg-white/[0.03]">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={on}
                disabled={busy}
                data-testid={`autonomy-${s.id}`}
                onChange={(event) => {
                  const checked = event.target.checked;
                  setDraft((cur) => (cur ? { ...cur, [s.id]: checked } : cur));
                  setResult(null);
                }}
              />
              <span className="min-w-0">
                <span className="text-white/90">{s.title}</span>
                <span className={on ? "text-emerald-300" : "text-white/45"}> · {on ? "автономно" : "с подтверждением"}</span>
                <span className="block text-[11px] text-white/45">{s.hint}</span>
                {idle ? (
                  <span className="block text-[11px] text-amber-200/80">
                    Пока выключено автоисправление, этот переключатель ничего не делает сам.
                  </span>
                ) : null}
              </span>
            </label>
          );
        })}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-2">
        <label className="block">
          Автоповторов подряд на одну ноду
          <input
            type="number"
            inputMode="numeric"
            min={retryLimits[0]}
            max={retryLimits[1]}
            step={1}
            value={retriesText}
            disabled={busy}
            data-testid="autonomy-retries"
            aria-invalid={!retriesOk}
            onChange={(event) => {
              setRetriesText(event.target.value);
              setResult(null);
            }}
            className="ml-2 w-16 rounded border border-white/20 bg-transparent px-1.5 py-1"
          />
          <span className="ml-1 text-white/40">
            от {retryLimits[0]} до {retryLimits[1]}
          </span>
        </label>
        <label className="block">
          Потолок одного автозапуска, $
          <input
            type="number"
            inputMode="decimal"
            min={spendLimits[0]}
            max={spendLimits[1]}
            step={0.1}
            value={spendText}
            disabled={busy}
            data-testid="autonomy-spend"
            aria-invalid={!spendOk}
            onChange={(event) => {
              setSpendText(event.target.value);
              setResult(null);
            }}
            className="ml-2 w-20 rounded border border-white/20 bg-transparent px-1.5 py-1"
          />
          <span className="ml-1 text-white/40">
            от {spendLimits[0]} до {spendLimits[1]}
          </span>
        </label>
      </div>
      <p className="text-[11px] text-white/40">
        Оценка автозапуска выше потолка или неизвестна — вместо запуска придёт карточка.
      </p>
      {!retriesOk || !spendOk ? (
        <p role="alert" className="text-amber-200">
          {!retriesOk
            ? `Число автоповторов — целое от ${retryLimits[0]} до ${retryLimits[1]}.`
            : `Потолок — от ${spendLimits[0]} до ${spendLimits[1]} $.`}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          data-testid="autonomy-save"
          disabled={busy || !dirty || !retriesOk || !spendOk}
          onClick={() => void save()}
        >
          {busy ? "Сохраняю…" : "Сохранить"}
        </Button>
        {dirty && !busy ? (
          <Button
            size="sm"
            variant="ghost"
            className="text-white/50"
            onClick={() => {
              absorb(view);
              setResult(null);
            }}
          >
            Отменить правки
          </Button>
        ) : null}
        {result ? (
          <span role={result.ok ? "status" : "alert"} className={result.ok ? "text-emerald-300" : "text-amber-200"}>
            {result.text}
          </span>
        ) : null}
      </div>
      {when ? <p className="text-[11px] text-white/40">Последнее изменение: {when}.</p> : null}
    </section>
  );
}
