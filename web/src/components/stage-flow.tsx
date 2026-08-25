"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, subscribeProject } from "@/lib/api";
import { StageCard } from "@/components/stage-card";
import { ProjectTextEditor } from "@/components/editors/text-editors";
import { FramesEditor } from "@/components/editors/frames-editor";
import { MediaEditor } from "@/components/editors/media-editor";
import { CastEditor } from "@/components/editors/cast-editor";
import { FinalView } from "@/components/editors/final-view";
import { PromptEditor } from "@/components/editors/prompt-editor";
import { Working } from "@/components/ui/bits";
import type { Project, Stage, StageId } from "@/lib/types";

/** Семь шагов проекта сверху вниз. Всё живое обновление — здесь. */
export function StageFlow({ project }: { project: Project }) {
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["stages", project.id],
    queryFn: () => api.stages(project.id),
    // Пока шаг идёт, сервер — единственный источник правды о прогрессе.
    refetchInterval: (q) => (q.state.data?.stage_run ? 4_000 : false),
  });

  // Живые события проекта: любое изменение статуса перечитывает экран.
  useEffect(() => {
    const refresh = () => {
      qc.invalidateQueries({ queryKey: ["stages", project.id] });
      qc.invalidateQueries({ queryKey: ["project", project.id] });
      qc.invalidateQueries({ queryKey: ["frames", project.id] });
      qc.invalidateQueries({ queryKey: ["media", project.id] });
      qc.invalidateQueries({ queryKey: ["assets", project.id] });
    };
    return subscribeProject(project.id, refresh);
  }, [project.id, qc]);

  const run = useMutation({
    mutationFn: (stage: StageId) => api.runStage(project.id, stage),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["stages", project.id] }),
    onError: (e: Error) => toast.error(e.message),
  });

  const stop = useMutation({
    mutationFn: () => api.stopStage(project.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["stages", project.id] }),
    onError: (e: Error) => toast.error(e.message),
  });

  if (isLoading || !data) return <Working label="читаю шаги" />;

  const needsIdea = !project.topic?.trim();
  // Пока один шаг идёт, запуск соседнего вытеснил бы его на полпути.
  const somethingRuns = data.stages.some((s) => s.state === "running");

  return (
    <div>
      {needsIdea && (
        <p className="mb-6 rounded-md border border-warn bg-warn-muted px-4 py-3 text-[13px] text-warn">
          Сначала опишите идею ролика выше — без неё сценарий писать не из чего.
        </p>
      )}
      {data.stages.map((stage, i) => (
        <StageCard
          key={stage.id}
          index={i + 1}
          stage={stage}
          defaultOpen={stage.state === "running" || stage.state === "ready" || isLast(data.stages, i)}
          busy={run.isPending || needsIdea || somethingRuns}
          onRun={() => run.mutate(stage.id)}
          onStop={() => stop.mutate()}
        >
          <StageBody stage={stage} project={project} />
          <StagePrompts stage={stage} projectId={project.id} />
        </StageCard>
      ))}
    </div>
  );
}

/** Последний завершённый шаг раскрыт: обычно на него и смотрят. */
function isLast(stages: Stage[], i: number): boolean {
  const lastDone = stages.map((s) => s.state).lastIndexOf("done");
  return i === lastDone && !stages.some((s) => s.state === "running");
}

/**
 * Промты стадии — под результатом, за отдельным раскрытием.
 *
 * Раскрытием, потому что порядок работы обратный порядку важности: смотрят на
 * результат, а к промту идут, когда результат не устроил. Развёрнутый по
 * умолчанию, он оттеснял бы то, ради чего экран открыли.
 *
 * Состав стадии приходит с сервера (`stage.prompts`): за одной карточкой
 * стоит от нуля до шести папок промтов, и знать это фронт сам не может.
 */
function StagePrompts({ stage, projectId }: { stage: Stage; projectId: number }) {
  const [open, setOpen] = useState(false);
  const prompts = stage.prompts ?? [];
  if (prompts.length === 0) return null;

  return (
    <div className="mt-5 border-t border-border pt-3">
      <button
        onClick={() => setOpen((v) => !v)}
        className="text-[12px] text-content-faint hover:text-accent"
      >
        {open ? "скрыть промт" : `промт шага${prompts.length > 1 ? ` (${prompts.length})` : ""}`}
      </button>
      {open && (
        <div className="rise mt-3">
          <PromptEditor prompts={prompts} projectId={projectId} />
        </div>
      )}
    </div>
  );
}

function StageBody({ stage, project }: { stage: Stage; project: Project }) {
  switch (stage.editor) {
    case "plan":
      return (
        <ProjectTextEditor
          project={project}
          field="general_plan"
          placeholder="Сценарий: крючок, развитие, финал"
        />
      );
    case "script":
      return (
        <ProjectTextEditor
          project={project}
          field="script_text"
          placeholder="Текст, который прочитает голос"
        />
      );
    case "frames":
      return <FramesEditor projectId={project.id} />;
    case "cast":
      return <CastEditor project={project} />;
    case "images":
      return <MediaEditor projectId={project.id} kind="images" />;
    case "videos":
      return <MediaEditor projectId={project.id} kind="videos" />;
    case "final":
      return <FinalView projectId={project.id} slug={project.slug} />;
    default:
      return null;
  }
}
