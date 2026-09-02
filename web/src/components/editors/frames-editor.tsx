"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/stage-api";
import { seconds } from "@/lib/format";
import { AutoTextarea, Empty, Working } from "@/components/ui/bits";
import type { Frame } from "@/lib/stage-types";

/** Кадры: номер, длительность и закадровый текст — правится на месте. */
export function FramesEditor({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const { data: frames, isLoading } = useQuery({
    queryKey: ["frames", projectId],
    queryFn: () => api.frames(projectId),
  });

  const save = useMutation({
    mutationFn: ({ id, text }: { id: number; text: string }) =>
      api.patchFrame(projectId, id, { voiceover_text: text }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["frames", projectId] }),
  });

  if (isLoading) return <Working label="читаю кадры" />;
  if (!frames?.length) return <Empty>Кадров ещё нет.</Empty>;

  const total = frames.reduce((acc, f) => acc + (f.duration_seconds ?? 0), 0);

  return (
    <div>
      <div className="mb-3 text-[12px] text-content-muted">
        {frames.length} кадров · {seconds(total)} суммарно
      </div>
      <ol className="divide-y divide-border border-y border-border">
        {frames.map((f) => (
          <FrameRow key={f.id} frame={f} onSave={(text) => save.mutate({ id: f.id, text })} />
        ))}
      </ol>
    </div>
  );
}

function FrameRow({ frame, onSave }: { frame: Frame; onSave: (text: string) => void }) {
  return (
    <li className="flex gap-4 py-3">
      <div className="w-10 shrink-0 pt-0.5">
        <div className="font-mono text-[11px] text-content-faint tabular-nums">
          {String(frame.number).padStart(2, "0")}
        </div>
        <div className="font-mono text-[10px] text-content-faint tabular-nums">
          {seconds(frame.duration_seconds)}
        </div>
      </div>
      <AutoTextarea
        rows={1}
        defaultValue={frame.voiceover_text}
        onBlur={(e) => {
          if (e.target.value !== frame.voiceover_text) onSave(e.target.value);
        }}
        className="text-[14px]"
      />
    </li>
  );
}
