"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Chip, Empty, Working } from "@/components/ui/bits";
import type { StagePrompt } from "@/lib/types";

/**
 * Правка промта шага — прямо в стадии, где этот промт работает.
 *
 * Промт задаёт всё, что модель делает на шаге. До сих пор он лежал файлом на
 * диске узла, то есть правил его только владелец машины по ssh: в интерфейсе
 * ручки не было вовсе, хотя в API она была всегда.
 *
 * **Сохранение — кнопкой, а не автосейвом.** Тексты ролика правятся
 * черновиком (`useDraft`) и это верно: там правка — основное занятие, а
 * кнопка у каждого абзаца была бы шумом. С промтом наоборот: он меняет
 * поведение генерации для всех будущих прогонов, и уезжать на сервер от
 * случайного нажатия не должен. Отсюда явное «Сохранить» и видимая пометка
 * о несохранённом.
 */
export function PromptEditor({ prompts, projectId }: { prompts: StagePrompt[]; projectId: number }) {
  const [step, setStep] = useState(prompts[0]?.step ?? "");

  if (prompts.length === 0) {
    return <Empty>У этого шага нет промта — он не обращается к модели.</Empty>;
  }

  return (
    <div>
      {prompts.length > 1 && (
        <div className="mb-3 flex flex-wrap gap-1">
          {prompts.map((p) => (
            <button
              key={p.step}
              onClick={() => setStep(p.step)}
              className={`rounded-sm px-2 py-1 text-[12px] transition-colors ${
                p.step === step
                  ? "bg-surface-sunken text-content"
                  : "text-content-faint hover:text-content-muted"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      )}
      <OnePrompt key={step} step={step} projectId={projectId} />
    </div>
  );
}

function OnePrompt({ step, projectId }: { step: string; projectId: number }) {
  const qc = useQueryClient();
  const [name, setName] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  // Какой файл шаг возьмёт на самом деле. Вариантов может быть несколько, и
  // без этого пользователь правил бы не тот, что применяется.
  const resolved = useQuery({
    queryKey: ["prompt-resolve", step, projectId],
    queryFn: () => api.promptResolve(step, projectId),
  });

  const files = useQuery({
    queryKey: ["prompt-files", step],
    queryFn: () => api.promptFiles(step),
  });

  const active = name ?? resolved.data?.name ?? null;

  const content = useQuery({
    queryKey: ["prompt-content", step, active],
    queryFn: () => api.promptContent(step, active as string),
    enabled: Boolean(active),
  });

  const save = useMutation({
    mutationFn: (text: string) => api.savePrompt(step, active as string, text),
    onSuccess: () => {
      setDraft(null);
      qc.invalidateQueries({ queryKey: ["prompt-content", step, active] });
      qc.invalidateQueries({ queryKey: ["prompt-history", step, active] });
      qc.invalidateQueries({ queryKey: ["prompt-resolve", step, projectId] });
      toast.success("Промт сохранён — следующий прогон пойдёт по нему");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  if (resolved.isLoading || files.isLoading) return <Working label="читаю промт" />;

  if (resolved.isError || files.isError) {
    return (
      <div className="space-y-2">
        <p className="text-[13px] text-danger">Промт не читается: {errorText(resolved.error ?? files.error)}</p>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            resolved.refetch();
            files.refetch();
          }}
        >
          Ещё раз
        </Button>
      </div>
    );
  }

  if (!active) return <Empty>Для этого шага промтов не заведено.</Empty>;

  const server = content.data?.content ?? "";
  const value = draft ?? server;
  const dirty = draft !== null && draft !== server;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          {(files.data ?? []).map((f) => (
            <button
              key={f.name}
              onClick={() => {
                setName(f.name);
                setDraft(null);
              }}
              className={`font-mono text-[11px] ${
                f.name === active ? "text-content" : "text-content-faint hover:text-content-muted"
              }`}
            >
              {f.name}
            </button>
          ))}
          {resolved.data && resolved.data.name === active && (
            <Chip tone="ok">{resolved.data.source_label}</Chip>
          )}
        </div>
        <button
          onClick={() => setShowHistory((v) => !v)}
          className="text-[12px] text-content-faint hover:text-accent"
        >
          {showHistory ? "скрыть историю" : "история"}
        </button>
      </div>

      {showHistory && <History step={step} name={active} onRestored={() => setDraft(null)} />}

      {content.isLoading ? (
        <Working label="читаю текст" />
      ) : (
        <textarea
          value={value}
          onChange={(e) => setDraft(e.target.value)}
          spellCheck={false}
          rows={16}
          className="w-full resize-y rounded-sm border border-border bg-surface-sunken p-3 font-mono text-[12px] leading-relaxed text-content focus:border-border-strong focus:outline-none"
        />
      )}

      {/* Сказать прямо: правка общая. Промт живёт в библиотеке, а не в
          ролике, и человек, правящий его «под этот ролик», обязан понимать,
          что меняет и все следующие. Молчать об этом — обещать переопре-
          деление, которого нет. */}
      {projectId > 0 && (
        <p className="text-[11px] text-content-faint">
          Правка общая: промт лежит в библиотеке и применится ко всем роликам, а не только к этому.
        </p>
      )}

      <div className="flex items-center justify-between">
        <span className="text-[11px] text-content-faint">
          {value.length.toLocaleString("ru")} знаков
          {dirty && " · не сохранено"}
        </span>
        <div className="flex gap-2">
          {dirty && (
            <Button size="sm" variant="ghost" onClick={() => setDraft(null)}>
              Отменить
            </Button>
          )}
          <Button
            size="sm"
            variant={dirty ? "primary" : "secondary"}
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate(value)}
          >
            {save.isPending ? "Сохраняю…" : "Сохранить"}
          </Button>
        </div>
      </div>
    </div>
  );
}

/**
 * История версий и откат.
 *
 * Промт правят наощупь, пробуя формулировки, и предыдущая редакция сплошь и
 * рядом оказывается лучше. Сервер пишет версию на каждое сохранение — здесь
 * она становится досягаемой.
 */
function History({
  step,
  name,
  onRestored,
}: {
  step: string;
  name: string;
  onRestored: () => void;
}) {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["prompt-history", step, name],
    queryFn: () => api.promptHistory(step, name),
  });

  const restore = useMutation({
    mutationFn: (versionId: string) => api.restorePromptVersion(step, name, versionId),
    onSuccess: () => {
      onRestored();
      qc.invalidateQueries({ queryKey: ["prompt-content", step, name] });
      qc.invalidateQueries({ queryKey: ["prompt-history", step, name] });
      toast.success("Версия восстановлена");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  if (list.isLoading) return <Working label="читаю историю" />;
  if (list.isError) return <p className="text-[12px] text-danger">История не читается.</p>;

  const versions = list.data ?? [];
  if (versions.length === 0) {
    return (
      <p className="rounded-sm bg-surface-sunken px-3 py-2 text-[12px] text-content-faint">
        Версий пока нет — они появляются при сохранении.
      </p>
    );
  }

  return (
    <ul className="divide-y divide-border rounded-sm bg-surface-sunken px-3">
      {versions.map((v) => (
        <li key={v.id} className="flex items-center justify-between gap-3 py-2">
          <span className="min-w-0 flex-1 truncate text-[12px] text-content-muted">
            {v.label || "без пометки"}
          </span>
          <span className="shrink-0 font-mono text-[11px] tabular-nums text-content-faint">
            {when(v.saved_at)}
          </span>
          <button
            onClick={() => restore.mutate(v.id)}
            disabled={restore.isPending}
            className="shrink-0 text-[12px] text-accent hover:text-accent-hover disabled:text-content-faint"
          >
            вернуть
          </button>
        </li>
      ))}
    </ul>
  );
}

function when(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString("ru", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : "неизвестная ошибка";
}
