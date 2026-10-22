/**
 * Форматтеры раздела «Расходы»: деньги, кредиты, единицы измерения и подписи
 * видов расхода. Деньги — себестоимость в USD, списания — в кредитах.
 */

import { fmtNum } from "./admin-kit";

/** Доллары: 2 знака; у малых сумм — 4 (или 6 у совсем копеечных), чтобы не было нулей. */
export const fmtUsdCost = (n?: number | null): string => {
  const v = n ?? 0;
  if (!Number.isFinite(v)) return "—";
  const digits = v === 0 ? 2 : Math.abs(v) < 0.01 ? 6 : Math.abs(v) < 1 ? 4 : 2;
  return `$${v.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
};

/** Цена из прайса: null — «нет цены», а не ноль. */
export const fmtPrice = (n?: number | null): string => (n === null || n === undefined ? "—" : fmtUsdCost(n));

/** Кредиты: всегда 2 знака после запятой. */
export const fmtCredits = (n?: number | null): string =>
  (n ?? 0).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Микрокредиты — точное целое (для подсказок). */
export const fmtMicro = (n?: number | null): string => fmtNum(n ?? 0);

const UNIT_LABELS: Record<string, string> = {
  second: "сек",
  seconds: "сек",
  minute: "мин",
  minute_second: "мин:сек",
  item: "шт",
  items: "шт",
  image: "картинка",
  call: "вызов",
  calls: "вызов",
  token: "токен",
  tokens: "токен",
  request: "запрос",
  char: "символ",
  character: "символ",
  megapixel: "Мпикс",
};

/** Единицы вызова: «12 сек», «3 шт». Ноль без единицы — прочерк. */
export const fmtUnits = (units?: number | null, unit?: string | null): string => {
  const value = units ?? 0;
  if (value === 0) return "—";
  const label = unit ? (UNIT_LABELS[unit] ?? unit) : "";
  return `${fmtNum(value)}${label ? " " + label : ""}`;
};

export const KIND_LABELS: Record<string, string> = {
  video: "Видео",
  image: "Картинки",
  llm: "LLM",
  audio: "Аудио",
  music: "Музыка",
  transcript: "Транскрипт",
};

export const kindLabel = (kind?: string | null): string => (kind ? (KIND_LABELS[kind] ?? kind) : "—");

/** Виды для фильтра: контракт перечисляет video/image/llm, медиа-леджер пишет ещё audio/music. */
export const KIND_OPTIONS: { v: string; t: string }[] = [
  { v: "", t: "Все" },
  { v: "video", t: "Видео" },
  { v: "image", t: "Картинки" },
  { v: "audio", t: "Аудио" },
  { v: "llm", t: "LLM" },
];

const isoDay = (d: Date): string => {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
};

/** Период «последние N дней» в ISO-датах (как ждут ручки: YYYY-MM-DD). */
export const costPeriod = (days: number): { from: string; to: string } => {
  const to = new Date();
  const from = new Date();
  from.setDate(from.getDate() - Math.max(0, days - 1));
  return { from: isoDay(from), to: isoDay(to) };
};

/** Короткая дата для подписи столбца: 2026-10-22 → 10-22. */
export const shortDay = (day?: string | null): string => (day && day.length >= 10 ? day.slice(5) : day || "—");
