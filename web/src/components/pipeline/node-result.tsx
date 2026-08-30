"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Empty, Working } from "@/components/ui/bits";
import type { NodeState } from "@/lib/types";

/**
 * Результат узла — коротко и по месту.
 *
 * Полные редакторы живут на листе ролика (сценарий, кадры, герои, медиа —
 * там правят). Здесь — быстрый взгляд, чтобы не прыгать между экранами,
 * решая «переделывать или нет»: первые строки текста, число кадров, сетка
 * картинок, и ссылка на стадию, где это правится целиком.
 */

const STAGE_OF_TYPE: Record<string, string> = {
  plan: "plan",
  script: "script",
  split: "frames",
  hero: "cast",
  items: "cast",
  image_prompts: "images",
  images: "images",
  animation_prompts: "videos",
  videos: "videos",
  audio: "final",
  music: "final",
  assemble: "final",
  publish: "final",
};

export function NodeResult({ projectId, type, state }: { projectId: number; type: string; state?: NodeState }) {
  const stage = STAGE_OF_TYPE[type];
  const link = stage ? (
    <Link href={`/?project=${projectId}#stage-${stage}`} className="text-[12px] text-content-faint hover:text-accent">
      открыть на листе ролика →
    </Link>
  ) : null;

  if (state !== "done" && state !== "waiting_hitl" && state !== "failed") {
    return <Empty>Узел ещё не отработал — результата пока нет.</Empty>;
  }

  return (
    <div className="space-y-2">
      <ResultBody projectId={projectId} type={type} />
      {link}
    </div>
  );
}

function ResultBody({ projectId, type }: { projectId: number; type: string }) {
  switch (type) {
    case "plan":
    case "script":
      return <TextResult projectId={projectId} field={type === "plan" ? "general_plan" : "script_text"} />;
    case "split":
    case "image_prompts":
    case "animation_prompts":
      return <FramesResult projectId={projectId} field={type === "split" ? "voiceover_text" : type === "image_prompts" ? "image_prompt" : "animation_prompt"} />;
    case "images":
    case "videos":
      return <MediaResult projectId={projectId} kind={type} />;
    case "hero":
    case "items":
    case "audio":
    case "music":
    case "assemble":
    case "publish":
      return <AssetsResult projectId={projectId} kind={type === "assemble" || type === "publish" ? "final" : type} />;
    default:
      return <Empty>У этого узла нет отдельного результата — он меняет таблицу или служебные файлы.</Empty>;
  }
}

function TextResult({ projectId, field }: { projectId: number; field: "general_plan" | "script_text" }) {
  const q = useQuery({ queryKey: ["project", projectId], queryFn: () => api.project(projectId) });
  if (q.isLoading) return <Working label="читаю" />;
  const text = q.data?.[field] ?? "";
  if (!text) return <Empty>Текст пуст.</Empty>;
  return (
    <p className="line-clamp-8 whitespace-pre-wrap font-display text-[13px] leading-relaxed text-content">{text}</p>
  );
}

function FramesResult({
  projectId,
  field,
}: {
  projectId: number;
  field: "voiceover_text" | "image_prompt" | "animation_prompt";
}) {
  const q = useQuery({ queryKey: ["frames", projectId], queryFn: () => api.frames(projectId) });
  if (q.isLoading) return <Working label="читаю кадры" />;
  const frames = q.data ?? [];
  if (frames.length === 0) return <Empty>Кадров нет.</Empty>;
  const filled = frames.filter((f) => (f[field] ?? "").toString().trim().length > 0).length;
  return (
    <div className="space-y-1">
      <p className="text-[12px] text-content-muted">
        Кадров: <span className="font-mono tabular-nums">{frames.length}</span>, заполнено:{" "}
        <span className="font-mono tabular-nums">{filled}</span>
      </p>
      <ol className="max-h-56 space-y-1 overflow-y-auto">
        {frames.slice(0, 12).map((f) => (
          <li key={f.id} className="flex gap-2 text-[12px]">
            <span className="w-5 shrink-0 font-mono text-[11px] tabular-nums text-content-faint">{f.number}</span>
            <span className="line-clamp-2 text-content-muted">{f[field] || "—"}</span>
          </li>
        ))}
        {frames.length > 12 && <li className="text-[11px] text-content-faint">…и ещё {frames.length - 12}</li>}
      </ol>
    </div>
  );
}

function MediaResult({ projectId, kind }: { projectId: number; kind: string }) {
  const k = kind === "videos" ? "videos" : "images";
  const q = useQuery({ queryKey: ["media", projectId, k], queryFn: () => api.media(projectId, k) });
  if (q.isLoading) return <Working label="читаю медиа" />;
  const items = (q.data ?? []).filter((m) => m.preview_url);
  if (items.length === 0) return <Empty>Готовых файлов нет.</Empty>;
  return (
    <div className="grid grid-cols-3 gap-1">
      {items.slice(0, 9).map((m) =>
        k === "videos" ? (
          <video key={m.frame_id} src={m.preview_url!} muted controls className="aspect-video w-full rounded-sm border border-border object-cover" />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img key={m.frame_id} src={m.preview_url!} alt={`кадр ${m.number}`} className="aspect-video w-full rounded-sm border border-border object-cover" />
        ),
      )}
      {items.length > 9 && <span className="self-center text-[11px] text-content-faint">+{items.length - 9}</span>}
    </div>
  );
}

function AssetsResult({ projectId, kind }: { projectId: number; kind: string }) {
  const q = useQuery({ queryKey: ["assets", projectId, kind], queryFn: () => api.assetsOf(projectId, kind) });
  if (q.isLoading) return <Working label="читаю файлы" />;
  const items = q.data ?? [];
  if (items.length === 0) return <Empty>Файлов нет.</Empty>;
  const images = items.filter((a) => a.preview_url && /image|hero|item/.test(a.kind));
  const rest = items.filter((a) => !images.includes(a));
  return (
    <div className="space-y-2">
      {images.length > 0 && (
        <div className="grid grid-cols-3 gap-1">
          {images.slice(0, 9).map((a) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img key={a.id} src={a.preview_url!} alt={a.label ?? a.kind} className="aspect-square w-full rounded-sm border border-border object-cover" />
          ))}
        </div>
      )}
      {rest.length > 0 && (
        <ul className="space-y-0.5">
          {rest.slice(0, 8).map((a) => (
            <li key={a.id} className="flex items-center gap-2 text-[12px] text-content-muted">
              <span className="font-mono text-[10px] text-content-faint">{a.kind}</span>
              <span className="truncate">{a.label ?? a.path.split("/").pop()}</span>
              {a.preview_url && (
                <a href={a.preview_url} target="_blank" rel="noreferrer" className="shrink-0 text-[11px] text-content-faint hover:text-accent">
                  открыть
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
