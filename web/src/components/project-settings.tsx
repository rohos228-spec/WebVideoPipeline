"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Label } from "@/components/ui/bits";
import type { Choice, Project } from "@/lib/types";

/**
 * Параметры ролика: формат, модели, разрешение.
 *
 * Свёрнуты по умолчанию — у них есть рабочие значения, и человек, который
 * пишет идею, не должен сперва выбирать генератор картинок.
 */
export function ProjectSettings({ project }: { project: Project }) {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const { data: options } = useQuery({
    queryKey: ["options"],
    queryFn: api.options,
    staleTime: 10 * 60_000,
    enabled: open,
  });

  const save = useMutation({
    mutationFn: (body: Partial<Project>) => api.patchProject(project.id, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project", project.id] }),
  });

  const imageResolutions =
    options?.image_resolutions_by_generator?.[project.image_generator ?? ""] ?? [];

  return (
    <div className="border-b border-border py-3">
      <button
        onClick={() => setOpen((v) => !v)}
        className="text-[12px] text-content-faint hover:text-accent"
      >
        {open ? "скрыть параметры" : "параметры ролика"}
      </button>

      {open && options && (
        <div className="rise mt-4 grid gap-4 sm:grid-cols-2">
          <Select
            label="Формат"
            value={project.aspect_ratio}
            choices={options.aspect_ratios}
            onChange={(v) => save.mutate({ aspect_ratio: v })}
          />
          <Select
            label="Генератор картинок"
            value={project.image_generator}
            choices={options.image_generators}
            onChange={(v) => save.mutate({ image_generator: v })}
          />
          <Select
            label="Разрешение картинок"
            value={project.image_resolution}
            choices={options.image_resolutions.filter(
              (c) => imageResolutions.length === 0 || imageResolutions.includes(c.id),
            )}
            onChange={(v) => save.mutate({ image_resolution: v })}
          />
          <Select
            label="Генератор видео"
            value={project.video_generator}
            choices={options.video_generators}
            onChange={(v) => save.mutate({ video_generator: v })}
          />
          <Select
            label="Разрешение видео"
            value={project.video_resolution}
            choices={options.video_resolutions}
            onChange={(v) => save.mutate({ video_resolution: v })}
          />
          <Select
            label="Персонажи"
            value={project.hero_mode}
            choices={[
              { id: "auto", label: "Решает ИИ" },
              { id: "hero", label: "С персонажем" },
              { id: "no_hero", label: "Без персонажа" },
            ]}
            onChange={(v) => save.mutate({ hero_mode: v })}
          />
        </div>
      )}
    </div>
  );
}

function Select({
  label,
  value,
  choices,
  onChange,
}: {
  label: string;
  value: string | null;
  choices: Choice[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <Label>{label}</Label>
      <select
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded-md border border-border bg-surface-raised px-2.5 py-1.5 text-[13px] outline-none focus-visible:border-accent"
      >
        {choices.map((c) => (
          <option key={c.id} value={c.id}>
            {c.label}
          </option>
        ))}
      </select>
    </label>
  );
}
