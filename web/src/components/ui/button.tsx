"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";

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
