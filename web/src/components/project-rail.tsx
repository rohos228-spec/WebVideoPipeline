"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/stage-api";
import { projectName, relativeDate } from "@/lib/format";
import type { ProjectSummary } from "@/lib/stage-types";

const DONE_STATUSES = new Set(["assembled", "published"]);

/** Левая колонка: только список роликов и кнопка «новый». Ничего больше. */
export function ProjectRail({
  currentId,
  onSelect,
  onNew,
}: {
  currentId: number | null;
  onSelect: (id: number) => void;
  onNew: () => void;
}) {
  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: api.projects });

  return (
    <nav className="flex h-full flex-col border-r border-border bg-surface-sunken">
      <div className="px-5 pt-5 pb-4">
        <button
          onClick={onNew}
          className="w-full rounded-md bg-accent px-3 py-2 text-[14px] font-medium text-content-on-accent transition-colors hover:bg-accent-hover"
        >
          Новый ролик
        </button>
      </div>

      <ul className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        {(projects ?? []).map((p) => (
          <li key={p.id}>
            <button
              onClick={() => onSelect(p.id)}
              className={`w-full rounded-md px-3 py-2.5 text-left transition-colors ${
                p.id === currentId ? "bg-surface-raised" : "hover:bg-surface"
              }`}
            >
              <div className="truncate text-[13px] text-content">{projectName(p)}</div>
              <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-content-faint">
                <StatusDot project={p} />
                {relativeDate(p.updated_at)}
              </div>
            </button>
          </li>
        ))}
        {projects?.length === 0 && (
          <li className="px-3 py-6 text-[13px] text-content-faint">Пока ни одного ролика.</li>
        )}
      </ul>
    </nav>
  );
}

function StatusDot({ project }: { project: ProjectSummary }) {
  const done = DONE_STATUSES.has(project.status);
  const bad = project.status === "failed";
  const color = bad ? "bg-danger" : done ? "bg-ok" : "bg-border-strong";
  return <span className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${color}`} />;
}
