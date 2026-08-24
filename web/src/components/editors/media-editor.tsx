"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { AutoTextarea, Empty, Working } from "@/components/ui/bits";
import type { MediaFrame } from "@/lib/types";

/**
 * Сетка кадров: превью + промт, которым оно сделано.
 *
 * Картинки и видео устроены одинаково — отличается вид превью и поле
 * промта, поэтому это один компонент с двумя режимами, а не два похожих.
 */
export function MediaEditor({ projectId, kind }: { projectId: number; kind: "images" | "videos" }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["media", projectId, kind],
    queryFn: () => api.media(projectId, kind),
  });

  const field = kind === "images" ? "image_prompt" : "animation_prompt";
  const save = useMutation({
    mutationFn: ({ id, text }: { id: number; text: string }) =>
      api.patchFrame(projectId, id, { [field]: text }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["media", projectId, kind] }),
  });

  if (isLoading) return <Working label="читаю кадры" />;
  if (!data?.length) return <Empty>Пока нечего показать.</Empty>;

  const ready = data.filter((f) => f.preview_url).length;

  return (
    <div className="space-y-3">
      <div className="text-[12px] text-content-muted">
        готово {ready} из {data.length}
      </div>
      <div className="grid gap-3 grid-cols-[repeat(auto-fill,minmax(150px,1fr))]">
        {data.map((f) => (
          <MediaCell
            key={f.frame_id}
            frame={f}
            kind={kind}
            prompt={(kind === "images" ? f.image_prompt : f.animation_prompt) ?? ""}
            onSave={(text) => save.mutate({ id: f.frame_id, text })}
          />
        ))}
      </div>
    </div>
  );
}

function MediaCell({
  frame,
  kind,
  prompt,
  onSave,
}: {
  frame: MediaFrame;
  kind: "images" | "videos";
  prompt: string;
  onSave: (text: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <figure className="group">
      <button
        onClick={() => setOpen((v) => !v)}
        className="block w-full overflow-hidden rounded-md border border-border bg-surface-sunken aspect-[9/16] focus-visible:border-accent"
        title={`Кадр ${frame.number} — промт`}
      >
        {frame.preview_url ? (
          kind === "images" ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={frame.preview_url}
              alt={`Кадр ${frame.number}`}
              className="h-full w-full object-cover"
              loading="lazy"
            />
          ) : (
            <video
              src={frame.preview_url}
              className="h-full w-full object-cover"
              muted
              loop
              playsInline
              onMouseEnter={(e) => void e.currentTarget.play().catch(() => {})}
              onMouseLeave={(e) => e.currentTarget.pause()}
            />
          )
        ) : (
          <span className="flex h-full items-center justify-center text-[11px] text-content-faint">
            нет
          </span>
        )}
      </button>
      <figcaption className="mt-1 flex items-baseline justify-between">
        <span className="font-mono text-[10px] text-content-faint tabular-nums">
          {String(frame.number).padStart(2, "0")}
        </span>
        <button
          onClick={() => setOpen((v) => !v)}
          className="text-[11px] text-content-faint hover:text-accent"
        >
          {open ? "скрыть промт" : "промт"}
        </button>
      </figcaption>
      {open && (
        <AutoTextarea
          rows={4}
          defaultValue={prompt}
          onBlur={(e) => {
            if (e.target.value !== prompt) onSave(e.target.value);
          }}
          className="rise mt-1 rounded-md border border-border bg-surface-raised p-2 font-mono text-[11px] leading-snug"
        />
      )}
    </figure>
  );
}
