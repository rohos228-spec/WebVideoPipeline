/** Русские подписи для UI — без технических slug с подчёркиваниями. */

import type { ProjectStatus } from "./types";
import type { NodeCategory } from "./node-catalog";

const PROJECT_STATUS: Partial<Record<ProjectStatus | string, string>> = {
  new: "новый",
  planning: "сценарий",
  plan_ready: "сценарий готов",
  scripting: "закадровый текст",
  script_ready: "закадровый текст готов",
  splitting: "разбивка",
  frames_ready: "кадры готовы",
  scene_designing: "сцены (агенты)",
  scene_agents_ready: "агенты сцен готовы",
  scene_assembling: "сборка сцен",
  scene_design_ready: "сцены готовы",
  generating_hero: "персонажи",
  hero_ready: "персонажи готовы",
  generating_items: "предметы",
  items_ready: "предметы готовы",
  enriching_1: "дополнение 1",
  enriching_2: "дополнение 2",
  enriching_3: "дополнение 3",
  enriching_4: "дополнение 4",
  enriching_5: "дополнение 5",
  enrich_1_ready: "дополнение 1 готово",
  enrich_2_ready: "дополнение 2 готово",
  enrich_3_ready: "дополнение 3 готово",
  enrich_4_ready: "дополнение 4 готово",
  enrich_5_ready: "дополнение 5 готово",
  generating_image_prompts: "промты картинок",
  image_prompts_ready: "промты картинок готовы",
  generating_images: "картинки",
  images_ready: "картинки готовы",
  generating_animation_prompts: "промты анимации",
  animation_prompts_ready: "промты анимации готовы",
  generating_videos: "видео",
  videos_ready: "видео готово",
  generating_audio: "озвучка",
  audio_ready: "озвучка готова",
  generating_music: "музыка",
  music_ready: "музыка готова",
  sfx_planning: "план звуков",
  sfx_plan_ready: "план звуков готов",
  generating_sfx: "звуки (sfx)",
  sfx_ready: "звуки готовы",
  assembling: "сборка",
  assembled: "собрано",
  publishing: "публикация",
  published: "опубликовано",
  paused: "пауза",
  failed: "ошибка",
};

const NODE_CATEGORY: Record<NodeCategory, string> = {
  planning: "планирование",
  objects: "объекты",
  enrich: "постановка сцен",
  media: "медиа",
  audio: "аудио",
  assembly: "сборка",
  publish: "публикация",
  hitl: "проверка",
};

const RUN_STATUS: Record<string, string> = {
  new: "новый",
  running: "работает",
  paused: "пауза",
  completed: "завершён",
  cancelled: "отменён",
  failed: "ошибка",
};

const HERO_MODE: Record<string, string> = {
  auto: "авто",
  hero: "с персонажем",
  no_hero: "без персонажа",
};

export function formatProjectStatus(status: string): string {
  return PROJECT_STATUS[status as ProjectStatus] ?? humanizeSlug(status);
}

export function formatNodeCategory(cat: string): string {
  return NODE_CATEGORY[cat as NodeCategory] ?? humanizeSlug(cat);
}

const NODE_TYPE_LABELS: Record<string, string> = {
  excel_gpt: "ИИ-режиссер",
  topic: "Тема",
  plan: "План",
  script: "Сценарий",
  split: "Нарезка кадров",
  hero: "Персонажи",
  items: "Предметы",
  scene_design: "Дизайн сцен",
  sd_agent: "Агент сцен",
  sd_assemble: "Сборка сцен",
  image_prompts: "Промты картинок",
  images: "Картинки",
  animation_prompts: "Промты анимации",
  videos: "Видео",
  music: "Музыка",
  sfx_plan: "План звуков",
  sfx_gen: "Звуки",
  audio: "Озвучка",
  assemble: "Монтаж",
  publish: "Публикация",
  storage: "Хранилище",
  shot_menu: "Меню съёмки",
  excel_feed: "Темы",
  hitl_hero: "Проверка персонажей",
  hitl_images: "Проверка картинок",
  hitl_videos: "Проверка видео",
  hitl_final: "Финальная проверка",
};

export function formatNodeType(type: string): string {
  if (type.startsWith("enrich_")) return "ИИ-режиссер";
  return NODE_TYPE_LABELS[type] ?? humanizeSlug(type);
}

export function formatRunStatus(status: string | null | undefined): string {
  if (typeof status !== "string" || status.length === 0) return "";
  return RUN_STATUS[status] ?? humanizeSlug(status);
}

export function formatHeroMode(mode: string): string {
  return HERO_MODE[mode] ?? humanizeSlug(mode);
}

export function formatStepCode(code: string): string {
  const map: Record<string, string> = {
    plan: "сценарий",
    script: "закадровый текст",
    split: "разбивка",
    scene_d: "сцены (агенты)",
    scene_asm: "сборка сцен",
    sd_char: "агент: персонажи",
    sd_world: "агент: мир",
    sd_style: "агент: стиль",
    sd_cam: "агент: камера",
    sd_act: "агент: действие",
    hero: "персонажи",
    items: "предметы",
    enrich_1: "режиссура 1",
    enrich_2: "режиссура 2",
    enrich_3: "режиссура 3",
    enrich_4: "режиссура 4",
    enrich_5: "режиссура 5",
    excel_gpt: "ИИ-режиссер",
    img_pr: "промты картинок",
    images: "картинки",
    anim_pr: "промты анимации",
    videos: "видео",
    audio: "озвучка",
    sfx_plan: "план звуков",
    sfx_gen: "звуки (sfx)",
    assemble: "сборка",
    publish: "публикация",
  };
  return map[code] ?? humanizeSlug(code);
}

/** Заменяет _ на пробелы, убирает лишние технические префиксы. */
export function humanizeSlug(value: string | null | undefined): string {
  if (typeof value !== "string") return "";
  return value
    .replace(/^n_/, "")
    .replace(/_/g, " ")
    .replace(/\bhitl\b/gi, "проверка")
    .replace(/\bgpt\b/gi, "GPT")
    .replace(/\boutsee\b/gi, "генератор")
    .trim();
}

export function formatNodeKeyLabel(key: string): string {
  return humanizeSlug(key);
}

const STYLE_PRESET_RU: Record<string, string> = {
  cats_pixelart_short: "Коты пиксель-арт (shorts)",
  humans_documentary: "Документальный (люди)",
};

export function formatStylePresetLabel(preset: { id: string; label?: string }): string {
  if (preset.label && !/^[A-Za-z][A-Za-z0-9 _-]+$/.test(preset.label.trim())) {
    return preset.label;
  }
  return STYLE_PRESET_RU[preset.id] ?? humanizeSlug(preset.id);
}

/**
 * Подпись строки Run в оверлее канваса (перенос из video-pipeline int/chat-v1).
 * null — пустой ответ (`[]`, null) или объект без status-slug: оверлей
 * показывает «не запущен», а не вызывает humanizeSlug(undefined).
 */
export function formatRunOverlayLabel(run: unknown): string | null {
  if (run == null || typeof run !== "object" || Array.isArray(run)) return null;
  const rec = run as { id?: unknown; status?: unknown };
  if (typeof rec.status !== "string" || rec.status.trim() === "") return null;
  if (typeof rec.id !== "number" && typeof rec.id !== "string") return null;
  const label = formatRunStatus(rec.status);
  if (!label) return null;
  return `#${rec.id} · ${label}`;
}

/**
 * Строка запуска над канвасом: номер выбранного проекта и состояние его прогона.
 * Номер прогона не показываем: «Run: #1» при выбранном #2 читался как другой проект.
 */
export function formatRunLine(projectId: number, run: unknown): string {
  const status =
    run != null && typeof run === "object" && !Array.isArray(run) ? (run as { status?: unknown }).status : null;
  const label = typeof status === "string" ? formatRunStatus(status) : "";
  return `проект #${projectId} · ${label || "не запущен"}`;
}

/** node_runs из ответа /run. Пустой или кривой payload → пустой список, без throw. */
export function nodeRunsFromPayload<T>(run: unknown): T[] {
  if (run == null || typeof run !== "object" || Array.isArray(run)) return [];
  const rows = (run as { node_runs?: unknown }).node_runs;
  return Array.isArray(rows) ? (rows as T[]) : [];
}
