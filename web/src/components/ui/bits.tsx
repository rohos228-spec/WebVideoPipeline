"use client";

import type { ButtonHTMLAttributes, ReactNode, TextareaHTMLAttributes } from "react";

/** Подпись раздела: маленькие прописные, единственная «служебная» гарнитура. */
export function Label({ children }: { children: ReactNode }) {
  return (
    <div className="text-[11px] uppercase tracking-[0.14em] text-content-faint font-medium">
      {children}
    </div>
  );
}

type Tone = "neutral" | "ok" | "warn" | "danger" | "accent";

const toneClass: Record<Tone, string> = {
  neutral: "bg-surface-sunken text-content-muted",
  ok: "bg-ok-muted text-ok",
  warn: "bg-warn-muted text-warn",
  danger: "bg-danger-muted text-danger",
  accent: "bg-accent-muted text-accent",
};

export function Chip({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium ${toneClass[tone]}`}
    >
      {children}
    </span>
  );
}

/** Текстовое поле, которое растёт под содержимое и не выглядит формой. */
export function AutoTextarea({
  className = "",
  serif = false,
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { serif?: boolean }) {
  return (
    <textarea
      rows={3}
      className={
        `w-full resize-none bg-transparent leading-relaxed outline-none ` +
        `placeholder:text-content-faint ${serif ? "font-display" : ""} ${className}`
      }
      {...rest}
    />
  );
}

/** Три полоски: шаг идёт. Не спиннер — спиннер врёт про прогресс. */
export function Working({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-content-muted text-[13px]">
      <span className="inline-flex gap-[3px]" aria-hidden>
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="pulse-line block h-3 w-[2px] bg-accent"
            style={{ animationDelay: `${i * 180}ms` }}
          />
        ))}
      </span>
      {label}
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="text-content-faint text-[13px] py-6">{children}</div>;
}

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

const base =
  "inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors " +
  "disabled:pointer-events-none disabled:opacity-40 whitespace-nowrap";

const variants: Record<Variant, string> = {
  primary: "bg-accent text-content-on-accent hover:bg-accent-hover",
  secondary:
    "bg-surface-raised text-content border border-border-strong hover:bg-surface-sunken",
  ghost: "text-content-muted hover:text-content hover:bg-surface-sunken",
  danger: "text-danger hover:bg-danger-muted",
};

const sizes: Record<Size, string> = {
  sm: "h-7 px-2.5 text-[13px]",
  md: "h-9 px-4 text-[14px]",
};

export function Button({
  variant = "secondary",
  size = "md",
  className = "",
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  children: ReactNode;
}) {
  return (
    <button className={`${base} ${variants[variant]} ${sizes[size]} ${className}`} {...rest}>
      {children}
    </button>
  );
}
