"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Chip, Empty, Label } from "@/components/ui/bits";
import { Toggle } from "./step-params";
import type { HitlKind, HitlRequest } from "@/lib/types";

/**
 * Проверка результата узла: человеком и GPT.
 *
 * Та же запись HITL и то же решение, что у кнопок в Telegram — одна модель
 * данных на оба интерфейса. Здесь: список ожидающих проверок по виду узла,
 * решения «принять / переделать / отклонить / переделать с правкой промта»,
 * и тумблер «GPT проверяет перед авто-апрувом» — он живёт в
 * `meta.auto_review_kinds` и решает, кто смотрит результат, когда включено
 * автопродвижение: модель или никто.
 */

/** Какой вид проверки относится к типу узла. Одна карта на сервер и бота. */
export function hitlKindForNodeType(type: string): HitlKind | null {
  switch (type) {
    case "plan":
      return "approve_plan";
    case "script":
      return "approve_script";
    case "hero":
    case "hitl_hero":
      return "approve_hero";
    case "images":
    case "hitl_images":
      return "approve_images";
    case "videos":
    case "hitl_videos":
      return "approve_videos";
    case "assemble":
    case "hitl_final":
      return "approve_final";
    default:
      return null;
  }
}

const KIND_TEXT: Record<HitlKind, string> = {
  approve_plan: "сценарий",
  approve_script: "закадровый текст",
  approve_hero: "персонажи",
  approve_images: "картинки",
  approve_videos: "видео",
  approve_final: "финальный ролик",
};

const DECISION_CHIP: Record<string, { tone: "ok" | "warn" | "danger" | "neutral"; text: string }> = {
  approved: { tone: "ok", text: "принято" },
  regenerate: { tone: "warn", text: "переделать" },
  rejected: { tone: "danger", text: "отклонено" },
};

export function HitlPanel({
  projectId,
  kind,
  autoReviewKinds,
  busy,
  onAutoReviewChange,
}: {
  projectId: number;
  kind: HitlKind;
  /** null — не настроено, действует умолчание сервера. */
  autoReviewKinds: string[] | null;
  busy: boolean;
  onAutoReviewChange: (next: string[]) => void;
}) {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["hitl", projectId],
    queryFn: () => api.hitlForProject(projectId),
    refetchInterval: 8_000,
  });
  const [editing, setEditing] = useState<number | null>(null);
  const [prompt, setPrompt] = useState("");

  const decide = useMutation({
    mutationFn: ({ id, decision, edited }: { id: number; decision: "approve" | "regenerate" | "reject" | "edit_prompt"; edited?: string }) =>
      api.hitlDecide(id, decision, edited),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hitl", projectId] });
      qc.invalidateQueries({ queryKey: ["graph", projectId] });
      qc.invalidateQueries({ queryKey: ["stages", projectId] });
      setEditing(null);
      toast.success("Решение записано");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const mine = (list.data ?? []).filter((r) => r.kind === kind);
  const pending = mine.filter((r) => r.decision === "pending");
  const recent = mine.filter((r) => r.decision !== "pending").slice(0, 3);
  const gptOn = autoReviewKinds?.includes(kind) ?? false;

  const toggleGpt = (v: boolean) => {
    const cur = new Set(autoReviewKinds ?? []);
    if (v) cur.add(kind);
    else cur.delete(kind);
    onAutoReviewChange([...cur]);
  };

  return (
    <div className="space-y-3">
      <Toggle
        label="GPT проверяет перед авто-апрувом"
        hint={
          gptOn
            ? `при автопродвижении модель посмотрит ${KIND_TEXT[kind]} и вернёт на переделку, если не так`
            : `при автопродвижении ${KIND_TEXT[kind]} принимается без проверки`
        }
        checked={gptOn}
        busy={busy}
        onChange={toggleGpt}
      />

      {list.isError && <p className="text-[12px] text-danger">Проверки не читаются: {list.error.message}</p>}

      {pending.length > 0 ? (
        <div className="space-y-2">
          <Label>Ждёт решения{pending.length > 1 ? ` · ${pending.length}` : ""}</Label>
          {pending.length > 1 && (
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="primary"
                disabled={decide.isPending}
                onClick={() => pending.forEach((r) => decide.mutate({ id: r.id, decision: "approve" }))}
              >
                Принять все
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={decide.isPending}
                onClick={() => pending.forEach((r) => decide.mutate({ id: r.id, decision: "regenerate" }))}
              >
                Переделать все
              </Button>
            </div>
          )}
          {pending.map((r) => (
            <PendingRow
              key={r.id}
              req={r}
              busy={decide.isPending}
              editing={editing === r.id}
              prompt={prompt}
              onPrompt={setPrompt}
              onEdit={() => {
                setEditing(editing === r.id ? null : r.id);
                setPrompt(String(r.payload?.prompt ?? r.payload?.edited_prompt ?? ""));
              }}
              onDecide={(decision, edited) => decide.mutate({ id: r.id, decision, edited })}
            />
          ))}
        </div>
      ) : (
        <Empty>Сейчас проверок нет — они появятся, когда узел закончит работу и остановится на человеке.</Empty>
      )}

      {recent.length > 0 && (
        <div>
          <Label>Прошлые решения</Label>
          <ul className="mt-1 space-y-0.5">
            {recent.map((r) => {
              const chip = DECISION_CHIP[r.decision] ?? { tone: "neutral" as const, text: r.decision };
              return (
                <li key={r.id} className="flex items-center gap-2 text-[12px] text-content-muted">
                  <Chip tone={chip.tone}>{chip.text}</Chip>
                  {r.frame_id ? <span className="font-mono text-[11px] text-content-faint">кадр #{r.frame_id}</span> : null}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

function PendingRow({
  req,
  busy,
  editing,
  prompt,
  onPrompt,
  onEdit,
  onDecide,
}: {
  req: HitlRequest;
  busy: boolean;
  editing: boolean;
  prompt: string;
  onPrompt: (v: string) => void;
  onEdit: () => void;
  onDecide: (decision: "approve" | "regenerate" | "reject" | "edit_prompt", edited?: string) => void;
}) {
  const preview = req.payload?.preview_url ?? req.payload?.image_url ?? null;
  return (
    <div className="rounded-sm border border-warn bg-warn-muted p-2">
      <div className="flex items-center justify-between gap-2 text-[12px] text-content">
        <span>
          {req.frame_id ? `Кадр #${req.frame_id}` : "Результат узла"}
        </span>
        <span className="font-mono text-[11px] text-content-faint">#{req.id}</span>
      </div>
      {typeof preview === "string" && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={preview} alt="" className="mt-2 max-h-40 rounded-sm border border-border object-contain" />
      )}
      <div className="mt-2 flex flex-wrap gap-2">
        <Button size="sm" variant="primary" disabled={busy} onClick={() => onDecide("approve")}>
          Принять
        </Button>
        <Button size="sm" variant="secondary" disabled={busy} onClick={() => onDecide("regenerate")}>
          Переделать
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={onEdit}>
          {editing ? "Без правки промта" : "Переделать с правкой промта"}
        </Button>
        <Button size="sm" variant="danger" disabled={busy} onClick={() => onDecide("reject")}>
          Отклонить
        </Button>
      </div>
      {editing && (
        <div className="mt-2 space-y-2">
          <textarea
            value={prompt}
            onChange={(e) => onPrompt(e.target.value)}
            rows={4}
            placeholder="Что изменить в промте для переделки"
            className="w-full rounded-sm border border-border bg-surface-raised px-2 py-1 text-[13px] text-content outline-none focus:border-accent"
          />
          <Button size="sm" variant="primary" disabled={busy || !prompt.trim()} onClick={() => onDecide("edit_prompt", prompt)}>
            Применить правку и переделать
          </Button>
        </div>
      )}
    </div>
  );
}
