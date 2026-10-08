"use client";

/**
 * Механика воркспейса: какие проекты видны и какая доска открывается.
 *
 * Строгая изоляция (docs/UNIFIED-MECHANICS-PLAN.md, R1): режим назначается
 * проекту при создании и immutable. Тумблер НЕ конвертирует проекты — он
 * переключает видимый набор (фильтр бэкенда `?pipeline_mode=`), проекты
 * чужой механики в список не попадают вовсе.
 */

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { PipelineMode } from "@/lib/types";
import { usePersistedState } from "./use-persisted-state";

const WORKSPACE_MODE_KEY = "studio.pipeline_mode";

function normalizeMode(raw: unknown): PipelineMode {
  return raw === "v2" ? "v2" : "v1";
}

/** Выбранная механика воркспейса. Дефолт — v1 (поведение продакшена). */
export function usePipelineMode(): [PipelineMode, (mode: PipelineMode) => void] {
  const [stored, setStored] = usePersistedState<unknown>(WORKSPACE_MODE_KEY, "v1");
  const mode = normalizeMode(stored);
  return [mode, (next: PipelineMode) => setStored(normalizeMode(next))];
}

/**
 * Режим конкретного проекта (для роутера досок и бейджа).
 * Без проекта — режим воркспейса. Пока грузится — воркспейс (v1 по дефолту).
 */
export function useProjectMode(projectId: number | null): PipelineMode {
  const [workspaceMode] = usePipelineMode();
  const detail = useQuery({
    queryKey: ["project-mode", projectId],
    queryFn: () => api.getProject(projectId!),
    enabled: projectId != null && projectId > 0,
    staleTime: 30_000,
    retry: false,
  });
  const raw: unknown = detail.data?.pipeline_mode;
  if (projectId == null) return workspaceMode;
  if (raw === "v2") return "v2";
  if (raw === "v1") return "v1";
  return workspaceMode;
}
