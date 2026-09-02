"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/stage-api";
import { Empty, Label, Working } from "@/components/ui/bits";

/** Финал: готовый ролик и озвучка. Здесь уже нечего править — только смотреть. */
export function FinalView({ projectId, slug }: { projectId: number; slug: string }) {
  const { data: assets, isLoading } = useQuery({
    queryKey: ["assets", projectId],
    queryFn: () => api.assets(projectId),
  });

  if (isLoading) return <Working label="ищу файлы" />;

  const video = (assets ?? []).find((a) => a.kind === "final_video");
  const voice = (assets ?? []).find((a) => a.kind === "audio");

  if (!video) return <Empty>Ролик ещё не собран.</Empty>;

  return (
    <div className="space-y-4">
      <video
        src={video.preview_url ?? ""}
        controls
        playsInline
        className="w-full max-w-[320px] rounded-lg border border-border bg-surface-sunken"
      />
      <div className="flex flex-wrap items-center gap-4">
        <a
          href={video.preview_url ?? "#"}
          download={`${slug}.mp4`}
          className="text-[13px] text-accent hover:text-accent-hover"
        >
          Скачать ролик
        </a>
        {voice && (
          <a
            href={voice.preview_url ?? "#"}
            download
            className="text-[13px] text-content-muted hover:text-content"
          >
            Скачать озвучку
          </a>
        )}
      </div>
      {voice && (
        <div className="space-y-2">
          <Label>Озвучка</Label>
          <audio src={voice.preview_url ?? ""} controls className="w-full max-w-[420px]" />
        </div>
      )}
    </div>
  );
}
