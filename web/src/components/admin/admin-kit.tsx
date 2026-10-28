"use client";

/**
 * Общие примитивы админ-панели VIA: карточки, цифры, кнопки, таблицы,
 * выдвижная панель, форматтеры. Стиль — как в студии (тёмный, #2dd4bf).
 */
import type { ReactNode } from "react";
import { CircleAlert, CircleDashed, X } from "lucide-react";

export const A = "#2dd4bf";
export const FONT = { fontFamily: "Rubik, sans-serif" } as const;

/* ─────────── форматтеры ─────────── */

export const fmtNum = (n: number | null | undefined) =>
  (n ?? 0).toLocaleString("ru-RU", { maximumFractionDigits: 2 });

export const fmtUsd = (n: number | null | undefined, digits = 2) =>
  `$${(n ?? 0).toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: 4 })}`;

export const fmtRub = (n: number | null | undefined) =>
  `${fmtNum(n)} ₽`;

/**
 * Парсер даты из бэкенда в UTC.
 * Бэкенд возвращает UTC-время (в PostgreSQL/SQLite как 'YYYY-MM-DDTHH:mm:ss' без Z,
 * либо с Z/таймзоной, либо unix-секунды/мс).
 * Добавляем Z, чтобы браузер не интерпретировал время как локальное,
 * и отображаем строго по московскому времени (MSK, UTC+3).
 */
export const parseUtcDate = (s?: string | number | null): Date | null => {
  if (s == null) return null;
  if (typeof s === "number") {
    const ms = s > 1e11 ? s : s * 1000;
    const d = new Date(ms);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  let str = String(s).trim();
  if (!str) return null;
  if (/^\d+$/.test(str)) {
    const num = Number(str);
    const ms = num > 1e11 ? num : num * 1000;
    const d = new Date(ms);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  if (str.includes(" ") && !str.includes("T")) {
    str = str.replace(" ", "T");
  }
  if (str.includes("T") && !/(?:Z|[+-]\d{2}(?::?\d{2})?)$/i.test(str)) {
    str += "Z";
  }
  const d = new Date(str);
  return Number.isNaN(d.getTime()) ? null : d;
};

export const fmtDate = (s?: string | number | null) => {
  const d = parseUtcDate(s);
  if (!d) return s ? String(s).slice(0, 10) : "—";
  return d.toLocaleDateString("ru-RU", {
    timeZone: "Europe/Moscow",
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
};

export const fmtDateTime = (s?: string | number | null) => {
  const d = parseUtcDate(s);
  if (!d) return s ? String(s).replace("T", " ").slice(0, 16) : "—";
  return d.toLocaleString("ru-RU", {
    timeZone: "Europe/Moscow",
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

export const timeAgo = (s?: string | number | null) => {
  const d = parseUtcDate(s);
  if (!d) return "—";
  const diff = Date.now() - d.getTime();
  if (diff < 0 && diff > -60_000) return "только что";
  if (diff < 60_000) return "только что";
  const min = Math.floor(diff / 60_000);
  if (min < 60) return `${min} мин назад`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} ч назад`;
  const days = Math.floor(h / 24);
  if (days < 30) return `${days} дн назад`;
  return fmtDate(s);
};

export const fmtBytes = (n?: number | null) => {
  if (!n) return "—";
  const units = ["Б", "КБ", "МБ", "ГБ"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
};

/* ─────────── каркас ─────────── */

export function Card({
  title,
  icon,
  right,
  children,
  className = "",
}: {
  title?: string;
  icon?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={"rounded-[16px] border border-white/[0.07] bg-white/[0.025] p-4 " + className}>
      {title ? (
        <div className="mb-3 flex items-center gap-2">
          {icon ? <span style={{ color: A }}>{icon}</span> : null}
          <span className="text-[13px] font-medium uppercase tracking-[0.08em] text-zinc-400">{title}</span>
          <div className="flex-1" />
          {right}
        </div>
      ) : null}
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  hint,
  extra,
  tone,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  extra?: string;
  tone?: "ok" | "warn" | "bad";
}) {
  const color = tone === "ok" ? A : tone === "warn" ? "#fbbf24" : tone === "bad" ? "#f87171" : "#ffffff";
  return (
    <div className="rounded-[12px] bg-black/25 px-4 py-3">
      <div className="text-[26px] font-medium leading-tight" style={{ color }}>
        {value}
      </div>
      <div className="mt-0.5 text-[13px] text-zinc-400">{label}</div>
      {hint ? <div className="mt-0.5 text-[12px] text-zinc-600">{hint}</div> : null}
      {extra ? <div className="mt-0.5 text-[12px] text-zinc-500">{extra}</div> : null}
    </div>
  );
}

export function Btn({
  children,
  onClick,
  accent,
  danger,
  disabled,
  title,
  type = "button",
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  accent?: boolean;
  danger?: boolean;
  disabled?: boolean;
  title?: string;
  type?: "button" | "submit";
  className?: string;
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={
        "inline-flex items-center justify-center gap-2 rounded-[10px] px-3.5 py-2 text-[14px] transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-45 " +
        (danger
          ? "bg-white/[0.04] text-zinc-300 hover:bg-red-500/12 hover:text-red-300 "
          : accent
            ? ""
            : "bg-white/[0.05] text-zinc-200 hover:bg-white/[0.09] ") +
        className
      }
      style={accent ? { background: A + "24", color: A } : undefined}
    >
      {children}
    </button>
  );
}

export function Seg<T extends string | number>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { v: T; t: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex gap-1 rounded-[11px] bg-black/30 p-1">
      {options.map((o) => (
        <button
          key={String(o.v)}
          type="button"
          onClick={() => onChange(o.v)}
          className="flex-1 whitespace-nowrap rounded-[8px] px-3 py-1.5 text-[14px] transition-all duration-200"
          style={value === o.v ? { background: A + "26", color: A } : { color: "#a1a1aa" }}
        >
          {o.t}
        </button>
      ))}
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[13px] text-zinc-400">{label}</span>
      {children}
      {hint ? <span className="text-[12px] text-zinc-600">{hint}</span> : null}
    </label>
  );
}

export const inputCls =
  "h-9 w-full rounded-[9px] border border-white/[0.08] bg-black/35 px-3 text-[14px] text-white outline-none transition-colors placeholder:text-zinc-600 focus:border-[#2dd4bf]/60";

export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={inputCls + " " + (props.className ?? "")} />;
}

export function TextArea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={"min-h-[72px] w-full rounded-[9px] border border-white/[0.08] bg-black/35 px-3 py-2 text-[14px] text-white outline-none transition-colors placeholder:text-zinc-600 focus:border-[#2dd4bf]/60 " + (props.className ?? "")}
    />
  );
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={"h-9 w-full rounded-[9px] border border-white/[0.08] bg-black/35 px-2.5 text-[14px] text-white outline-none transition-colors focus:border-[#2dd4bf]/60 " + (props.className ?? "")}
    />
  );
}

export function Toggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={() => onChange(!on)}
      className="relative h-[24px] w-[42px] shrink-0 rounded-full transition-colors duration-200"
      style={{ background: on ? A : "rgba(255,255,255,0.12)" }}
    >
      <span className="absolute top-[3px] h-[18px] w-[18px] rounded-full bg-white shadow transition-all duration-200" style={{ left: on ? 21 : 3 }} />
    </button>
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "ok" | "warn" | "bad" | "info" | "neutral";
}) {
  const map = {
    ok: { bg: A + "22", fg: A },
    warn: { bg: "rgba(251,191,36,.14)", fg: "#fbbf24" },
    bad: { bg: "rgba(248,113,113,.14)", fg: "#f87171" },
    info: { bg: "rgba(56,189,248,.14)", fg: "#38bdf8" },
    neutral: { bg: "rgba(255,255,255,.07)", fg: "#d4d4d8" },
  }[tone];
  return (
    <span className="inline-flex items-center rounded-[6px] px-1.5 py-0.5 text-[12px] font-medium" style={{ background: map.bg, color: map.fg }}>
      {children}
    </span>
  );
}

export function Empty({ text, action }: { text: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-[12px] bg-black/20 px-4 py-8 text-center text-[14px] text-zinc-500">
      {text}
      {action}
    </div>
  );
}

export function Skel({ h = 80, className = "" }: { h?: number; className?: string }) {
  return <div className={"animate-pulse rounded-[12px] bg-white/[0.04] " + className} style={{ height: h }} />;
}

export function Spinner({ className = "h-4 w-4" }: { className?: string }) {
  return <CircleDashed className={className + " animate-spin text-zinc-500"} />;
}

export function ErrorBox({ err, retry }: { err: unknown; retry: () => void }) {
  return (
    <div className="flex items-center gap-3 rounded-[12px] bg-red-500/[0.07] px-4 py-3 text-[14px] text-red-300">
      <CircleAlert className="h-4 w-4" />
      {String((err as Error)?.message || err)}
      <div className="flex-1" />
      <Btn onClick={retry}>Повторить</Btn>
    </div>
  );
}

/* ─────────── таблица ─────────── */

export const tableWrap = "overflow-x-auto rounded-[12px] border border-white/[0.06]";
export const tableCls = "w-full min-w-[640px] border-collapse text-left text-[14px]";
export const thCls =
  "whitespace-nowrap border-b border-white/[0.07] bg-black/25 px-3 py-2 text-[12px] font-medium uppercase tracking-[0.06em] text-zinc-500";
export const tdCls = "border-b border-white/[0.04] px-3 py-2 align-middle text-zinc-300";

export function Pager({
  total,
  limit,
  offset,
  onChange,
}: {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}) {
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);
  return (
    <div className="mt-2 flex items-center gap-3 text-[13px] text-zinc-500">
      <span>
        {fmtNum(from)}–{fmtNum(to)} из {fmtNum(total)}
      </span>
      <div className="flex-1" />
      <Btn disabled={offset <= 0} onClick={() => onChange(Math.max(0, offset - limit))}>
        Назад
      </Btn>
      <Btn disabled={to >= total} onClick={() => onChange(offset + limit)}>
        Дальше
      </Btn>
    </div>
  );
}

/* ─────────── выдвижная панель ─────────── */

export function Drawer({
  open,
  title,
  subtitle,
  onClose,
  children,
  width = 560,
  extraHeader,
}: {
  open: boolean;
  title: ReactNode;
  subtitle?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  width?: number | string;
  extraHeader?: ReactNode;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[80] flex justify-end" style={FONT}>
      <button type="button" aria-label="Закрыть" className="absolute inset-0 bg-black/55 backdrop-blur-[2px]" onClick={onClose} />
      <aside
        className={`relative flex h-full ${width === "100vw" || width === "100%" ? "w-screen" : "max-w-full"} flex-col overflow-hidden border-l border-white/10 bg-[#141518] shadow-2xl transition-[width] duration-200`}
        style={{ width, animation: "vpDrawerIn .22s ease both" }}
      >
        <style>{`@keyframes vpDrawerIn{from{transform:translateX(24px);opacity:.5}to{transform:none;opacity:1}}`}</style>
        <div className="flex items-start gap-3 border-b border-white/[0.07] px-4 py-3.5">
          <div className="min-w-0 flex-1">
            <div className="truncate text-[17px] font-medium text-white">{title}</div>
            {subtitle ? <div className="mt-0.5 truncate text-[13px] text-zinc-500">{subtitle}</div> : null}
          </div>
          {extraHeader}
          <button type="button" onClick={onClose} aria-label="Закрыть" className="flex h-8 w-8 items-center justify-center rounded-[8px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-white">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div>
      </aside>
    </div>
  );
}

/* ─────────── мини-график (столбцы, без зависимостей) ─────────── */

export function MiniBars({
  points,
  height = 120,
  format,
  accent = A,
}: {
  points: { label: string; value: number; hint?: string }[];
  height?: number;
  format?: (n: number) => string;
  accent?: string;
}) {
  const max = Math.max(1, ...points.map((p) => p.value));
  return (
    <div>
      <div className="flex items-end gap-[2px]" style={{ height }}>
        {points.map((p, i) => (
          <div key={p.label + i} className="group flex h-full flex-1 flex-col justify-end" title={`${p.label}: ${format ? format(p.value) : fmtNum(p.value)}${p.hint ? " · " + p.hint : ""}`}>
            <div
              className="w-full rounded-[3px] transition-all duration-500 group-hover:opacity-80"
              style={{ height: Math.max(p.value > 0 ? 3 : 1, (p.value / max) * (height - 6)), background: p.value > 0 ? accent : "#3f3f46" }}
            />
          </div>
        ))}
      </div>
      {points.length ? (
        <div className="mt-1.5 flex justify-between text-[12px] text-zinc-600">
          <span>{points[0]?.label}</span>
          <span>{points[points.length - 1]?.label}</span>
        </div>
      ) : null}
    </div>
  );
}
