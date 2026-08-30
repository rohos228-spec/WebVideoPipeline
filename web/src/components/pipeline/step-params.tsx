"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/bits";
import type { ElevenLabsVoice, StepParams } from "@/lib/types";

/**
 * Параметры шага — то, что шаг читает из `meta.node_step_params[step]`.
 *
 * Не промт и не модель, а числа и тумблеры: длина ролика, лимиты символов
 * в кадре, голос озвучки, хвост после озвучки, громкость музыки, субтитры.
 * Раньше это была отдельная «студия ноды»; теперь — раздел инспектора у
 * узла, к которому параметры относятся. Список полей на шаг здесь, потому
 * что читают их шаги на сервере по этим самым ключам — см.
 * `app/services/node_step_params.py`; менять имя поля можно только там и тут.
 *
 * Числа сохраняются кнопкой, тумблеры — сразу: у тумблера два состояния и
 * промах не стоит ничего, а недописанное число уезжать не должно.
 */

const CHARS_PER_SEC = 14;

export function StepParamsPanel({
  step,
  params,
  voices,
  bgmDefault,
  busy,
  onSave,
}: {
  step: string;
  params: StepParams;
  voices: ElevenLabsVoice[];
  /** `meta.bgm_level` проекта — дефолт для громкости, если у сборки своя не задана. */
  bgmDefault: number | null;
  busy: boolean;
  /** Патч бакета шага; сервер мержит по ключам, `null` пропускает. */
  onSave: (step: string, patch: Record<string, unknown>) => void;
}) {
  const own = params[step] ?? {};

  if (step === "plan" || step === "script") {
    const inherited = step === "script" ? numberOf(params.plan?.duration_seconds) : null;
    return (
      <DurationField
        key={step}
        value={numberOf(own.duration_seconds)}
        inherited={inherited}
        busy={busy}
        onSave={(v) => onSave(step, { duration_seconds: v ?? 0 })}
      />
    );
  }

  if (step === "split") {
    return <SplitFields key={step} own={own} busy={busy} onSave={(patch) => onSave(step, patch)} />;
  }

  if (step === "audio") {
    const current = (own.elevenlabs_voice_id as string) || "";
    return (
      <div>
        <Label>Голос озвучки</Label>
        <select
          value={current}
          disabled={busy}
          onChange={(e) => onSave(step, { elevenlabs_voice_id: e.target.value || "" })}
          className="mt-1.5 w-full rounded-sm border border-border bg-surface-raised px-2 py-1 text-[13px] text-content"
        >
          <option value="">по умолчанию</option>
          {voices.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
              {v.description ? ` — ${v.description}` : ""}
            </option>
          ))}
        </select>
      </div>
    );
  }

  if (step === "assemble") {
    return (
      <AssembleFields key={step} own={own} bgmDefault={bgmDefault} busy={busy} onSave={(patch) => onSave(step, patch)} />
    );
  }

  return null;
}

/** Есть ли у шага параметры вообще — чтобы не рисовать пустой раздел. */
export function stepHasParams(step: string | null | undefined): boolean {
  return step === "plan" || step === "script" || step === "split" || step === "audio" || step === "assemble";
}

function numberOf(v: unknown): number | null {
  const n = typeof v === "number" ? v : typeof v === "string" ? Number.parseFloat(v) : NaN;
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** Как `numberOf`, но ноль — значение, а не «не задано»: громкость 0 — тишина. */
function numberOrZero(v: unknown): number | null {
  const n = typeof v === "number" ? v : typeof v === "string" ? Number.parseFloat(v) : NaN;
  return Number.isFinite(n) && n >= 0 ? n : null;
}

function DurationField({
  value,
  inherited,
  busy,
  onSave,
}: {
  value: number | null;
  inherited: number | null;
  busy: boolean;
  onSave: (v: number | null) => void;
}) {
  const [text, setText] = useState(value?.toString() ?? "");
  useEffect(() => setText(value?.toString() ?? ""), [value]);
  const n = numberOf(text);
  const dirty = (n ?? null) !== (value ?? null);
  return (
    <div>
      <Label>Длина ролика, секунд</Label>
      <div className="mt-1.5 flex items-center gap-2">
        <input
          type="number"
          min={1}
          value={text}
          placeholder={inherited ? String(inherited) : "не задана"}
          onChange={(e) => setText(e.target.value)}
          className="w-28 rounded-sm border border-border bg-surface-raised px-2 py-1 text-[13px] tabular-nums text-content outline-none focus:border-accent"
        />
        <span className="font-mono text-[11px] text-content-faint">
          {n ? `≈ ${Math.round(n * CHARS_PER_SEC)} символов` : inherited ? `наследует из сценария: ${inherited} с` : "—"}
        </span>
        <Button size="sm" variant={dirty ? "primary" : "secondary"} disabled={!dirty || busy} onClick={() => onSave(n)}>
          Сохранить
        </Button>
      </div>
      <p className="mt-1 text-[11px] text-content-faint">
        Подставляется в текст для модели как ориентир длины. Пусто — модель решает сама.
      </p>
    </div>
  );
}

const SPLIT_FIELDS: { key: string; label: string; hint: number }[] = [
  { key: "cell_min_chars", label: "Мин. символов в кадре", hint: 30 },
  { key: "cell_max_chars", label: "Макс. символов в кадре", hint: 120 },
  { key: "cell_avg_min", label: "Среднее от", hint: 50 },
  { key: "cell_avg_max", label: "Среднее до", hint: 80 },
];

function SplitFields({
  own,
  busy,
  onSave,
}: {
  own: Record<string, unknown>;
  busy: boolean;
  onSave: (patch: Record<string, unknown>) => void;
}) {
  const initial = () => Object.fromEntries(SPLIT_FIELDS.map((f) => [f.key, numberOf(own[f.key])?.toString() ?? ""]));
  const [form, setForm] = useState<Record<string, string>>(initial);
  // Сервер прислал новые значения — форма следует за ними, пока её не трогали.
  const snapshot = JSON.stringify(SPLIT_FIELDS.map((f) => own[f.key] ?? null));
  useEffect(() => setForm(initial()), [snapshot]); // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = SPLIT_FIELDS.some((f) => (numberOf(form[f.key]) ?? null) !== (numberOf(own[f.key]) ?? null));
  return (
    <div>
      <Label>Лимиты текста в кадре</Label>
      <div className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-2">
        {SPLIT_FIELDS.map((f) => (
          <label key={f.key} className="text-[11px] text-content-muted">
            {f.label}
            <input
              type="number"
              min={1}
              value={form[f.key]}
              placeholder={String(f.hint)}
              onChange={(e) => setForm((cur) => ({ ...cur, [f.key]: e.target.value }))}
              className="mt-0.5 w-full rounded-sm border border-border bg-surface-raised px-2 py-1 text-[13px] tabular-nums text-content outline-none focus:border-accent"
            />
          </label>
        ))}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <Button
          size="sm"
          variant={dirty ? "primary" : "secondary"}
          disabled={!dirty || busy}
          onClick={() => onSave(Object.fromEntries(SPLIT_FIELDS.map((f) => [f.key, numberOf(form[f.key]) ?? 0])))}
        >
          Сохранить
        </Button>
        <span className="text-[11px] text-content-faint">Пусто — значение по умолчанию (в серых подсказках).</span>
      </div>
    </div>
  );
}

function AssembleFields({
  own,
  bgmDefault,
  busy,
  onSave,
}: {
  own: Record<string, unknown>;
  bgmDefault: number | null;
  busy: boolean;
  onSave: (patch: Record<string, unknown>) => void;
}) {
  const tail = numberOrZero(own.post_voiceover_tail_seconds) ?? 0;
  const bgm = numberOrZero(own.bgm_level) ?? bgmDefault ?? 35;
  const subtitles = own.subtitles_enabled === true;
  const toMain = own.send_to_main_pc === true;
  const skipOn = own.skip_intro_enabled === true;
  const skip = numberOf(own.skip_intro_seconds) ?? 0.5;

  return (
    <div className="space-y-3">
      <Stepper
        label="Хвост видео после озвучки, с"
        value={tail}
        min={0}
        max={120}
        step={1}
        busy={busy}
        onChange={(v) => onSave({ post_voiceover_tail_seconds: v })}
      />
      <Stepper
        label="Громкость фоновой музыки"
        value={bgm}
        min={0}
        max={100}
        step={5}
        suffix="%"
        busy={busy}
        onChange={(v) => onSave({ bgm_level: v })}
      />
      <Toggle label="Субтитры в ролике" checked={subtitles} busy={busy} onChange={(v) => onSave({ subtitles_enabled: v })} />
      <Toggle
        label="Отправить на основной ПК"
        hint="после сборки ролик уедет в очередь монтажа основной машины"
        checked={toMain}
        busy={busy}
        onChange={(v) => onSave({ send_to_main_pc: v })}
      />
      <Toggle
        label="Не учитывать первые секунды"
        hint="обрезать начало готового ролика"
        checked={skipOn}
        busy={busy}
        onChange={(v) => onSave({ skip_intro_enabled: v, skip_intro_seconds: skipOn ? skip : 0.5 })}
      />
      {skipOn && (
        <Stepper
          label="Сколько секунд обрезать"
          value={skip}
          min={0}
          max={2}
          step={0.1}
          digits={2}
          busy={busy}
          onChange={(v) => onSave({ skip_intro_seconds: v })}
        />
      )}
    </div>
  );
}

function Stepper({
  label,
  value,
  min,
  max,
  step,
  digits = 0,
  suffix,
  busy,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  digits?: number;
  suffix?: string;
  busy: boolean;
  onChange: (v: number) => void;
}) {
  const clamp = (v: number) => Math.min(max, Math.max(min, Number(v.toFixed(digits))));
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-[12px] text-content-muted">{label}</span>
      <span className="inline-flex items-center gap-1">
        <button
          type="button"
          disabled={busy || value <= min}
          onClick={() => onChange(clamp(value - step))}
          className="h-6 w-6 rounded-sm border border-border text-content-muted hover:bg-surface-sunken disabled:opacity-40"
        >
          −
        </button>
        <span className="min-w-[64px] text-center font-mono text-[12px] tabular-nums text-content">
          {value.toFixed(digits).replace(".", ",")}
          {suffix ? ` ${suffix}` : ""}
        </span>
        <button
          type="button"
          disabled={busy || value >= max}
          onClick={() => onChange(clamp(value + step))}
          className="h-6 w-6 rounded-sm border border-border text-content-muted hover:bg-surface-sunken disabled:opacity-40"
        >
          +
        </button>
      </span>
    </div>
  );
}

export function Toggle({
  label,
  hint,
  checked,
  busy,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  busy?: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-start justify-between gap-3">
      <span className="min-w-0">
        <span className="block text-[12px] text-content-muted">{label}</span>
        {hint && <span className="block text-[11px] text-content-faint">{hint}</span>}
      </span>
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={busy}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--color-accent)]"
      />
    </label>
  );
}
