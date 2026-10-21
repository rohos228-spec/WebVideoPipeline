"use client";

import type { SyntheticEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Clock,
  Download,
  FileSpreadsheet,
  FileText,
  Film,
  Image as ImageIcon,
  Layers,
  Loader2,
  MessageSquareText,
  Mic,
  Music,
  Pause,
  Play,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Sliders,
  Sparkles,
  Trash2,
  Upload,
  Volume2,
  X,
  Zap,
} from "lucide-react";
import { toast } from "sonner";
import type { ProjectDetail } from "@/lib/types";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import { getNodeSpec } from "@/lib/node-catalog";
import { getNodeIcon } from "@/lib/node-icons";
import { nodeTypeFromKey } from "@/lib/node-key";
import { stepCodeForNodeType, stepHasPromptVariants } from "@/lib/node-step-map";
import {
  defaultPromptSlots,
  nodeTypeRequiresExcel,
  pipelinePromptSlots,
  resolvePromptSlots,
  resolvePromptSlotsForNode,
  sceneAgentFromNodeKey,
  type NodePromptSlot,
} from "@/lib/node-prompts";
import {
  excelGptAttachmentChipTitle,
  excelGptSlotIndex,
  isExcelGptNode,
  EXCEL_GPT_STEP_CODE,
  workModeLabel,
  type ExcelGptNodeConfig,
} from "@/lib/excel-gpt-config";
import { ExcelGptSettingsPanel } from "@/components/studio/excel-gpt-settings-panel";
import { CheckNodePromptPanel } from "@/components/studio/check-node-prompt-panel";
import { VoiceLibraryModal, getVoiceById } from "@/components/outsee/voice-library-modal";
import { DEFAULT_ELEVENLABS_VOICE_ID, findElevenLabsVoice } from "@/lib/elevenlabs-voices";
import { readNodeStepParams, withNodeStepParams } from "@/lib/node-step-params";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { formatNodeKeyLabel, humanizeSlug } from "@/lib/format-labels";
import { cn } from "@/lib/utils";
import { promptPathsForNode, legacyPromptFolder } from "@/lib/prompt-catalog";
import {
  activeVariantForExcelGpt,
  activeVariantForSlot,
  activeVariantSourceForExcelGpt,
  activeVariantSourceForSlot,
  preferredPromptFileName,
  promptSourceLabel,
  withSlotVariant,
} from "@/lib/prompt-slot-storage";
import {
  pickDefaultSheetForNode,
  xlsxPreviewFocusForNode,
  xlsxStudioPreviewParams,
} from "@/lib/xlsx-sheets";
import { StudioExcelGrid } from "@/components/studio/studio-excel-grid";
import { FramePromptsPanel } from "@/components/studio/frame-prompts-panel";
import { NodeResultViewBody } from "@/components/canvas/node-result-views";
import { resolveNodeResult } from "@/lib/node-result-resolver";
import type { FrameDTO } from "@/lib/types";
import { NodeStepParamsPanel } from "@/components/studio/node-step-params-panel";
import { PromptFilesPanel } from "@/components/studio/prompt-files-panel";
import { shouldShowStopBar } from "@/lib/project-running";

type StudioTab = "settings" | "prompts" | "results";

function slotStepCode(slot: NodePromptSlot | null, nodeStepCode: string | undefined): string | undefined {
  return slot?.stepCode ?? nodeStepCode;
}

export function NodeStudio({
  open,
  onOpenChange,
  projectId,
  nodeKey,
  initialTab = "settings",
  promptFocus,
  nodeDisabled = false,
  promptSlots: promptSlotsProp,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  projectId: number | null;
  nodeKey: string | null;
  initialTab?: StudioTab;
  promptFocus?: NodePromptSlot | null;
  nodeDisabled?: boolean;
  promptSlots?: NodePromptSlot[];
}) {
  const nodeType = nodeTypeFromKey(nodeKey);
  const spec = getNodeSpec(nodeType, nodeKey);
  const stepCode = stepCodeForNodeType(nodeType);
  const NodeIcon = getNodeIcon(spec.iconKey);

  const [tab, setTab] = useState<StudioTab>(initialTab);
  const [activeSlotId, setActiveSlotId] = useState<string | null>(null);
  const [xlsxSheet, setXlsxSheet] = useState<string>("");
  /** false = весь лист (дефолт); true = узкий ключевой фрагмент по типу ноды */
  const [xlsxFocusKeyRows, setXlsxFocusKeyRows] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const qc = useQueryClient();
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId!),
    enabled: open && projectId != null,
    refetchInterval: (q) =>
      open && shouldShowStopBar(q.state.data?.status, q.state.data?.generation_active)
        ? 1500
        : false,
  });
  const globalActivePrompts = useQuery({
    queryKey: ["prompt-global-active"],
    queryFn: () => api.getGlobalActivePrompts(),
    enabled: open,
    staleTime: 5000,
  });
  const generationRunning = shouldShowStopBar(
    project.data?.status,
    project.data?.generation_active,
  );

  const excelGptConfig = useMemo((): ExcelGptNodeConfig => {
    if (!nodeKey || !isExcelGptNode(nodeType)) return {};
    const meta = (project.data?.meta || {}) as {
      excel_gpt_nodes?: Record<string, ExcelGptNodeConfig>;
    };
    const cfg = meta.excel_gpt_nodes?.[nodeKey] ?? {};
    const slotIndex = excelGptSlotIndex(nodeKey, cfg.slotIndex);
    return {
      label: cfg.label ?? spec.label,
      inputSource: cfg.inputSource ?? "project_xlsx",
      uploadedFileName: cfg.uploadedFileName,
      uploadedPreviewUrl: cfg.uploadedPreviewUrl,
      slotIndex,
      workMode: cfg.workMode ?? "assist",
      lastReplyPath: cfg.lastReplyPath,
      lastReplyAt: cfg.lastReplyAt,
    };
  }, [project.data?.meta, nodeKey, nodeType, spec.label]);

  const [excelConfig, setExcelConfig] = useState<ExcelGptNodeConfig>(excelGptConfig);
  useEffect(() => {
    setExcelConfig(excelGptConfig);
  }, [excelGptConfig]);

  // Тот же queryKey, что у ExcelGptSettingsPanel — общий кэш, лишних запросов нет.
  const operatorResolve = useQuery({
    queryKey: ["gpt-operator-resolve", projectId, nodeKey],
    queryFn: () => api.resolveGptOperator(projectId!, nodeKey!),
    enabled: open && projectId != null && !!nodeKey && isExcelGptNode(nodeType),
    staleTime: 5000,
  });
  const isCheckNode =
    isExcelGptNode(nodeType) && operatorResolve.data?.checkMode === true;

  const patchExcelNodeData = (patch: Partial<ExcelGptNodeConfig>) => {
    if (!nodeKey) return;
    setExcelConfig((prev) => ({ ...prev, ...patch }));
    window.dispatchEvent(
      new CustomEvent("canvas-patch-node-data", {
        detail: { nodeKey, patch },
      }),
    );
  };
  const artifacts = useQuery({
    queryKey: ["artifacts", projectId, nodeType],
    queryFn: () => api.listArtifacts({ project_id: projectId! }),
    enabled: open && projectId != null,
  });
  const allSlots = useMemo(() => {
    if (promptSlotsProp?.length) return resolvePromptSlots(nodeType, promptSlotsProp, nodeKey ?? undefined);
    const meta = (project.data?.meta || {}) as { custom_prompts?: Record<string, NodePromptSlot[]> };
    if (nodeKey) return resolvePromptSlotsForNode(nodeKey, nodeType, meta.custom_prompts);
    return resolvePromptSlots(nodeType, null);
  }, [project.data?.meta, nodeKey, nodeType, promptSlotsProp]);

  const pipelineSlots = useMemo(() => pipelinePromptSlots(allSlots), [allSlots]);

  const showStepParams =
    projectId != null &&
    (nodeType === "plan" ||
      nodeType === "script" ||
      nodeType === "split" ||
      nodeType === "assemble");

  const hasSettings = useMemo(() => {
    if (nodeDisabled) return true;
    if (isExcelGptNode(nodeType)) return true;
    if (showStepParams) return true;
    if (nodeType === "sfx_plan" || nodeType === "sfx_gen" || nodeType === "sfx") return true;
    if (nodeType === "image_prompts" || nodeType === "images") return true;
    if (nodeType === "animation_prompts" || nodeType === "videos" || nodeType === "audio") return true;
    return false;
  }, [nodeDisabled, nodeType, showStepParams]);

  useEffect(() => {
    if (!open) return;
    if (promptFocus) {
      setTab(promptFocus.kind === "excel" && hasSettings ? "settings" : "prompts");
      return;
    }
    const defaultTab =
      !hasSettings && initialTab === "settings"
        ? (pipelineSlots.some((s) => s.kind !== "excel") ? "prompts" : "results")
        : (initialTab === ("excel" as unknown) ? "settings" : initialTab);
    setTab(defaultTab);
  }, [open, initialTab, promptFocus, hasSettings, pipelineSlots]);

  useEffect(() => {
    if (!open) return;
    if (promptFocus?.id) {
      setActiveSlotId(promptFocus.id);
      return;
    }
    const firstPrompt = pipelineSlots.find((s) => s.kind !== "excel");
    setActiveSlotId(firstPrompt?.id ?? pipelineSlots[0]?.id ?? null);
  }, [promptFocus, pipelineSlots, open, nodeKey]);

  const activeSlot =
    (tab === "prompts"
      ? allSlots.find((s) => s.id === activeSlotId && s.kind !== "excel") ??
        pipelineSlots.find((s) => s.kind !== "excel")
      : null) ??
    allSlots.find((s) => s.id === activeSlotId) ??
    (promptFocus?.kind === "text" ? promptFocus : null) ??
    pipelineSlots.find((s) => s.kind !== "excel") ??
    pipelineSlots[0] ??
    null;

  const activeStepCode = slotStepCode(activeSlot, stepCode);
  const promptStepCode =
    isExcelGptNode(nodeType) && activeSlot?.kind === "gpt"
      ? EXCEL_GPT_STEP_CODE
      : activeStepCode;
  const promptPaths = promptPathsForNode(nodeType);
  const metaRecord = (project.data?.meta || {}) as Record<string, unknown>;
  const promptOverrides = (project.data?.prompt_overrides || {}) as Record<string, unknown>;
  const activeVariant =
    activeSlot && nodeKey
      ? isExcelGptNode(nodeType) && activeSlot.kind === "gpt"
        ? activeVariantForExcelGpt(
            metaRecord,
            nodeKey,
            activeSlot,
            promptOverrides,
            excelConfig.slotIndex,
            globalActivePrompts.data,
          )
        : activeVariantForSlot(
            metaRecord,
            nodeKey,
            activeSlot,
            promptOverrides,
            promptStepCode,
            globalActivePrompts.data,
          )
      : "default";
  const activeVariantSource =
    activeSlot && nodeKey
      ? isExcelGptNode(nodeType) && activeSlot.kind === "gpt"
        ? activeVariantSourceForExcelGpt(
            metaRecord,
            nodeKey,
            activeSlot,
            promptOverrides,
            excelConfig.slotIndex,
            globalActivePrompts.data,
          )
        : activeVariantSourceForSlot(
            metaRecord,
            nodeKey,
            activeSlot,
            promptOverrides,
            promptStepCode,
            globalActivePrompts.data,
          )
      : "default";
  const activeVariantSourceLabel = promptSourceLabel(activeVariantSource);
  const sdAgentFromKey = sceneAgentFromNodeKey(nodeKey);
  const preferredFile =
    preferredPromptFileName(activeSlot) ??
    (sdAgentFromKey ? `sd_${sdAgentFromKey}` : undefined);

  const activateVariant = useMutation({
    mutationFn: async (variant: string) => {
      if (!projectId || !promptStepCode || !nodeKey || !activeSlot) {
        return Promise.reject(new Error("no step"));
      }
      const meta = withSlotVariant(metaRecord, nodeKey, activeSlot.id, variant);
      // excel_gpt: SSoT — meta.prompt_slot_variants[nodeKey]. Не пишем в
      // prompt_overrides["excel_gpt"], иначе все ноды «Работа с GPT» шлют
      // один и тот же последний выбранный файл.
      if (isExcelGptNode(nodeType) && activeSlot.kind === "gpt") {
        await api.patchProject(projectId, { meta });
        return;
      }
      const prompt_overrides = {
        ...((project.data?.prompt_overrides || {}) as Record<string, unknown>),
        [promptStepCode]: variant,
      };
      await api.patchProject(projectId, { meta, prompt_overrides });
    },
    onSuccess: () => {
      toast.success("Активный промт обновлён");
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["prompt-global-active"] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const runStep = useMutation({
    mutationFn: (variables?: { mode?: "full" | "resume"; forceWipe?: boolean; continuing?: boolean }) =>
      api.runProjectStep(projectId!, stepCode!, {
        nodeKey: nodeKey ?? undefined,
        mode: variables?.mode ?? "resume",
        forceWipe: variables?.forceWipe,
      }),
    onSuccess: (_, vars) => {
      const isFull = vars?.mode === "full";
      toast.success(
        vars?.forceWipe
          ? `Шаг «${spec.label}» запущен заново`
          : vars?.continuing
            ? `Шаг «${spec.label}» продолжается, доделываем недостающее`
            : isFull
              ? `Шаг «${spec.label}» запущен начисто`
              : `Шаг «${spec.label}» запущен`,
      );
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["project-run", projectId] });
      qc.invalidateQueries({ queryKey: ["db-graph-project", projectId] });
      qc.invalidateQueries({ queryKey: ["project-frames", projectId] });
      qc.invalidateQueries({ queryKey: ["frames", projectId] });
      qc.invalidateQueries({ queryKey: ["artifacts", projectId] });
      void qc.refetchQueries({ queryKey: ["project-run", projectId] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });


  const reloadXlsx = useMutation({
    mutationFn: () => api.reloadProjectXlsx(projectId!),
    onSuccess: () => {
      toast.success("Таблица перечитана из файла");
      qc.invalidateQueries({ queryKey: ["xlsx-preview", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-sheets", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-general-plan", projectId] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const uploadXlsx = useMutation({
    mutationFn: async (file: File) => {
      if (isExcelGptNode(nodeType) && nodeKey) {
        return api.uploadExcelGptFile(projectId!, nodeKey, file);
      }
      await api.uploadProjectXlsx(projectId!, file, {
        nodeKey: nodeKey ?? undefined,
      });
      return { fileName: file.name };
    },
    onSuccess: (res) => {
      const name =
        res && typeof res === "object" && "fileName" in res
          ? String((res as { fileName?: string }).fileName || "")
          : "";
      toast.success(
        isExcelGptNode(nodeType) && name
          ? `Excel подменён: ${name} (вход только этот файл)`
          : "Excel загружен",
      );
      qc.invalidateQueries({ queryKey: ["xlsx-preview", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-sheets", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-general-plan", projectId] });
      qc.invalidateQueries({ queryKey: ["v-menu-xlsx-preview", projectId] });
      qc.invalidateQueries({ queryKey: ["gpt-operator-resolve", projectId] });
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      void qc.refetchQueries({ queryKey: ["xlsx-preview", projectId] });
      void qc.refetchQueries({ queryKey: ["xlsx-sheets", projectId] });
      if (isExcelGptNode(nodeType) && nodeKey && name) {
        window.dispatchEvent(
          new CustomEvent("canvas-patch-node-data", {
            detail: {
              nodeKey,
              patch: {
                inputSource: "upload",
                uploadedFileName: name,
                takeFromEdges: false,
              },
            },
          }),
        );
      }
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const filteredArtifacts = useMemo(() => {
    const list = artifacts.data ?? [];
    if (nodeType.includes("image") || nodeType === "images") {
      return list.filter((a) => a.kind.includes("image") || a.kind.includes("scene"));
    }
    if (nodeType.includes("video") || nodeType === "videos") {
      return list.filter((a) => a.kind.includes("video"));
    }
    if (nodeType === "hero" || nodeType === "items") {
      return list.filter((a) => a.kind.includes("hero") || a.kind.includes("item"));
    }
    if (nodeType === "audio") {
      return list.filter((a) => a.kind.includes("audio") && !a.kind.includes("music"));
    }
    if (nodeType === "music") {
      return list.filter((a) => a.kind.includes("music") || /[/\\]music[/\\]/i.test(a.path || ""));
    }
    if (nodeType === "assemble" || nodeType === "hitl_final") {
      return list.filter((a) => a.kind.includes("final"));
    }
    return [];
  }, [artifacts.data, nodeType]);

  const assets = useQuery({
    queryKey: ["project-assets", projectId],
    queryFn: () => api.listProjectAssets(projectId!),
    enabled: open && projectId != null,
  });

  const dbBrowser = useQuery({
    queryKey: ["db-graph-project", projectId],
    queryFn: () => api.dbGraph(projectId!),
    enabled: open && projectId != null,
  });

  const frames = useQuery({
    queryKey: ["project-frames", projectId],
    queryFn: () => api.listFrames(projectId!),
    enabled: open && projectId != null,
  });

  const isRunning = shouldShowStopBar(project.data?.status);

  // Реактивная инвалидация и обновление данных при смене статуса или окончании запуска
  const prevStatusRef = useRef(project.data?.status);
  const prevRunningRef = useRef(generationRunning);
  useEffect(() => {
    if (!open || !projectId) return;
    const statusChanged =
      prevStatusRef.current !== undefined &&
      prevStatusRef.current !== project.data?.status;
    const runFinished = prevRunningRef.current && !generationRunning;

    if (statusChanged || runFinished) {
      void qc.invalidateQueries({ queryKey: ["db-graph-project", projectId] });
      void qc.invalidateQueries({ queryKey: ["project-frames", projectId] });
      void qc.invalidateQueries({ queryKey: ["frames", projectId] });
      void qc.invalidateQueries({ queryKey: ["artifacts", projectId] });
      void qc.invalidateQueries({ queryKey: ["project-assets", projectId] });
      void qc.invalidateQueries({ queryKey: ["media-review", projectId] });
      void qc.invalidateQueries({ queryKey: ["gpt-operator-resolve", projectId] });
      void dbBrowser.refetch();
      void frames.refetch();
    }
    prevStatusRef.current = project.data?.status;
    prevRunningRef.current = generationRunning;
  }, [open, projectId, project.data?.status, generationRunning, qc]);

  // Обновление при переключении на вкладку "results"
  useEffect(() => {
    if (!open || !projectId || tab !== "results") return;
    void dbBrowser.refetch();
    void frames.refetch();
    void artifacts.refetch();
    void assets.refetch();
    void mediaImages.refetch();
    void mediaVideos.refetch();
    if (isExcelGptNode(nodeType)) {
      void operatorResolve.refetch();
    }
  }, [tab, open, projectId, nodeType]);

  // Обновление при открытии модального окна Studio
  useEffect(() => {
    if (!open || !projectId) return;
    void dbBrowser.refetch();
    void frames.refetch();
    void artifacts.refetch();
    void assets.refetch();
    if (isExcelGptNode(nodeType)) {
      void operatorResolve.refetch();
    }
  }, [open, projectId, nodeType]);

  const mediaImages = useQuery({
    queryKey: ["media-review", projectId, "images"],
    queryFn: () => api.listMediaReview(projectId!, "images"),
    enabled: open && projectId != null,
    refetchInterval:
      open && isRunning && (nodeType === "images" || nodeType === "hitl_images") ? 5000 : false,
  });

  const mediaVideos = useQuery({
    queryKey: ["media-review", projectId, "videos"],
    queryFn: () => api.listMediaReview(projectId!, "videos"),
    enabled: open && projectId != null,
    refetchInterval:
      open && isRunning && (nodeType === "videos" || nodeType === "hitl_videos") ? 5000 : false,
  });

  const mapMedia = useCallback(
    (rows: NonNullable<typeof mediaImages.data>, kind: "images" | "videos") =>
      rows
        .filter((r) => r.preview_url)
        .map((r) => ({
          source: "frame" as const,
          id: String(r.frame_id),
          kind,
          path: r.file_path,
          preview_url: r.preview_url,
          label: `Кадр ${r.number}`,
          frame_id: r.frame_id,
          voiceover: r.voiceover_text,
        })),
    [],
  );

  const effectiveFrames = useMemo(() => {
    const listFramesData = (frames.data || []) as FrameDTO[];
    const dbFramesData = ((dbBrowser.data?.frames as unknown as FrameDTO[]) || []);
    if (listFramesData.some((f) => f.image_prompt?.trim())) return listFramesData;
    if (dbFramesData.some((f) => f.image_prompt?.trim())) return dbFramesData;
    return listFramesData.length ? listFramesData : dbFramesData;
  }, [frames.data, dbBrowser.data?.frames]);

  const resultSnapshot = useMemo(() => {
    if (!projectId) return null;
    return resolveNodeResult(
      nodeType,
      {
        project: project.data,
        artifacts: artifacts.data ?? [],
        assets: assets.data ?? [],
        frames: effectiveFrames,
        mediaImages: mapMedia(mediaImages.data ?? [], "images"),
        mediaVideos: mapMedia(mediaVideos.data ?? [], "videos"),
        operatorResolve: operatorResolve.data,
      },
      undefined,
      nodeKey,
    );
  }, [
    nodeType,
    projectId,
    project.data,
    artifacts.data,
    assets.data,
    effectiveFrames,
    mediaImages.data,
    mediaVideos.data,
    mapMedia,
    nodeKey,
    operatorResolve.data,
  ]);

  const showFramePromptsPanel =
    activeSlot?.kind === "frame_prompts" && projectId != null;
  const showFilesPanel =
    activeSlot?.kind === "gpt" &&
    Boolean(promptStepCode) &&
    stepHasPromptVariants(promptStepCode);
  const [mounted, setMounted] = useState(false);
  const backdropGuardUntil = useRef(0);
  useEffect(() => setMounted(true), []);

  useEffect(() => {
    if (open) backdropGuardUntil.current = Date.now() + 500;
  }, [open]);

  const isThisNodeRunning = useMemo(() => {
    if (runStep.isPending) return true;
    const status = project.data?.status as string | undefined;
    const genActive = Boolean(project.data?.generation_active);
    if (!status) return false;
    if (status === "generating_images" && (nodeType === "images" || stepCode === "img")) return true;
    if (status === "generating_image_prompts" && (nodeType === "image_prompts" || stepCode === "img_pr")) return true;
    if (status === "generating_animation_prompts" && (nodeType === "animation_prompts" || stepCode === "anim_pr")) return true;
    if (status === "generating_videos" && (nodeType === "videos" || stepCode === "video")) return true;
    if (status === "generating_audio" && (nodeType === "audio" || stepCode === "audio")) return true;
    if (status === "generating_music" && (nodeType === "music" || stepCode === "music")) return true;
    if (status === "sfx_planning" && (nodeType === "sfx_plan" || stepCode === "sfx_plan")) return true;
    if (status === "generating_sfx" && (nodeType === "sfx_gen" || nodeType === "sfx" || stepCode === "sfx_gen")) return true;
    if (status === "assembling" && (nodeType === "assemble" || stepCode === "assemble")) return true;
    if (status === "generating_hero" && (nodeType === "hero" || stepCode === "hero")) return true;
    if (status === "generating_items" && (nodeType === "items" || stepCode === "items")) return true;
    if (status === "planning" && (nodeType === "plan" || stepCode === "plan")) return true;
    if (status === "scripting" && (nodeType === "script" || stepCode === "script")) return true;
    if (status === "splitting" && (nodeType === "split" || stepCode === "split")) return true;
    if (status === "scene_designing" && (nodeType === "scene_design" || stepCode === "scene_d")) return true;
    if (status.startsWith("enriching_") && isExcelGptNode(nodeType)) return true;
    if (genActive && (status.includes(stepCode || "") || status.includes(nodeType))) return true;
    return false;
  }, [runStep.isPending, project.data?.status, project.data?.generation_active, nodeType, stepCode]);

  if (!nodeKey || !mounted || !open) return null;

  const closeNow = (e: SyntheticEvent) => {
    if (Date.now() < backdropGuardUntil.current) return;
    e.preventDefault();
    e.stopPropagation();
    onOpenChange(false);
  };

  return createPortal(
    <>
      <button
        type="button"
        aria-label="Закрыть студию"
        className="fixed inset-0 z-[90] bg-black/45 backdrop-blur-[2px]"
        onPointerDown={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
        onMouseDown={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
        onClick={closeNow}
      />
      <aside
        className="premium-sheet fixed right-0 top-0 z-[100] flex h-full w-[min(920px,92vw)] flex-col border-l border-white/10 shadow-2xl"
        role="dialog"
        aria-modal="true"
      >
        <div className="flex h-full flex-col">
          <header className="relative shrink-0 border-b border-white/10 bg-gradient-to-r from-amber-500/5 via-transparent to-violet-500/5 px-5 py-4">
            <button
              type="button"
              aria-label="Закрыть студию"
              title="Закрыть (Esc)"
              className="absolute right-3 top-3 z-[210] inline-flex h-9 w-9 cursor-pointer items-center justify-center rounded-md bg-background/90 text-foreground/90 ring-1 ring-white/10 transition hover:bg-destructive hover:text-destructive-foreground"
              onPointerDown={(e) => e.stopPropagation()}
              onMouseDown={(e) => e.stopPropagation()}
              onClickCapture={closeNow}
              onClick={closeNow}
            >
              <X className="h-5 w-5" />
            </button>
            <div className="flex items-start justify-between gap-4 pr-12">
              <div>
                <h2 className="flex items-center gap-2 text-lg font-semibold">
                  <span
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl shadow-inner"
                    style={{
                      background: `linear-gradient(135deg, hsl(${spec.accent} / 0.25), hsl(${spec.accent} / 0.08))`,
                      color: `hsl(${spec.accent})`,
                    }}
                  >
                    <NodeIcon className="h-4 w-4" />
                  </span>
                  {excelConfig.label?.trim() || spec.label}
                </h2>
                <p className="text-xs text-muted-foreground">{spec.description}</p>
                {isExcelGptNode(nodeType) && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    <Badge variant="muted" className="text-[10px] text-violet-200/90">
                      {workModeLabel(excelConfig.workMode)}
                    </Badge>
                  </div>
                )}
              </div>
              <div className="flex w-full flex-col gap-2 sm:flex-row sm:flex-wrap items-center">
                {stepCode && (
                  <>
                    <Button
                      size="sm"
                      onClick={() => runStep.mutate({ mode: "resume", forceWipe: true })}
                      disabled={!projectId || isThisNodeRunning || nodeDisabled}
                      className={cn(
                        "transition-all duration-200 gap-2 h-9 px-4 font-semibold text-xs text-white bg-gradient-to-r from-emerald-500 via-emerald-500 to-teal-500 hover:from-emerald-400 hover:to-teal-400 active:scale-[0.98] shadow-lg shadow-emerald-500/35 border border-emerald-300/40 rounded-xl backdrop-blur-md",
                        isThisNodeRunning &&
                          "border-emerald-400/80 bg-emerald-500/25 text-emerald-200 animate-pulse font-medium shadow-[0_0_20px_rgba(16,185,129,0.35)]",
                      )}
                      title={
                        nodeDisabled
                          ? "Нода отключена в графе"
                          : isThisNodeRunning
                            ? "Шаг сейчас выполняется..."
                            : "Запустить шаг заново: стереть старые результаты и сгенерировать всё с нуля"
                      }
                    >
                      {isThisNodeRunning ? (
                        <>
                          <Loader2 className="h-3.5 w-3.5 animate-spin text-emerald-200" />
                          <span>В работе...</span>
                        </>
                      ) : (
                        <>
                          <Play className="h-3.5 w-3.5 fill-current" />
                          <span>Запустить шаг</span>
                        </>
                      )}
                    </Button>
                    {!isThisNodeRunning && !["plan", "script", "split", "assemble", "publish"].includes(nodeType) && (
                      <Button
                        size="sm"
                        onClick={() => runStep.mutate({ mode: "resume", continuing: true })}
                        disabled={!projectId || isThisNodeRunning || nodeDisabled}
                        className="transition-all duration-200 gap-2 h-9 px-4 font-semibold text-xs text-amber-950 bg-gradient-to-r from-amber-400 via-amber-300 to-yellow-400 hover:from-amber-300 hover:to-yellow-300 active:scale-[0.98] shadow-lg shadow-amber-500/25 border border-amber-200/60 rounded-xl backdrop-blur-md"
                        title="Доделать недостающее: готовое не трогаем, генерируем только то, чего не хватает"
                      >
                        <Play className="h-3.5 w-3.5 text-amber-950 fill-current" />
                        <span>Продолжить / Доделать</span>
                      </Button>
                    )}

                  </>
                )}
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-1">
              {(
                [
                  ...(hasSettings ? ([["settings", "Настройки", Settings2]] as const) : []),
                  ...(pipelineSlots.some((s) => s.kind !== "excel")
                    ? ([["prompts", "Промпты", FileText]] as const)
                    : []),
                  ["results", "Результаты", FileText],
                ] as const
              ).map(([id, label, Icon]) => (
                <Button
                  key={id}
                  type="button"
                  size="sm"
                  variant="ghost"
                  className={cn(
                    "gap-1.5 text-xs font-semibold rounded-lg transition-all",
                    tab === id
                      ? "bg-zinc-800 text-zinc-100 border border-zinc-700 shadow-sm"
                      : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50",
                  )}
                  onClick={() => {
                    setTab(id);
                    const targetSlot =
                      id === "prompts"
                        ? (pipelineSlots.find((s) => s.kind !== "excel") ?? pipelineSlots[0])
                        : pipelineSlots[0];
                    setActiveSlotId(targetSlot?.id ?? null);
                  }}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {label}
                </Button>
              ))}
            </div>
            {tab === "prompts" && pipelineSlots.filter((slot) => slot.kind !== "excel").length > 1 && (
              <div className="mt-3 flex flex-wrap gap-1 border-t border-white/5 pt-3">
                {pipelineSlots
                  .filter((slot) => slot.kind !== "excel")
                  .map((slot) => (
                    <Button
                      key={slot.id}
                      size="sm"
                      variant="ghost"
                      className={cn(
                        "h-7 text-xs font-medium rounded-lg transition-all",
                        activeSlotId === slot.id
                          ? "bg-zinc-800 text-white border border-zinc-700 shadow-sm"
                          : "border border-zinc-800 bg-zinc-900/60 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/40",
                      )}
                      onClick={() => {
                        setActiveSlotId(slot.id);
                      }}
                    >
                      {slot.title}
                    </Button>
                  ))}
              </div>
            )}
          </header>

          <ScrollArea className="min-h-0 flex-1">
            <div className="p-5">
              {tab === "settings" && (
                <div className="flex flex-col gap-4 text-sm text-muted-foreground">
                  {isExcelGptNode(nodeType) && projectId && nodeKey ? (
                    <ExcelGptSettingsPanel
                      projectId={projectId}
                      nodeKey={nodeKey}
                      config={excelConfig}
                      onConfigChange={patchExcelNodeData}
                    />
                  ) : null}
                  {showStepParams ? (
                    <NodeStepParamsPanel projectId={projectId!} nodeType={nodeType} />
                  ) : null}
                  {nodeType === "sfx_plan" && projectId ? (
                    <SfxPlanSettingsPanel projectId={projectId} />
                  ) : null}
                  {(nodeType === "sfx_gen" || nodeType === "sfx") && projectId ? (
                    <SfxGenSettingsPanel projectId={projectId} />
                  ) : null}
                  {nodeType === "image_prompts" && projectId ? (
                    <ImagePromptsSettingsPanel
                      projectId={projectId}
                      onOpenPrompts={() => {
                        setTab("prompts");
                        const targetSlot = pipelineSlots.find((s) => s.kind !== "excel") ?? pipelineSlots[0];
                        setActiveSlotId(targetSlot?.id ?? null);
                      }}
                    />
                  ) : null}
                  {nodeType === "images" && projectId ? (
                    <ImagesSettingsPanel projectId={projectId} />
                  ) : null}
                  {nodeType === "animation_prompts" && projectId ? (
                    <AnimationPromptsSettingsPanel
                      projectId={projectId}
                      onOpenPrompts={() => {
                        setTab("prompts");
                        const targetSlot = pipelineSlots.find((s) => s.id === "main") ?? pipelineSlots[0];
                        setActiveSlotId(targetSlot?.id ?? null);
                      }}
                      onOpenFramePrompts={() => {
                        setTab("prompts");
                        const targetSlot = pipelineSlots.find((s) => s.id === "frame_prompts") ?? pipelineSlots[0];
                        setActiveSlotId(targetSlot?.id ?? null);
                      }}
                    />
                  ) : null}
                  {nodeType === "videos" && projectId ? (
                    <VideosSettingsPanel
                      projectId={projectId}
                      onOpenFramePrompts={() => {
                        setTab("prompts");
                        const targetSlot = pipelineSlots.find((s) => s.id === "frame_prompts") ?? pipelineSlots[0];
                        setActiveSlotId(targetSlot?.id ?? null);
                      }}
                    />
                  ) : null}
                  {nodeType === "audio" && projectId ? (
                    <AudioSettingsPanel
                      projectId={projectId}
                      onOpenVoiceover={() => {
                        setTab("prompts");
                        const targetSlot = pipelineSlots.find((s) => s.id === "voiceover" || s.kind === "frame_prompts") ?? pipelineSlots[0];
                        setActiveSlotId(targetSlot?.id ?? null);
                      }}
                    />
                  ) : null}
                  {nodeDisabled && (
                    <p className="text-amber-400">Нода отключена в графе — шаг не запустится.</p>
                  )}
                </div>
              )}

              {tab === "prompts" && (
                <div className="flex flex-col gap-4">
                  {activeSlot && showFilesPanel && (
                    <p className="text-xs text-muted-foreground">
                      Редактируется:{" "}
                      <span className="font-medium text-foreground">{activeSlot.title}</span>
                    </p>
                  )}
                  {showFramePromptsPanel ? (
                    <FramePromptsPanel
                      key={`frame-prompts-${projectId}-${nodeType}`}
                      projectId={projectId}
                      field={
                        nodeType === "audio"
                          ? "voiceover_text"
                          : nodeType === "videos" || nodeType === "animation_prompts"
                            ? "animation_prompt"
                            : "image_prompt"
                      }
                    />
                  ) : showFilesPanel && isCheckNode ? (
                    <CheckNodePromptPanel
                      projectId={projectId!}
                      nodeKey={nodeKey!}
                      resolve={operatorResolve.data}
                      loading={operatorResolve.isLoading}
                    />
                  ) : showFilesPanel && promptStepCode ? (
                    <PromptFilesPanel
                      key={`files-${nodeKey}-${activeSlot?.id}-${promptStepCode}`}
                      stepCode={promptStepCode}
                      slotId={activeSlot?.id}
                      projectId={projectId ?? undefined}
                      preferredFile={preferredFile}
                      folderHint={
                        legacyPromptFolder(promptStepCode) ??
                        (activeSlot?.stepCode && activeSlot.stepCode !== stepCode
                          ? activeSlot.stepCode
                          : (promptPaths.legacyDir ?? promptStepCode))
                      }
                      activeVariant={activeVariant}
                      activeVariantSourceLabel={activeVariantSourceLabel}
                      onActivateVariant={(variant) => activateVariant.mutate(variant)}
                      activating={activateVariant.isPending}
                    />
                  ) : (
                    <div className="flex flex-col gap-2 rounded-xl border border-white/10 bg-white/[0.02] p-4 text-sm text-muted-foreground">
                      <p>
                        Для этой ноды нет отдельных текстовых промтов на этом шаге.
                      </p>
                      <p className="text-xs text-white/50">
                        Промпты и параметры настраиваются на предшествующих шагах.
                      </p>
                    </div>
                  )}
                </div>
              )}


              {tab === "results" && (
                <div className="flex flex-col gap-4">
                  <div className="flex items-center justify-between pb-2 border-b border-white/5">
                    <span className="text-xs text-muted-foreground">
                      {resultSnapshot?.summary || "Результаты шага"}
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-7 gap-1.5 text-xs text-zinc-400 hover:text-zinc-100"
                      onClick={() => {
                        void dbBrowser.refetch();
                        void frames.refetch();
                        void artifacts.refetch();
                        void assets.refetch();
                        if (isExcelGptNode(nodeType)) {
                          void operatorResolve.refetch();
                        }
                      }}
                    >
                      <RefreshCw
                        className={cn(
                          "h-3.5 w-3.5",
                          (dbBrowser.isFetching || frames.isFetching) && "animate-spin",
                        )}
                      />
                      Обновить
                    </Button>
                  </div>

                  {resultSnapshot && (resultSnapshot.hasResult || resultSnapshot.items.length > 0 || isExcelGptNode(nodeType)) ? (
                    <NodeResultViewBody
                      projectId={projectId!}
                      nodeKey={nodeKey}
                      nodeType={nodeType}
                      snapshot={resultSnapshot}
                    />
                  ) : filteredArtifacts.length > 0 ? (
                    <div className="grid gap-3 sm:grid-cols-2">
                      {filteredArtifacts.map((a) => (
                        <div
                          key={a.id}
                          className="rounded-xl border border-white/10 bg-white/5 p-2"
                        >
                          <div className="text-[10px] uppercase text-muted-foreground">
                            {humanizeSlug(a.kind)}
                          </div>
                          {a.path.match(/\.(mp4|webm)$/i) ? (
                            <video
                              controls
                              className="mt-1 max-h-40 w-full rounded"
                              src={api.artifactFileUrl(a.uuid)}
                            />
                          ) : a.path.match(/\.(mp3|wav|m4a|ogg|aac|flac)$/i) ? (
                            <div className="mt-2 flex flex-col gap-1 rounded bg-black/40 p-2">
                              <audio
                                controls
                                className="w-full"
                                src={api.artifactFileUrl(a.uuid)}
                              />
                            </div>
                          ) : a.path.match(/\.(json|txt|tsv|md|csv)$/i) ? (
                            <div className="mt-1 flex max-h-40 flex-col overflow-auto rounded bg-black/40 p-2 text-[11px] font-mono text-zinc-300">
                              <div className="text-[9px] text-zinc-500">
                                {a.path.split(/[\\/]/).pop()}
                              </div>
                              <span className="mt-1 text-[10px] text-zinc-400">Текстовый артефакт (доступен для скачивания)</span>
                            </div>
                          ) : (
                            <img
                              alt=""
                              className="mt-1 max-h-40 w-full rounded object-contain"
                              src={api.artifactFileUrl(a.uuid)}
                            />
                          )}
                          <a
                            href={api.artifactFileUrl(a.uuid)}
                            download
                            className="mt-2 inline-flex items-center gap-1 text-[10px] text-primary hover:underline"
                          >
                            <Download className="h-3 w-3" />
                            Скачать
                          </a>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="flex flex-col items-center justify-center py-12 text-center text-muted-foreground gap-3">
                      <p className="text-sm">
                        {resultSnapshot?.summary || "Результаты ещё не сформированы. Запустите шаг для генерации."}
                      </p>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="gap-1.5 text-xs"
                        onClick={() => {
                          void dbBrowser.refetch();
                          void frames.refetch();
                          void artifacts.refetch();
                        }}
                      >
                        <RefreshCw
                          className={cn(
                            "h-3.5 w-3.5",
                            (dbBrowser.isFetching || frames.isFetching) && "animate-spin",
                          )}
                        />
                        Обновить результаты
                      </Button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </ScrollArea>
        </div>
      </aside>
    </>,
    document.body,
  );
}

function SfxPlanSettingsPanel({ projectId }: { projectId: number }) {
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
  });

  const meta = (project.data?.meta || {}) as Record<string, unknown>;
  const aiJobs = (meta.ai_jobs || {}) as Record<string, unknown>;
  const sfxPlan = (aiJobs.sfx_plan || meta.sfx_plan) as
    | {
        total_duration?: number;
        events?: unknown[];
      }
    | undefined;

  const count = sfxPlan?.events?.length ?? 0;
  const duration = sfxPlan?.total_duration;

  return (
    <section className="flex flex-col gap-4 rounded-xl border border-white/10 bg-white/[0.02] p-5">
      <div>
        <h3 className="text-sm font-semibold text-foreground">Параметры звукорежиссуры (SFX)</h3>
        <p className="mt-1 text-xs text-muted-foreground leading-relaxed">
          ИИ-агент автоматически планирует акцентные звуки и переходы по хронометражу дикторского текста.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-white/10 bg-white/[0.02] p-3">
          <div className="text-[11px] font-medium text-zinc-400">Статус плана звуков</div>
          <div className="mt-1.5 flex items-center gap-2">
            <span
              className={cn(
                "h-2 w-2 rounded-full",
                count > 0 ? "bg-emerald-400" : "bg-amber-400",
              )}
            />
            <span className="text-sm font-medium text-foreground">
              {count > 0 ? `Составлен (${count} событий)` : "Ожидает запуска"}
            </span>
            {duration ? (
              <span className="text-xs text-muted-foreground">· {duration} с</span>
            ) : null}
          </div>
        </div>

        <div className="rounded-lg border border-white/10 bg-white/[0.02] p-3">
          <div className="text-[11px] font-medium text-zinc-400">Провайдер генерации SFX</div>
          <div className="mt-1.5 text-sm font-medium text-foreground">
            ElevenLabs SFX API + Локальный синтез
          </div>
        </div>
      </div>

      <div className="rounded-lg border border-white/10 bg-black/20 p-3.5">
        <div className="text-xs font-semibold text-zinc-300 mb-2">Категории звуковых меток:</div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px] text-zinc-400">
          <div><strong className="text-rose-300">hit</strong> — выстрелы, удары, взрывы</div>
          <div><strong className="text-cyan-300">whoosh</strong> — взмахи меча, пролёты</div>
          <div><strong className="text-emerald-300">foley</strong> — шаги, лязг керамита</div>
          <div><strong className="text-purple-300">stinger</strong> — акценты кульминации</div>
          <div><strong className="text-amber-300">riser</strong> — нарастание напряжения</div>
          <div><strong className="text-indigo-300">ambience</strong> — фоновый гул локации</div>
          <div><strong className="text-sky-300">transition</strong> — переходы сцен</div>
          <div><strong className="text-zinc-300">duck</strong> — авто-приглушение</div>
        </div>
      </div>
    </section>
  );
}

function SfxGenSettingsPanel({ projectId }: { projectId: number }) {
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
  });

  const meta = (project.data?.meta || {}) as Record<string, unknown>;
  const aiJobs = (meta.ai_jobs || {}) as Record<string, unknown>;
  const sfxPlan = (aiJobs.sfx_plan || meta.sfx_plan) as
    | {
        total_duration?: number;
        events?: unknown[];
      }
    | undefined;

  const sfxFiles = (aiJobs.sfx_files || {}) as { files?: unknown[] };
  const plannedCount = sfxPlan?.events?.length ?? 0;
  const generatedCount = sfxFiles?.files?.length ?? 0;

  return (
    <section className="flex flex-col gap-4 rounded-xl border border-white/10 bg-white/[0.02] p-5">
      <div>
        <h3 className="text-sm font-semibold text-foreground">Параметры синтеза звуков (SFX)</h3>
        <p className="mt-1 text-xs text-muted-foreground leading-relaxed">
          Генерация аудиофайлов для каждого запланированного звукового события через нейросеть ElevenLabs Sound Generation или встроенный офлайн-синтезатор.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-white/10 bg-white/[0.02] p-3">
          <div className="text-[11px] font-medium text-zinc-400">Статус генерации файлов</div>
          <div className="mt-1.5 flex items-center gap-2">
            <span
              className={cn(
                "h-2 w-2 rounded-full",
                generatedCount > 0 ? "bg-emerald-400" : plannedCount > 0 ? "bg-amber-400" : "bg-zinc-500",
              )}
            />
            <span className="text-sm font-medium text-foreground">
              {generatedCount > 0
                ? `Готово: ${generatedCount} / ${plannedCount || generatedCount} файлов`
                : plannedCount > 0
                  ? `Ожидает запуска (${plannedCount} событий в плане)`
                  : "Нет плана звуков"}
            </span>
          </div>
        </div>

        <div className="rounded-lg border border-white/10 bg-white/[0.02] p-3">
          <div className="text-[11px] font-medium text-zinc-400">Провайдер ИИ-звуков</div>
          <div className="mt-1.5 flex items-center justify-between">
            <span className="text-sm font-medium text-foreground">ElevenLabs SFX API</span>
            <span className="rounded bg-violet-500/15 px-1.5 py-0.5 text-[10px] font-medium text-violet-300 border border-violet-500/30">
              ~$0.01 / звук
            </span>
          </div>
        </div>
      </div>

      <div className="rounded-lg border border-white/10 bg-black/20 p-3.5 flex flex-col gap-2">
        <div className="text-xs font-semibold text-zinc-300">Архитектурные гарантии качества:</div>
        <ul className="text-[11px] text-zinc-400 space-y-1.5 list-disc list-inside">
          <li>
            <strong className="text-zinc-200">Резервный синтез (Fallback):</strong> при сбоях сети или исчерпании лимита API автоматически срабатывает встроенный локальный синтезатор WAV.
          </li>
          <li>
            <strong className="text-zinc-200">Пофайловые чекпоинты:</strong> при повторном запуске уже созданные валидные файлы не перезаписываются, сохраняя баланс API.
          </li>
          <li>
            <strong className="text-zinc-200">Quality Guard:</strong> каждый звук проверяется через ffprobe и RMS громкости — брак и тишина отсекаются.
          </li>
          <li>
            <strong className="text-zinc-200">Финальный микс:</strong> в следующем шаге «Сборка» все звуки автоматически позиционируются на таймлайне и микшируются с озвучкой и музыкой.
          </li>
        </ul>
      </div>
    </section>
  );
}

function ImagePromptsSettingsPanel({
  projectId,
}: {
  projectId: number;
  onOpenPrompts?: () => void;
}) {
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
  });
  const frames = useQuery({
    queryKey: ["project-frames", projectId],
    queryFn: () => api.listFrames(projectId),
  });

  const frameList = frames.data || [];
  const totalCount = frameList.length;
  const readyCount = frameList.filter((f) => Boolean(f.image_prompt?.trim())).length;
  const generatorName = project.data?.image_generator || "Outsee (Midjourney / Flux)";
  const isMiniMax = generatorName.toLowerCase().includes("minimax");
  const promptLimit = isMiniMax ? "до 1 460 знаков (Компактный замок)" : "до 4 860 знаков (Полный замок)";

  return (
    <section className="flex flex-col gap-4 rounded-xl border border-white/10 bg-white/[0.02] p-5">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-white/10 bg-white/[0.02] p-3">
          <div className="text-[11px] font-medium text-zinc-400">Статус генерации промптов</div>
          <div className="mt-1.5 flex items-center gap-2">
            <span
              className={cn(
                "h-2 w-2 rounded-full",
                readyCount === totalCount && totalCount > 0
                  ? "bg-emerald-400"
                  : readyCount > 0
                    ? "bg-amber-400"
                    : "bg-zinc-500",
              )}
            />
            <span className="text-sm font-medium text-foreground">
              {readyCount === totalCount && totalCount > 0
                ? `Готово: ${readyCount} из ${totalCount} кадров`
                : readyCount > 0
                  ? `Частично: ${readyCount} / ${totalCount} кадров`
                  : totalCount > 0
                    ? `Ожидает запуска (${totalCount} кадров)`
                    : "Кадры не найдены"}
            </span>
          </div>
        </div>

        <div className="rounded-lg border border-white/10 bg-white/[0.02] p-3">
          <div className="text-[11px] font-medium text-zinc-400">Целевой генератор и лимит символов</div>
          <div className="mt-1.5 flex items-center justify-between">
            <span className="text-sm font-medium text-foreground">{generatorName}</span>
            <span className="rounded bg-violet-500/15 px-1.5 py-0.5 text-[10px] font-medium text-violet-300 border border-violet-500/30">
              {promptLimit}
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}

function ImagesSettingsPanel({ projectId }: { projectId: number }) {
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
    enabled: Boolean(projectId),
  });

  const frames = useQuery({
    queryKey: ["project-frames", projectId],
    queryFn: () => api.listFrames(projectId),
    enabled: Boolean(projectId),
  });

  const excelHero = useQuery({
    queryKey: ["excel-hero", projectId],
    queryFn: () => api.getExcelHero(projectId),
    enabled: Boolean(projectId),
  });

  const currentStreams =
    typeof (project.data?.meta as Record<string, unknown> | undefined)?.img_streams === "number"
      ? ((project.data?.meta as Record<string, unknown>).img_streams as number)
      : 2;

  const totalFrames = frames.data?.length ?? 0;
  const readyPromptsCount =
    frames.data?.filter((f) => Boolean(f.image_prompt && f.image_prompt.trim())).length ?? 0;
  const allPromptsReady = totalFrames > 0 && readyPromptsCount === totalFrames;

  const chars = excelHero.data?.characters ?? [];
  const items = (project.data?.item_descriptions ?? []).filter((d: string) => d && d.trim());

  return (
    <section className="flex flex-col gap-4 rounded-xl border border-white/10 bg-white/[0.02] p-5">
      {/* Карточка готовности и преемственности */}
      <div className="rounded-lg border border-white/10 bg-black/20 p-3.5 flex flex-col gap-2.5">
        <div className="flex items-center justify-between text-xs">
          <div className="flex items-center gap-2">
            <CheckCircle2 className={cn("h-4 w-4", allPromptsReady ? "text-emerald-400" : "text-amber-400")} />
            <span className="font-semibold text-zinc-200">
              Готовность к запуску:
            </span>
            <span className={cn("font-medium", allPromptsReady ? "text-emerald-300" : "text-amber-300")}>
              {readyPromptsCount} из {totalFrames} кадров с готовыми промтами
            </span>
          </div>
          <span className="text-[11px] text-zinc-400">
            Оценка времени: ~{Math.ceil(totalFrames / (currentStreams * 4))}–{Math.ceil(totalFrames / (currentStreams * 2))} мин
          </span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs border-t border-white/5 pt-2.5">
          <div className="flex items-center gap-2 text-[11px] text-zinc-400">
            <ShieldCheck className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
            <span>
              Каст персонажей:{" "}
              {chars.length > 0 ? (
                <strong className="text-emerald-300">
                  {chars.map((c: { code?: string; name?: string }) => c.code || "c01").join(", ")} ({chars.length})
                </strong>
              ) : (
                <span className="text-zinc-500">c01.png (из папки characters)</span>
              )}
            </span>
          </div>
          <div className="flex items-center gap-2 text-[11px] text-zinc-400">
            <Layers className="h-3.5 w-3.5 text-cyan-400 shrink-0" />
            <span>
              Реквизит / предметы:{" "}
              {items.length > 0 ? (
                <strong className="text-cyan-300">
                  {items.length} {items.length === 1 ? "предмет" : "предмета"} (predmet1)
                </strong>
              ) : (
                <span className="text-zinc-500">predmet1.png (из папки items)</span>
              )}
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}

function AnimationPromptsSettingsPanel({
  projectId,
  onOpenPrompts,
  onOpenFramePrompts,
}: {
  projectId: number;
  onOpenPrompts?: () => void;
  onOpenFramePrompts?: () => void;
}) {
  const queryClient = useQueryClient();
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
    enabled: Boolean(projectId),
  });

  const frames = useQuery({
    queryKey: ["project-frames", projectId],
    queryFn: () => api.listFrames(projectId),
    enabled: Boolean(projectId),
  });

  const frameList = frames.data || [];
  const totalFrames = frameList.length;
  const readyAnimPrompts = frameList.filter((f) => Boolean(f.animation_prompt?.trim())).length;
  const readyImgPrompts = frameList.filter((f) => Boolean(f.image_prompt?.trim())).length;

  const rawBatchSize = (project.data?.meta as Record<string, unknown> | undefined)?.anim_pr_batch_size;
  const batchSize =
    typeof rawBatchSize === "number" && rawBatchSize >= 2 && rawBatchSize <= 8
      ? rawBatchSize
      : 6;

  const patchBatchSize = useMutation({
    mutationFn: async (size: number) => {
      const meta = { ...((project.data?.meta as Record<string, unknown>) || {}), anim_pr_batch_size: size };
      await api.patchProject(projectId, { meta });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      toast.success("Размер батча анимации сохранён");
    },
    onError: (err) => {
      toast.error(errorMessageFromUnknown(err) || "Не удалось сохранить размер батча");
    },
  });

  const calculatedBatches = totalFrames > 0 ? Math.ceil(totalFrames / batchSize) : 0;
  const framesPerCalcBatch = calculatedBatches > 0 ? Math.ceil(totalFrames / calculatedBatches) : batchSize;

  const isAllReady = readyAnimPrompts === totalFrames && totalFrames > 0;
  const isPartial = readyAnimPrompts > 0 && !isAllReady;

  return (
    <section className="flex flex-col gap-4 rounded-xl border border-white/10 bg-white/[0.02] p-5">
      <div className="rounded-lg border border-white/10 bg-white/[0.02] p-3">
        <div className="text-[11px] font-medium text-zinc-400">Статус готовности видеопромптов</div>
        <div className="mt-1.5 flex items-center gap-2">
          <span
            className={cn(
              "h-2 w-2 rounded-full",
              isAllReady
                ? "bg-emerald-400"
                : isPartial
                ? "bg-amber-400"
                : "bg-zinc-500",
            )}
          />
          <span className="text-sm font-medium text-foreground">
            {isAllReady
              ? `Готово: ${readyAnimPrompts} из ${totalFrames} кадров`
              : isPartial
              ? `Частично: ${readyAnimPrompts} / ${totalFrames} кадров`
              : totalFrames > 0
              ? `Ожидает запуска (${totalFrames} кадров)`
              : "Кадры не найдены"}
          </span>
        </div>
        <div className="mt-1.5 text-[11px] text-zinc-400">
          Исходные промпты картинок: <strong className="text-zinc-200">{readyImgPrompts} из {totalFrames}</strong>
        </div>
      </div>

      {/* Настройка размера батча */}
      <div className="rounded-lg border border-white/10 bg-white/[0.02] p-3.5 flex flex-col gap-3">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-foreground">Кадров в одном батче:</span>
              <span className="rounded bg-sky-500/15 px-2 py-0.5 text-xs font-bold text-sky-300 border border-sky-500/30">
                {batchSize} {batchSize === 1 ? "кадр" : batchSize < 5 ? "кадра" : "кадров"}
              </span>
              {batchSize === 6 && (
                <span className="text-[10px] text-emerald-400 font-medium bg-emerald-500/10 px-1.5 py-0.5 rounded border border-emerald-500/20">
                  по умолчанию
                </span>
              )}
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground leading-snug">
              Кадры склеиваются в горизонтальную ленту-стрип и отправляются в GPT vision с привязкой по frame_uuid.
            </p>
          </div>

          <div className="flex items-center gap-1.5 self-start sm:self-auto">
            <span className="text-[11px] text-zinc-400 mr-1 hidden sm:inline">Выбор:</span>
            {[2, 3, 4, 5, 6, 7, 8].map((size) => (
              <Button
                key={size}
                size="sm"
                variant={batchSize === size ? "default" : "outline"}
                disabled={patchBatchSize.isPending}
                onClick={() => patchBatchSize.mutate(size)}
                className={cn(
                  "h-7 w-7 p-0 text-xs font-medium",
                  batchSize === size
                    ? "bg-sky-600 text-white hover:bg-sky-500 font-bold border-sky-500"
                    : "border-white/10 hover:bg-white/5 text-zinc-300",
                )}
              >
                {size}
              </Button>
            ))}
          </div>
        </div>

        {totalFrames > 0 && (
          <div className="flex items-center justify-between rounded-md bg-black/30 px-3 py-2 text-xs border border-white/5">
            <span className="text-zinc-400">
              Расчёт для этого проекта ({totalFrames} кадров):
            </span>
            <span className="font-medium text-sky-200">
              {calculatedBatches} {calculatedBatches === 1 ? "батч" : calculatedBatches < 5 ? "батча" : "батчей"} (по ~{framesPerCalcBatch} кадров)
            </span>
          </div>
        )}
      </div>
    </section>
  );
}

function VideosSettingsPanel({
  projectId,
  onOpenFramePrompts,
}: {
  projectId: number;
  onOpenFramePrompts?: () => void;
}) {
  const queryClient = useQueryClient();
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
    enabled: Boolean(projectId),
  });

  const frames = useQuery({
    queryKey: ["project-frames", projectId],
    queryFn: () => api.listFrames(projectId),
    enabled: Boolean(projectId),
  });

  const mediaImages = useQuery({
    queryKey: ["media-review", projectId, "images"],
    queryFn: () => api.listMediaReview(projectId, "images"),
    enabled: Boolean(projectId),
  });

  const mediaVideos = useQuery({
    queryKey: ["media-review", projectId, "videos"],
    queryFn: () => api.listMediaReview(projectId, "videos"),
    enabled: Boolean(projectId),
  });

  const frameList = frames.data || [];
  const totalFrames = frameList.length;
  const readyImagesCount = (mediaImages.data || []).filter((m) => Boolean(m.preview_url)).length;
  const readyAnimPrompts = frameList.filter((f) => Boolean(f.animation_prompt?.trim())).length;
  const generatedVideosCount = (mediaVideos.data || []).filter((v) => Boolean(v.preview_url)).length;

  const currentStreams = Number(
    (project.data?.meta as Record<string, unknown> | undefined)?.outsee_streams ??
      (project.data?.meta as Record<string, unknown> | undefined)?.img_streams ??
      2,
  );

  const [streams, setStreams] = useState<number>(currentStreams);

  useEffect(() => {
    const meta = project.data?.meta as Record<string, unknown> | undefined;
    setStreams(Number(meta?.outsee_streams ?? meta?.img_streams ?? 2));
  }, [project.data?.meta]);

  const patchStreams = useMutation({
    mutationFn: async (val: number) => {
      const meta = {
        ...((project.data?.meta as Record<string, unknown>) || {}),
        img_streams: val,
        outsee_streams: val,
      };
      await api.patchProject(projectId, { meta });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      toast.success("Параллельные потоки сохранены");
    },
    onError: (err) => {
      toast.error(errorMessageFromUnknown(err) || "Не удалось сохранить потоки");
    },
  });

  const handleSelectStreams = (val: number) => {
    setStreams(val);
    patchStreams.mutate(val);
  };

  const isReadyForVideo = totalFrames > 0 && readyAnimPrompts === totalFrames && readyImagesCount === totalFrames;

  return (
    <section className="flex flex-col gap-4 rounded-xl border border-white/10 bg-white/[0.02] p-5">
      {/* Шапка */}
      <div className="flex items-center gap-2.5">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
          <Film className="h-5 w-5" />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-foreground">Параметры генерации видео</h3>
          <p className="text-xs text-muted-foreground">
            Параллельные потоки генерации и статус готовности входных данных
          </p>
        </div>
      </div>

      {/* Индикатор готовности входных данных */}
      <div className="flex flex-col gap-3 rounded-lg border border-white/10 bg-white/[0.02] p-4">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-foreground">Готовность кадров к генерации:</span>
          {isReadyForVideo ? (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-[11px] font-medium text-emerald-400 border border-emerald-500/30">
              <CheckCircle2 className="h-3 w-3" /> Все {totalFrames} кадров готовы
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/15 px-2.5 py-0.5 text-[11px] font-medium text-amber-400 border border-amber-500/30">
              <Clock className="h-3 w-3" /> В процессе подготовки ({readyAnimPrompts}/{totalFrames} промптов)
            </span>
          )}
        </div>

        {/* Метрики готовности */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 pt-1">
          <div className="rounded-md border border-white/5 bg-white/[0.03] p-3 flex flex-col gap-1">
            <span className="text-[11px] text-muted-foreground">1. Исходные картинки</span>
            <div className="flex items-baseline gap-1.5">
              <span className="text-lg font-bold text-foreground">{readyImagesCount}</span>
              <span className="text-xs text-muted-foreground">/ {totalFrames}</span>
            </div>
            <span className="text-[10px] text-muted-foreground/80">
              {readyImagesCount === totalFrames ? "✓ Все картинки готовы" : "Ожидают генерации"}
            </span>
          </div>

          <div className="rounded-md border border-white/5 bg-white/[0.03] p-3 flex flex-col gap-1">
            <span className="text-[11px] text-muted-foreground">2. Промты анимации</span>
            <div className="flex items-baseline gap-1.5">
              <span className="text-lg font-bold text-foreground">{readyAnimPrompts}</span>
              <span className="text-xs text-muted-foreground">/ {totalFrames}</span>
            </div>
            <span className="text-[10px] text-muted-foreground/80">
              {readyAnimPrompts === totalFrames ? "✓ Все промпты созданы" : "Ожидают генерации"}
            </span>
          </div>

          <div className="rounded-md border border-white/5 bg-white/[0.03] p-3 flex flex-col gap-1">
            <span className="text-[11px] text-muted-foreground">3. Сгенерировано видео</span>
            <div className="flex items-baseline gap-1.5">
              <span className="text-lg font-bold text-foreground">{generatedVideosCount}</span>
              <span className="text-xs text-muted-foreground">/ {totalFrames}</span>
            </div>
            <span className="text-[10px] text-muted-foreground/80">
              {generatedVideosCount > 0 ? `${generatedVideosCount} клипов готово` : "Клипы ещё не созданы"}
            </span>
          </div>
        </div>

        {onOpenFramePrompts && (
          <div className="pt-1">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="text-xs h-7 text-muted-foreground hover:text-foreground"
              onClick={onOpenFramePrompts}
            >
              Просмотреть промпты анимации по кадрам →
            </Button>
          </div>
        )}
      </div>

      {/* Параллельные потоки */}
      <div className="flex flex-col gap-3 rounded-lg border border-white/10 bg-white/[0.02] p-4">
        <div>
          <h4 className="text-xs font-semibold text-foreground">
            Параллельные потоки генерации
          </h4>
          <p className="mt-1 text-xs text-muted-foreground">
            Количество одновременных задач в API для генерации видеоклипов
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 pt-1">
          {[1, 2, 3, 4].map((num) => (
            <Button
              key={num}
              type="button"
              size="sm"
              variant={streams === num ? "default" : "outline"}
              disabled={patchStreams.isPending}
              className={cn(
                "h-8 px-3.5 text-xs",
                streams === num
                  ? "bg-primary text-black font-semibold shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
              onClick={() => handleSelectStreams(num)}
            >
              {num} {num === 1 ? "поток" : num < 5 ? "потока" : "потоков"}
              {num === 2 && <span className="ml-1 opacity-60 text-[10px]">(дефолт)</span>}
              {num === 4 && <span className="ml-1 opacity-60 text-[10px]">(макс)</span>}
            </Button>
          ))}
        </div>

        <p className="text-[11px] text-muted-foreground">
          💡 При выборе 3 или 4 потоков генерация клипов завершается быстрее, если ваш провайдер и баланс поддерживают параллельные запросы.
        </p>
      </div>
    </section>
  );
}

function AudioSettingsPanel({
  projectId,
  onOpenVoiceover,
}: {
  projectId: number;
  onOpenVoiceover?: () => void;
}) {
  const queryClient = useQueryClient();
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
    enabled: Boolean(projectId),
  });

  const frames = useQuery({
    queryKey: ["project-frames", projectId],
    queryFn: () => api.listFrames(projectId),
    enabled: Boolean(projectId),
  });

  const artifacts = useQuery({
    queryKey: ["artifacts", projectId, "audio"],
    queryFn: () => api.listArtifacts({ project_id: projectId }),
    enabled: Boolean(projectId),
  });

  const voicesStatus = useQuery({
    queryKey: ["elevenlabs-voices-status"],
    queryFn: () => api.elevenLabsVoicesStatus(),
    staleTime: 3600_000,
    retry: false,
  });

  const frameList = frames.data || [];
  const totalFrames = frameList.length;
  const readyVoCount = frameList.filter((f) => Boolean(f.voiceover_text?.trim())).length;
  const totalVoChars = frameList.reduce((acc, f) => acc + (f.voiceover_text?.trim().length || 0), 0);
  const estSeconds = totalVoChars > 0 ? Math.round(totalVoChars / 15) : 0;
  const estMinutes = (estSeconds / 60).toFixed(1);
  const estCostUsd = (totalVoChars * 0.0001).toFixed(2);

  const audioArtifact = (artifacts.data || []).find(
    (a) => a.kind === "audio" && a.path?.match(/\.(mp3|wav|m4a|ogg)$/i),
  );
  const whisperArtifact = (artifacts.data || []).find(
    (a) => a.kind === "whisper_words" || a.path?.includes("words_"),
  );

  const metaRecord = useMemo(
    () => ((project.data?.meta || {}) as Record<string, unknown>),
    [project.data?.meta],
  );
  const stepParams = useMemo(() => readNodeStepParams(metaRecord), [metaRecord]);

  const savedVoiceId =
    stepParams.audio?.elevenlabs_voice_id ?? DEFAULT_ELEVENLABS_VOICE_ID;
  const [voiceDraft, setVoiceDraft] = useState(savedVoiceId);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [isPlayingSample, setIsPlayingSample] = useState(false);
  const sampleAudioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    setVoiceDraft(savedVoiceId);
  }, [savedVoiceId]);

  useEffect(() => {
    return () => {
      if (sampleAudioRef.current) {
        sampleAudioRef.current.pause();
        sampleAudioRef.current = null;
      }
    };
  }, []);

  const selectedStandard = findElevenLabsVoice(voiceDraft);
  const ruVoice = getVoiceById(voiceDraft);

  const deadIds = new Set(
    voicesStatus.data?.checked ? (voicesStatus.data?.dead ?? []) : [],
  );

  const patchVoice = useMutation({
    mutationFn: async (voiceId: string) => {
      const nextMeta = withNodeStepParams(metaRecord, "audio", {
        elevenlabs_voice_id: voiceId,
      });
      await api.patchProject(projectId, { meta: nextMeta });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (err) => {
      toast.error(errorMessageFromUnknown(err) || "Не удалось сохранить голос");
    },
  });

  const handleSelectVoice = (voiceId: string, voiceName: string) => {
    setVoiceDraft(voiceId);
    patchVoice.mutate(voiceId);
    toast.success(`Выбран голос: ${voiceName}`);
  };

  const togglePlaySample = () => {
    if (isPlayingSample) {
      if (sampleAudioRef.current) {
        sampleAudioRef.current.pause();
        sampleAudioRef.current = null;
      }
      setIsPlayingSample(false);
      return;
    }
    const sampleUrl = ruVoice?.sample_url || (ruVoice ? `/api/voices/${ruVoice.id}/sample` : null);
    if (!sampleUrl) return;
    const a = new Audio(sampleUrl);
    sampleAudioRef.current = a;
    setIsPlayingSample(true);
    a.play().catch(() => setIsPlayingSample(false));
    a.onended = () => {
      setIsPlayingSample(false);
      sampleAudioRef.current = null;
    };
    a.onerror = () => {
      setIsPlayingSample(false);
      sampleAudioRef.current = null;
    };
  };

  const isReadyForAudio = totalFrames > 0 && readyVoCount === totalFrames;
  const isAudioGenerated = Boolean(audioArtifact);

  return (
    <section className="flex flex-col gap-4 rounded-xl border border-white/10 bg-white/[0.02] p-5">
      {/* Шапка */}
      <div className="flex items-center gap-2.5">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-pink-500/15 text-pink-400 border border-pink-500/30">
          <Mic className="h-5 w-5" />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-foreground">Параметры озвучки (ElevenLabs v4)</h3>
          <p className="text-xs text-muted-foreground">
            Выбор голоса диктора, готовность закадрового текста и синтезированное аудио
          </p>
        </div>
      </div>

      {/* Индикатор готовности входных данных */}
      <div className="flex flex-col gap-3 rounded-lg border border-white/10 bg-white/[0.02] p-4">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-foreground">Готовность к озвучке:</span>
          {isAudioGenerated ? (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-[11px] font-medium text-emerald-400 border border-emerald-500/30">
              <CheckCircle2 className="h-3 w-3" /> Озвучка готова
            </span>
          ) : isReadyForAudio ? (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-cyan-500/15 px-2.5 py-0.5 text-[11px] font-medium text-cyan-400 border border-cyan-500/30">
              <CheckCircle2 className="h-3 w-3" /> Закадр готов ({totalFrames} кадров)
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/15 px-2.5 py-0.5 text-[11px] font-medium text-amber-400 border border-amber-500/30">
              <Clock className="h-3 w-3" /> Не все кадры содержат закадр ({readyVoCount}/{totalFrames})
            </span>
          )}
        </div>

        {/* Метрики готовности */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 pt-1">
          <div className="rounded-md border border-white/5 bg-white/[0.03] p-3 flex flex-col gap-1">
            <span className="text-[11px] text-muted-foreground">1. Закадровый текст</span>
            <div className="flex items-baseline gap-1.5">
              <span className="text-lg font-bold text-foreground">{readyVoCount}</span>
              <span className="text-xs text-muted-foreground">/ {totalFrames}</span>
            </div>
            <span className="text-[10px] text-muted-foreground/80">
              {totalVoChars > 0
                ? `${totalVoChars.toLocaleString()} симв. (~${estMinutes} мин) · ~$${estCostUsd}`
                : "Закадр пуст"}
            </span>
          </div>

          <div className="rounded-md border border-white/5 bg-white/[0.03] p-3 flex flex-col gap-1">
            <span className="text-[11px] text-muted-foreground">2. Выбранный голос</span>
            <div className="flex items-baseline gap-1.5 truncate">
              <span className="text-sm font-bold text-foreground truncate">
                {ruVoice ? ruVoice.name : selectedStandard ? selectedStandard.name : "По умолчанию"}
              </span>
            </div>
            <span className="text-[10px] text-muted-foreground/80 truncate">
              {ruVoice
                ? `${ruVoice.gender === "female" ? "Женский" : "Мужской"} · ElevenLabs v4`
                : "Стандартный ElevenLabs"}
            </span>
          </div>

          <div className="rounded-md border border-white/5 bg-white/[0.03] p-3 flex flex-col gap-1">
            <span className="text-[11px] text-muted-foreground">3. Синтез и субтитры</span>
            <div className="flex items-baseline gap-1.5">
              <span className="text-sm font-bold text-foreground">
                {isAudioGenerated ? "Синтезировано" : "Не сгенерировано"}
              </span>
            </div>
            <span className="text-[10px] text-muted-foreground/80">
              {whisperArtifact ? "✓ Whisper субтитры готовы" : isAudioGenerated ? "ASR завершён" : "Ожидает запуска шага"}
            </span>
          </div>
        </div>

        {onOpenVoiceover && (
          <div className="pt-1">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="text-xs h-7 text-muted-foreground hover:text-foreground"
              onClick={onOpenVoiceover}
            >
              Просмотреть закадр по кадрам →
            </Button>
          </div>
        )}
      </div>

      {/* Карточка выбранного голоса */}
      <div className="flex flex-col gap-3 rounded-lg border border-white/10 bg-white/[0.02] p-4">
        <div>
          <div className="flex items-center gap-2">
            <h4 className="text-xs font-semibold text-foreground">Голос диктора (WaveSpeed ElevenLabs v4)</h4>
            <span className="rounded bg-cyan-500/10 border border-cyan-500/30 px-1.5 py-0.5 text-[10px] font-medium text-cyan-400">
              200 русских голосов
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Премиальные голоса с естественной интонацией, расстановкой пауз и пословной разметкой Whisper ASR
          </p>
        </div>

        {voicesStatus.data?.checked && deadIds.size > 0 && deadIds.has(voiceDraft) ? (
          <p className="text-xs font-medium text-amber-400">
            Выбранный голос недоступен в API — выбери другой голос из библиотеки.
          </p>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-3">
          <div className="flex items-center gap-3 min-w-0">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-pink-500/10 border border-pink-500/25 text-pink-400">
              <Mic className="h-5 w-5" />
            </div>
            <div className="min-w-0 flex flex-col">
              <div className="flex items-center gap-2">
                <span className="truncate text-sm font-semibold text-foreground">
                  {ruVoice ? ruVoice.name : selectedStandard ? selectedStandard.name : `Голос ${voiceDraft}`}
                </span>
                {ruVoice ? (
                  <span className="shrink-0 rounded bg-cyan-500/15 border border-cyan-500/30 px-1.5 py-0.5 text-[10px] text-cyan-300">
                    {ruVoice.gender === "female" ? "Женский" : "Мужской"} #{ruVoice.index}
                  </span>
                ) : null}
              </div>
              <span className="truncate text-xs text-muted-foreground">
                {ruVoice ? ruVoice.description : selectedStandard ? selectedStandard.description : "Пользовательский голос"}
              </span>
              <span className="font-mono text-[10px] text-muted-foreground/60">
                ID: {voiceDraft}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {ruVoice ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={togglePlaySample}
                className="h-8 gap-1.5 px-2.5 text-xs border-cyan-500/30 text-cyan-300 hover:bg-cyan-500/10"
                title="Прослушать образец голоса"
              >
                {isPlayingSample ? <Pause className="h-3.5 w-3.5 fill-current" /> : <Play className="h-3.5 w-3.5 fill-current" />}
                {isPlayingSample ? "Пауза" : "Слушать"}
              </Button>
            ) : null}

            <Button
              type="button"
              size="sm"
              onClick={() => setLibraryOpen(true)}
              className="h-8 gap-1.5 px-3 text-xs font-medium bg-cyan-600 hover:bg-cyan-500 text-white rounded-lg border border-cyan-400/30 shadow-sm shadow-cyan-500/20"
            >
              <Sparkles className="h-3.5 w-3.5" />
              Библиотека 200 голосов
            </Button>
          </div>
        </div>

        {patchVoice.isPending ? (
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-cyan-400" />
            Сохранение голоса…
          </span>
        ) : null}
      </div>

      {/* Встроенный аудиоплеер полной озвучки (если уже сгенерирована) */}
      {audioArtifact ? (
        <div className="flex flex-col gap-2.5 rounded-lg border border-emerald-500/20 bg-emerald-500/[0.03] p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Volume2 className="h-4 w-4 text-emerald-400" />
              <span className="text-xs font-semibold text-foreground">Сгенерированная озвучка (voice_full)</span>
            </div>
            <a
              href={api.artifactFileUrl(audioArtifact.uuid)}
              download
              className="text-[11px] text-emerald-400 hover:underline inline-flex items-center gap-1"
            >
              <Download className="h-3 w-3" /> Скачать MP3
            </a>
          </div>
          <audio
            controls
            className="w-full mt-1"
            src={api.artifactFileUrl(audioArtifact.uuid)}
          />
        </div>
      ) : null}

      <VoiceLibraryModal
        open={libraryOpen}
        onOpenChange={setLibraryOpen}
        selectedVoiceId={voiceDraft}
        onSelectVoice={handleSelectVoice}
      />
    </section>
  );
}
