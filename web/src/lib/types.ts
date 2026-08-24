/** Формы данных бэкенда — ровно те поля, которые рисует интерфейс. */

export type StageId = "plan" | "script" | "frames" | "cast" | "images" | "videos" | "final";

export type StageState = "locked" | "ready" | "running" | "done" | "failed" | "paused";

export interface Stage {
  id: StageId;
  label: string;
  hint: string;
  editor: string;
  state: StageState;
  target_status: string;
  price_micro: number;
  price_credits: string;
  exact: boolean;
  active: boolean;
}

export interface StagesResponse {
  project_id: number;
  status: string;
  generation_active: boolean;
  stage_run: { stage: StageId; target: string } | null;
  stages: Stage[];
  remaining_micro: number;
  remaining_credits: string;
}

export interface ProjectSummary {
  id: number;
  slug: string;
  title: string | null;
  topic: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface Project extends ProjectSummary {
  hero_mode: string;
  auto_mode: boolean;
  general_plan: string | null;
  script_text: string | null;
  hero_count: number | null;
  hero_descriptions: string[];
  item_descriptions: string[];
  image_generator: string | null;
  aspect_ratio: string | null;
  image_resolution: string | null;
  video_generator: string | null;
  video_resolution: string | null;
  enrich_slots_count: number | null;
  generation_active: boolean;
}

export interface Frame {
  id: number;
  project_id: number;
  number: number;
  voiceover_text: string;
  duration_seconds: number | null;
  image_prompt: string | null;
  animation_prompt: string | null;
  status: string;
}

export interface MediaFrame {
  frame_id: number;
  number: number;
  voiceover_text: string;
  image_prompt: string | null;
  animation_prompt: string | null;
  status: string;
  artifact_uuid: string | null;
  preview_url: string | null;
}

export interface Asset {
  source: string;
  id: string;
  kind: string;
  path: string;
  preview_url: string | null;
  frame_id: number | null;
  label?: string;
  meta?: Record<string, unknown> | null;
}

export interface Balance {
  tenant_id: string | null;
  /**
   * true — кассы у этого пользователя нет: админ студии или установка без
   * учётных записей. Рисуется как «∞», а не как число: у админа не большой
   * баланс, а отсутствие тарификации (docs/SAAS-PIVOT.md §5.8).
   */
  unlimited: boolean;
  balance_credits: string;
  free_tier: { active: boolean; projects: number; max_projects: number };
}

/** Единственная ручка, которую можно спросить до входа. */
export interface AuthStatus {
  auth_required: boolean;
  accounts: boolean;
}

export interface LoginResult {
  token: string;
  email: string;
  role: "admin" | "member";
  expires_in: number;
}

export interface Me {
  accounts_enabled: boolean;
  tenant_id: string | null;
  email: string;
  role: "" | "admin" | "member";
  display_name: string;
  unlimited: boolean;
  balance_micro: number;
  balance_credits: string;
}

export interface Choice {
  id: string;
  label: string;
  description?: string;
}

export interface GenerationOptions {
  image_generators: Choice[];
  aspect_ratios: Choice[];
  image_resolutions: Choice[];
  image_resolutions_by_generator: Record<string, string[]>;
  video_generators: Choice[];
  video_resolutions: Choice[];
}
