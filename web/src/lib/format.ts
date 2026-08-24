/** Мелкая правка чисел и дат — чтобы в разметке не было арифметики. */

/** «0.033» → «0,03 кр»; ноль показываем словом, а не нулём. */
export function credits(value: string | number): string {
  const n = typeof value === "number" ? value : Number.parseFloat(value || "0");
  if (!Number.isFinite(n) || n <= 0) return "бесплатно";
  const digits = n < 1 ? 2 : n < 10 ? 2 : 1;
  return `${n.toFixed(digits).replace(".", ",")} кр`;
}

export function seconds(value: number | null | undefined): string {
  if (!value || !Number.isFinite(value)) return "—";
  return `${value.toFixed(1).replace(".", ",")} с`;
}

export function relativeDate(iso: string): string {
  const then = new Date(iso.endsWith("Z") ? iso : `${iso}Z`).getTime();
  if (!Number.isFinite(then)) return "";
  const diff = Date.now() - then;
  const mins = Math.round(diff / 60_000);
  if (mins < 1) return "только что";
  if (mins < 60) return `${mins} мин назад`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} ч назад`;
  const days = Math.round(hours / 24);
  return days === 1 ? "вчера" : `${days} дн назад`;
}

/** Заголовок проекта из идеи: первая мысль, без хвоста на пол-экрана. */
export function titleFromIdea(idea: string): string {
  const clean = idea.trim().replace(/\s+/g, " ");
  if (!clean) return "Новый ролик";
  const stop = clean.search(/[.!?…]\s/);
  const head = stop > 12 ? clean.slice(0, stop) : clean;
  return head.length > 70 ? `${head.slice(0, 67).trimEnd()}…` : head;
}

/** Показное имя ролика: у старых проектов title пустой. */
export function projectName(p: { title: string | null; slug?: string }): string {
  return p.title?.trim() || p.slug || "Без названия";
}
