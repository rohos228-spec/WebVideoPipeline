export type ModelHealthPoint = { ts: string; success: number; errors: number };
export type ModelHealth = {
  model: string;
  title?: string;
  kind?: "video" | "image" | "llm" | "audio" | string;
  success: number;
  errors: number;
  success_rate?: number;
  avg_latency_s?: number;
  last_error?: string;
  updated_at?: string;
  series?: ModelHealthPoint[];
  last_runs?: { ts: string; ok: boolean }[];
};
export type ModelHealthResponse = { models: ModelHealth[]; window_hours?: number; updated_at?: string };
