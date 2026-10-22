/**
 * Карточка NII 67 на клиенте.
 * Форма совпадает с app/services/nii67_card.py: две двери, блоки A–F и свой,
 * один ГГ, общий финал. Сервер ещё раз нормализует карточку при сохранении.
 * «Сгенерировать сценарий» заполняет миры и тексты; оператор загружает видео
 * и может править любые сгенерированные поля.
 */

export const GENERATE_PENDING = "Строю раскадровку по плану двух дверей…";
export const GENERATE_HINT =
  "Достаточно загрузить исходное видео. Дальше откройте карточку и добавьте блок в дверь — кадры соберутся автоматически, условия можно уточнить в «Доп. данных для промтов».";

import type { ProjectDetail } from "@/lib/types";

export const TEXT_LIMIT = 4000;
export const FINALE_LIMIT = 500;
export const SHORT_LIMIT = 180;

export const BLOCK_KINDS = ["death", "creature", "neutral", "back"] as const;

export type BlockKind = (typeof BLOCK_KINDS)[number];

export const CANONICAL_KINDS: BlockKind[] = [];

export const KIND_LABEL: Record<BlockKind, string> = {
  death: "Смерть",
  creature: "Существо",
  neutral: "Действие",
  back: "Возвращается обратно",
};

export const KIND_MARK: Record<BlockKind, string> = {
  death: "☠",
  creature: "◉",
  neutral: "▣",
  back: "↩",
};

/** Какой ход двери включает выбранный блок и какие кадры собираются. */
export const BLOCK_VARIANT: Record<
  BlockKind,
  { move: MatrixMove; creature_action?: MatrixCreatureAction }
> = {
  death: { move: "death_world" },
  creature: { move: "creature", creature_action: "interact" },
  neutral: { move: "neutral" },
  back: { move: "scout_exit" },
};

export const CREATURE_TYPES = [
  { id: "human", label: "Человек" },
  { id: "beast", label: "Зверь" },
  { id: "spirit", label: "Дух" },
  { id: "machine", label: "Механизм" },
  { id: "myth", label: "Мифическое" },
  { id: "other", label: "Другое" },
] as const;

export const TEMPERAMENTS = [
  { id: "calm", label: "Спокойный" },
  { id: "mysterious", label: "Загадочный" },
  { id: "friendly", label: "Дружелюбный" },
  { id: "wary", label: "Осторожный" },
  { id: "harsh", label: "Жёсткий" },
] as const;

const CREATURE_IDS = new Set<string>(CREATURE_TYPES.map((item) => item.id));
const TEMPER_IDS = new Set<string>(TEMPERAMENTS.map((item) => item.id));

export interface Nii67Character {
  enabled: boolean;
  name: string;
  creature_type: string;
  temperament: string;
  position: string;
  behavior: string;
  appearance: string;
}

/** Исход существа для блока kind=creature. */
export type BlockOutcome = "" | "kill" | "run" | "interact";

export const BLOCK_OUTCOME_OPTIONS: { id: BlockOutcome; label: string }[] = [
  { id: "", label: "Исход не задан" },
  { id: "kill", label: "Убивает" },
  { id: "run", label: "Герой убегает" },
  { id: "interact", label: "Взаимодействует" },
];

const BLOCK_OUTCOME_IDS = new Set<string>(
  BLOCK_OUTCOME_OPTIONS.map((option) => option.id).filter(Boolean),
);

export function asBlockOutcome(raw: unknown): BlockOutcome {
  const value = typeof raw === "string" ? raw : "";
  return (BLOCK_OUTCOME_IDS.has(value) ? value : "") as BlockOutcome;
}

export interface Nii67Block {
  id: string;
  kind: BlockKind;
  collapsed: boolean;
  text: string;
  text2: string;
  image_prompt: string;
  image_name: string;
  image_name2: string;
  outcome: BlockOutcome;
  custom_title: string;
  exit_action: string;
  exit_animation: string;
  room_after: string;
  character: Nii67Character;
}

export interface Nii67MatrixState {
  death_action: string;
  death_cause: string;
  death_why: string;
  death_trace: string;
  illustration: string;
  place: string;
  interaction_result: string;
  world_description: string;
  environment: string;
  death_text: string;
  world_text: string;
}

export type MatrixMove = "" | "death_world" | "neutral" | "creature" | "scout_exit";
export type MatrixCreatureAction = "" | "kill" | "run" | "interact";

export const MATRIX_MOVE_OPTIONS: { id: MatrixMove; label: string }[] = [
  { id: "", label: "Авто — из плана" },
  { id: "death_world", label: "Смерть от мира" },
  { id: "neutral", label: "Нейтральный проход" },
  { id: "creature", label: "Существо" },
  { id: "scout_exit", label: "Уход обратно в дверь" },
];

export const MATRIX_CREATURE_ACTION_OPTIONS: { id: MatrixCreatureAction; label: string }[] = [
  { id: "", label: "Выбери действие" },
  { id: "kill", label: "Убивает" },
  { id: "run", label: "Герой убегает" },
  { id: "interact", label: "Взаимодействует" },
];

export const MATRIX_MOVE_SHORT: Record<MatrixMove, string> = {
  "": "авто",
  death_world: "смерть",
  neutral: "проход",
  creature: "существо",
  scout_exit: "уход",
};

export const MATRIX_CREATURE_ACTION_SHORT: Record<MatrixCreatureAction, string> = {
  "": "авто",
  kill: "убивает",
  run: "убегает",
  interact: "контакт",
};

export const MATRIX_STATE_FIELDS: {
  key: keyof Nii67MatrixState;
  label: string;
  rows: number;
  hint: string;
}[] = [
  { key: "death_action", label: "Действие перед смертью", rows: 2, hint: "что герой пытается сделать" },
  { key: "death_cause", label: "От чего умер", rows: 1, hint: "причина смерти" },
  { key: "death_why", label: "Почему не спасся", rows: 2, hint: "что помешало" },
  { key: "death_trace", label: "След на месте", rows: 1, hint: "что осталось" },
  { key: "illustration", label: "Иллюстрация исхода", rows: 2, hint: "тематическая картинка" },
  { key: "place", label: "Куда пришёл", rows: 1, hint: "для нейтрального прохода" },
  { key: "interaction_result", label: "Результат взаимодействия", rows: 2, hint: "для существа interact" },
  { key: "world_description", label: "Что за мир", rows: 2, hint: "чем мир отличается" },
  { key: "environment", label: "Что меняется вокруг", rows: 1, hint: "среда кадров" },
  { key: "death_text", label: "Готовый текст смерти", rows: 2, hint: "необязательно" },
  { key: "world_text", label: "Готовый текст мира", rows: 2, hint: "необязательно" },
];

export function emptyMatrixState(): Nii67MatrixState {
  return {
    death_action: "",
    death_cause: "",
    death_why: "",
    death_trace: "",
    illustration: "",
    place: "",
    interaction_result: "",
    world_description: "",
    environment: "",
    death_text: "",
    world_text: "",
  };
}

export interface Nii67Door {
  id: "door1" | "door2";
  title: string;
  branch: string;
  theme: string;
  theme_note: string;
  image_prompt: string;
  image_name: string;
  blocks: Nii67Block[];
  move: MatrixMove;
  creature_action: MatrixCreatureAction;
  state: Nii67MatrixState;
}

export const PORTAL_FIELDS = [
  "image_name",
  "video_name",
  "left_design",
  "right_design",
  "left_anim",
  "right_anim",
  "left_image_name",
  "right_image_name",
] as const;
export type PortalField = (typeof PORTAL_FIELDS)[number];
export type Nii67Portal = Record<PortalField, string>;

export function defaultPortal(): Nii67Portal {
  return {
    image_name: "",
    video_name: "",
    left_design: "",
    right_design: "",
    left_anim: "",
    right_anim: "",
    left_image_name: "",
    right_image_name: "",
  };
}

export type PlanOutcome = "" | "death" | "escape" | "transformation";
export interface Nii67PlanDoor {
  world: string;
  happens: string;
  outcome: PlanOutcome;
  category: string;
}
export interface Nii67Plan {
  door1: Nii67PlanDoor;
  door2: Nii67PlanDoor;
  finale: string;
}
export const PLAN_LIMIT = 1200;
export const PLAN_OUTCOMES: { id: PlanOutcome; label: string }[] = [
  { id: "", label: "Исход: решит раскадровка" },
  { id: "death", label: "Смерть" },
  { id: "escape", label: "Спасение" },
  { id: "transformation", label: "Превращение" },
];

export function defaultPlan(): Nii67Plan {
  const door = (): Nii67PlanDoor => ({ world: "", happens: "", outcome: "", category: "" });
  return { door1: door(), door2: door(), finale: "" };
}

function asPlan(raw: unknown): Nii67Plan {
  const out = defaultPlan();
  if (!raw || typeof raw !== "object") return out;
  const src = raw as Record<string, unknown>;
  for (const id of ["door1", "door2"] as const) {
    const d = src[id] && typeof src[id] === "object" ? (src[id] as Record<string, unknown>) : {};
    const outcome = typeof d.outcome === "string" ? d.outcome : "";
    out[id] = {
      world: clip(typeof d.world === "string" ? d.world : "", PLAN_LIMIT),
      happens: clip(typeof d.happens === "string" ? d.happens : "", PLAN_LIMIT),
      outcome: (["death", "escape", "transformation"].includes(outcome) ? outcome : "") as PlanOutcome,
      category: clip(typeof d.category === "string" ? d.category : "", 60),
    };
  }
  out.finale = clip(typeof src.finale === "string" ? src.finale : "", PLAN_LIMIT);
  return out;
}

export function planReady(plan: Nii67Plan): boolean {
  return [plan.door1, plan.door2].every((d) => d.world.trim() && d.happens.trim());
}

export function updatePlanDoor(card: Nii67Card, id: "door1" | "door2", patch: Partial<Nii67PlanDoor>): Nii67Card {
  return { ...card, plan: { ...card.plan, [id]: { ...card.plan[id], ...patch } } };
}

export function updatePlanFinale(card: Nii67Card, finale: string): Nii67Card {
  return { ...card, plan: { ...card.plan, finale: clip(finale, PLAN_LIMIT) } };
}

export interface Nii67Card {
  version: 1;
  plan: Nii67Plan;
  intro: { text: string; video_name: string };
  portal: Nii67Portal;
  gg: { name: string; note: string };
  hands_image_name: string;
  doors: Nii67Door[];
  finale: { standard: string; notes: string };
  generated_text: string;
  saved_at: string;
  last_save: "" | "draft" | "save";
  generation?: {
    version?: number;
    models?: {
      llm_model_id?: string;
      image_model_id?: string;
      video_model_id?: string;
    };
  };
}

export type BlockPatch = Partial<
  Pick<
    Nii67Block,
    | "text"
    | "text2"
    | "collapsed"
    | "image_prompt"
    | "image_name"
    | "image_name2"
    | "outcome"
    | "custom_title"
    | "exit_action"
    | "exit_animation"
    | "room_after"
  >
> & { character?: Partial<Nii67Character> };

function newId(prefix: string): string {
  const raw =
    globalThis.crypto?.randomUUID?.().replace(/-/g, "") ??
    Math.random().toString(16).slice(2).padEnd(12, "0");
  return `${prefix}_${raw.slice(0, 12)}`;
}

function clip(value: string, limit: number): string {
  return value.slice(0, limit);
}

export function emptyCharacter(): Nii67Character {
  return {
    enabled: false,
    name: "",
    creature_type: "",
    temperament: "",
    position: "",
    behavior: "",
    appearance: "",
  };
}

export function newBlock(kind: BlockKind): Nii67Block {
  return {
    id: newId("b"),
    kind,
    collapsed: false,
    text: "",
    text2: "",
    image_prompt: "",
    image_name: "",
    image_name2: "",
    outcome: "",
    custom_title: "",
    exit_action: "",
    exit_animation: "",
    room_after: "",
    character: emptyCharacter(),
  };
}

function defaultDoor(id: "door1" | "door2"): Nii67Door {
  const title = id === "door1" ? "Door 1" : "Door 2";
  const branch = id === "door1" ? "Ветвь 1" : "Ветвь 2";
  return {
    id,
    title,
    branch,
    theme: "",
    theme_note: "",
    image_prompt: "",
    image_name: "",
    blocks: [],
    move: "",
    creature_action: "",
    state: emptyMatrixState(),
  };
}

export function defaultCard(): Nii67Card {
  return {
    version: 1,
    intro: { text: "", video_name: "" },
    portal: defaultPortal(),
    gg: { name: "", note: "" },
    hands_image_name: "",
    doors: [defaultDoor("door1"), defaultDoor("door2")],
    finale: { standard: "", notes: "" },
    plan: defaultPlan(),
    generated_text: "",
    saved_at: "",
    last_save: "",
    generation: {
      version: 1,
      models: { llm_model_id: "", image_model_id: "", video_model_id: "" },
    },
  };
}

function isBlockKind(value: string): value is BlockKind {
  return (BLOCK_KINDS as readonly string[]).includes(value);
}

function asCharacter(raw: unknown): Nii67Character {
  const src = raw && typeof raw === "object" ? (raw as Partial<Nii67Character>) : {};
  const creature = typeof src.creature_type === "string" ? src.creature_type : "";
  const temper = typeof src.temperament === "string" ? src.temperament : "";
  return {
    enabled: src.enabled === true,
    name: clip(typeof src.name === "string" ? src.name : "", SHORT_LIMIT),
    creature_type: CREATURE_IDS.has(creature) ? creature : "",
    temperament: TEMPER_IDS.has(temper) ? temper : "",
    position: clip(typeof src.position === "string" ? src.position : "", TEXT_LIMIT),
    behavior: clip(typeof src.behavior === "string" ? src.behavior : "", TEXT_LIMIT),
    appearance: clip(typeof src.appearance === "string" ? src.appearance : "", TEXT_LIMIT),
  };
}

const MATRIX_MOVE_IDS = new Set<string>(MATRIX_MOVE_OPTIONS.map((option) => option.id).filter(Boolean));
const MATRIX_ACTION_IDS = new Set<string>(
  MATRIX_CREATURE_ACTION_OPTIONS.map((option) => option.id).filter(Boolean),
);

export function asMove(raw: unknown): MatrixMove {
  const value = typeof raw === "string" ? raw : "";
  return (MATRIX_MOVE_IDS.has(value) ? value : "") as MatrixMove;
}

export function asCreatureAction(raw: unknown): MatrixCreatureAction {
  const value = typeof raw === "string" ? raw : "";
  return (MATRIX_ACTION_IDS.has(value) ? value : "") as MatrixCreatureAction;
}

export function asMatrixState(raw: unknown): Nii67MatrixState {
  const src = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const out = emptyMatrixState();
  for (const key of Object.keys(out) as (keyof Nii67MatrixState)[]) {
    out[key] = clip(typeof src[key] === "string" ? (src[key] as string) : "", TEXT_LIMIT);
  }
  return out;
}

const LEGACY_BLOCK_KIND: Record<string, BlockKind> = {
  exit: "back",
  world_character: "creature",
  world_frame: "neutral",
  enter_action: "neutral",
  character_action: "neutral",
  gg_action: "neutral",
};

function asBlock(raw: unknown): Nii67Block | null {
  if (!raw || typeof raw !== "object") return null;
  const src = raw as Partial<Nii67Block>;
  const rawKind = typeof src.kind === "string" ? src.kind : "";
  const kind: BlockKind | null = isBlockKind(rawKind)
    ? (rawKind as BlockKind)
    : (LEGACY_BLOCK_KIND[rawKind] ?? null);
  // Неизвестные блоки не выбрасываем: старые карточки иначе теряют текст
  // при первом сохранении. Неизвестный kind считаем нейтральным действием.
  const resolved: BlockKind = kind ?? "neutral";
  const id = typeof src.id === "string" && src.id.trim() ? src.id.trim().slice(0, 40) : newId("b");
  return {
    ...newBlock(resolved),
    id,
    kind: resolved,
    collapsed: src.collapsed === true,
    text: clip(typeof src.text === "string" ? src.text : "", TEXT_LIMIT),
    text2: clip(typeof src.text2 === "string" ? src.text2 : "", TEXT_LIMIT),
    image_prompt: clip(typeof src.image_prompt === "string" ? src.image_prompt : "", TEXT_LIMIT),
    image_name: clip(typeof src.image_name === "string" ? src.image_name : "", SHORT_LIMIT),
    image_name2: clip(typeof src.image_name2 === "string" ? src.image_name2 : "", SHORT_LIMIT),
    outcome: resolved === "creature" ? asBlockOutcome(src.outcome) : "",
    custom_title: clip(typeof src.custom_title === "string" ? src.custom_title : "", SHORT_LIMIT),
    exit_action: clip(typeof src.exit_action === "string" ? src.exit_action : "", TEXT_LIMIT),
    exit_animation: clip(typeof src.exit_animation === "string" ? src.exit_animation : "", TEXT_LIMIT),
    room_after: clip(typeof src.room_after === "string" ? src.room_after : "", TEXT_LIMIT),
    character: asCharacter(src.character),
  };
}

/** Принять ответ сервера. Битая форма откатывается к шаблону двух дверей. */
export function cardFromUnknown(raw: unknown): Nii67Card {
  if (!raw || typeof raw !== "object") return defaultCard();
  const src = raw as Partial<Nii67Card>;
  const base = defaultCard();
  const doorsIn = Array.isArray(src.doors) ? src.doors : [];
  const byId = new Map<string, Nii67Door>();
  for (const item of doorsIn) {
    if (!item || typeof item !== "object") continue;
    const door = item as Partial<Nii67Door>;
    if (door.id !== "door1" && door.id !== "door2") continue;
    if (byId.has(door.id)) continue;
    const fallback = base.doors.find((entry) => entry.id === door.id) ?? defaultDoor(door.id);
    const blocks = Array.isArray(door.blocks)
      ? door.blocks.map(asBlock).filter((block): block is Nii67Block => block !== null).slice(0, 40)
      : fallback.blocks;
    byId.set(door.id, {
      ...fallback,
      id: door.id,
      title: clip(typeof door.title === "string" && door.title.trim() ? door.title : fallback.title, SHORT_LIMIT),
      branch: clip(
        typeof door.branch === "string" && door.branch.trim() ? door.branch : fallback.branch,
        SHORT_LIMIT,
      ),
      theme: clip(typeof door.theme === "string" ? door.theme : "", 240),
      theme_note: clip(typeof door.theme_note === "string" ? door.theme_note : "", TEXT_LIMIT),
      image_prompt: clip(typeof door.image_prompt === "string" ? door.image_prompt : "", TEXT_LIMIT),
      image_name: clip(typeof door.image_name === "string" ? door.image_name : "", SHORT_LIMIT),
      blocks,
      move: asMove(door.move),
      creature_action: asCreatureAction(door.creature_action),
      state: asMatrixState(door.state),
    });
  }
  const intro: { text?: unknown; video_name?: unknown } =
    src.intro && typeof src.intro === "object" ? src.intro : {};
  const portalSrc: Record<string, unknown> =
    src.portal && typeof src.portal === "object" ? (src.portal as Record<string, unknown>) : {};
  const portal = defaultPortal();
  for (const key of PORTAL_FIELDS) {
    const raw = portalSrc[key];
    portal[key] = clip(typeof raw === "string" ? raw : "", key.endsWith("_name") ? SHORT_LIMIT : TEXT_LIMIT);
  }
  const gg = src.gg && typeof src.gg === "object" ? src.gg : { name: "", note: "" };
  const finale = src.finale && typeof src.finale === "object" ? src.finale : { standard: "", notes: "" };
  const last = src.last_save === "draft" || src.last_save === "save" ? src.last_save : "";
  const genModels =
    src.generation && typeof src.generation === "object" && src.generation.models && typeof src.generation.models === "object"
      ? src.generation.models
      : {};
  return {
    version: 1,
    intro: {
      text: clip(typeof intro.text === "string" ? intro.text : "", TEXT_LIMIT),
      video_name: clip(typeof intro.video_name === "string" ? intro.video_name : "", SHORT_LIMIT),
    },
    portal,
    gg: {
      name: clip(typeof gg.name === "string" ? gg.name : "", SHORT_LIMIT),
      note: clip(typeof gg.note === "string" ? gg.note : "", TEXT_LIMIT),
    },
    hands_image_name: clip(
      typeof src.hands_image_name === "string" ? src.hands_image_name : "",
      SHORT_LIMIT,
    ),
    doors: [
      byId.get("door1") ?? defaultDoor("door1"),
      byId.get("door2") ?? defaultDoor("door2"),
    ],
    finale: {
      standard: clip(typeof finale.standard === "string" ? finale.standard : "", FINALE_LIMIT),
      notes: clip(typeof finale.notes === "string" ? finale.notes : "", FINALE_LIMIT),
    },
    plan: asPlan(src.plan),
    generated_text: clip(typeof src.generated_text === "string" ? src.generated_text : "", 20_000),
    saved_at: clip(typeof src.saved_at === "string" ? src.saved_at : "", 40),
    last_save: last,
    generation: {
      version: 1,
      models: {
        llm_model_id: clip(typeof genModels.llm_model_id === "string" ? genModels.llm_model_id : "", SHORT_LIMIT),
        image_model_id: clip(typeof genModels.image_model_id === "string" ? genModels.image_model_id : "", SHORT_LIMIT),
        video_model_id: clip(typeof genModels.video_model_id === "string" ? genModels.video_model_id : "", SHORT_LIMIT),
      },
    },
  };
}

function mapDoor(card: Nii67Card, doorId: string, mapper: (door: Nii67Door) => Nii67Door): Nii67Card {
  return {
    ...card,
    doors: card.doors.map((door) => (door.id === doorId ? mapper(door) : door)),
  };
}

export function insertBlock(card: Nii67Card, doorId: string, index: number, kind: BlockKind): Nii67Card {
  return mapDoor(card, doorId, (door) => {
    if (door.blocks.length >= 40) return door;
    const next = door.blocks.slice();
    const at = Math.max(0, Math.min(index, next.length));
    next.splice(at, 0, newBlock(kind));
    return { ...door, blocks: next };
  });
}

export function duplicateBlock(card: Nii67Card, doorId: string, blockId: string): Nii67Card {
  return mapDoor(card, doorId, (door) => {
    if (door.blocks.length >= 40) return door;
    const index = door.blocks.findIndex((block) => block.id === blockId);
    if (index < 0) return door;
    const source = door.blocks[index];
    const copy: Nii67Block = {
      ...structuredClone(source),
      id: newId("b"),
      collapsed: false,
    };
    const next = door.blocks.slice();
    next.splice(index + 1, 0, copy);
    return { ...door, blocks: next };
  });
}

export function deleteBlock(card: Nii67Card, doorId: string, blockId: string): Nii67Card {
  return mapDoor(card, doorId, (door) => ({
    ...door,
    blocks: door.blocks.filter((block) => block.id !== blockId),
  }));
}

export function moveBlock(card: Nii67Card, doorId: string, blockId: string, delta: number): Nii67Card {
  return mapDoor(card, doorId, (door) => {
    const index = door.blocks.findIndex((block) => block.id === blockId);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= door.blocks.length) return door;
    const next = door.blocks.slice();
    const [item] = next.splice(index, 1);
    next.splice(target, 0, item);
    return { ...door, blocks: next };
  });
}

export function reorderBlock(
  card: Nii67Card,
  doorId: string,
  fromIndex: number,
  toIndex: number,
): Nii67Card {
  return mapDoor(card, doorId, (door) => {
    if (
      fromIndex === toIndex ||
      fromIndex < 0 ||
      toIndex < 0 ||
      fromIndex >= door.blocks.length ||
      toIndex >= door.blocks.length
    ) {
      return door;
    }
    const next = door.blocks.slice();
    const [item] = next.splice(fromIndex, 1);
    next.splice(toIndex, 0, item);
    return { ...door, blocks: next };
  });
}

export function updateBlock(
  card: Nii67Card,
  doorId: string,
  blockId: string,
  patch: BlockPatch,
): Nii67Card {
  return mapDoor(card, doorId, (door) => ({
    ...door,
    blocks: door.blocks.map((block) => {
      if (block.id !== blockId) return block;
      const character = asCharacter({ ...block.character, ...patch.character });
      return {
        ...block,
        text: clip(patch.text ?? block.text, TEXT_LIMIT),
        text2: clip(patch.text2 ?? block.text2, TEXT_LIMIT),
        collapsed: patch.collapsed ?? block.collapsed,
        image_prompt: clip(patch.image_prompt ?? block.image_prompt, TEXT_LIMIT),
        image_name: clip(patch.image_name ?? block.image_name, SHORT_LIMIT),
        image_name2: clip(patch.image_name2 ?? block.image_name2, SHORT_LIMIT),
        outcome: patch.outcome !== undefined ? asBlockOutcome(patch.outcome) : block.outcome,
        custom_title: clip(patch.custom_title ?? block.custom_title, SHORT_LIMIT),
        exit_action: clip(patch.exit_action ?? block.exit_action, TEXT_LIMIT),
        exit_animation: clip(patch.exit_animation ?? block.exit_animation, TEXT_LIMIT),
        room_after: clip(patch.room_after ?? block.room_after, TEXT_LIMIT),
        character,
      };
    }),
  }));
}

export function updateIntro(card: Nii67Card, text: string): Nii67Card {
  return { ...card, intro: { ...card.intro, text: clip(text, TEXT_LIMIT) } };
}

export function updateIntroVideo(card: Nii67Card, video_name: string): Nii67Card {
  return { ...card, intro: { ...card.intro, video_name: clip(video_name, SHORT_LIMIT) } };
}

export function updatePortal(card: Nii67Card, patch: Partial<Nii67Portal>): Nii67Card {
  const portal = { ...card.portal };
  for (const key of PORTAL_FIELDS) {
    const value = patch[key];
    if (typeof value === "string") portal[key] = clip(value, key.endsWith("_name") ? SHORT_LIMIT : TEXT_LIMIT);
  }
  return { ...card, portal };
}

export function updateGg(card: Nii67Card, patch: Partial<Nii67Card["gg"]>): Nii67Card {
  return {
    ...card,
    gg: {
      name: clip(patch.name ?? card.gg.name, SHORT_LIMIT),
      note: clip(patch.note ?? card.gg.note, TEXT_LIMIT),
    },
  };
}

export function updateHandsImage(card: Nii67Card, hands_image_name: string): Nii67Card {
  return { ...card, hands_image_name: clip(hands_image_name, SHORT_LIMIT) };
}

export function updateDoor(
  card: Nii67Card,
  doorId: string,
  patch: Partial<
    Pick<
      Nii67Door,
      "theme" | "theme_note" | "image_prompt" | "image_name" | "move" | "creature_action" | "blocks"
    >
  > & { state?: Partial<Nii67MatrixState> },
): Nii67Card {
  return mapDoor(card, doorId, (door) => {
    const move = patch.move !== undefined ? asMove(patch.move) : door.move;
    const creatureAction =
      patch.creature_action !== undefined ? asCreatureAction(patch.creature_action) : door.creature_action;
    return {
      ...door,
      theme: clip(patch.theme ?? door.theme, 240),
      theme_note: clip(patch.theme_note ?? door.theme_note, TEXT_LIMIT),
      image_prompt: clip(patch.image_prompt ?? door.image_prompt, TEXT_LIMIT),
      image_name: clip(patch.image_name ?? door.image_name, SHORT_LIMIT),
      blocks: patch.blocks ?? door.blocks,
      move,
      creature_action: move === "creature" ? creatureAction : "",
      state: asMatrixState({ ...door.state, ...(patch.state ?? {}) }),
    };
  });
}

/** Добавить блок в дверь: блоки накапливаются, ход берётся из последнего. */
export function addDoorBlock(card: Nii67Card, doorId: string, kind: BlockKind): Nii67Card {
  const variant = BLOCK_VARIANT[kind];
  const block = newBlock(kind);
  if (kind === "creature") {
    block.character = { ...block.character, enabled: true };
  }
  const door = card.doors.find((entry) => entry.id === doorId);
  return updateDoor(card, doorId, {
    blocks: [...(door?.blocks ?? []), block],
    move: variant.move,
    creature_action: variant.creature_action ?? "",
  });
}

/** Удалить один блок двери; если блоков не осталось — сбросить ход. */
export function removeDoorBlock(card: Nii67Card, doorId: string, blockId: string): Nii67Card {
  const door = card.doors.find((entry) => entry.id === doorId);
  if (!door) return card;
  const blocks = door.blocks.filter((block) => block.id !== blockId);
  return updateDoor(card, doorId, {
    blocks,
    ...(blocks.length === 0 ? { move: "" as MatrixMove, creature_action: "" as MatrixCreatureAction } : {}),
  });
}

/** Текст блока попадает в поля двери, из которых матрица собирает промты. */
export function blockDoorPatch(block: Nii67Block): {
  state?: Partial<Nii67MatrixState>;
  creature_action?: MatrixCreatureAction;
} {
  if (block.kind === "death") {
    return { state: { death_action: block.text, illustration: block.text2 } };
  }
  if (block.kind === "neutral") {
    return { state: { place: block.text } };
  }
  if (block.kind === "back") {
    return { state: { world_description: block.text } };
  }
  // Исход существа задаёт ход двери; пустой исход не перетирает текущий.
  if (block.kind === "creature" && block.outcome) {
    return { creature_action: asCreatureAction(block.outcome) };
  }
  return {};
}

export function updateFinale(
  card: Nii67Card,
  patch: Partial<Nii67Card["finale"]>,
): Nii67Card {
  return {
    ...card,
    finale: {
      standard: clip(patch.standard ?? card.finale.standard, FINALE_LIMIT),
      notes: clip(patch.notes ?? card.finale.notes, FINALE_LIMIT),
    },
  };
}

export function projectUsesNii67Script(project: ProjectDetail | null): boolean {
  if (!project) return false;
  if (project.prompt_overrides?.script === "nii67") return true;
  const slots = project.meta?.prompt_slot_variants;
  if (!slots || typeof slots !== "object") return false;
  const node = (slots as Record<string, unknown>).n_script;
  if (!node || typeof node !== "object") return false;
  return (node as Record<string, unknown>).main === "nii67";
}
