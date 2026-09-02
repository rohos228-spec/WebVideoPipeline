"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/stage-api";
import { ProjectRail } from "@/components/project-rail";
import { IdeaComposer } from "@/components/idea-composer";
import { ProjectView } from "@/components/project-view";
import { credits } from "@/lib/format";
import Link from "next/link";
import { Button } from "@/components/ui/bits";
import { ChatPanel } from "@/components/chat/chat-panel";

const LAST_PROJECT_KEY = "vp.last-project";
const CHAT_KEY = "vp.chat-open";

export default function Page() {
  // null = экран новой идеи; число = открытый ролик.
  const [current, setCurrent] = useState<number | null>(null);
  const [restored, setRestored] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);

  // Чат — колонка справа, а не окно поверх: разговор об этом ролике идёт
  // рядом с самим роликом, и результат инструмента виден в стадиях сразу.
  useEffect(() => {
    try {
      setChatOpen(localStorage.getItem(CHAT_KEY) === "1");
    } catch {
      /* без хранилища — закрыт */
    }
  }, []);
  const toggleChat = () => {
    setChatOpen((v) => {
      try {
        localStorage.setItem(CHAT_KEY, v ? "0" : "1");
      } catch {
        /* см. выше */
      }
      return !v;
    });
  };

  const { data: projects } = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const { data: balance } = useQuery({ queryKey: ["balance"], queryFn: api.balance });
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: api.me });

  // Возвращаемся туда, где были: перезагрузка не должна стоить контекста.
  // ?project=N («← к ролику» из конструктора схемы) важнее сохранённого:
  // ссылка обязана открывать именно тот ролик, даже в чужом браузере.
  useEffect(() => {
    if (restored || !projects) return;
    const fromUrl = Number(new URLSearchParams(window.location.search).get("project") ?? "");
    const saved = Number(localStorage.getItem(LAST_PROJECT_KEY) ?? "");
    const want = projects.some((p) => p.id === fromUrl)
      ? fromUrl
      : projects.some((p) => p.id === saved)
        ? saved
        : null;
    if (want != null) setCurrent(want);
    setRestored(true);
  }, [projects, restored]);

  const open = (id: number | null) => {
    setCurrent(id);
    if (id === null) localStorage.removeItem(LAST_PROJECT_KEY);
    else localStorage.setItem(LAST_PROJECT_KEY, String(id));
  };

  // Три состояния, а не два. У админа кассы нет вовсе — это «∞», а не число
  // и не пустое место: пустое место читалось бы как «баланс не загрузился».
  const money = balance?.unlimited
    ? "∞"
    : balance?.tenant_id
      ? credits(balance.balance_credits)
      : null;

  return (
    <div className={`grid h-screen ${chatOpen ? "grid-cols-[240px_1fr_380px]" : "grid-cols-[240px_1fr]"}`}>
      <ProjectRail currentId={current} onSelect={open} onNew={() => open(null)} />

      <div className="flex min-h-0 flex-col">
        <header className="flex h-12 shrink-0 items-center justify-between border-b border-border px-6">
          <span className="font-display text-[15px] text-content">Видеостудия</span>
          <div className="flex items-center gap-4">
            <button
              onClick={toggleChat}
              className={`text-[12px] transition-colors hover:text-accent ${chatOpen ? "text-accent" : "text-content-faint"}`}
              title="Оркестратор: скажите, что сделать с роликом"
            >
              Оркестратор
            </button>
            <Link
              href={current === null ? "/pipeline" : `/pipeline?project=${current}`}
              className="text-[12px] text-content-faint transition-colors hover:text-accent"
              title={current === null ? "Шаблон схемы для новых роликов" : "Схема этого ролика"}
            >
              Схема
            </Link>
            {money !== null && (
              <span
                className="font-mono text-[12px] tabular-nums text-content-muted"
                title={balance?.unlimited ? "У администратора шаги не тарифицируются" : undefined}
              >
                баланс {money}
              </span>
            )}
            {me?.accounts_enabled && me.email && (
              <>
                <span className="text-[12px] text-content-faint">{me.email}</span>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={async () => {
                    await api.logout();
                    window.location.reload();
                  }}
                >
                  Выйти
                </Button>
              </>
            )}
          </div>
        </header>

        <main className="min-h-0 flex-1 overflow-y-auto">
          {current === null ? (
            <IdeaComposer onCreated={open} />
          ) : (
            <ProjectView projectId={current} onDeleted={() => open(null)} />
          )}
        </main>
      </div>

      {chatOpen && (
        <aside className="flex min-h-0 flex-col border-l border-border bg-surface-raised">
          <div className="flex h-12 shrink-0 items-center justify-between border-b border-border px-4">
            <span className="font-display text-[14px] text-content">Оркестратор</span>
            <button onClick={toggleChat} className="text-[12px] text-content-faint hover:text-content">
              закрыть
            </button>
          </div>
          <ChatPanel key={current ?? "none"} projectId={current} />
        </aside>
      )}
    </div>
  );
}
