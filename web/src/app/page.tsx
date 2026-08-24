"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ProjectRail } from "@/components/project-rail";
import { IdeaComposer } from "@/components/idea-composer";
import { ProjectView } from "@/components/project-view";
import { credits } from "@/lib/format";

const LAST_PROJECT_KEY = "vp.last-project";

export default function Page() {
  // null = экран новой идеи; число = открытый ролик.
  const [current, setCurrent] = useState<number | null>(null);
  const [restored, setRestored] = useState(false);

  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const { data: balance } = useQuery({ queryKey: ["balance"], queryFn: api.balance });

  // Возвращаемся туда, где были: перезагрузка не должна стоить контекста.
  useEffect(() => {
    if (restored || !projects) return;
    const saved = Number(localStorage.getItem(LAST_PROJECT_KEY) ?? "");
    const exists = projects.some((p) => p.id === saved);
    if (exists) setCurrent(saved);
    setRestored(true);
  }, [projects, restored]);

  const open = (id: number | null) => {
    setCurrent(id);
    if (id === null) localStorage.removeItem(LAST_PROJECT_KEY);
    else localStorage.setItem(LAST_PROJECT_KEY, String(id));
  };

  const showBalance = Boolean(balance?.tenant_id);

  return (
    <div className="grid h-screen grid-cols-[240px_1fr]">
      <ProjectRail currentId={current} onSelect={open} onNew={() => open(null)} />

      <div className="flex min-h-0 flex-col">
        <header className="flex h-12 shrink-0 items-center justify-between border-b border-border px-6">
          <span className="font-display text-[15px] text-content">Видеостудия</span>
          {showBalance && (
            <span className="font-mono text-[12px] tabular-nums text-content-muted">
              баланс {credits(balance!.balance_credits)}
            </span>
          )}
        </header>

        <main className="min-h-0 flex-1 overflow-y-auto">
          {current === null ? (
            <IdeaComposer onCreated={open} />
          ) : (
            <ProjectView projectId={current} onDeleted={() => open(null)} />
          )}
        </main>
      </div>
    </div>
  );
}
