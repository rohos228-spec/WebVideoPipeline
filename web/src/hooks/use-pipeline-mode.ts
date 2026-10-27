"use client";

/**
 * Механика воркспейса: какие проекты видны и какая доска открывается.
 *
 * Строгая изоляция (docs/UNIFIED-MECHANICS-PLAN.md, R1): режим назначается
 * проекту при создании и immutable. Тумблер НЕ конвертирует проекты — он
 * переключает видимый набор (фильтр бэкенда `?pipeline_mode=`), проекты
 * чужой механики в список не попадают вовсе.
 */

import { useCallback, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { PipelineMode } from "@/lib/types";
import { usePersistedState } from "./use-persisted-state";

export const WORKSPACE_MODE_KEY = "studio.pipeline_mode";
export const SELECTED_PROJECT_KEY = "vp-studio-selected-project-id";
export const WORKSPACE_MODE_EVENT = "studio-pipeline-mode-sync";

export function normalizeMode(raw: unknown): PipelineMode {
  return raw === "v2" ? "v2" : "v1";
}

/**
 * Бесшовное переключение механики воркспейса:
 * 1. Сохраняет целевой режим в localStorage.
 * 2. Очищает выбранный проект (устраняет конфликт со старым проектом).
 * 3. Автоматически перенаправляет на чистый корень '/' без необходимости нажимать F5.
 */
export function switchPipelineMode(next: PipelineMode) {
  if (typeof window === "undefined") return;
  const target = normalizeMode(next);
  try {
    localStorage.setItem(WORKSPACE_MODE_KEY, JSON.stringify(target));
    localStorage.removeItem(SELECTED_PROJECT_KEY);
    window.dispatchEvent(new CustomEvent(WORKSPACE_MODE_EVENT, { detail: target }));
  } catch {}
  // Чистый переход в корень — сбрасывает состояние и query-параметры
  window.location.href = "/";
}

/** Выбранная механика воркспейса. Дефолт — v1. Синхронизируется реактивно между всеми компонентами. */
export function usePipelineMode(): [PipelineMode, (mode: PipelineMode) => void] {
  const [stored, setStored] = usePersistedState<unknown>(WORKSPACE_MODE_KEY, "v1");
  const mode = normalizeMode(stored);

  useEffect(() => {
    const onSync = (e: Event) => {
      const detail = (e as CustomEvent<PipelineMode>).detail;
      if (detail) {
        setStored(normalizeMode(detail));
      }
    };
    window.addEventListener(WORKSPACE_MODE_EVENT, onSync);
    return () => window.removeEventListener(WORKSPACE_MODE_EVENT, onSync);
  }, [setStored]);

  const setMode = useCallback(
    (next: PipelineMode) => {
      const target = normalizeMode(next);
      setStored(target);
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent(WORKSPACE_MODE_EVENT, { detail: target }));
      }
    },
    [setStored],
  );

  return [mode, setMode];
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
