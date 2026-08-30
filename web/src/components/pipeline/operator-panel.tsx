"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Chip, Empty, Label, Working } from "@/components/ui/bits";
import { Toggle } from "./step-params";
import type { OperatorEmitKind, OperatorOutputMode, OperatorResolve, OperatorRole } from "@/lib/types";

/**
 * Узел «Работа с GPT» — универсальный шаг над файлами со стрелок.
 *
 * Роль решает, что модель делает с входом (помогает, проверяет, переделывает,
 * достаёт данные, сравнивает, пропускает или нет); «Проверка» — режим, в
 * котором узел смотрит результат соседа по критериям и ведёт по стрелкам
 * «ок» / «не ок». Конфиг лежит в `meta.excel_gpt_nodes[nodeKey]` и
 * правится своей ручкой, а не через граф: граф говорит, что узел есть,
 * конфиг — как он работает. Список файлов — живой, с диска: сюда падают
 * результаты соседей, пока те идут.
 */

const ROLES: { id: OperatorRole; label: string; hint: string }[] = [
  { id: "assist", label: "Помощник", hint: "делает, что сказано в промте, с файлами на входе" },
  { id: "review", label: "Ок / не ок", hint: "оценивает вход и ведёт по стрелкам «ок» и «не ок»" },
  { id: "transform", label: "Переделывает", hint: "переписывает вход и отдаёт результат дальше" },
  { id: "extract", label: "Достаёт данные", hint: "вытаскивает из входа структуру" },
  { id: "compare", label: "Сравнивает", hint: "сопоставляет несколько входов" },
  { id: "gate", label: "Шлагбаум", hint: "пропускает дальше только при «ок»" },
];

const OUTPUT_MODES: { id: OperatorOutputMode; label: string }[] = [
  { id: "text", label: "Текст ответа (gpt_reply.txt)" },
  { id: "project_file", label: "Таблица проекта (Excel)" },
  { id: "sidecar", label: "Отдельный .txt рядом" },
];

const EMIT_KINDS: { id: OperatorEmitKind; label: string }[] = [
  { id: "result", label: "Результат" },
  { id: "reply_txt", label: "Текст ответа" },
  { id: "analysis", label: "Разбор проверки" },
  { id: "inputs", label: "Вход как есть" },
];

export function OperatorPanel({
  projectId,
  nodeKey,
  onLabel,
}: {
  projectId: number;
  nodeKey: string;
  /** Смена роли меняет и подпись узла — сообщить графу. */
  onLabel?: (label: string) => void;
}) {
  const qc = useQueryClient();
  const key = ["operator", projectId, nodeKey];
  const fileInput = useRef<HTMLInputElement>(null);
  const agentInput = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<string | null>(null);

  const resolve = useQuery({
    queryKey: key,
    queryFn: () => api.operatorResolve(projectId, nodeKey),
    refetchInterval: 8_000,
  });
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: key });
    qc.invalidateQueries({ queryKey: ["graph", projectId] });
  };

  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.operatorPatch(projectId, nodeKey, body),
    onSuccess: (r) => {
      qc.setQueryData(key, r);
      invalidate();
      if (r.label) onLabel?.(r.label);
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const uploadFile = useMutation({
    mutationFn: (f: File) => api.operatorUpload(projectId, nodeKey, f),
    onSuccess: (r) => {
      invalidate();
      toast.success(`Загружено: ${r.fileName}`);
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const uploadAgent = useMutation({
    mutationFn: (f: File) => api.operatorCheckAgentUpload(projectId, nodeKey, f),
    onSuccess: () => {
      invalidate();
      toast.success("Агент проверки загружен");
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const clearAgent = useMutation({
    mutationFn: () => api.operatorCheckAgentClear(projectId, nodeKey),
    onSuccess: () => {
      invalidate();
      toast.success("Агент сброшен — критерии снова из промта источника");
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const showPreview = useMutation({
    mutationFn: () => api.operatorCheckPromptPreview(projectId, nodeKey),
    onSuccess: (r) => setPreview(r.text ?? r.prompt ?? r.preview ?? ""),
    onError: (e: Error) => toast.error(e.message),
  });

  if (resolve.isLoading) return <Working label="сверяю узел с файлами" />;
  if (resolve.isError || !resolve.data) {
    return (
      <div className="space-y-2">
        <p className="text-[12px] text-danger">Узел не читается: {resolve.error?.message}</p>
        <Button size="sm" variant="secondary" onClick={() => resolve.refetch()}>
          Ещё раз
        </Button>
      </div>
    );
  }

  const r: OperatorResolve = resolve.data;
  const busy = patch.isPending || uploadFile.isPending || uploadAgent.isPending || clearAgent.isPending;
  const branching = r.checkMode || r.role === "review" || r.role === "gate" || r.role === "compare";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-1.5">
        <Chip tone={r.consistent ? "ok" : "warn"}>{r.consistent ? "вход в порядке" : "рассинхрон входа"}</Chip>
        <Chip tone="neutral">файлов: {r.okFileCount}</Chip>
        {branching && (
          <Chip tone={r.branching.hasPass && r.branching.hasFail ? "ok" : "warn"}>
            ветки: {r.branching.hasPass ? "ок" : "—"} / {r.branching.hasFail ? "не ок" : "—"}
          </Chip>
        )}
        {r.branching.verdict && <Chip tone={r.branching.verdict === "pass" ? "ok" : "danger"}>вердикт: {r.branching.verdict}</Chip>}
      </div>

      {r.errors.length > 0 && (
        <ul className="space-y-0.5 text-[12px] text-danger">
          {r.errors.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      )}
      {r.warnings.length > 0 && (
        <ul className="space-y-0.5 text-[12px] text-content-faint">
          {r.warnings.map((w, i) => (
            <li key={i}>⚠ {w}</li>
          ))}
        </ul>
      )}

      <div>
        <Label>Роль</Label>
        <select
          value={r.role}
          disabled={busy}
          onChange={(e) => patch.mutate({ role: e.target.value })}
          className="mt-1.5 w-full rounded-sm border border-border bg-surface-raised px-2 py-1 text-[13px] text-content"
        >
          {ROLES.map((x) => (
            <option key={x.id} value={x.id}>
              {x.label}
            </option>
          ))}
        </select>
        <p className="mt-1 text-[11px] text-content-faint">{ROLES.find((x) => x.id === r.role)?.hint}</p>
      </div>

      <div className="space-y-2">
        <Toggle
          label="Проверка"
          hint="узел оценивает результат соседа по критериям и ведёт по стрелкам ок / не ок"
          checked={r.checkMode}
          busy={busy}
          onChange={(v) => patch.mutate({ checkMode: v })}
        />
        {r.checkMode && (
          <div className="space-y-2 border-l border-border pl-3">
            <div className="flex gap-1">
              <Seg active={r.checkFix} disabled={busy} onClick={() => patch.mutate({ checkFix: true })}>
                Чинить
              </Seg>
              <Seg active={!r.checkFix} disabled={busy} onClick={() => patch.mutate({ checkFix: false })}>
                Только отчёт
              </Seg>
            </div>
            <div>
              <Label>Критерии проверки</Label>
              <div className="mt-1 flex gap-1">
                <Seg
                  active={r.checkPromptSource === "upstream"}
                  disabled={busy}
                  onClick={() => patch.mutate({ checkPromptSource: "upstream" })}
                >
                  Промт источника
                </Seg>
                <Seg
                  active={r.checkPromptSource === "agent"}
                  disabled={busy}
                  onClick={() => patch.mutate({ checkPromptSource: "agent" })}
                >
                  Готовый агент
                </Seg>
              </div>
              {r.checkPromptSource === "upstream" && r.sourcePrompts && r.sourcePrompts.length > 0 && (
                <ul className="mt-1 space-y-0.5 text-[11px] text-content-muted">
                  {r.sourcePrompts.map((s, i) => (
                    <li key={i}>
                      <span className={s.ok === false ? "text-danger" : "text-ok"}>{s.ok === false ? "✗" : "✓"}</span>{" "}
                      {s.label || s.step || s.nodeKey}
                    </li>
                  ))}
                </ul>
              )}
              {r.checkPromptSource === "agent" && (
                <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-content-muted">
                  <span>
                    {r.checkAgentFileName
                      ? `${r.checkAgentFileName}${r.checkAgentChars ? ` · ${r.checkAgentChars} симв.` : ""}`
                      : "встроенный агент"}
                  </span>
                  <Button size="sm" variant="secondary" disabled={busy} onClick={() => agentInput.current?.click()}>
                    Загрузить .txt/.md
                  </Button>
                  {r.checkAgentFileName && (
                    <Button size="sm" variant="ghost" disabled={busy} onClick={() => clearAgent.mutate()}>
                      Сбросить
                    </Button>
                  )}
                  <input
                    ref={agentInput}
                    type="file"
                    accept=".txt,.md"
                    hidden
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      e.target.value = "";
                      if (f) uploadAgent.mutate(f);
                    }}
                  />
                </div>
              )}
            </div>
            <div>
              <Button size="sm" variant="ghost" disabled={showPreview.isPending} onClick={() => showPreview.mutate()}>
                {showPreview.isPending ? "Собираю…" : "Показать промт проверки"}
              </Button>
              {preview !== null && (
                <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap rounded-sm border border-border bg-surface-sunken p-2 text-[11px] text-content-muted">
                  {preview || "(пусто)"}
                </pre>
              )}
            </div>
            {r.analysis?.summary && (
              <div>
                <Label>Разбор последней проверки</Label>
                <p className="mt-0.5 whitespace-pre-wrap text-[12px] text-content-muted">{r.analysis.summary}</p>
              </div>
            )}
          </div>
        )}
      </div>

      <div>
        <Label>Формат ответа</Label>
        <select
          value={r.outputMode}
          disabled={busy}
          onChange={(e) => patch.mutate({ outputMode: e.target.value })}
          className="mt-1.5 w-full rounded-sm border border-border bg-surface-raised px-2 py-1 text-[13px] text-content"
        >
          {OUTPUT_MODES.map((x) => (
            <option key={x.id} value={x.id}>
              {x.label}
            </option>
          ))}
        </select>
      </div>

      <div>
        <Label>Что отдаёт дальше по стрелкам</Label>
        <div className="mt-1.5 flex flex-wrap gap-1">
          {EMIT_KINDS.map((k) => {
            const on = r.emitKinds.includes(k.id);
            return (
              <Seg
                key={k.id}
                active={on}
                disabled={busy || (on && r.emitKinds.length === 1)}
                onClick={() =>
                  patch.mutate({ emitKinds: on ? r.emitKinds.filter((x) => x !== k.id) : [...r.emitKinds, k.id] })
                }
              >
                {k.label}
              </Seg>
            );
          })}
        </div>
      </div>

      <div>
        <Label>Что принимает</Label>
        <div className="mt-1.5 flex flex-wrap gap-1">
          <Seg active={r.takeFromEdges} disabled={busy} onClick={() => patch.mutate({ takeFromEdges: true })}>
            Со стрелок ({r.incomingEdges.length})
          </Seg>
          <Seg active={!r.takeFromEdges} disabled={busy} onClick={() => patch.mutate({ takeFromEdges: false })}>
            Только своё
          </Seg>
          <Seg active={r.useSnapshot} disabled={busy} onClick={() => patch.mutate({ useSnapshot: !r.useSnapshot })}>
            Снимок таблицы
          </Seg>
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => fileInput.current?.click()}>
            Загрузить файл
          </Button>
          <input
            ref={fileInput}
            type="file"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) uploadFile.mutate(f);
            }}
          />
        </div>
        {r.files.length === 0 ? (
          <Empty>На входе пока ничего — файлы придут со стрелок или загрузите свой.</Empty>
        ) : (
          <ul className="mt-2 space-y-0.5">
            {r.files.map((f, i) => (
              <li key={`${f.name}-${i}`} className="flex items-center gap-2 text-[12px]">
                <span className={f.ok === false ? "text-danger" : "text-ok"}>{f.ok === false ? "✗" : "✓"}</span>
                <span className="truncate text-content" title={f.path ?? f.name}>
                  {f.name}
                </span>
                {f.fromNode && <span className="shrink-0 font-mono text-[10px] text-content-faint">← {f.fromNode}</span>}
                {f.error && <span className="truncate text-[11px] text-danger">{f.error}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>

      {r.lastResult?.replyPreview && (
        <div>
          <Label>Последний ответ</Label>
          <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-sm border border-border bg-surface-sunken p-2 text-[11px] text-content-muted">
            {r.lastResult.replyPreview}
          </pre>
        </div>
      )}
    </div>
  );
}

/** Кнопка-сегмент: один из нескольких, как вкладка, но в одну строку. */
function Seg({
  active,
  disabled,
  onClick,
  children,
}: {
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`rounded-full border px-2 py-0.5 text-[11px] transition-colors disabled:opacity-40 ${
        active ? "border-accent bg-accent-muted text-accent" : "border-border text-content-muted hover:text-content"
      }`}
    >
      {children}
    </button>
  );
}
