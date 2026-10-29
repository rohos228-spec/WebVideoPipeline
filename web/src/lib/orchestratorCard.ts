/** Тексты и раскладка карточки подтверждения и ленты чата оркестратора: суммы, цена, шаги. */

const LONG_MONEY = /\$(\d+\.\d{3,})(?!\d)/g;

/** Сумма до центов; меньше цента так и пишем, а не «$0.00». */
export function moneyCents(value: number): string {
  if (value > 0 && value < 0.005) return "меньше $0.01";
  // Сдвиг через запись «1.005e2»: у числа 1.005 лежит 1.00499…, и toFixed округлил бы вниз.
  const cents = Math.round(Number(`${value}e2`));
  return Number.isFinite(cents) ? `$${(cents / 100).toFixed(2)}` : `$${value.toFixed(2)}`;
}

/** «$0.4415» в тексте ответа → «$0.44». Суммы с двумя знаками и меньше не трогаем. */
export function roundMoneyText(text: string): string {
  return text.replace(LONG_MONEY, (_match, num: string) => moneyCents(Number(num)));
}

export function pluralRu(count: number, one: string, few: string, many: string): string {
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return many;
  if (count % 10 === 1) return one;
  if (count % 10 >= 2 && count % 10 <= 4) return few;
  return many;
}

export interface CardPriceInput {
  costEstimate: number | null;
  costCredits: { low: number; high: number } | null;
  costText: string;
  /** Хоть один шаг карточки платный. */
  paid: boolean;
  /** Последствия шага: «воркер сам пройдёт конвейер и потратит деньги» — траты будут позже. */
  effects?: string[];
}

const SPENDS_LATER = /потрат\S* деньги|платные (?:шаги|вызовы)/i;

export interface CardPrice {
  text: string;
  /** Цена неизвестна: карточка выделяет её жёлтым. */
  unknown: boolean;
}

/**
 * Цена для карточки. «около $0» у шага, который тратит деньги, не пишем никогда:
 * оценка $0 при платных шагах значит «сумму заранее не посчитать».
 */
export function cardPrice({ costEstimate, costCredits, costText, paid: paidSteps, effects = [] }: CardPriceInput): CardPrice {
  const paid = paidSteps || effects.some((text) => SPENDS_LATER.test(text));
  if (costEstimate != null && Number.isFinite(costEstimate) && costEstimate > 0) {
    return { text: `около ${moneyCents(costEstimate)}`, unknown: false };
  }
  if (costCredits) {
    const { low, high } = costCredits;
    return { text: low === high ? `${low} кр.` : `${low}–${high} кр.`, unknown: false };
  }
  const text = roundMoneyText(costText.trim());
  const unknownText = /^неизвестн/i.test(text);
  if (text && !unknownText && !(paid && /\$0(?:\.0+)?(?!\.?\d)/.test(text))) return { text, unknown: false };
  if (unknownText) return { text: `Цена ${text}`, unknown: true };
  if (paid) return { text: "Цена неизвестна, но траты будут", unknown: true };
  if (costEstimate === 0) return { text: "бесплатно", unknown: false };
  return { text: "Цена неизвестна", unknown: true };
}

/** «проект #5: шаг 1: …» → «проект #5: …»: номер шага плана человеку ничего не говорит. */
export function blockReasonText(text: string): string {
  return roundMoneyText(text.replace(/^(проект #\d+: )?шаг \d+: /, "$1"));
}

export interface CardStepLike {
  title: string;
  paid: boolean;
  risk: string;
}

/**
 * Шаги карточки: что меняется (main) и что только читается (reads). Четырнадцать строк
 * «Статус проекта» перед одним запуском сворачиваются в одну строку.
 */
export function splitCardSteps<T extends CardStepLike>(steps: T[]): { main: T[]; reads: string } {
  const isRead = (step: T) => !step.paid && step.risk === "none";
  const main = steps.filter((step) => !isRead(step));
  if (main.length === 0) return { main: steps, reads: "" };
  const counts = new Map<string, number>();
  for (const step of steps) {
    if (!isRead(step)) continue;
    const title = step.title.charAt(0).toLowerCase() + step.title.slice(1);
    counts.set(title, (counts.get(title) ?? 0) + 1);
  }
  const reads = [...counts]
    .map(([title, count]) => (count > 1 ? `${title} (${count} ${pluralRu(count, "раз", "раза", "раз")})` : title))
    .join(", ");
  return { main, reads };
}

const DONE_STATUSES = new Set(["done", "failed", "cancelled", "killed"]);

/** Счётчик на вкладке задач: только те, что ещё идут или ждут, а не все за всё время. */
export function activeTaskCount(tasks: { status: string }[]): number {
  return tasks.filter((task) => !DONE_STATUSES.has(task.status)).length;
}

export function tasksTabLabel(tasks: { status: string }[]): string {
  const active = activeTaskCount(tasks);
  return active > 0 ? `Задачи (${active} в работе)` : "Задачи";
}

/**
 * «Подробности» под ответом нужны, когда задача что-то меняла, тратила или упала.
 * Ответ на вопрос (одни чтения) уже сказан в сообщении — ссылка была бы шумом.
 * Флаг mutating ставит планировщик и может забыть: риск шага выше «none» тоже значит
 * изменение, «none» справочник даёт только чтениям.
 */
export function detailsWorthShowing(
  plan: { mutating: boolean; paid: boolean; risk?: string }[],
  steps: { paid: boolean; status: string }[],
): boolean {
  if (steps.some((step) => step.paid || step.status === "failed")) return true;
  if (plan.length === 0) return true;
  return plan.some((step) => step.mutating || step.paid || (step.risk ?? "none") !== "none");
}

/** Как builtin_insight._TYPE_RU на сервере: одна нода называется одинаково в карточке и в ответе. */
const NODE_TYPE_RU: Record<string, string> = {
  plan: "План",
  script: "Закадровый текст",
  split: "Разбивка",
  hero: "Персонажи",
  items: "Предметы",
  image_prompts: "Промты картинок",
  images: "Картинки",
  animation_prompts: "Промты анимации",
  videos: "Видео",
  audio: "Озвучка",
  music: "Музыка",
  assemble: "Сборка",
  publish: "Публикация",
  topic: "Тема",
  storage: "Хранилище",
  excel_gpt: "Работа с GPT",
  sd_agent: "Агент сцен",
  sd_assemble: "Сборка сцен",
  scene_design: "Дизайн сцен",
  sfx_plan: "План звуков",
  sfx_gen: "Звуки",
  shot_menu: "Меню съёмки",
  excel_feed: "Вход Excel",
};

/** Название ноды по ключу (n_excel_gpt_2 → «Работа с GPT 2»); незнакомый ключ — пустая строка, не код. */
export function nodeKeyRu(key: string): string {
  const found = /^n_([a-z_]+?)(?:_(\d+))?$/.exec(key.trim());
  if (!found) return "";
  const name = NODE_TYPE_RU[found[1]];
  if (!name) return "";
  return found[2] ? `${name} ${found[2]}` : name;
}
