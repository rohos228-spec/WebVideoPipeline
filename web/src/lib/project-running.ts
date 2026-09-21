import type { ProjectStatus } from "@/lib/types";

/** Статусы, в которых воркер выполняет шаг (как `is_running_status` на бэкенде). */
const RUNNING_PROJECT_STATUSES: ReadonlySet<string> = new Set([
  "planning",
  "scripting",
  "splitting",
  "scene_designing",
  "scene_assembling",
  "generating_hero",
  "generating_items",
  "enriching_1",
  "enriching_2",
  "enriching_3",
  "enriching_4",
  "enriching_5",
  "generating_image_prompts",
  "generating_images",
  "generating_animation_prompts",
  "generating_videos",
  "generating_audio",
  "generating_music",
  "sfx_planning",
  "generating_sfx",
  "assembling",
  "publishing",
]);

export function isProjectRunningStatus(
  status: ProjectStatus | string | undefined | null,
): boolean {
  if (!status) return false;
  const s = String(status);
  return (
    RUNNING_PROJECT_STATUSES.has(s) ||
    s.startsWith("generating_") ||
    (s.endsWith("ing") && !s.includes("ready")) ||
    s.includes("_running")
  );
}

/** Показывать ⏹ пока running-статус ИЛИ воркер ещё держит asyncio-task. */
export function shouldShowStopBar(
  status: ProjectStatus | string | undefined | null,
  generationActive?: boolean,
): boolean {
  return isProjectRunningStatus(status) || Boolean(generationActive);
}
