"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Clock,
  Copy,
  Download,
  FileSpreadsheet,
  FileText,
  Film,
  Loader2,
  Maximize2,
  Replace,
  Save,
  ScrollText,
  Sparkles,
  Upload,
  Volume2,
} from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import type { NodeResultItem, NodeResultSnapshot } from "@/lib/node-result-resolver";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { MediaFrameGallery } from "@/components/hitl/media-frame-gallery";
import { TopicEditor } from "@/components/inspector/topic-editor";
import {
  ROW_VOICEOVER_V8,
  SHEET_PLAN_V8,
} from "@/lib/xlsx-sheets";
import { NodeAudioMiniPlayer, ResultAudioCard } from "./custom-audio-player";

export function NodeResultViewBody({
  projectId,
  nodeKey,
  nodeType,
  snapshot,
  onHeroReplaced,
}: {
  projectId: number;
  nodeKey?: string | null;
  nodeType: string;
  snapshot: NodeResultSnapshot;
  onHeroReplaced?: () => void;
}) {
  switch (snapshot.viewMode) {
    case "director_plan":
    case "xlsx_general_plan":
      return (
        <DirectorPlanView
          projectId={projectId}
          nodeKey={nodeKey}
          nodeType={nodeType}
          snapshot={snapshot}
        />
      );
    case "voiceover_wide":
      return <VoiceoverWideView projectId={projectId} snapshot={snapshot} />;
    case "xlsx_split_row":
      return (
        <SplitRowView
          projectId={projectId}
          nodeKey={nodeKey}
          nodeType={nodeType}
        />
      );
    case "frame_prompts":
      return <FramePromptsView items={snapshot.items} />;
    case "frame_images":
      return (
        <FrameImagesView
          projectId={projectId}
          nodeType={nodeType}
          items={snapshot.items}
          onHeroReplaced={onHeroReplaced}
        />
      );
    case "frame_videos":
      if (nodeType === "videos" || nodeType === "hitl_videos") {
        return <SceneVideosGalleryView projectId={projectId} />;
      }
      return <FrameVideosView items={snapshot.items} />;
    case "topic_edit":
      return <TopicEditView projectId={projectId} snapshot={snapshot} />;
    case "html_report":
      return <ShotsReportView snapshot={snapshot} />;
    case "sfx_plan":
      return <SfxPlanView projectId={projectId} snapshot={snapshot} />;
    case "sfx_gen":
      return <SfxGenView projectId={projectId} snapshot={snapshot} />;
    default:
      if (nodeKey?.endsWith("_fw_report")) {
        return <ShotsReportView snapshot={snapshot} />;
      }
      if (nodeType === "sfx_plan") {
        return <SfxPlanView projectId={projectId} snapshot={snapshot} />;
      }
      if (nodeType === "sfx" || nodeType === "sfx_gen") {
        return <SfxGenView projectId={projectId} snapshot={snapshot} />;
      }
      if (
        nodeType === "excel_gpt" ||
        Boolean(nodeType?.startsWith("enrich_"))
      ) {
        return (
          <ExcelGptResultView
            projectId={projectId}
            nodeKey={nodeKey}
            nodeType={nodeType}
            snapshot={snapshot}
          />
        );
      }
      return (
        <DefaultResultView
          projectId={projectId}
          nodeKey={nodeKey}
          snapshot={snapshot}
        />
      );
  }
}

function LoadingBlock() {
  return (
    <div className="flex items-center justify-center py-12">
      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
    </div>
  );
}

function isSplitRowLabel(text: string): boolean {
  const t = text.trim().toLowerCase();
  if (!t) return true;
  return (
    t === "закадровый текст" ||
    t === "закадровый" ||
    t === "voiceover" ||
    (t.includes("закадров") && t.length < 48)
  );
}

function XlsxUploadBar({
  projectId,
  nodeKey,
  nodeType,
}: {
  projectId: number;
  nodeKey?: string | null;
  nodeType?: string | null;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const isExcelGpt = nodeType === "excel_gpt" || Boolean(nodeType?.startsWith("enrich_"));
  const upload = useMutation({
    mutationFn: async (file: File) => {
      if (isExcelGpt && nodeKey) {
        return api.uploadExcelGptFile(projectId, nodeKey, file);
      }
      await api.uploadProjectXlsx(projectId, file, {
        nodeKey: nodeKey ?? undefined,
      });
      return { fileName: file.name, replacedXlsx: true as const };
    },
    onSuccess: (res) => {
      const name =
        res && typeof res === "object" && "fileName" in res
          ? String((res as { fileName?: string }).fileName || "")
          : "";
      toast.success(
        isExcelGpt && name ? `Excel подменён: ${name}` : "Excel загружен",
      );
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-preview", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-general-plan", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-split-row", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-sheets", projectId] });
      qc.invalidateQueries({ queryKey: ["v-menu-xlsx-preview", projectId] });
      qc.invalidateQueries({ queryKey: ["gpt-operator-resolve", projectId] });
      if (isExcelGpt && nodeKey && name) {
        window.dispatchEvent(
          new CustomEvent("canvas-patch-node-data", {
            detail: {
              nodeKey,
              patch: { inputSource: "upload", uploadedFileName: name },
            },
          }),
        );
      }
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  return (
    <div className="mb-2 flex flex-wrap gap-2">
      <Button
        size="sm"
        variant="outline"
        onClick={() => fileRef.current?.click()}
        disabled={upload.isPending}
        className="h-8 gap-1.5 text-xs text-zinc-300 hover:text-white border-white/10 hover:bg-zinc-800"
      >
        {upload.isPending ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Upload className="h-3.5 w-3.5" />
        )}
        Загрузить файл
      </Button>
      <input
        ref={fileRef}
        type="file"
        accept=".xlsx,.xls"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) upload.mutate(f);
          e.target.value = "";
        }}
      />
    </div>
  );
}

function DirectorPlanView({
  projectId,
  nodeKey,
  nodeType,
  snapshot,
}: {
  projectId: number;
  nodeKey?: string | null;
  nodeType?: string | null;
  snapshot?: NodeResultSnapshot;
}) {
  const qc = useQueryClient();
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
  });

  const rawPlanText =
    project.data?.general_plan?.trim() ||
    snapshot?.items.find((i) => i.kind === "text")?.content?.trim() ||
    "";
  const [text, setText] = useState(rawPlanText);
  const [dirty, setDirty] = useState(false);
  const [copied, setCopied] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!dirty && rawPlanText) {
      setText(rawPlanText);
    }
  }, [rawPlanText, dirty]);

  const save = useMutation({
    mutationFn: (body: string) =>
      api.patchProject(projectId, { general_plan: body }),
    onSuccess: (updated) => {
      const saved = (updated.general_plan ?? text).trim();
      setText(saved);
      setDirty(false);
      qc.setQueryData(["project", projectId], updated);
      toast.success("Сценарий успешно сохранён");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  // Hotkey Ctrl+S / Cmd+S
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        if (dirty && !save.isPending) {
          save.mutate(text);
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [dirty, text, save]);

  const handleCopy = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      toast.success("Сценарий скопирован в буфер обмена");
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Не удалось скопировать текст");
    }
  };

  const handleDownloadTxt = () => {
    if (!text) return;
    const topic = project.data?.topic?.slice(0, 30)?.trim() || "plan";
    const filename = `${topic.replace(/[^a-zA-Zа-яА-Я0-9_-]/g, "_")}_script.txt`;
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    toast.success(`Скачан файл ${filename}`);
  };

  const handleDownloadMd = () => {
    if (!text) return;
    const topic = project.data?.topic?.trim() || "Режиссёрский сценарий";
    const filename = `${topic.slice(0, 30).replace(/[^a-zA-Zа-яА-Я0-9_-]/g, "_")}_script.md`;
    const mdContent = `# Режиссёрский сценарий: ${topic}\n\n${text}`;
    const blob = new Blob([mdContent], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    toast.success(`Скачан файл ${filename}`);
  };

  if (project.isLoading && !rawPlanText) return <LoadingBlock />;

  const charCount = text.length;
  const wordCount = text.trim() ? text.trim().split(/\s+/).length : 0;
  // Estimated reading / voiceover duration (~14 chars/sec or ~2.5 words/sec)
  const estSeconds = Math.round(wordCount / 2.5) || Math.round(charCount / 14);
  const estMin = Math.floor(estSeconds / 60);
  const estSecRem = estSeconds % 60;
  const estDuration = estMin > 0 ? `${estMin} мин ${estSecRem} сек` : `${estSecRem} сек`;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      {/* Top Director Bar */}
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-white/10 pb-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-2 rounded-lg bg-teal-500/10 border border-teal-500/30 px-3 py-1.5 text-xs font-semibold text-teal-300">
            <ScrollText className="h-4 w-4 text-teal-400" />
            <span>Режиссёрский сценарий</span>
          </div>

          {dirty ? (
            <span className="flex items-center gap-1.5 rounded-full bg-amber-500/15 border border-amber-500/30 px-2.5 py-0.5 text-[11px] font-medium text-amber-300 animate-pulse">
              <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
              Не сохранено (Ctrl+S)
            </span>
          ) : charCount > 0 ? (
            <span className="flex items-center gap-1.5 rounded-full bg-emerald-500/15 border border-emerald-500/30 px-2.5 py-0.5 text-[11px] font-medium text-emerald-300">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              Синхронизировано
            </span>
          ) : null}

          {charCount > 0 && (
            <div className="hidden sm:flex items-center gap-2 rounded-lg bg-zinc-900/80 border border-white/5 px-2.5 py-1 text-xs text-zinc-400">
              <span className="text-zinc-200 font-mono font-medium">{charCount.toLocaleString("ru-RU")}</span>
              <span className="text-[11px] text-zinc-500">симв.</span>
              <span className="text-zinc-600">·</span>
              <span className="text-zinc-200 font-mono font-medium">{wordCount}</span>
              <span className="text-[11px] text-zinc-500">слов</span>
              <span className="text-zinc-600">·</span>
              <Clock className="h-3 w-3 text-zinc-400" />
              <span className="text-zinc-300 font-medium">≈ {estDuration}</span>
            </div>
          )}
        </div>

        {/* Action Buttons */}
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            className={cn(
              "h-8 gap-1.5 text-xs font-medium transition-all",
              dirty
                ? "bg-gradient-to-r from-teal-500 to-emerald-600 hover:from-teal-400 hover:to-emerald-500 text-white shadow-md shadow-teal-500/20 border-0"
                : "bg-zinc-800 text-zinc-400 hover:text-zinc-200 border border-white/10"
            )}
            disabled={save.isPending || !dirty}
            onClick={() => save.mutate(text)}
            title="Сохранить сценарий в проект (Ctrl+S)"
          >
            {save.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Save className="h-3.5 w-3.5" />
            )}
            Сохранить
          </Button>

          <Button
            size="sm"
            variant="outline"
            className="h-8 gap-1.5 text-xs text-zinc-300 hover:text-white border-white/10 hover:bg-zinc-800"
            onClick={handleCopy}
            disabled={!text}
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-emerald-400" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
            {copied ? "Скопировано" : "Копировать"}
          </Button>

          <Button
            size="sm"
            variant="outline"
            className="h-8 gap-1.5 text-xs text-zinc-300 hover:text-white border-white/10 hover:bg-zinc-800"
            onClick={handleDownloadTxt}
            disabled={!text}
            title="Скачать сценарий в текстовом формате .txt"
          >
            <Download className="h-3.5 w-3.5 text-zinc-400" />
            .txt
          </Button>

          <Button
            size="sm"
            variant="outline"
            className="h-8 gap-1.5 text-xs text-zinc-300 hover:text-white border-white/10 hover:bg-zinc-800"
            onClick={handleDownloadMd}
            disabled={!text}
            title="Скачать сценарий в формате Markdown .md"
          >
            <Download className="h-3.5 w-3.5 text-zinc-400" />
            .md
          </Button>
        </div>
      </div>

      {/* Editor & Content */}
      {text || dirty ? (
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          <Textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setDirty(true);
            }}
            className="min-h-[55vh] flex-1 resize-none rounded-xl border border-white/10 bg-zinc-950/80 p-5 font-mono text-xs sm:text-[13px] leading-relaxed text-zinc-200 placeholder:text-zinc-600 focus-visible:ring-1 focus-visible:ring-teal-500/50 focus-visible:border-teal-500/50 shadow-inner"
            placeholder="Введите или отредактируйте режиссёрский сценарий ролика...&#10;&#10;Структура:&#10;1. Главная тема и хук&#10;2. Драматургическая идея&#10;3. Развитие сюжета и тезисы&#10;4. Кульминация и финал"
          />
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-white/10 bg-black/30 py-20 px-4 text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-teal-500/10 border border-teal-500/20 text-teal-400 mb-4 shadow-lg shadow-teal-500/10">
            <ScrollText className="h-7 w-7" />
          </div>
          <p className="text-base font-semibold text-zinc-200">Режиссёрский сценарий ещё не сгенерирован</p>
          <p className="mt-1.5 max-w-md text-xs text-zinc-400 leading-normal">
            Запустите шаг «Сценарий» на канвасе для генерации через ИИ или начните писать план ролика вручную.
          </p>
          <Button
            size="sm"
            variant="outline"
            className="mt-5 h-8 gap-1.5 text-xs text-teal-300 border-teal-500/30 hover:bg-teal-500/10"
            onClick={() => {
              setText(" ");
              setDirty(true);
              setTimeout(() => textareaRef.current?.focus(), 50);
            }}
          >
            <Sparkles className="h-3.5 w-3.5" />
            Написать сценарий вручную
          </Button>
        </div>
      )}
    </div>
  );
}

const GeneralPlanSheetView = DirectorPlanView;

function VoiceoverWideView({
  projectId,
  snapshot,
}: {
  projectId: number;
  snapshot: NodeResultSnapshot;
}) {
  const qc = useQueryClient();
  const textItem = snapshot.items.find((i) => i.kind === "text");
  const fileItem = snapshot.items.find((i) => i.downloadUrl);
  const [text, setText] = useState(textItem?.content ?? "");
  const [dirty, setDirty] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const fileText = useQuery({
    queryKey: ["voiceover-file", fileItem?.downloadUrl],
    queryFn: async () => {
      const res = await fetch(fileItem!.downloadUrl!);
      if (!res.ok) throw new Error(await res.text());
      return res.text();
    },
    enabled: Boolean(fileItem?.downloadUrl) && !textItem?.content && !dirty,
  });

  useEffect(() => {
    if (dirty) return;
    if (textItem?.content) setText(textItem.content);
    else if (fileText.data) setText(fileText.data);
  }, [textItem?.content, fileText.data, dirty]);

  const save = useMutation({
    mutationFn: (body: string) => api.patchProject(projectId, { script_text: body }),
    onSuccess: (updated) => {
      const saved = (updated.script_text ?? text).trim();
      setText(saved);
      setDirty(false);
      qc.setQueryData(["project", projectId], updated);
      toast.success("Текст сохранён");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      toast.success("Закадровый текст скопирован в буфер");
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Не удалось скопировать текст");
    }
  };

  if (fileText.isLoading && !text) return <LoadingBlock />;
  if (fileText.isError && !text) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        Не удалось загрузить voiceover.txt. Откройте проект заново или сохраните текст вручную.
      </p>
    );
  }

  const charCount = text.length;
  const wordCount = text.trim() ? text.trim().split(/\s+/).length : 0;
  const estSeconds = Math.round(charCount / 14);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-white/10 pb-2">
        <div className="flex flex-wrap items-center gap-2">
          {fileItem?.downloadUrl && (
            <Button size="sm" variant="outline" className="h-8 gap-1.5 text-xs" asChild>
              <a href={fileItem.downloadUrl} download target="_blank" rel="noreferrer">
                <Download className="h-3.5 w-3.5" />
                Скачать voiceover.txt
              </a>
            </Button>
          )}
          <Button
            size="sm"
            className="h-8 gap-1.5 text-xs bg-teal-600 hover:bg-teal-500 text-white shadow-sm"
            disabled={save.isPending}
            onClick={() => save.mutate(text)}
          >
            {save.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Save className="h-3.5 w-3.5" />
            )}
            Сохранить текст
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-8 gap-1.5 text-xs"
            onClick={handleCopy}
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-emerald-400" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
            {copied ? "Скопировано" : "Копировать"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-8 gap-1.5 text-xs"
            onClick={() => fileRef.current?.click()}
          >
            <Upload className="h-3.5 w-3.5" />
            Загрузить файл
          </Button>
          <input
            ref={fileRef}
            type="file"
            accept=".txt,text/plain"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (!f) return;
              const reader = new FileReader();
              reader.onload = () => {
                const body = String(reader.result ?? "");
                setText(body);
                save.mutate(body);
              };
              reader.readAsText(f, "utf-8");
              e.target.value = "";
            }}
          />
        </div>

        {charCount > 0 && (
          <div className="flex items-center gap-2 text-xs text-zinc-400">
            <span className="rounded bg-zinc-800 px-2 py-0.5 text-[11px] font-medium text-teal-300 border border-zinc-700/60">
              {charCount.toLocaleString("ru-RU")} симв.
            </span>
            <span className="text-[11px] text-zinc-400">
              {wordCount} слов
            </span>
            <span className="text-[11px] text-zinc-500">
              ≈ {estSeconds} сек (14 зн/сек)
            </span>
          </div>
        )}
      </div>

      <Textarea
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setDirty(true);
        }}
        className="min-h-[55vh] flex-1 resize-none rounded-lg border border-white/10 bg-black/40 p-4 font-mono text-xs leading-relaxed text-zinc-200 focus-visible:ring-1 focus-visible:ring-teal-500"
        placeholder="Закадровый текст…"
      />
    </div>
  );
}

function SplitRowView({
  projectId,
  nodeKey,
  nodeType,
}: {
  projectId: number;
  nodeKey?: string | null;
  nodeType?: string | null;
}) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [copied, setCopied] = useState(false);
  const isExcelGpt = nodeType === "excel_gpt" || Boolean(nodeType?.startsWith("enrich_"));

  const upload = useMutation({
    mutationFn: async (file: File) => {
      if (isExcelGpt && nodeKey) {
        return api.uploadExcelGptFile(projectId, nodeKey, file);
      }
      await api.uploadProjectXlsx(projectId, file, {
        nodeKey: nodeKey ?? undefined,
      });
      return { fileName: file.name, replacedXlsx: true as const };
    },
    onSuccess: (res) => {
      const name =
        res && typeof res === "object" && "fileName" in res
          ? String((res as { fileName?: string }).fileName || "")
          : "";
      toast.success(name ? `Файл обновлён: ${name}` : "Файл загружен");
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["frames", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-preview", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-split-row", projectId] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  // DB SoT: после replace_frames UI должен показывать кадры из БД, не stale xlsx/snapshot.
  const framesQ = useQuery({
    queryKey: ["frames", projectId],
    queryFn: () => api.listFrames(projectId),
    refetchInterval: 8000,
  });

  const row = useQuery({
    queryKey: ["xlsx-split-row", projectId, nodeKey ?? "live"],
    queryFn: () =>
      api.previewProjectXlsx(projectId, {
        sheet: SHEET_PLAN_V8,
        row: ROW_VOICEOVER_V8,
        maxCols: 300,
        nodeKey: nodeKey ?? undefined,
      }),
    enabled: (framesQ.data?.length ?? 0) < 2,
  });

  if (framesQ.isLoading) return <LoadingBlock />;

  const dbFrames = (framesQ.data ?? [])
    .slice()
    .sort((a, b) => a.number - b.number)
    .filter((f) => (f.voiceover_text || "").trim());

  const isDb = dbFrames.length >= 2;
  const cells = row.data?.cells ?? [];
  const frameCells = cells
    .map((cell, colIndex) => ({ cell, colIndex }))
    .filter(({ cell }) => cell.trim() && !isSplitRowLabel(cell));

  if (!isDb && row.isLoading) return <LoadingBlock />;

  const items = isDb
    ? dbFrames.map((fr) => ({
        id: String(fr.id),
        number: fr.number,
        text: (fr.voiceover_text || "").trim(),
        subLabel: undefined as string | undefined,
      }))
    : frameCells.map(({ cell, colIndex }, idx) => ({
        id: `cell_${colIndex}`,
        number: idx + 1,
        text: cell.trim(),
        subLabel: `кол. ${colIndex + 1}`,
      }));

  if (items.length === 0) {
    return (
      <div className="flex flex-col gap-3 py-16 items-center justify-center text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-zinc-800/80 border border-white/10 text-zinc-400 mb-2">
          <Film className="h-6 w-6" />
        </div>
        <p className="text-sm font-semibold text-zinc-200">Кадры ещё не разбиты</p>
        <p className="text-xs text-muted-foreground max-w-sm leading-relaxed">
          Запустите шаг «Разбивка» на канвасе для разделения текста на отдельные кадры ролика.
        </p>
        <Button
          size="sm"
          variant="outline"
          className="mt-3 h-8 gap-1.5 text-xs text-zinc-300 hover:text-white border-white/10 hover:bg-zinc-800"
          onClick={() => fileRef.current?.click()}
          disabled={upload.isPending}
        >
          {upload.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
          Загрузить файл
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".xlsx,.xls,.txt"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) upload.mutate(f);
            e.target.value = "";
          }}
        />
      </div>
    );
  }

  const totalFrames = items.length;
  const totalChars = items.reduce((acc, it) => acc + it.text.length, 0);
  const totalWords = items.reduce(
    (acc, it) => acc + (it.text ? it.text.split(/\s+/).length : 0),
    0,
  );
  const estSeconds = Math.round(totalWords / 2.5) || Math.round(totalChars / 14);
  const estMin = Math.floor(estSeconds / 60);
  const estSecRem = estSeconds % 60;
  const estTotalDuration = estMin > 0 ? `${estMin} мин ${estSecRem} сек` : `${estSecRem} сек`;

  const handleCopyAll = () => {
    const fullText = items
      .map((it) => `Кадр ${it.number}:\n${it.text}`)
      .join("\n\n");
    navigator.clipboard.writeText(fullText);
    setCopied(true);
    toast.success("Все кадры скопированы");
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      {/* Top Action & Stats Bar */}
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-white/10 pb-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-2 rounded-lg bg-zinc-800/80 border border-white/10 px-3 py-1.5 text-xs font-semibold text-zinc-200">
            <Film className="h-3.5 w-3.5 text-zinc-400" />
            <span>Раскадровка</span>
          </div>
          <div className="flex items-center gap-2 rounded-lg bg-zinc-900/80 border border-white/5 px-2.5 py-1 text-xs text-zinc-400">
            <span className="font-mono font-medium text-zinc-200">{totalFrames}</span>
            <span className="text-[11px] text-zinc-500">кадров</span>
            <span className="text-zinc-600">·</span>
            <span className="font-mono font-medium text-zinc-200">{totalChars.toLocaleString("ru-RU")}</span>
            <span className="text-[11px] text-zinc-500">симв.</span>
            <span className="text-zinc-600">·</span>
            <Clock className="h-3 w-3 text-zinc-400" />
            <span className="text-zinc-300 font-medium">≈ {estTotalDuration}</span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            className="h-8 gap-1.5 text-xs text-zinc-300 hover:text-white border-white/10 hover:bg-zinc-800"
            onClick={handleCopyAll}
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-emerald-400" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
            {copied ? "Скопировано" : "Копировать всё"}
          </Button>

          <Button
            size="sm"
            variant="outline"
            className="h-8 gap-1.5 text-xs text-zinc-300 hover:text-white border-white/10 hover:bg-zinc-800"
            onClick={() => fileRef.current?.click()}
            disabled={upload.isPending}
          >
            {upload.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Upload className="h-3.5 w-3.5" />
            )}
            Загрузить файл
          </Button>
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx,.xls"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) upload.mutate(f);
              e.target.value = "";
            }}
          />
        </div>
      </div>

      {/* Vertical Cards Grid - scrolls DOWN */}
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 pb-4">
          {items.map((it) => {
            const charCount = it.text.length;
            const estSec = Math.max(1, Math.round(charCount / 14));
            return (
              <div
                key={it.id}
                className="group relative flex flex-col justify-between rounded-xl border border-white/[0.08] bg-zinc-900/40 p-3.5 transition-all hover:border-white/20 hover:bg-zinc-900/70 shadow-sm"
              >
                <div className="flex items-center justify-between gap-2 border-b border-white/5 pb-2">
                  <div className="flex items-center gap-1.5">
                    <span className="inline-flex items-center rounded-md bg-white/[0.08] border border-white/10 px-2.5 py-0.5 font-mono text-xs font-semibold text-zinc-100">
                      Кадр {it.number}
                    </span>
                    {it.subLabel && (
                      <span className="text-[10px] text-zinc-500 font-mono">
                        {it.subLabel}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 text-[11px] font-mono text-zinc-500">
                    <span>{charCount} зн.</span>
                    <span>·</span>
                    <span>≈ {estSec}с</span>
                  </div>
                </div>
                <p className="mt-2.5 whitespace-pre-wrap text-[13px] leading-relaxed text-zinc-200 font-normal select-text">
                  {it.text}
                </p>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export function FramePromptsView({ items }: { items: NodeResultItem[] }) {
  const [selectedId, setSelectedId] = useState(items[0]?.id ?? "");
  const selected = items.find((i) => i.id === selectedId) ?? items[0];

  useEffect(() => {
    if (items[0]?.id) setSelectedId(items[0].id);
  }, [items]);

  return (
    <div className="grid min-h-0 flex-1 gap-3 md:grid-cols-[200px_1fr] md:items-stretch">
      <ScrollArea className="h-[65vh] rounded-lg border border-white/10">
        <div className="p-1">
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setSelectedId(item.id)}
              className={cn(
                "mb-1 w-full rounded-md px-2 py-2 text-left text-[11px] transition",
                selected?.id === item.id
                  ? "bg-white/10 text-white font-medium border border-white/15"
                  : "text-muted-foreground hover:bg-white/5",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      </ScrollArea>
      {selected?.previewUrl ? (
        <div className="grid h-[65vh] min-h-0 grid-cols-1 gap-3 md:grid-cols-[260px_1fr]">
          <div className="flex flex-col gap-2 rounded-lg border border-white/10 bg-black/20 p-2.5">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                Стартовый кадр (I2V)
              </span>
              <a
                href={selected.previewUrl}
                target="_blank"
                rel="noreferrer"
                className="text-[10px] text-muted-foreground hover:text-white underline"
              >
                Открыть
              </a>
            </div>
            <div className="relative flex flex-1 items-center justify-center overflow-hidden rounded-md border border-white/5 bg-black/40">
              <img
                src={selected.previewUrl}
                alt={selected.label}
                className="h-full w-full object-contain"
              />
            </div>
          </div>
          <div className="flex flex-col gap-2 min-h-0">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              Промпт движения (анимация)
            </span>
            <Textarea
              readOnly
              value={selected?.content ?? ""}
              className="flex-1 resize-none text-xs leading-relaxed"
            />
          </div>
        </div>
      ) : (
        <Textarea
          readOnly
          value={selected?.content ?? ""}
          className="h-[65vh] resize-none text-xs leading-relaxed"
        />
      )}
    </div>
  );
}

function FrameImagesView({
  projectId,
  nodeType,
  items,
  onHeroReplaced,
}: {
  projectId: number;
  nodeType: string;
  items: NodeResultItem[];
  onHeroReplaced?: () => void;
}) {
  if (nodeType === "images") {
    return <SceneImagesGalleryView projectId={projectId} />;
  }

  const [index, setIndex] = useState(0);
  const [zoomOpen, setZoomOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const isHero = nodeType === "hero" || nodeType === "hitl_hero";
  const isItems = nodeType === "items";
  const current = items[index] ?? items[0];

  const replaceHero = useMutation({
    mutationFn: (file: File) =>
      api.replaceHeroImage(projectId, file, current?.filePath ?? undefined),
    onSuccess: () => {
      toast.success("Персонаж заменён");
      qc.invalidateQueries({ queryKey: ["project-assets", projectId] });
      onHeroReplaced?.();
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto pr-1 pb-4">
      <div className="flex flex-wrap items-center gap-2">
        {isHero && (
          <>
            <input
              ref={fileRef}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) replaceHero.mutate(f);
                e.target.value = "";
              }}
            />
            <Button
              size="sm"
              variant="outline"
              disabled={replaceHero.isPending}
              onClick={() => fileRef.current?.click()}
            >
              {replaceHero.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Replace className="h-3.5 w-3.5" />
              )}
              Заменить персонажа
            </Button>
          </>
        )}
        {current?.previewUrl && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => setZoomOpen(true)}
          >
            <Maximize2 className="h-3.5 w-3.5" />
            Открыть
          </Button>
        )}
        {current?.downloadUrl && (
          <Button size="sm" variant="outline" asChild>
            <a href={current.downloadUrl} download target="_blank" rel="noreferrer">
              <Download className="h-3.5 w-3.5" />
              Скачать
            </a>
          </Button>
        )}
        {items.length > 1 && (
          <>
            <Button
              size="icon"
              variant="ghost"
              className="h-8 w-8"
              disabled={index <= 0}
              onClick={() => setIndex((i) => Math.max(0, i - 1))}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <span className="text-[11px] text-muted-foreground">
              {index + 1} / {items.length}
            </span>
            <Button
              size="icon"
              variant="ghost"
              className="h-8 w-8"
              disabled={index >= items.length - 1}
              onClick={() => setIndex((i) => Math.min(items.length - 1, i + 1))}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </>
        )}
      </div>
      {items.length > 1 && (
        <div className="flex gap-2 overflow-x-auto pb-1">
          {items.map((item, i) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setIndex(i)}
              className={cn(
                "relative h-16 w-24 shrink-0 overflow-hidden rounded-lg border transition",
                i === index ? "border-primary ring-1 ring-primary/40 shadow-sm" : "border-white/10 hover:border-white/20",
              )}
            >
              {item.previewUrl ? (
                <img src={item.previewUrl} alt="" className="h-full w-full object-cover" />
              ) : (
                <span className="flex h-full items-center justify-center px-1 text-[9px]">{item.label}</span>
              )}
              <span className="absolute bottom-1 left-1 rounded bg-black/80 px-1.5 py-0.5 text-[9px] font-mono font-medium text-white backdrop-blur-sm">
                #{i + 1}
              </span>
            </button>
          ))}
        </div>
      )}
      {current?.previewUrl && (
        <div
          className="group relative flex justify-center rounded-xl border border-white/10 bg-black/30 p-2 cursor-pointer transition hover:border-white/25"
          onClick={() => setZoomOpen(true)}
          title="Нажмите, чтобы открыть на весь экран"
        >
          <img src={current.previewUrl} alt="" className="max-h-[45vh] w-full object-contain transition group-hover:opacity-95" />
          <div className="absolute right-3 top-3 flex items-center gap-1.5 rounded-md bg-black/70 px-2.5 py-1 text-xs text-white/90 opacity-0 backdrop-blur-sm transition group-hover:opacity-100">
            <Maximize2 className="h-3.5 w-3.5" />
            Увеличить
          </div>
        </div>
      )}
      {isItems ? (
        <div className="rounded-lg border border-white/10 bg-black/20 p-3">
          <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            Описание предмета
          </p>
          <p className="max-h-36 overflow-auto whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">
            {current?.content?.trim() || "—"}
          </p>
        </div>
      ) : !isHero ? (
        <div className="rounded-lg border border-white/10 bg-black/20 p-3">
          <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            Закадровый текст
          </p>
          <p className="max-h-36 overflow-auto whitespace-pre-wrap text-sm leading-relaxed">
            {current?.content?.trim() || "—"}
          </p>
        </div>
      ) : null}

      {zoomOpen && current?.previewUrl && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 p-4 backdrop-blur-sm"
          onClick={() => setZoomOpen(false)}
        >
          <div
            className="relative flex max-h-[95vh] max-w-[95vw] flex-col items-center justify-center"
            onClick={(e) => e.stopPropagation()}
          >
            <img
              src={current.previewUrl}
              alt=""
              className="max-h-[88vh] max-w-[92vw] rounded-lg object-contain shadow-2xl"
            />
            <div className="absolute right-2 top-2 flex items-center gap-2">
              <Button size="sm" variant="secondary" className="h-8 gap-1.5 text-xs" asChild>
                <a href={current.previewUrl} target="_blank" rel="noreferrer">
                  <Maximize2 className="h-3.5 w-3.5" />
                  В новой вкладке
                </a>
              </Button>
              {current.downloadUrl && (
                <Button size="sm" variant="secondary" className="h-8 gap-1.5 text-xs" asChild>
                  <a href={current.downloadUrl} download target="_blank" rel="noreferrer">
                    <Download className="h-3.5 w-3.5" />
                    Скачать
                  </a>
                </Button>
              )}
              <Button
                size="icon"
                variant="secondary"
                className="h-8 w-8"
                onClick={() => setZoomOpen(false)}
              >
                ✕
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function FrameVideosView({ items }: { items: NodeResultItem[] }) {
  const [index, setIndex] = useState(0);
  const videoRef = useRef<HTMLVideoElement>(null);
  const current = items[index] ?? items[0];

  const goFullscreen = async () => {
    const el = videoRef.current;
    if (!el) return;
    try {
      if (el.requestFullscreen) await el.requestFullscreen();
    } catch {
      toast.error("Полноэкранный режим недоступен");
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto pr-1 pb-4">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" onClick={goFullscreen} disabled={!current?.previewUrl}>
          <Maximize2 className="h-3.5 w-3.5" />
          На весь экран
        </Button>
        {current?.downloadUrl && (
          <Button size="sm" variant="outline" asChild>
            <a href={current.downloadUrl} download target="_blank" rel="noreferrer">
              <Download className="h-3.5 w-3.5" />
              Скачать
            </a>
          </Button>
        )}
        {items.length > 1 && (
          <>
            <Button
              size="icon"
              variant="ghost"
              className="h-8 w-8"
              disabled={index <= 0}
              onClick={() => setIndex((i) => Math.max(0, i - 1))}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <span className="text-[11px] text-muted-foreground">
              {index + 1} / {items.length}
            </span>
            <Button
              size="icon"
              variant="ghost"
              className="h-8 w-8"
              disabled={index >= items.length - 1}
              onClick={() => setIndex((i) => Math.min(items.length - 1, i + 1))}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </>
        )}
      </div>
      <div className="flex flex-1 items-center justify-center rounded-xl border border-white/10 bg-black/40 p-2">
        {current?.previewUrl ? (
          <video
            ref={videoRef}
            src={current.previewUrl}
            controls
            className="max-h-[60vh] w-full object-contain"
          />
        ) : (
          <p className="py-12 text-sm text-muted-foreground">Видео ещё не сгенерировано</p>
        )}
      </div>
    </div>
  );
}

function SceneImagesGalleryView({ projectId }: { projectId: number }) {
  const media = useQuery({
    queryKey: ["media-review", projectId, "images"],
    queryFn: () => api.listMediaReview(projectId, "images"),
    refetchInterval: 5000,
  });

  if (media.isLoading) return <LoadingBlock />;

  const items = (media.data ?? []).filter((f) => f.preview_url);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto pr-1 pb-4">
      <MediaFrameGallery
        projectId={projectId}
        kind="images"
        items={items}
        showApproveButtons={false}
      />
    </div>
  );
}

function SceneVideosGalleryView({ projectId }: { projectId: number }) {
  const media = useQuery({
    queryKey: ["media-review", projectId, "videos"],
    queryFn: () => api.listMediaReview(projectId, "videos"),
    refetchInterval: 5000,
  });

  if (media.isLoading) return <LoadingBlock />;

  const items = (media.data ?? []).filter((f) => f.preview_url);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto pr-1 pb-4">
      <MediaFrameGallery
        projectId={projectId}
        kind="videos"
        items={items}
        showApproveButtons={false}
      />
    </div>
  );
}

function TopicEditView({
  projectId,
  snapshot,
}: {
  projectId: number;
  snapshot: NodeResultSnapshot;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pr-1 pb-4">
      <TopicEditor
        projectId={projectId}
        initialTopic={snapshot.items[0]?.content ?? ""}
      />
    </div>
  );
}

function ShotsReportView({ snapshot }: { snapshot: NodeResultSnapshot }) {
  const url =
    snapshot.items.find((i) => i.previewUrl || i.downloadUrl)?.previewUrl ||
    snapshot.items.find((i) => i.downloadUrl)?.downloadUrl ||
    "";
  if (!url) {
    return (
      <p className="text-sm text-muted-foreground">
        Отчёт ещё не собран. Запусти ноду «Отчёт: кадры + промты».
      </p>
    );
  }
  return (
    <div className="min-h-0 flex-1 overflow-auto bg-black">
      <iframe
        title="Отчёт кадров"
        src={url}
        sandbox="allow-scripts"
        className="block h-full min-h-[70vh] w-full border-0 bg-black"
      />
    </div>
  );
}

function ExcelGptResultView({
  projectId,
  nodeKey,
  nodeType,
  snapshot,
}: {
  projectId: number;
  nodeKey?: string | null;
  nodeType?: string | null;
  snapshot: NodeResultSnapshot;
}) {
  const resolve = useQuery({
    queryKey: ["gpt-operator-resolve", projectId, nodeKey],
    queryFn: () => api.resolveGptOperator(projectId, nodeKey!),
    enabled: Boolean(nodeKey),
    staleTime: 5000,
  });
  const replyFromMeta = snapshot.items.find((i) => i.kind === "text")?.content?.trim();
  const replyFromApi =
    typeof resolve.data?.lastResult?.replyPreview === "string"
      ? String(resolve.data.lastResult.replyPreview).trim()
      : "";
  const reply = replyFromMeta || replyFromApi;
  const xlsxItem = snapshot.items.find((i) => i.kind === "xlsx");

  return (
    <div className="space-y-3 overflow-y-auto pr-1 pb-4">
      <XlsxUploadBar
        projectId={projectId}
        nodeKey={nodeKey}
        nodeType={nodeType}
      />
      {xlsxItem ? (
        <p className="text-xs text-muted-foreground">{snapshot.summary}</p>
      ) : null}
      {resolve.isLoading && !reply ? (
        <LoadingBlock />
      ) : reply ? (
        <div
          tabIndex={0}
          className="max-h-[520px] min-h-[140px] overflow-y-auto overflow-x-auto rounded-lg border border-white/10 bg-black/40 p-3.5 select-text outline-none focus:border-white/25 [scrollbar-gutter:stable] [scrollbar-width:thin] [scrollbar-color:hsl(0_0%_55%_/_0.55)_transparent] [&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar]:h-2 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-white/25 [&::-webkit-scrollbar-thumb:hover]:bg-white/45 [&::-webkit-scrollbar-track]:bg-transparent"
        >
          <pre className="whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-foreground/90">
            {reply}
          </pre>
        </div>
      ) : (
        <p className="py-4 text-sm text-muted-foreground">
          Ответа GPT пока нет. Запустите ноду — после прогона здесь появится
          текст из gpt_reply.txt.
        </p>
      )}
    </div>
  );
}

function DefaultResultView({
  projectId,
  nodeKey,
  snapshot,
}: {
  projectId: number;
  nodeKey?: string | null;
  snapshot: NodeResultSnapshot;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const uploadXlsx = useMutation({
    mutationFn: (file: File) =>
      api.uploadProjectXlsx(projectId, file, {
        nodeKey: nodeKey ?? undefined,
      }),
    onSuccess: () => {
      toast.success("Excel заменён");
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-preview", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-sheets", projectId] });
      qc.invalidateQueries({ queryKey: ["v-menu-xlsx-preview", projectId] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const item = snapshot.items[0];
  if (!item) {
    return <p className="text-sm text-muted-foreground">Нет данных для отображения.</p>;
  }

  return (
    <div className="space-y-3 overflow-y-auto pr-1 pb-4">
      {item.kind === "xlsx" && (
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => fileRef.current?.click()} className="h-8 gap-1.5 text-xs text-zinc-300 hover:text-white border-white/10 hover:bg-zinc-800">
            <Upload className="h-3.5 w-3.5" />
            Загрузить файл
          </Button>
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx,.xls"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) uploadXlsx.mutate(f);
              e.target.value = "";
            }}
          />
        </div>
      )}
      {item.previewUrl && item.kind === "image" && (
        <img src={item.previewUrl} alt="" className="max-h-64 rounded-lg object-contain" />
      )}
      {item.previewUrl && item.kind === "video" && (
        <div className="space-y-2">
          <video
            src={item.previewUrl}
            controls
            className="max-h-[60vh] w-full rounded-lg bg-black object-contain"
          />
          {item.downloadUrl ? (
            <Button size="sm" variant="outline" asChild>
              <a href={item.downloadUrl} download>
                <Download className="h-3.5 w-3.5" />
                Скачать
              </a>
            </Button>
          ) : null}
        </div>
      )}
      {item.previewUrl && item.kind === "audio" && (
        <ResultAudioCard
          src={item.previewUrl}
          label={item.label}
          downloadUrl={item.downloadUrl}
        />
      )}
      {item.kind === "file" && (
        <div className="flex items-center justify-between rounded-xl border border-white/10 bg-white/[0.02] p-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-white/5 border border-white/10">
              <FileText className="h-4 w-4 text-primary" />
            </div>
            <div>
              <p className="text-sm font-medium text-foreground">{item.label}</p>
              <p className="text-xs text-muted-foreground">Файл результатов готов</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {(item.previewUrl || item.downloadUrl) && (
              <Button size="sm" variant="outline" asChild>
                <a
                  href={item.previewUrl || item.downloadUrl || "#"}
                  target="_blank"
                  rel="noreferrer"
                >
                  Открыть отчёт
                </a>
              </Button>
            )}
            {item.downloadUrl && (
              <Button size="sm" variant="outline" asChild>
                <a href={item.downloadUrl} download>
                  <Download className="h-3.5 w-3.5" />
                  Скачать
                </a>
              </Button>
            )}
          </div>
        </div>
      )}
      {item.content && (
        <pre className="max-h-[480px] overflow-auto whitespace-pre-wrap rounded-lg bg-black/40 p-3.5 text-xs select-text [scrollbar-gutter:stable] [scrollbar-width:thin] [scrollbar-color:hsl(0_0%_55%_/_0.55)_transparent] [&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar]:h-2 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-white/25 [&::-webkit-scrollbar-thumb:hover]:bg-white/45 [&::-webkit-scrollbar-track]:bg-transparent">
          {item.content}
        </pre>
      )}
    </div>
  );
}

function SfxPlanView({
  projectId,
  snapshot,
}: {
  projectId: number;
  snapshot: NodeResultSnapshot;
}) {
  const [copied, setCopied] = useState(false);
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
  });

  if (project.isLoading) return <LoadingBlock />;

  const meta = (project.data?.meta || {}) as Record<string, unknown>;
  const aiJobs = (meta.ai_jobs || {}) as Record<string, unknown>;
  const rawPlan = (aiJobs.sfx_plan || meta.sfx_plan) as
    | {
        total_duration?: number;
        events?: Array<{
          frame_number: number;
          t_start: number;
          duration: number;
          kind: string;
          prompt: string;
          gain: number;
          duck: boolean;
        }>;
      }
    | undefined;

  const events = rawPlan?.events || [];
  const totalDuration = rawPlan?.total_duration;

  const kindColors: Record<string, { bg: string; text: string; border: string }> = {
    hit: { bg: "bg-rose-500/15", text: "text-rose-300", border: "border-rose-500/30" },
    whoosh: { bg: "bg-cyan-500/15", text: "text-cyan-300", border: "border-cyan-500/30" },
    foley: { bg: "bg-emerald-500/15", text: "text-emerald-300", border: "border-emerald-500/30" },
    stinger: { bg: "bg-purple-500/15", text: "text-purple-300", border: "border-purple-500/30" },
    riser: { bg: "bg-amber-500/15", text: "text-amber-300", border: "border-amber-500/30" },
    ambience: { bg: "bg-indigo-500/15", text: "text-indigo-300", border: "border-indigo-500/30" },
    transition: { bg: "bg-sky-500/15", text: "text-sky-300", border: "border-sky-500/30" },
  };

  const copyJson = () => {
    if (!rawPlan) return;
    navigator.clipboard.writeText(JSON.stringify(rawPlan, null, 2));
    setCopied(true);
    toast.success("План звуков скопирован в буфер");
    setTimeout(() => setCopied(false), 2000);
  };

  if (!events.length) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-white/10 bg-white/[0.02] p-8 text-center">
        <Volume2 className="h-8 w-8 text-muted-foreground/60" />
        <div>
          <p className="text-sm font-medium text-foreground">План звуков ещё не составлен</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Запустите шаг «План звуков», чтобы ИИ-агент расставил эффекты по таймлайну.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pr-1 pb-4">
      {/* Сводная карточка */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-white/10 bg-gradient-to-r from-amber-500/5 via-violet-500/5 to-transparent p-4">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-500/15 text-amber-300 border border-amber-500/30">
            <Volume2 className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-foreground">План звуков (SFX)</span>
              <span className="rounded-md bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium text-emerald-300 border border-emerald-500/30">
                Готов · {events.length} событий
              </span>
            </div>
            <p className="text-xs text-muted-foreground mt-0.5">
              Хронометраж: {totalDuration ? `${totalDuration} с` : "—"} · Подготовлено для шага «Звуки (SFX)»
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" className="h-8 gap-1.5 text-xs" onClick={copyJson}>
            {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
            {copied ? "Скопировано" : "Копировать JSON"}
          </Button>
          {snapshot.items[0]?.downloadUrl ? (
            <Button size="sm" variant="outline" className="h-8 gap-1.5 text-xs" asChild>
              <a href={snapshot.items[0].downloadUrl} download>
                <Download className="h-3.5 w-3.5" />
                Скачать
              </a>
            </Button>
          ) : null}
        </div>
      </div>

      {/* Список звуковых событий */}
      <div className="flex flex-col gap-2">
        {events.map((ev, idx) => {
          const style = kindColors[ev.kind.toLowerCase()] || {
            bg: "bg-white/5",
            text: "text-zinc-300",
            border: "border-white/10",
          };
          return (
            <div
              key={idx}
              className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-xl border border-white/10 bg-white/[0.02] hover:bg-white/[0.04] p-3.5 transition-colors"
            >
              <div className="flex items-start sm:items-center gap-3">
                <span className="text-xs font-mono text-muted-foreground w-6 shrink-0">
                  #{idx + 1}
                </span>
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={cn(
                      "rounded-lg px-2.5 py-1 text-xs font-semibold uppercase tracking-wider border",
                      style.bg,
                      style.text,
                      style.border,
                    )}
                  >
                    {ev.kind}
                  </span>
                  <span className="rounded-md bg-white/5 px-2 py-0.5 text-xs font-mono text-zinc-300 border border-white/10">
                    {ev.t_start.toFixed(1)} с (+{ev.duration.toFixed(1)} с)
                  </span>
                  <span className="rounded-md bg-white/5 px-2 py-0.5 text-xs text-zinc-400 border border-white/10">
                    Кадр {ev.frame_number}
                  </span>
                  {ev.duck && (
                    <span className="rounded-md bg-blue-500/10 px-2 py-0.5 text-[11px] text-blue-300 border border-blue-500/20">
                      Duck (под голосом)
                    </span>
                  )}
                </div>
              </div>
              <div className="flex items-center justify-between sm:justify-end gap-3 min-w-0 flex-1 sm:max-w-[55%]">
                <p className="text-xs text-zinc-200 truncate font-sans" title={ev.prompt}>
                  «{ev.prompt}»
                </p>
                <span className="shrink-0 text-[11px] font-mono text-muted-foreground">
                  {Math.round(ev.gain * 100)}%
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SfxGenView({
  projectId,
  snapshot,
}: {
  projectId: number;
  snapshot: NodeResultSnapshot;
}) {
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
  });

  if (project.isLoading) return <LoadingBlock />;

  const meta = (project.data?.meta || {}) as Record<string, unknown>;
  const aiJobs = (meta.ai_jobs || {}) as Record<string, unknown>;
  const sfxPlan = (aiJobs.sfx_plan || meta.sfx_plan) as
    | {
        total_duration?: number;
        events?: Array<{
          frame_number: number;
          t_start: number;
          duration: number;
          kind: string;
          prompt: string;
          gain: number;
          duck: boolean;
        }>;
      }
    | undefined;

  const sfxFilesRecord = aiJobs.sfx_files as
    | {
        files?: Array<{
          idx: number;
          path: string;
          frame_number: number;
          t_start: number;
          duration: number;
          kind: string;
          gain: number;
          duck: boolean;
          provider: string;
          prompt?: string;
        }>;
      }
    | undefined;

  const plannedEvents = sfxPlan?.events || [];
  const generatedFiles = sfxFilesRecord?.files || [];
  const items = snapshot.items;
  const hasFiles = generatedFiles.length > 0 || items.length > 0;

  const kindColors: Record<string, { bg: string; text: string; border: string }> = {
    hit: { bg: "bg-rose-500/15", text: "text-rose-300", border: "border-rose-500/30" },
    whoosh: { bg: "bg-cyan-500/15", text: "text-cyan-300", border: "border-cyan-500/30" },
    foley: { bg: "bg-emerald-500/15", text: "text-emerald-300", border: "border-emerald-500/30" },
    stinger: { bg: "bg-purple-500/15", text: "text-purple-300", border: "border-purple-500/30" },
    riser: { bg: "bg-amber-500/15", text: "text-amber-300", border: "border-amber-500/30" },
    ambience: { bg: "bg-indigo-500/15", text: "text-indigo-300", border: "border-indigo-500/30" },
    transition: { bg: "bg-sky-500/15", text: "text-sky-300", border: "border-sky-500/30" },
  };

  if (!hasFiles) {
    return (
      <div className="flex flex-col gap-4">
        <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-white/10 bg-white/[0.02] p-8 text-center">
          <Volume2 className="h-8 w-8 text-muted-foreground/60" />
          <div>
            <p className="text-sm font-medium text-foreground">Звуки ещё не сгенерированы</p>
            <p className="mt-1 text-xs text-muted-foreground max-w-md">
              {plannedEvents.length > 0
                ? `В плане подготовлено ${plannedEvents.length} звуковых событий. Нажмите «Запустить шаг», чтобы синтезировать звуки через ElevenLabs API / локальный синтезатор.`
                : "Сначала запустите шаг «План звуков», чтобы разметить звуковые эффекты по таймлайну."}
            </p>
          </div>
        </div>

        {plannedEvents.length > 0 && (
          <div className="flex flex-col gap-2 opacity-60">
            <div className="text-xs font-semibold text-zinc-400 px-1">
              Очередь генерации по плану ({plannedEvents.length} событий):
            </div>
            {plannedEvents.map((ev, idx) => {
              const style = kindColors[ev.kind.toLowerCase()] || {
                bg: "bg-white/5",
                text: "text-zinc-300",
                border: "border-white/10",
              };
              return (
                <div
                  key={idx}
                  className="flex items-center justify-between gap-3 rounded-xl border border-white/5 bg-white/[0.01] p-3 text-xs"
                >
                  <div className="flex items-center gap-2.5">
                    <span className="font-mono text-zinc-500">#{idx + 1}</span>
                    <span
                      className={cn(
                        "rounded px-2 py-0.5 text-[11px] font-semibold uppercase border",
                        style.bg,
                        style.text,
                        style.border,
                      )}
                    >
                      {ev.kind}
                    </span>
                    <span className="text-zinc-400 font-mono">
                      {ev.t_start.toFixed(1)} с · Кадр {ev.frame_number}
                    </span>
                  </div>
                  <span className="text-zinc-400 truncate max-w-[50%]">«{ev.prompt}»</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  const displayItems = generatedFiles.length > 0
    ? generatedFiles.map((file, idx) => {
        const planEv = plannedEvents[file.idx ?? idx];
        const prompt = file.prompt || planEv?.prompt || "";
        const url = `/api/files?path=${encodeURIComponent(file.path)}`;
        return {
          idx: file.idx ?? idx,
          kind: file.kind,
          provider: file.provider,
          tStart: file.t_start,
          duration: file.duration,
          frameNumber: file.frame_number,
          gain: file.gain,
          duck: file.duck,
          prompt,
          audioUrl: url,
          fileName: file.path.split(/[/\\]/).pop() || `sfx_${idx}.mp3`,
        };
      })
    : items.map((item, idx) => {
        const planEv = plannedEvents[idx];
        const rawKind = item.label?.split("_")[2]?.replace(/\.\w+$/, "") || planEv?.kind || "sfx";
        return {
          idx,
          kind: rawKind,
          provider: "elevenlabs",
          tStart: planEv?.t_start ?? 0,
          duration: planEv?.duration ?? 0,
          frameNumber: planEv?.frame_number ?? (idx + 1),
          gain: planEv?.gain ?? 0.5,
          duck: planEv?.duck ?? false,
          prompt: planEv?.prompt || item.label || "",
          audioUrl: item.previewUrl || item.downloadUrl || "",
          fileName: item.label || `sfx_${idx}.mp3`,
        };
      });

  const providers = Array.from(new Set(displayItems.map((d) => d.provider)));
  const providerLabel = providers.includes("elevenlabs")
    ? providers.includes("local_synth")
      ? "ElevenLabs + Синтез"
      : "ElevenLabs SFX API"
    : "Локальный синтез";

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pr-1 pb-4">
      {/* Сводная карточка */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-white/10 bg-gradient-to-r from-emerald-500/5 via-violet-500/5 to-transparent p-4">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
            <Volume2 className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-foreground">Звуки сопровождения (SFX)</span>
              <span className="rounded-md bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium text-emerald-300 border border-emerald-500/30">
                Готово · {displayItems.length} файл(ов)
              </span>
            </div>
            <p className="text-xs text-muted-foreground mt-0.5">
              Провайдер: <strong className="text-zinc-200">{providerLabel}</strong> · Готовы к миксу в шаге «Сборка»
            </p>
          </div>
        </div>
      </div>

      {/* Список звуков с аудиоплеерами */}
      <div className="flex flex-col gap-2.5">
        {displayItems.map((item) => {
          const style = kindColors[item.kind.toLowerCase()] || {
            bg: "bg-white/5",
            text: "text-zinc-300",
            border: "border-white/10",
          };
          return (
            <div
              key={item.idx}
              className="flex flex-col gap-2 rounded-xl border border-white/10 bg-white/[0.02] hover:bg-white/[0.04] p-3.5 transition-colors"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2.5">
                  <span className="text-xs font-mono text-muted-foreground w-6">
                    #{item.idx + 1}
                  </span>
                  <span
                    className={cn(
                      "rounded-lg px-2.5 py-0.5 text-xs font-semibold uppercase tracking-wider border",
                      style.bg,
                      style.text,
                      style.border,
                    )}
                  >
                    {item.kind}
                  </span>
                  <span className="rounded-md bg-white/5 px-2 py-0.5 text-xs font-mono text-zinc-300 border border-white/10">
                    {item.tStart.toFixed(1)} с (+{item.duration.toFixed(1)} с)
                  </span>
                  <span className="rounded-md bg-white/5 px-2 py-0.5 text-xs text-zinc-400 border border-white/10">
                    Кадр {item.frameNumber}
                  </span>
                  {item.duck && (
                    <span className="rounded-md bg-blue-500/10 px-2 py-0.5 text-[11px] text-blue-300 border border-blue-500/20">
                      Duck
                    </span>
                  )}
                  <span className="text-[11px] font-mono text-zinc-400">
                    Громкость: {Math.round(item.gain * 100)}%
                  </span>
                </div>

                <div className="flex items-center gap-2">
                  <span
                    className={cn(
                      "rounded px-1.5 py-0.5 text-[10px] font-medium border",
                      item.provider === "elevenlabs"
                        ? "bg-violet-500/10 text-violet-300 border-violet-500/20"
                        : "bg-zinc-500/10 text-zinc-300 border-zinc-500/20",
                    )}
                  >
                    {item.provider === "elevenlabs" ? "ElevenLabs" : "Синтез"}
                  </span>
                  <Button size="sm" variant="ghost" className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground" asChild>
                    <a href={item.audioUrl} download={item.fileName} title="Скачать аудио">
                      <Download className="h-3.5 w-3.5" />
                    </a>
                  </Button>
                </div>
              </div>

              {item.prompt && (
                <p className="text-xs text-zinc-300 italic pl-8 leading-relaxed">
                  «{item.prompt}»
                </p>
              )}

              <div className="pl-8 pt-0.5 max-w-xl">
                <NodeAudioMiniPlayer
                  src={item.audioUrl}
                  label={item.fileName}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
