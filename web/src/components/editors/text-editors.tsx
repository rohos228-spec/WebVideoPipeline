"use client";

import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { saveHint, useDraft } from "@/lib/use-draft";
import { AutoTextarea, Empty } from "@/components/ui/bits";
import type { Project } from "@/lib/types";

function SaveMark({ hint }: { hint: string }) {
  if (!hint) return null;
  return <span className="text-[11px] text-content-faint">{hint}</span>;
}

/** Сценарий и закадровый текст — два поля проекта, правятся одинаково. */
export function ProjectTextEditor({
  project,
  field,
  placeholder,
}: {
  project: Project;
  field: "general_plan" | "script_text";
  placeholder: string;
}) {
  const qc = useQueryClient();
  const [value, setValue, state] = useDraft(project[field] ?? "", async (next) => {
    await api.patchProject(project.id, { [field]: next });
    qc.invalidateQueries({ queryKey: ["project", project.id] });
  });

  if (!value.trim() && state === "idle") return <Empty>Пока пусто — запустите шаг.</Empty>;

  return (
    <div className="space-y-2">
      <AutoTextarea
        serif
        value={value}
        placeholder={placeholder}
        onChange={(e) => setValue(e.target.value)}
        className="text-[15px] text-content"
      />
      <div className="flex justify-between">
        <span className="text-[11px] text-content-faint">
          {value.length.toLocaleString("ru")} знаков
        </span>
        <SaveMark hint={saveHint(state)} />
      </div>
    </div>
  );
}

/** Списки героев и предметов: одна строка — одно описание. */
export function DescriptionListEditor({
  project,
  field,
  addLabel,
}: {
  project: Project;
  field: "hero_descriptions" | "item_descriptions";
  addLabel: string;
}) {
  const qc = useQueryClient();
  const server = (project[field] ?? []) as string[];
  const [items, setItems, state] = useDraft(server, async (next) => {
    const payload: Partial<Project> = { [field]: next.filter((s) => s.trim()) };
    if (field === "hero_descriptions") payload.hero_count = next.filter((s) => s.trim()).length;
    await api.patchProject(project.id, payload);
    qc.invalidateQueries({ queryKey: ["project", project.id] });
  });

  return (
    <div className="space-y-2">
      {items.length === 0 && <Empty>Ничего не описано.</Empty>}
      {items.map((text, i) => (
        <div key={i} className="flex gap-3 border-b border-border pb-2 last:border-0">
          <span className="mt-0.5 font-mono text-[11px] text-content-faint tabular-nums">
            {String(i + 1).padStart(2, "0")}
          </span>
          <AutoTextarea
            rows={1}
            value={text}
            onChange={(e) => setItems(items.map((v, j) => (j === i ? e.target.value : v)))}
            className="text-[14px]"
          />
          <button
            onClick={() => setItems(items.filter((_, j) => j !== i))}
            className="text-content-faint hover:text-danger text-[12px] shrink-0"
            aria-label="Убрать"
          >
            ✕
          </button>
        </div>
      ))}
      <div className="flex items-center justify-between pt-1">
        <button
          onClick={() => setItems([...items, ""])}
          className="text-[13px] text-accent hover:text-accent-hover"
        >
          + {addLabel}
        </button>
        <SaveMark hint={saveHint(state)} />
      </div>
    </div>
  );
}
