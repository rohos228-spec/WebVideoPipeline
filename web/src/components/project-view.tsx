"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { credits, projectName } from "@/lib/format";
import { saveHint, useDraft } from "@/lib/use-draft";
import { AutoTextarea, Working } from "@/components/ui/bits";
import { ProjectSettings } from "@/components/project-settings";
import { StageFlow } from "@/components/stage-flow";

/** Экран ролика: идея сверху, семь шагов под ней. */
export function ProjectView({ projectId, onDeleted }: { projectId: number; onDeleted: () => void }) {
  const qc = useQueryClient();
  const { data: project, isLoading } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.project(projectId),
  });
  const { data: stages } = useQuery({
    queryKey: ["stages", projectId],
    queryFn: () => api.stages(projectId),
  });

  const [title, setTitle, titleState] = useDraft(project?.title ?? "", async (next) => {
    await api.patchProject(projectId, { title: next });
    qc.invalidateQueries({ queryKey: ["projects"] });
  });

  const [topic, setTopic, topicState] = useDraft(project?.topic ?? "", async (next) => {
    await api.patchProject(projectId, { topic: next });
    qc.invalidateQueries({ queryKey: ["project", projectId] });
  });

  if (isLoading || !project) {
    return (
      <div className="px-10 py-10">
        <Working label="открываю ролик" />
      </div>
    );
  }

  const remove = async () => {
    if (!confirm(`Удалить «${projectName(project)}» со всеми файлами?`)) return;
    try {
      await api.deleteProject(projectId);
      qc.invalidateQueries({ queryKey: ["projects"] });
      onDeleted();
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  return (
    <div className="mx-auto max-w-[760px] px-10 py-10">
      <header className="pb-6">
        <div className="flex items-start justify-between gap-6">
          <AutoTextarea
            serif
            rows={1}
            value={title}
            onChange={(e) => setTitle(e.target.value.replace(/\n/g, ""))}
            className="text-[28px] leading-tight tracking-[-0.02em] text-content"
            placeholder="Название ролика"
          />
          <button
            onClick={remove}
            className="mt-2 shrink-0 text-[12px] text-content-faint hover:text-danger"
          >
            удалить
          </button>
        </div>
        <div className="mt-1 flex gap-3 text-[11px] text-content-faint">
          <span>{saveHint(titleState)}</span>
        </div>

        <div className="mt-4">
          <AutoTextarea
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="Идея ролика: место, герой, что происходит"
            className="text-[14px] text-content-muted"
            rows={2}
          />
          <div className="flex justify-between text-[11px] text-content-faint">
            <span>идея — из неё пишется сценарий</span>
            <span>{saveHint(topicState)}</span>
          </div>
        </div>

        {stages && stages.remaining_micro > 0 && (
          <div className="mt-4 text-[12px] text-content-muted">
            Осталось шагов на{" "}
            <span className="font-mono tabular-nums">{credits(stages.remaining_credits)}</span>
          </div>
        )}
      </header>

      <ProjectSettings project={project} />
      <StageFlow project={project} />
    </div>
  );
}
