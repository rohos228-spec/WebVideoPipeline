"use client";

import { useState } from "react";
import { Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PlanCard } from "@/components/action-layer/plan-card";
import { RunProgress } from "@/components/action-layer/run-progress";
import { describeError, previewFromError, previewPlan, type PlanPreviewResponse } from "@/lib/action-layer";
import { formatNoticeTime, NOTICE_LEVEL_RU, type ChatNotice, type NoticeAction } from "@/lib/chat-notices";
import { cn } from "@/lib/utils";

const LEVEL_CLASS: Record<ChatNotice["level"], string> = {
  info: "border-sky-400/30 bg-sky-500/[0.07]",
  success: "border-emerald-400/30 bg-emerald-500/[0.07]",
  warning: "border-amber-400/40 bg-amber-500/[0.08]",
  error: "border-red-400/40 bg-red-500/[0.08]",
};

const LEVEL_TEXT: Record<ChatNotice["level"], string> = {
  info: "text-sky-200",
  success: "text-emerald-200",
  warning: "text-amber-200",
  error: "text-red-200",
};

export function ChatNoticeCard({
  notice,
  showProject,
  onRead,
}: {
  notice: ChatNotice;
  showProject: boolean;
  onRead: (id: number) => void;
}) {
  const [plan, setPlan] = useState<PlanPreviewResponse | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  async function preview(action: NoticeAction) {
    setBusy(action.action_id);
    setError("");
    setRunId(null);
    try {
      const res = await previewPlan([{ action_id: action.action_id, params: action.params }], action.label);
      setPlan(res);
    } catch (e) {
      const pv = previewFromError(e);
      if (pv) setPlan({ plan_id: null, preview: pv });
      else setError(describeError(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div
      data-testid="chat-notice"
      data-level={notice.level}
      data-read={notice.read ? "true" : "false"}
      className={cn("mb-3 rounded-xl border px-3 py-2.5 text-[12px]", LEVEL_CLASS[notice.level], notice.read && "opacity-70")}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className={cn("text-[10px] font-semibold", LEVEL_TEXT[notice.level])}>{NOTICE_LEVEL_RU[notice.level]}</span>
        {showProject && notice.project_id != null ? (
          <span className="text-[10px] text-white/45">проект #{notice.project_id}</span>
        ) : null}
        <span className="text-[10px] text-white/35">{formatNoticeTime(notice.created_at)}</span>
      </div>
      <div className="mt-0.5 font-medium text-white/90">{notice.title}</div>
      {notice.text ? <p className="mt-0.5 whitespace-pre-wrap leading-relaxed text-white/70">{notice.text}</p> : null}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {notice.suggested_actions.map((action) => (
          <Button
            key={`${action.action_id}-${action.label}`}
            type="button"
            size="sm"
            variant="outline"
            className="h-7 text-[11px]"
            data-testid="chat-notice-action"
            disabled={busy != null}
            onClick={() => void preview(action)}
          >
            {busy === action.action_id ? "Готовлю план…" : action.label}
          </Button>
        ))}
        {!notice.read ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 gap-1 text-[11px] text-white/60"
            data-testid="chat-notice-read"
            onClick={() => onRead(notice.id)}
          >
            <Check className="h-3 w-3" />
            Прочитано
          </Button>
        ) : (
          <span className="text-[10px] text-white/35">прочитано</span>
        )}
      </div>
      {error ? <p role="alert" className="mt-2 text-[11px] text-red-300">{error}</p> : null}
      {plan && !runId ? (
        <div className="mt-2">
          <PlanCard
            plan={plan}
            onStarted={(id) => {
              setRunId(id);
              setPlan(null);
            }}
            onDismiss={() => setPlan(null)}
          />
        </div>
      ) : null}
      {runId ? (
        <div className="mt-2">
          <RunProgress planId={runId} onFinished={() => undefined} />
        </div>
      ) : null}
    </div>
  );
}
