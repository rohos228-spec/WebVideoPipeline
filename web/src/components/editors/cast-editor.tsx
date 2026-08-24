"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Label, Working } from "@/components/ui/bits";
import { DescriptionListEditor } from "./text-editors";
import type { Project } from "@/lib/types";

/** Герои и предметы: описания правятся текстом, референсы просто показаны. */
export function CastEditor({ project }: { project: Project }) {
  const { data: assets, isLoading } = useQuery({
    queryKey: ["assets", project.id],
    queryFn: () => api.assets(project.id),
  });

  const refs = (assets ?? []).filter(
    (a) => a.kind === "hero_reference" || a.kind === "item_reference",
  );

  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <Label>Персонажи</Label>
        <DescriptionListEditor project={project} field="hero_descriptions" addLabel="персонаж" />
      </section>

      <section className="space-y-2">
        <Label>Предметы</Label>
        <DescriptionListEditor project={project} field="item_descriptions" addLabel="предмет" />
      </section>

      <section className="space-y-2">
        <Label>Референсы</Label>
        {isLoading ? (
          <Working label="читаю референсы" />
        ) : refs.length === 0 ? (
          <div className="text-[13px] text-content-faint">Ещё не сгенерированы.</div>
        ) : (
          <div className="grid gap-3 grid-cols-[repeat(auto-fill,minmax(120px,1fr))]">
            {refs.map((a) => (
              <figure key={a.id}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={a.preview_url ?? ""}
                  alt={a.kind === "hero_reference" ? "Персонаж" : "Предмет"}
                  className="w-full rounded-md border border-border bg-surface-sunken object-cover"
                  loading="lazy"
                />
                <figcaption className="mt-1 text-[10px] text-content-faint">
                  {a.kind === "hero_reference" ? "персонаж" : "предмет"}
                </figcaption>
              </figure>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
