"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { titleFromIdea } from "@/lib/format";
import { Button } from "@/components/ui/button";

const EXAMPLE =
  "Ночной обмен в последнем вагоне метро, 03:40. Курьер держит на коленях конверт, которого по документам не существует…";

/** Первый экран: чистый лист под идею. Дальше вся студия строится из неё. */
export function IdeaComposer({ onCreated }: { onCreated: (id: number) => void }) {
  const [idea, setIdea] = useState("");
  const qc = useQueryClient();

  const create = useMutation({
    mutationFn: async (text: string) => {
      const project = await api.createProject(titleFromIdea(text));
      await api.patchProject(project.id, { topic: text.trim() });
      return project;
    },
    onSuccess: (project) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      onCreated(project.id);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <div className="mx-auto flex min-h-full max-w-[680px] flex-col justify-center px-8 py-16">
      <h1 className="font-display text-[38px] leading-tight text-content">О чём ролик?</h1>
      <p className="mt-3 text-[15px] text-content-muted">
        Опишите идею своими словами — место, героя, что происходит. Остальное студия разложит на
        шаги, и каждый вы утвердите отдельно.
      </p>

      <textarea
        autoFocus
        value={idea}
        onChange={(e) => setIdea(e.target.value)}
        placeholder={EXAMPLE}
        rows={7}
        className="mt-8 w-full resize-none rounded-lg border border-border bg-surface-raised p-5 font-display text-[16px] leading-relaxed text-content outline-none placeholder:text-content-faint focus-visible:border-accent"
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && idea.trim()) create.mutate(idea);
        }}
      />

      <div className="mt-5 flex items-center gap-4">
        <Button
          variant="primary"
          disabled={!idea.trim() || create.isPending}
          onClick={() => create.mutate(idea)}
        >
          {create.isPending ? "Создаю…" : "Начать"}
        </Button>
        <span className="text-[12px] text-content-faint">
          Пока ничего не тратится — цена появится на первом шаге.
        </span>
      </div>
    </div>
  );
}
