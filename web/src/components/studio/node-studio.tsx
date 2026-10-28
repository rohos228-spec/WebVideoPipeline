"use client";
import { getNodeCustomBg } from "@/lib/node-custom-bg";

import type { SyntheticEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  FileSpreadsheet,
  FileText,
  Loader2,
  MessageSquareText,
  Play,
  RefreshCw,
  Settings2,
  Upload,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import { Copy as CopyIcon, Play as PlayIcon, X as XIcon, Loader2 as SpinIcon, Maximize2 as OpenIcon } from "lucide-react";
import { NodeModelPicker } from "@/components/canvas/node-model-picker";

import { isTextNodeType } from "@/lib/node-model-catalog";
import { getNodeSpec } from "@/lib/node-catalog";
import { getNodeIcon } from "@/lib/node-icons";
import { nodeTypeFromKey } from "@/lib/node-key";
import { stepCodeForNodeType, stepHasPromptVariants } from "@/lib/node-step-map";
import {
  defaultPromptSlots,
  gptTextSlotForNode,
  nodeTypeRequiresExcel,
  pipelinePromptSlots,
  resolvePromptSlots,
  resolvePromptSlotsForNode,
  sceneAgentFromNodeKey,
  type NodePromptSlot,
} from "@/lib/node-prompts";
import { nodeSupportsGptText } from "@/lib/gpt-text-steps";
import {
  excelGptAttachmentChipTitle,
  excelGptSlotIndex,
  isExcelGptNode,
  isShotsReportNode,
  EXCEL_GPT_STEP_CODE,
  workModeLabel,
  type ExcelGptNodeConfig,
} from "@/lib/excel-gpt-config";
import { ExcelGptSettingsPanel } from "@/components/studio/excel-gpt-settings-panel";
import { ItemsConfigPanel } from "@/components/canvas/items-config-panel";
import { HeroConfigPanel } from "@/components/canvas/hero-config-panel";
import { Nii67CardPanel } from "@/components/canvas/nii67-card-panel";
import { projectUsesNii67Script } from "@/lib/nii67";
/** Steps that never call a text LLM (pure FFmpeg / TTS / SFX / upload) or have no model (topic). */
const NO_LLM_NODE_TYPES = new Set(["topic", "assemble", "audio", "publish", "sfx_gen"]);
import { CheckNodePromptPanel } from "@/components/studio/check-node-prompt-panel";
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
import { NodeStepParamsPanel, StreamsPicker } from "@/components/studio/node-step-params-panel";
import { NodePromptBlock } from "@/components/studio/node-prompt-block";
import { NodeStyleVars } from "@/components/studio/node-style-vars";
const STREAM_NODE_TYPES = new Set(["images", "videos", "hero"]);
import { nodeBgFor, promptBgFor } from "@/components/prompts/prompt-card-catalog";
import { PromptFilesPanel } from "@/components/studio/prompt-files-panel";
import { GptTextPanel } from "@/components/studio/gpt-text-panel";
import { shouldShowStopBar } from "@/lib/project-running";

type StudioTab = "settings" | "prompts" | "results" | "excel";

function slotStepCode(slot: NodePromptSlot | null, nodeStepCode: string | undefined): string | undefined {
  return slot?.stepCode ?? nodeStepCode;
}

export function NodeStudio({
  open,
  onOpenChange,
  projectId,
  nodeKey,
  initialTab = "settings",
  nodeData: nodeDataProp,
  promptFocus,
  nodeDisabled = false,
  onEnableNode,
  promptSlots: promptSlotsProp,
  inlineTargetId: _inlineTargetId = null,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  projectId: number | null;
  nodeKey: string | null;
  initialTab?: StudioTab;
  nodeData?: Record<string, any> | null;
  promptFocus?: NodePromptSlot | null;
  nodeDisabled?: boolean;
  onEnableNode?: () => void;
  promptSlots?: NodePromptSlot[];
  inlineTargetId?: string | null;
}) {
  const nodeType = nodeTypeFromKey(nodeKey);
  const spec = getNodeSpec(nodeType);
  const stepCode = stepCodeForNodeType(nodeType);
  const NodeIcon = getNodeIcon(spec.iconKey);

  const [tab, setTab] = useState<StudioTab>(initialTab);
  const [catalogMode, setCatalogMode] = useState(true);
  useEffect(() => {
    if (open) setCatalogMode(initialTab !== "settings");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, nodeKey]);
  const [activeSlotId, setActiveSlotId] = useState<string | null>(null);
  const [xlsxSheet, setXlsxSheet] = useState<string>("");
  /** false = весь лист (дефолт); true = узкий ключевой фрагмент по типу ноды */
  const [xlsxFocusKeyRows, setXlsxFocusKeyRows] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const backdropMouseDownRef = useRef<EventTarget | null>(null);

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

  const isReportNode = isShotsReportNode(nodeKey);
  const showExcel =
    !isReportNode &&
    (nodeTypeRequiresExcel(nodeType) ||
      allSlots.some((s) => s.kind === "excel") ||
      tab === "excel" ||
      isExcelGptNode(nodeType));

  const xlsxSheetsMeta = useQuery({
    queryKey: ["xlsx-sheets", projectId, nodeKey ?? "live"],
    queryFn: () =>
      api.previewProjectXlsx(projectId!, {
        maxRows: 1,
        nodeKey: nodeKey ?? undefined,
      }),
    enabled: open && projectId != null && showExcel,
  });

  const focusAvailable = useMemo(() => xlsxPreviewFocusForNode(nodeType) != null, [nodeType]);
  const xlsxParams = useMemo(
    () => xlsxStudioPreviewParams(nodeType, { focusKeyRows: xlsxFocusKeyRows && focusAvailable }),
    [nodeType, xlsxFocusKeyRows, focusAvailable],
  );

  const xlsxPreview = useQuery({
    queryKey: [
      "xlsx-preview",
      projectId,
      nodeKey ?? "live",
      xlsxSheet,
      xlsxParams.startRow,
      xlsxParams.maxRows,
      xlsxFocusKeyRows,
    ],
    queryFn: () =>
      api.previewProjectXlsx(projectId!, {
        sheet: xlsxSheet || undefined,
        raw: true,
        maxRows: xlsxParams.maxRows,
        maxCols: xlsxParams.maxCols,
        startRow: xlsxParams.startRow,
        nodeKey: nodeKey ?? undefined,
      }),
    enabled:
      open &&
      projectId != null &&
      tab === "excel" &&
      Boolean(xlsxSheet || xlsxSheetsMeta.data?.sheets?.length),
  });

  const pipelineSlots = useMemo(() => pipelinePromptSlots(allSlots), [allSlots]);
  const hasPrompts = pipelineSlots.length > 0;

  useEffect(() => {
    if (!open) return;
    if (isShotsReportNode(nodeKey)) {
      setTab(initialTab === "settings" ? "settings" : "results");
      return;
    }
    if (!hasPrompts) {
      setTab(initialTab === "results" ? "results" : "settings");
      return;
    }
    if (promptFocus && initialTab !== "settings") {
      setTab(promptFocus.kind === "excel" ? "excel" : "prompts");
      return;
    }
    setTab(initialTab ?? "prompts");
  }, [open, initialTab, promptFocus, nodeKey, hasPrompts]);

  useEffect(() => {
    if (!open) return;
    if (promptFocus?.id) {
      setActiveSlotId(promptFocus.id);
      return;
    }
    const firstPrompt = pipelineSlots.find((s) => s.kind !== "excel");
    if (firstPrompt) {
      setActiveSlotId(firstPrompt.id);
      return;
    }
    const excel = pipelineSlots.find((s) => s.kind === "excel");
    if (excel) {
      setActiveSlotId(excel.id);
    } else {
      setActiveSlotId(pipelineSlots[0]?.id ?? null);
    }
  }, [promptFocus, pipelineSlots, open, nodeKey]);

  useEffect(() => {
    if (!open || tab !== "excel") return;
    const sheets = xlsxSheetsMeta.data?.sheets ?? [];
    if (!sheets.length) return;
    setXlsxSheet((prev) => {
      if (prev && sheets.includes(prev)) return prev;
      return pickDefaultSheetForNode(nodeType, sheets);
    });
  }, [open, tab, nodeType, xlsxSheetsMeta.data?.sheets]);

  useEffect(() => {
    if (!open) {
      setXlsxSheet("");
      setXlsxFocusKeyRows(false);
    }
  }, [open, nodeKey]);

  const activeSlot =
    (activeSlotId === "gpt_text" ? gptTextSlotForNode(nodeType) : null) ??
    allSlots.find((s) => s.id === activeSlotId) ??
    (promptFocus?.kind === "text" ? promptFocus : null) ??
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
    mutationFn: (variables?: { mode?: "full" | "resume" }) =>
      api.runProjectStep(projectId!, stepCode!, {
        nodeKey: nodeKey ?? undefined,
        mode: variables?.mode ?? "resume",
      }),
    onSuccess: (_, vars) => {
      const isFull = vars?.mode === "full";
      toast.success(
        isFull
          ? `Шаг «${spec.label}» запущен начисто`
          : `Шаг «${spec.label}» запущен`,
      );
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["project-run", projectId] });
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
          ? `Файл подменён: ${name} (вход только этот файл)`
          : "Таблица загружена",
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
    if (nodeType === "audio" || nodeType === "music") {
      return list.filter((a) => a.kind.includes("audio") || a.kind.includes("music"));
    }
    if (nodeType === "assemble" || nodeType === "hitl_final") {
      return list.filter((a) => a.kind.includes("final"));
    }
    return [];
  }, [artifacts.data, nodeType]);

  // Inline "Результат ноды": node's OWN result only. Plan/Сценарий output lives in the
  // project.xlsx "Общий план" sheet even when project.general_plan is empty.
  const ownFileNode =
    nodeType.includes("image") ||
    nodeType.includes("video") ||
    nodeType === "hero" ||
    nodeType === "items";
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

  const isRunning = shouldShowStopBar(project.data?.status);

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

  const resultSnapshot = useMemo(() => {
    if (!projectId) return null;
    return resolveNodeResult(
      nodeType,
      {
        project: project.data,
        artifacts: artifacts.data ?? [],
        assets: assets.data ?? [],
        frames: (dbBrowser.data?.frames as unknown as FrameDTO[]) ?? [],
        mediaImages: mapMedia(mediaImages.data ?? [], "images"),
        mediaVideos: mapMedia(mediaVideos.data ?? [], "videos"),
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
    dbBrowser.data?.frames,
    mediaImages.data,
    mediaVideos.data,
    mapMedia,
    nodeKey,
  ]);
  const projectHasXlsxAsset = (assets.data ?? []).some((a) =>
    /\.xlsx$/i.test(String((a as { name?: string; path?: string }).name ?? (a as { path?: string }).path ?? "")),
  );
  const inlineResult = useMemo(() => {
    if (!resultSnapshot) return null;
    if (!resultSnapshot.hasResult && resultSnapshot.viewMode === "xlsx_general_plan" && projectHasXlsxAsset) {
      return {
        ...resultSnapshot,
        hasResult: true,
        itemCount: 1,
        summary: "Сценарий проекта",
        items: [{ id: "xlsx_general", label: "Сценарий", kind: "xlsx" as const }],
      };
    }
    return resultSnapshot;
  }, [resultSnapshot, projectHasXlsxAsset]);

  const showStepParams =
    projectId != null &&
    (nodeType === "plan" ||
      nodeType === "script" ||
      nodeType === "split" ||
      nodeType === "audio" ||
      nodeType === "assemble" ||
      nodeType === "images" ||
      nodeType === "videos");

  const showGptTextPanel = activeSlot?.kind === "text" && activeStepCode && projectId;
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

  const canCatalog = Boolean(showFilesPanel && promptStepCode && !isCheckNode && !showGptTextPanel && !showFramePromptsPanel);
  const catalogActive = open && catalogMode && canCatalog && tab === "prompts";
  useEffect(() => {
    if (open && catalogMode && canCatalog && tab !== "prompts" && initialTab !== "settings") setTab("prompts");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, catalogMode, canCatalog]);
  const [catalogDetail, setCatalogDetail] = useState<{ nodeKey: string; item: { id: string; label: string } | null } | null>(null);
  useEffect(() => {
    const on = (ev: Event) => setCatalogDetail((ev as CustomEvent<{ nodeKey: string; item: { id: string; label: string } | null }>).detail);
    window.addEventListener("vp-catalog-detail", on);
    return () => window.removeEventListener("vp-catalog-detail", on);
  }, []);
  const wasOpenRef = useRef(false);
  useEffect(() => {
    if (wasOpenRef.current && !open) window.dispatchEvent(new CustomEvent("node-studio-closed"));
    wasOpenRef.current = open;
  }, [open]);

  const requestClose = useCallback(() => {
    if (Date.now() < backdropGuardUntil.current) return;
    onOpenChange(false);
  }, [onOpenChange]);

  useEffect(() => {
    const onClose = () => requestClose();
    window.addEventListener("node-studio-close", onClose);
    return () => window.removeEventListener("node-studio-close", onClose);
  }, [requestClose]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") requestClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, requestClose]);

  const metaNodeData = useMemo(() => {
    if (!nodeKey) return null;
    const meta = (project.data?.meta || {}) as {
      canvas_graph?: { nodes?: Array<{ id: string; data?: Record<string, any> }> };
      workflow?: { nodes?: Array<{ id: string; data?: Record<string, any> }> };
    };
    const cgNode = meta.canvas_graph?.nodes?.find((n) => n.id === nodeKey)?.data;
    if (cgNode) return cgNode;
    return meta.workflow?.nodes?.find((n) => n.id === nodeKey)?.data ?? null;
  }, [project.data?.meta, nodeKey]);

  const [liveNodeData, setLiveNodeData] = useState<Record<string, any> | null>(() => {
    if (nodeDataProp && (!nodeDataProp.nodeKey || nodeDataProp.nodeKey === nodeKey)) {
      return { ...(metaNodeData || {}), ...nodeDataProp };
    }
    return metaNodeData ?? null;
  });

  const lastNodeKeyRef = useRef(nodeKey);
  useEffect(() => {
    if (!open || !nodeKey) return;
    if (lastNodeKeyRef.current !== nodeKey) {
      lastNodeKeyRef.current = nodeKey;
      const fromProp = nodeDataProp && (!nodeDataProp.nodeKey || nodeDataProp.nodeKey === nodeKey) ? nodeDataProp : null;
      setLiveNodeData({
        ...(metaNodeData || {}),
        ...(fromProp || {}),
      });
      return;
    }
    if (nodeDataProp && (!nodeDataProp.nodeKey || nodeDataProp.nodeKey === nodeKey)) {
      setLiveNodeData((prev) => ({
        ...(metaNodeData || {}),
        ...(prev || {}),
        ...nodeDataProp,
      }));
    } else if (metaNodeData) {
      setLiveNodeData((prev) => ({
        ...metaNodeData,
        ...(prev || {}),
      }));
    }
  }, [open, nodeKey, nodeDataProp, metaNodeData]);

  useEffect(() => {
    if (!open || !nodeKey) return;
    const onPatch = (ev: Event) => {
      const detail = (ev as CustomEvent<{ nodeKey: string; patch: Record<string, any> }>).detail;
      if (!detail || detail.nodeKey !== nodeKey) return;
      setLiveNodeData((prev) => ({
        ...(prev || {}),
        ...detail.patch,
        ...(detail.patch.modelId != null ? { modelPinned: true } : {}),
      }));
    };
    const onGlobalModel = (ev: Event) => {
      const detail = (ev as CustomEvent<{ modelId: string }>).detail;
      if (!detail?.modelId) return;
      if (!isTextNodeType(nodeType)) return;
      setLiveNodeData((prev) => {
        if (prev?.modelPinned === true) return prev;
        return {
          ...(prev || {}),
          modelId: detail.modelId,
        };
      });
    };
    window.addEventListener("canvas-patch-node-data", onPatch);
    window.addEventListener("canvas-patch-global-model", onGlobalModel);
    return () => {
      window.removeEventListener("canvas-patch-node-data", onPatch);
      window.removeEventListener("canvas-patch-global-model", onGlobalModel);
    };
  }, [open, nodeKey, nodeType]);

  if (!nodeKey || !mounted || !open) return null;

  const hasSettings =
    (isExcelGptNode(nodeType) && !!projectId) ||
    !!showStepParams ||
    ((nodeType === "items" || nodeType === "hero" || nodeType === "script") && !!projectId);
  const inTab0 = hasPrompts && tab === "prompts" ? "prompts" : tab === "results" ? "results" : "settings";
  const activeName = String(activeVariant || "default");
  const detailOpen = inTab0 === "prompts" && !!catalogDetail && catalogDetail.nodeKey === nodeKey && !!catalogDetail.item;
  const headPrompt =
    inTab0 === "prompts" && catalogDetail && catalogDetail.nodeKey === nodeKey && catalogDetail.item
      ? catalogDetail.item
      : { id: activeName, label: activeName };
  const inTab: "settings" | "prompts" | "results" =
    hasPrompts && tab === "prompts" ? "prompts" : tab === "results" ? "results" : "settings";

  const tabsList = hasPrompts
    ? ([
        ["prompts", "Каталог промтов"],
        ["settings", "Настройки"],
        ["results", "Результат"],
      ] as const)
    : ([
        ["settings", "Настройки"],
        ["results", "Результат"],
      ] as const);

  return createPortal(
    <div
      className="fixed inset-0 z-[120] flex items-center justify-center p-4 bg-black/65 backdrop-blur-sm"
      onMouseDown={(e) => {
        backdropMouseDownRef.current = e.target;
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && backdropMouseDownRef.current === e.currentTarget) {
          requestClose();
        }
      }}
    >
      <aside
        className="vp-menu nowheel nopan relative isolate flex max-h-[88vh] h-[720px] w-[min(880px,96vw)] cursor-auto flex-col overflow-hidden rounded-[14px] border border-white/[0.12] bg-[#0c0c0e] text-left text-white shadow-2xl backdrop-blur-xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div
          data-vp-menu-header
          className="vp-head-img relative h-[60px] shrink-0 overflow-hidden bg-cover"
          style={{ backgroundImage: `url(${getNodeCustomBg(nodeKey) || nodeBgFor(nodeKey, hasPrompts ? promptBgFor(headPrompt.id, headPrompt.label, nodeKey) : null)})`, backgroundPosition: "center top" }}
        >
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/60 via-black/15 to-black/30" />
            <div className={cn("absolute inset-0 flex items-center px-4 text-[16px] font-medium leading-[1.2] text-white [text-shadow:0_1px_6px_rgba(0,0,0,0.6)]", detailOpen ? "pr-56" : "pr-14")}>
              <span className="truncate">
                {hasPrompts
                  ? (excelConfig.label?.trim() || spec.label) + ": " + headPrompt.label.replace(/\.(md|txt)$/i, "")
                  : (excelConfig.label?.trim() || spec.label)}
              </span>
            </div>
            {detailOpen ? (
              <button
                type="button"
                data-vp-catalog-back
                onClick={() => window.dispatchEvent(new CustomEvent("vp-catalog-back", { detail: { nodeKey } }))}
                className="nodrag absolute right-12 top-1/2 z-[1] -translate-y-1/2 px-1 text-[13px] text-white/80 [text-shadow:0_1px_4px_rgba(0,0,0,0.8)] hover:text-white"
              >
                ← Назад к каталогу
              </button>
            ) : null}
            <button
              type="button"
              title="Закрыть"
              data-vp-menu-close
              onClick={() => requestClose()}
              className="nodrag absolute right-2 top-1/2 z-[1] flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md bg-black/45 text-white/85 backdrop-blur-sm hover:bg-black/70 hover:text-white"
            >
              <XIcon className="h-4 w-4" />
            </button>
          </div>
        <div className="flex shrink-0 cursor-grab items-stretch gap-2 p-3 pb-0 active:cursor-grabbing" title="Перетащите за верхнюю панель">
          {detailOpen ? <div id={`vp-detail-actions-${nodeKey}`} className="flex flex-1" /> : (
          <div className={cn("grid flex-1 border border-white/10", tabsList.length === 2 ? "grid-cols-2" : "grid-cols-3")}>
            {tabsList.map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => setTab(id)}
                className={cn(
                  "flex h-12 items-center justify-center rounded-none px-3 py-3 text-[13px] font-medium uppercase leading-[1.25] tracking-[0.08em] transition-colors",
                  inTab === id ? "bg-white text-black" : "text-white/60 hover:bg-white/[0.05] hover:text-white",
                )}
              >
                {label}
              </button>
            ))}
          </div>
          )}
        </div>
        {inTab === "settings" ? (
          <div data-vp-settings className="nodrag flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3">
            {nodeDisabled ? (
              <div className="flex flex-col gap-2 rounded-[10px] border border-amber-400/30 px-4 py-3">
                <p className="text-[13px] text-amber-300">Нода отключена в графе — шаг не запустится.</p>
                {onEnableNode ? (
                  <button
                    type="button"
                    className="h-9 w-fit rounded-md border border-amber-400/40 px-3 text-[13px] text-amber-200"
                    onClick={onEnableNode}
                  >
                    Включить ноду
                  </button>
                ) : null}
              </div>
            ) : null}
            {!NO_LLM_NODE_TYPES.has(nodeType) ? (
            <div className="flex items-center gap-3 rounded-[10px] border border-white/[0.06] px-4 py-3">
              <span className="text-[11px] uppercase tracking-[0.12em] text-white/50">Модель</span>
              <div className="min-w-0 font-mono text-[12px] [&_*]:!font-mono [&_*]:!text-[12px]">
                  <NodeModelPicker
                    nodeKey={nodeKey}
                    nodeType={nodeType}
                    modelId={liveNodeData?.modelId}
                    imageResolution={liveNodeData?.imageResolution || project.data?.image_resolution || undefined}
                    imageQuality={liveNodeData?.imageQuality || project.data?.image_quality || undefined}
                    aspectRatio={liveNodeData?.aspectRatio || project.data?.aspect_ratio || undefined}
                    reasoningEffort={liveNodeData?.reasoningEffort}
                  />
                </div>
              <span className="flex-1" />
              {STREAM_NODE_TYPES.has(nodeType) && projectId ? <StreamsPicker projectId={projectId} /> : null}
            </div>
            ) : null}
            {hasSettings ? (
              <div className="flex flex-col gap-3 text-[14px] text-white/85">
                {isExcelGptNode(nodeType) && projectId ? (
                  <ExcelGptSettingsPanel projectId={projectId} nodeKey={nodeKey} config={excelConfig} onConfigChange={patchExcelNodeData} />
                ) : null}
                {showStepParams ? <NodeStepParamsPanel projectId={projectId!} nodeType={nodeType} /> : null}
                {nodeType === "items" && projectId ? <ItemsConfigPanel projectId={projectId} /> : null}
                {nodeType === "hero" && projectId ? <HeroConfigPanel projectId={projectId} /> : null}
                {nodeType === "script" && projectId && projectUsesNii67Script(project.data ?? null) ? <Nii67CardPanel projectId={projectId} /> : null}
                {nodeType === "sfx_plan" && projectId ? <SfxPlanSettingsPanel projectId={projectId} /> : null}
                {(nodeType === "sfx_gen" || nodeType === "sfx") && projectId ? <SfxGenSettingsPanel projectId={projectId} /> : null}
              </div>
            ) : null}
            {promptStepCode && projectId && nodeKey ? (
              <NodeStyleVars projectId={projectId} stepCode={promptStepCode} nodeKey={nodeKey} slotId={activeSlot?.id} activeVariant={activeVariant ? String(activeVariant) : undefined} />
            ) : null}
            {showFilesPanel && promptStepCode ? (
              <NodePromptBlock
                stepCode={promptStepCode}
                activeVariant={activeVariant ? String(activeVariant) : undefined}
                onActivate={(v) => activateVariant.mutate(v)}
                nodeKey={nodeKey ?? ""}
                projectId={projectId ?? undefined}
                description={liveNodeData?.description || spec.description || ""}
              />
            ) : null}
            {showFilesPanel && promptStepCode && !canCatalog ? (
              <div className="rounded-[10px] border border-white/[0.06] bg-black/35 p-1 [&_section]:!rounded-[10px] [&_section]:!border-0 [&_section]:!bg-transparent">
                <PromptFilesPanel
                  key={`inline-files-${nodeKey}-${activeSlot?.id}-${promptStepCode}`}
                  stepCode={promptStepCode ?? ""}
                  slotId={activeSlot?.id}
                  projectId={projectId ?? undefined}
                  preferredFile={preferredFile}
                  folderHint={legacyPromptFolder(promptStepCode) ?? promptPaths.legacyDir ?? promptStepCode}
                  activeVariant={activeVariant}
                  activeVariantSourceLabel={activeVariantSourceLabel}
                  onActivateVariant={(variant) => activateVariant.mutate(variant)}
                  activating={activateVariant.isPending}
                />
              </div>
            ) : null}
          </div>
        ) : inTab === "results" ? (
          <div className="nodrag flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3 [scrollbar-gutter:stable] [scrollbar-width:thin] [scrollbar-color:hsl(0_0%_55%_/_0.55)_transparent] [&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-white/20 [&::-webkit-scrollbar-thumb:hover]:bg-white/40 [&::-webkit-scrollbar-track]:bg-transparent">
            <div className="rounded-[10px] border border-white/[0.06] bg-black/35 p-3 text-[13px] text-white/85 [&_.rounded-xl]:!rounded-[10px] [&_.rounded-xl]:!border-white/[0.06] [&_.rounded-xl]:!bg-black/30">
              <div className="flex flex-col gap-4">
                  {inlineResult && (inlineResult.hasResult || inlineResult.items.length > 0) ? (
                    <NodeResultViewBody
                      projectId={projectId!}
                      nodeKey={nodeKey}
                      nodeType={nodeType}
                      snapshot={inlineResult}
                    />
                  ) : ownFileNode && filteredArtifacts.length > 0 ? (
                    <div className="grid gap-3 sm:grid-cols-2">
                      {filteredArtifacts.map((a) => (
                        <div
                          key={a.id}
                          className="rounded-xl border border-white/10 bg-white/5 p-2"
                        >
                          <div className="text-[13px] uppercase text-muted-foreground">
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
                            <div className="mt-1 flex max-h-40 flex-col overflow-auto rounded bg-black/40 p-2 text-[13px] font-mono text-zinc-300">
                              <div className="text-[12px] text-zinc-500">
                                {a.path.split(/[\\/]/).pop()}
                              </div>
                              <span className="mt-1 text-[13px] text-zinc-400">Текстовый артефакт (доступен для скачивания)</span>
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
                            className="mt-2 inline-flex items-center gap-1 text-[13px] text-primary hover:underline"
                          >
                            <Download className="h-3 w-3" />
                            Скачать
                          </a>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="flex flex-col items-center justify-center py-12 text-center text-muted-foreground">
                      <p className="text-[15px]">
                        {inlineResult?.summary || "Результаты ещё не сформированы. Запустите шаг для генерации."}
                      </p>
                    </div>
                  )}
                </div>
            </div>
          </div>
        ) : (
          <div className="nodrag flex min-h-0 flex-auto flex-col overflow-hidden p-3">
            {showGptTextPanel && projectId != null && activeStepCode ? (
              <GptTextPanel
                key={`inline-gpt-${activeSlot?.id}-${activeStepCode}`}
                projectId={projectId}
                stepCode={activeStepCode}
              />
            ) : showFramePromptsPanel && projectId != null ? (
              <FramePromptsPanel
                key={`inline-frame-prompts-${projectId}-${nodeType}`}
                projectId={projectId}
                field={nodeType === "videos" ? "animation_prompt" : "image_prompt"}
              />
            ) : showFilesPanel && isCheckNode && projectId != null && nodeKey ? (
              <CheckNodePromptPanel
                projectId={projectId}
                nodeKey={nodeKey}
                resolve={operatorResolve.data}
                loading={operatorResolve.isLoading}
              />
            ) : showFilesPanel && promptStepCode ? (
              <PromptFilesPanel
                key={`inline-cat-${nodeKey}-${activeSlot?.id}-${promptStepCode}`}
                stepCode={promptStepCode}
                slotId={activeSlot?.id}
                projectId={projectId ?? undefined}
                preferredFile={preferredFile}
                folderHint={legacyPromptFolder(promptStepCode) ?? promptPaths.legacyDir ?? promptStepCode}
                activeVariant={activeVariant}
                activeVariantSourceLabel={activeVariantSourceLabel}
                onActivateVariant={(variant) => activateVariant.mutate(variant)}
                activating={activateVariant.isPending}
                catalogPanel={{ nodeKey, embedded: true, onOpenSettings: () => setTab("settings"), onClose: () => requestClose() }}
              />
            ) : (
              <p className="p-4 text-[13px] text-white/50">У этой ноды нет каталога промтов.</p>
            )}
          </div>
        )}
      </aside>
    </div>,
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
