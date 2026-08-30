/** Формы данных бэкенда — ровно те поля, которые рисует интерфейс. */

export type StageId = "plan" | "script" | "frames" | "cast" | "images" | "videos" | "final";

export type StageState = "locked" | "ready" | "running" | "done" | "failed" | "paused" | "skipped";

/** Промт одного шага внутри стадии. */
export interface StagePrompt {
  step: string;
  label: string;
}

export interface Stage {
  id: StageId;
  label: string;
  hint: string;
  editor: string;
  /** Какие промты правятся на этой стадии. Пустой список — править нечего. */
  prompts: StagePrompt[];
  state: StageState;
  target_status: string;
  price_micro: number;
  price_credits: string;
  exact: boolean;
  active: boolean;
  /** Узлы графа проекта, которые эта стадия сворачивает. */
  nodes: StageNode[];
}

/** Узел графа внутри стадии — то, из чего стадия на самом деле состоит. */
export interface StageNode {
  id: string;
  type: string;
  label: string;
  step_code: string | null;
  state: NodeState;
  disabled: boolean;
  model_id: string | null;
  price_micro: number;
  price_credits: string;
  has_prompt: boolean;
}

export type NodeState =
  | "pending"
  | "queued"
  | "running"
  | "waiting_hitl"
  | "done"
  | "failed"
  | "skipped";

export interface StagesResponse {
  project_id: number;
  status: string;
  generation_active: boolean;
  stage_run: { stage: StageId; target: string } | null;
  stages: Stage[];
  remaining_micro: number;
  remaining_credits: string;
  graph_source: GraphSource;
  graph_proposal: GraphProposal | null;
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


// ── Библиотека промтов ──────────────────────────────────────────────────

export interface PromptFileInfo {
  name: string;
  filename: string;
  size: number;
  modified: number | null;
  is_default: boolean;
}

export interface PromptFileContent {
  name: string;
  filename: string;
  content: string;
  size: number;
  modified: number | null;
}

/** Какой файл реально возьмёт шаг: свой, проектный или общий по умолчанию. */
export interface PromptResolveInfo {
  name: string;
  source: string;
  source_label: string;
  modified: number | null;
}

export interface PromptVersion {
  id: string;
  label: string;
  saved_at: number;
  size: number;
}

export interface PromptVersionContent {
  id: string;
  label: string;
  content: string;
}

// ── Конструктор конвейера ───────────────────────────────────────────────

export interface GraphNode {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
  /** `kind`: after — связь; pass/fail — ветки «Ок»/«Не ок» проверки; gate — шлагбаум. */
  data?: Record<string, unknown>;
}

export type EdgeKind = "after" | "pass" | "fail" | "gate";

/** Откуда взят граф проекта: свой (canvas) или ещё шаблонный. */
export type GraphSource = "canvas" | "run" | "workflow" | "default";

export interface StepPrice {
  price_micro: number;
  hold_micro: number;
  exact: boolean;
  price_credits: string;
}

export interface ModelChoice {
  id: string;
  label: string;
  vendor?: string | null;
}

/** Граф конкретного ролика — то, по чему он на самом деле идёт. */
export interface ProjectGraph {
  project_id: number;
  status: string;
  source: GraphSource;
  workflow_id: number | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
  states: Record<string, NodeState>;
  prices: Record<string, StepPrice>;
  catalog: NodeKindInfo[];
  scene_agents: SceneAgentChoice[];
  models: { text: ModelChoice[]; image: ModelChoice[]; video: ModelChoice[] };
  voices: ElevenLabsVoice[];
  settings: GraphSettings;
  proposal: GraphProposal | null;
}

export interface GraphDiffNode {
  id: string;
  type: string;
  label: string;
  changes?: string[];
}

export interface GraphDiffEdge {
  source: string;
  target: string;
  kind: string;
  was?: string;
}

export interface GraphDiff {
  added_nodes: GraphDiffNode[];
  removed_nodes: GraphDiffNode[];
  changed_nodes: GraphDiffNode[];
  added_edges: GraphDiffEdge[];
  removed_edges: GraphDiffEdge[];
  changed_edges: GraphDiffEdge[];
  summary: string;
  empty: boolean;
}

export interface ResetPlan {
  first_step: string | null;
  steps: string[];
  reasons: Record<string, string>;
}

/** Что будет, если применить граф: разница и что сгорит. */
export interface GraphDiffResponse {
  valid: boolean;
  errors: string[];
  warnings: string[];
  diff: GraphDiff;
  reset: ResetPlan;
}

/** Предложение агента по графу — ждёт решения человека. */
export interface GraphProposal {
  id: string;
  author: string;
  reason: string;
  created_at: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  diff: GraphDiff;
  reset: ResetPlan;
  warnings: string[];
}

export interface GraphApplyResult {
  diff: GraphDiff;
  reset: ResetPlan;
  reset_done: boolean;
  warnings: string[];
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface WorkflowSummary {
  id: number;
  name: string;
  description: string | null;
  version: number;
  is_default: boolean;
  updated_at: string;
}

export interface Workflow extends WorkflowSummary {
  nodes: GraphNode[];
  edges: GraphEdge[];
  meta: Record<string, unknown>;
}

/** Ответ проверки графа: что сломано и где. */
export interface GraphValidation {
  valid: boolean;
  errors: string[];
  warnings?: string[];
}


/** Тип узла, доступный конструктору. Приходит с сервера — см. /workflows/catalog. */
export interface NodeKindInfo {
  type: string;
  label: string;
  kind: string;
  step_code: string | null;
  has_prompt: boolean;
  stage?: StageId | null;
}

export interface NodeCatalog {
  kinds: Record<string, string>;
  nodes: NodeKindInfo[];
  scene_agents: SceneAgentChoice[];
}

/** Роль узла «Работа с GPT» в веере сцен — значение `data.sd_agent`. */
export interface SceneAgentChoice {
  id: string;
  label: string;
}

// ── Настройки узлов вне графа ───────────────────────────────────────────
//
// Параметры шага, GPT-проверка перед авто-апрувом, автопродвижение — живут в
// `meta` проекта, а не в узле: узел описывает форму конвейера, а это —
// поведение конкретного ролика. Читаются из `/graph`, пишутся через
// `PATCH /projects/{id}` с `meta`.

export interface ElevenLabsVoice {
  id: string;
  name: string;
  description?: string;
}

/** `meta.node_step_params[step]` — то, что читают сами шаги. */
export type StepParams = Record<string, Record<string, unknown>>;

export interface GraphSettings {
  step_params: StepParams;
  /** null — GPT-проверка не настроена, действует поведение по умолчанию. */
  auto_review_kinds: string[] | null;
  ai_new_window_per_check: boolean;
  auto_mode: boolean;
  bgm_level: number | null;
  /** node id → назначенный узлу вариант промта (`meta.prompt_slot_variants`). */
  prompt_variants: Record<string, string>;
}

/** Виды проверок человеком / GPT — один список на сервер и Telegram. */
export type HitlKind =
  | "approve_plan"
  | "approve_script"
  | "approve_hero"
  | "approve_images"
  | "approve_videos"
  | "approve_final";

export interface HitlRequest {
  id: number;
  project_id: number;
  frame_id: number | null;
  kind: HitlKind | string;
  decision: "pending" | "approved" | "regenerate" | "rejected" | string;
  payload: Record<string, unknown>;
  decided_at: string | null;
  created_at: string;
}

export type HitlDecision = "approve" | "regenerate" | "reject" | "edit_prompt";

// ── Группы узлов ────────────────────────────────────────────────────────

export interface NodeGroupSummary {
  id: string;
  title: string;
  description: string;
  category: string;
  node_count: number;
  nodes: { key: string; label: string; type: string }[];
  default_after_type: string | null;
  builtin: boolean;
  updated_at: string | null;
}

// ── Узел «Хранилище» ────────────────────────────────────────────────────

export interface StoredFile {
  name: string;
  path: string;
  size: number;
  kind: string;
  ok: boolean;
  fromNode: string | null;
  fromLabel: string | null;
  savedAt: string | null;
  originalName: string;
  preview_url: string | null;
  download_url: string;
}

export interface StorageResolve {
  nodeKey: string;
  label: string;
  formats: string[];
  autoSync: boolean;
  files: StoredFile[];
  okFileCount: number;
  incomingSources: { source?: string; label?: string; kind?: string; fileCount?: number }[];
  storageDir: string;
  lastSyncAt: string | null;
  lastSyncCopied?: number | null;
}

// ── Узел «Работа с GPT» ─────────────────────────────────────────────────

export type OperatorRole = "assist" | "review" | "transform" | "extract" | "compare" | "gate";
export type OperatorOutputMode = "text" | "project_file" | "sidecar";
export type OperatorEmitKind = "result" | "reply_txt" | "analysis" | "inputs";

export interface OperatorFile {
  name: string;
  path?: string;
  kind?: string;
  ok?: boolean;
  fromNode?: string | null;
  error?: string | null;
}

export interface OperatorResolve {
  nodeKey: string;
  role: OperatorRole;
  outputMode: OperatorOutputMode;
  emitKinds: OperatorEmitKind[];
  useSnapshot: boolean;
  takeFromEdges: boolean;
  checkMode: boolean;
  checkFix: boolean;
  checkPromptSource: "upstream" | "agent";
  checkAgentStep?: string | null;
  checkAgentFileName: string | null;
  checkAgentChars?: number | null;
  sourcePrompts?: { step?: string; label?: string; nodeKey?: string; ok?: boolean }[];
  label: string;
  files: OperatorFile[];
  okFileCount: number;
  incomingEdges: { id: string; source: string; kind: string; fileCount: number; ok: boolean; errors: string[] }[];
  outgoingEdges?: { id?: string; target?: string; kind?: string }[];
  branching: { hasPass?: boolean; hasFail?: boolean; verdict?: string | null; passEdges?: unknown[]; failEdges?: unknown[] };
  analysis?: { summary?: string; checks?: unknown[]; raw_error?: string | null } | null;
  errors: string[];
  warnings: string[];
  consistent: boolean;
  canRun: boolean;
  lastResult?: { replyPreview?: string; at?: string; ok?: boolean } | null;
  config: Record<string, unknown>;
}
