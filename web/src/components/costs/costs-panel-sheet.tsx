"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  Bot,
  CheckCircle2,
  Clock,
  Coins,
  ExternalLink,
  Flame,
  HelpCircle,
  Image as ImageIcon,
  Layers,
  ListOrdered,
  Music,
  RotateCw,
  Search,
  Shield,
  Sliders,
  Sparkles,
  Video,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useDevRole } from "@/hooks/use-dev-role";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import {
  api,
  UsageHistoryModelItem,
  UsageHistoryRecentItem,
  UsageHistoryResponse,
} from "@/lib/api";

const usd = (v: number | null | undefined) =>
  v == null ? "—" : `$${v.toFixed(v >= 1 ? 2 : 4)}`;
const tok = (v: number) => (v ?? 0).toLocaleString("ru-RU");

function formatSec(sec: number | null | undefined): string {
  if (sec == null) return "—";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m === 0) return `${s} сек`;
  return `${m} мин ${s} сек`;
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString("ru-RU", {
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

function KindIcon({ kind, className }: { kind: string; className?: string }) {
  const k = kind.toLowerCase();
  if (k === "image") return <ImageIcon className={cn("text-cyan-400", className)} />;
  if (k === "video") return <Video className={cn("text-purple-400", className)} />;
  if (k === "audio" || k === "sound" || k === "music" || k === "voice")
    return <Music className={cn("text-emerald-400", className)} />;
  if (k === "llm") return <Bot className={cn("text-amber-400", className)} />;
  if (k === "topup") return <Coins className={cn("text-amber-300", className)} />;
  return <Sparkles className={cn("text-zinc-400", className)} />;
}

function KindBadge({ kind }: { kind: string }) {
  const k = kind.toLowerCase();
  const map: Record<string, { label: string; bg: string; text: string }> = {
    image: { label: "Фото / Image", bg: "bg-cyan-500/10 border-cyan-500/30", text: "text-cyan-300" },
    video: { label: "Видео / Video", bg: "bg-purple-500/10 border-purple-500/30", text: "text-purple-300" },
    audio: { label: "Аудио / Музыка", bg: "bg-emerald-500/10 border-emerald-500/30", text: "text-emerald-300" },
    llm: { label: "Текст / LLM", bg: "bg-amber-500/10 border-amber-500/30", text: "text-amber-300" },
    topup: { label: "Пополнение", bg: "bg-green-500/10 border-green-500/30", text: "text-green-300" },
    promo: { label: "Промо / Подарок", bg: "bg-blue-500/10 border-blue-500/30", text: "text-blue-300" },
  };
  const c = map[k] || { label: kind, bg: "bg-zinc-800 border-zinc-700", text: "text-zinc-300" };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-medium leading-none",
        c.bg,
        c.text,
      )}
    >
      <KindIcon kind={k} className="h-2.5 w-2.5" />
      {c.label}
    </span>
  );
}

// ── Legacy Budget & Node Breakdown Types ──────────────────────────────
interface Agg {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  failed: number;
  contract_rejected: number;
  unbilled: number;
}

interface ProjectCosts {
  project_id: number;
  nodes: Array<Agg & { node_key: string }>;
  models: Array<Agg & { model: string }>;
  total: Agg;
  budget: {
    budget_usd: number;
    spent_usd: number;
    enabled: boolean;
    exhausted: boolean;
    override: boolean;
    paused_for_budget: boolean;
  };
  failed_inserts: number;
  unpersisted_spent_usd: number;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return (await res.json()) as T;
}

export function CostsPanelSheet({
  open,
  onOpenChange,
  selectedProjectId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selectedProjectId: number | null;
}) {
  const [data, setData] = useState<UsageHistoryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters & Tabs
  const [activeTab, setActiveTab] = useState<"models" | "recent" | "budget">("models");
  const [kindFilter, setKindFilter] = useState<"all" | "image" | "video" | "audio" | "llm">("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [allTenants, setAllTenants] = useState(false);

  // Legacy Budget State
  const [projectId, setProjectId] = useState<number | null>(selectedProjectId);
  const [detail, setDetail] = useState<ProjectCosts | null>(null);
  const [budgetInput, setBudgetInput] = useState("");
  const [busyBudget, setBusyBudget] = useState(false);
  const { isMemberPreview } = useDevRole();
  const isAdmin = isMemberPreview ? false : Boolean(data?.is_admin);

  useEffect(() => {
    if (open && selectedProjectId != null) setProjectId(selectedProjectId);
  }, [open, selectedProjectId]);

  const loadUsage = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getUsageHistory({
        project_id: projectId,
        kind: kindFilter,
        all_tenants: allTenants,
        limit: 150,
      });
      setData(res);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [projectId, kindFilter, allTenants]);

  const loadLegacyDetail = useCallback(async (pid: number) => {
    try {
      const d = await getJson<ProjectCosts>(`/api/projects/${pid}/llm-costs`);
      setDetail(d);
      setBudgetInput(String(d.budget.budget_usd));
    } catch {
      // Игнорируем для обычных пользователей без прав на старый эндпоинт
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    void loadUsage();
  }, [open, loadUsage]);

  useEffect(() => {
    if (!open || projectId == null || !isAdmin) return;
    void loadLegacyDetail(projectId);
  }, [open, projectId, isAdmin, loadLegacyDetail]);

  const saveBudget = async () => {
    if (projectId == null) return;
    const v = Number(budgetInput.replace(",", "."));
    if (!Number.isFinite(v) || v < 0) {
      toast.error("Бюджет — число ≥ 0 (0 = выключить для проекта)");
      return;
    }
    setBusyBudget(true);
    try {
      const res = await fetch(`/api/projects/${projectId}/llm-budget`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ budget_usd: v }),
      });
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      const j = (await res.json()) as { pause_reason_cleared: boolean };
      toast.success(
        j.pause_reason_cleared
          ? "Бюджет поднят, причина паузы снята"
          : "Бюджет проекта сохранён",
      );
      await loadLegacyDetail(projectId);
      await loadUsage();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyBudget(false);
    }
  };

  // Фильтрация моделей по поиску
  const filteredModels = useMemo(() => {
    if (!data?.models) return [];
    let list = data.models;
    if (kindFilter !== "all") {
      list = list.filter((m) => m.kind === kindFilter);
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase().trim();
      list = list.filter(
        (m) =>
          m.display_name.toLowerCase().includes(q) ||
          m.model.toLowerCase().includes(q) ||
          m.provider.toLowerCase().includes(q),
      );
    }
    return list;
  }, [data?.models, kindFilter, searchQuery]);

  // Фильтрация истории по поиску
  const filteredRecent = useMemo(() => {
    if (!data?.recent) return [];
    let list = data.recent;
    if (kindFilter !== "all") {
      list = list.filter((r) => r.kind === kindFilter);
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase().trim();
      list = list.filter(
        (r) =>
          r.model.toLowerCase().includes(q) ||
          r.provider.toLowerCase().includes(q) ||
          r.description.toLowerCase().includes(q),
      );
    }
    return list;
  }, [data?.recent, kindFilter, searchQuery]);

  const totalSpentMicro = data?.summary.total_spent_micro ?? 0;
  const maxModelMicro = useMemo(() => {
    return Math.max(1, ...(data?.models.map((m) => m.credits_spent_micro) ?? [1]));
  }, [data?.models]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-full max-w-[min(96vw,1200px)] flex-col gap-0 border-l border-white/10 bg-zinc-950 p-0 text-zinc-100 sm:max-w-[min(96vw,1200px)]"
      >
        {/* Шапка модального окна */}
        <SheetHeader className="border-b border-white/10 bg-zinc-900/60 px-5 py-4 backdrop-blur-md">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2.5">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-400 shadow-inner">
                <Coins className="h-5 w-5" />
              </div>
              <div>
                <SheetTitle className="text-base font-bold text-zinc-100 flex items-center gap-2">
                  Расходы и использование моделей
                  {allTenants && (
                    <span className="rounded bg-rose-500/20 px-2 py-0.5 text-[11px] font-semibold text-rose-300 border border-rose-500/30">
                      Вся студия
                    </span>
                  )}
                </SheetTitle>
                <SheetDescription className="text-xs text-zinc-400">
                  Все генерации (фото, видео, музыка) и запросы LLM в кредитах и статистике.
                </SheetDescription>
              </div>
            </div>

            <div className="flex items-center gap-2 mr-10 sm:mr-12">
              {isAdmin && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setAllTenants((prev) => !prev)}
                  className={cn(
                    "h-8 gap-1.5 text-xs transition-colors",
                    allTenants
                      ? "border-rose-500/50 bg-rose-500/10 text-rose-300 hover:bg-rose-500/20"
                      : "border-white/10 text-zinc-300 hover:bg-white/[0.06]",
                  )}
                  title="Переключить просмотр: только мои расходы или суммарно по всем арендаторам"
                >
                  <Shield className="h-3.5 w-3.5" />
                  {allTenants ? "Режим: Вся студия" : "Режим: Мои расходы"}
                </Button>
              )}

              <Button
                variant="outline"
                size="sm"
                onClick={() => void loadUsage()}
                disabled={loading}
                className="h-8 gap-1.5 border-white/10 text-xs text-zinc-300 hover:bg-white/[0.06]"
              >
                <RotateCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
                Обновить
              </Button>
            </div>
          </div>
        </SheetHeader>

        {/* Тело дашборда */}
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4 space-y-5 text-sm">
          {error && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-xs text-rose-300 flex items-center gap-2">
              <XCircle className="h-4 w-4 shrink-0 text-rose-400" />
              <span>{error}</span>
            </div>
          )}

          {/* Карточки KPI вверху */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {/* Всего потрачено */}
            <div className="relative overflow-hidden rounded-xl border border-amber-500/20 bg-gradient-to-br from-amber-500/10 via-zinc-900/60 to-zinc-900/80 p-3.5 shadow-sm">
              <div className="flex items-center justify-between text-xs text-amber-300 font-medium">
                <span>Всего потрачено</span>
                <Coins className="h-4 w-4 text-amber-400" />
              </div>
              <div className="mt-2 text-xl font-bold tracking-tight tabular-nums text-zinc-100">
                {data?.summary.total_spent_credits ?? "0"} <span className="text-xs font-normal text-amber-400">кр.</span>
              </div>
              <div className="mt-1 text-[11px] text-zinc-400 flex items-center justify-between">
                <span>все операции</span>
                {isAdmin && data?.summary.total_cost_usd != null && (
                  <span className="font-mono text-zinc-400" title="Себестоимость провайдеров">
                    {usd(data.summary.total_cost_usd)}
                  </span>
                )}
              </div>
            </div>

            {/* Картинки */}
            <div className="rounded-xl border border-cyan-500/20 bg-gradient-to-br from-cyan-500/10 via-zinc-900/60 to-zinc-900/80 p-3.5 shadow-sm">
              <div className="flex items-center justify-between text-xs text-cyan-300 font-medium">
                <span>🖼️ Фото / Картинки</span>
                <ImageIcon className="h-4 w-4 text-cyan-400" />
              </div>
              <div className="mt-2 text-xl font-bold tracking-tight tabular-nums text-zinc-100">
                {data?.summary.by_kind.image?.credits ?? "0"} <span className="text-xs font-normal text-cyan-400">кр.</span>
              </div>
              <div className="mt-1 text-[11px] text-zinc-400 flex items-center justify-between">
                <span>{data?.summary.by_kind.image?.calls ?? 0} генераций</span>
                {isAdmin && data?.summary.by_kind.image?.cost_usd != null && (
                  <span className="font-mono text-zinc-400">{usd(data.summary.by_kind.image.cost_usd)}</span>
                )}
              </div>
            </div>

            {/* Видео */}
            <div className="rounded-xl border border-purple-500/20 bg-gradient-to-br from-purple-500/10 via-zinc-900/60 to-zinc-900/80 p-3.5 shadow-sm">
              <div className="flex items-center justify-between text-xs text-purple-300 font-medium">
                <span>🎬 Видео</span>
                <Video className="h-4 w-4 text-purple-400" />
              </div>
              <div className="mt-2 text-xl font-bold tracking-tight tabular-nums text-zinc-100">
                {data?.summary.by_kind.video?.credits ?? "0"} <span className="text-xs font-normal text-purple-400">кр.</span>
              </div>
              <div className="mt-1 text-[11px] text-zinc-400 flex items-center justify-between">
                <span>{data?.summary.by_kind.video?.calls ?? 0} клипов</span>
                {isAdmin && data?.summary.by_kind.video?.cost_usd != null && (
                  <span className="font-mono text-zinc-400">{usd(data.summary.by_kind.video.cost_usd)}</span>
                )}
              </div>
            </div>

            {/* Аудио / Музыка */}
            <div className="rounded-xl border border-emerald-500/20 bg-gradient-to-br from-emerald-500/10 via-zinc-900/60 to-zinc-900/80 p-3.5 shadow-sm">
              <div className="flex items-center justify-between text-xs text-emerald-300 font-medium">
                <span>🎵 Аудио / Музыка</span>
                <Music className="h-4 w-4 text-emerald-400" />
              </div>
              <div className="mt-2 text-xl font-bold tracking-tight tabular-nums text-zinc-100">
                {data?.summary.by_kind.audio?.credits ?? "0"} <span className="text-xs font-normal text-emerald-400">кр.</span>
              </div>
              <div className="mt-1 text-[11px] text-zinc-400 flex items-center justify-between">
                <span>{data?.summary.by_kind.audio?.calls ?? 0} генераций</span>
                {isAdmin && data?.summary.by_kind.audio?.cost_usd != null && (
                  <span className="font-mono text-zinc-400">{usd(data.summary.by_kind.audio.cost_usd)}</span>
                )}
              </div>
            </div>

            {/* Текст LLM */}
            <div className="rounded-xl border border-amber-500/20 bg-gradient-to-br from-amber-500/10 via-zinc-900/60 to-zinc-900/80 p-3.5 shadow-sm col-span-2 sm:col-span-1">
              <div className="flex items-center justify-between text-xs text-amber-300 font-medium">
                <span>💬 Текст (LLM)</span>
                <Bot className="h-4 w-4 text-amber-400" />
              </div>
              <div className="mt-2 text-xl font-bold tracking-tight tabular-nums text-zinc-100">
                {data?.summary.by_kind.llm?.credits ?? "0"} <span className="text-xs font-normal text-amber-400">кр.</span>
              </div>
              <div className="mt-1 text-[11px] text-zinc-400 flex items-center justify-between">
                <span>{data?.summary.by_kind.llm?.calls ?? 0} запросов</span>
                {isAdmin && data?.summary.by_kind.llm?.cost_usd != null && (
                  <span className="font-mono text-zinc-400">{usd(data.summary.by_kind.llm.cost_usd)}</span>
                )}
              </div>
            </div>
          </div>

          {/* Панель вкладок и фильтров */}
          <div className="flex flex-col gap-3 rounded-xl border border-white/10 bg-zinc-900/40 p-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              {/* Вкладки переключения вида */}
              <div className="flex items-center gap-1.5 rounded-lg border border-white/10 bg-zinc-950/60 p-1 text-xs">
                <button
                  type="button"
                  onClick={() => setActiveTab("models")}
                  className={cn(
                    "flex items-center gap-1.5 rounded-md px-3 py-1.5 font-medium transition-all",
                    activeTab === "models"
                      ? "bg-primary text-primary-foreground shadow-sm"
                      : "text-zinc-400 hover:text-zinc-200",
                  )}
                >
                  <Layers className="h-3.5 w-3.5" />
                  По моделям
                  <span className="ml-1 rounded-full bg-white/10 px-1.5 py-0.2 text-[10px]">
                    {filteredModels.length}
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab("recent")}
                  className={cn(
                    "flex items-center gap-1.5 rounded-md px-3 py-1.5 font-medium transition-all",
                    activeTab === "recent"
                      ? "bg-primary text-primary-foreground shadow-sm"
                      : "text-zinc-400 hover:text-zinc-200",
                  )}
                >
                  <ListOrdered className="h-3.5 w-3.5" />
                  История операций
                  <span className="ml-1 rounded-full bg-white/10 px-1.5 py-0.2 text-[10px]">
                    {filteredRecent.length}
                  </span>
                </button>
                {isAdmin && projectId != null && (
                  <button
                    type="button"
                    onClick={() => setActiveTab("budget")}
                    className={cn(
                      "flex items-center gap-1.5 rounded-md px-3 py-1.5 font-medium transition-all",
                      activeTab === "budget"
                        ? "bg-primary text-primary-foreground shadow-sm"
                        : "text-zinc-400 hover:text-zinc-200",
                    )}
                  >
                    <Sliders className="h-3.5 w-3.5" />
                    Бюджет прогона #{projectId}
                  </button>
                )}
              </div>

              {/* Поле поиска */}
              <div className="relative min-w-[220px] flex-1 sm:max-w-xs">
                <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-zinc-500" />
                <Input
                  type="text"
                  placeholder="Поиск модели или провайдера…"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="h-8 pl-8 text-xs bg-zinc-950/60 border-white/10 text-zinc-200 placeholder:text-zinc-500"
                />
              </div>
            </div>

            {/* Фильтры категорий (Pills) */}
            <div className="flex flex-wrap items-center gap-1.5 pt-1 border-t border-white/[0.06] text-xs">
              <span className="text-[11px] text-zinc-500 mr-1">Категория:</span>
              {(
                [
                  { id: "all", label: "Все модели" },
                  { id: "image", label: "🖼️ Картинки" },
                  { id: "video", label: "🎬 Видео" },
                  { id: "audio", label: "🎵 Аудио" },
                  { id: "llm", label: "💬 LLM текст" },
                ] as const
              ).map((f) => (
                <button
                  key={f.id}
                  type="button"
                  onClick={() => setKindFilter(f.id)}
                  className={cn(
                    "rounded-md px-2.5 py-1 text-xs transition-colors",
                    kindFilter === f.id
                      ? "bg-white/15 text-zinc-100 font-semibold border border-white/20"
                      : "text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-200",
                  )}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>

          {/* ═══════════════════════════════════════════════════════════════ */}
          {/* ВКЛАДКА 1: По моделям                                          */}
          {/* ═══════════════════════════════════════════════════════════════ */}
          {activeTab === "models" && (
            <div className="space-y-3">
              {filteredModels.length === 0 ? (
                <div className="rounded-xl border border-white/10 bg-zinc-900/30 py-12 text-center text-xs text-zinc-500">
                  {loading ? "Загрузка аналитики моделей…" : "Модели не найдены по заданным фильтрам."}
                </div>
              ) : (
                <div className="overflow-hidden rounded-xl border border-white/10 bg-zinc-900/40">
                  <table className="w-full border-collapse text-left text-xs">
                    <thead>
                      <tr className="border-b border-white/10 bg-zinc-950/60 text-zinc-400">
                        <th className="px-4 py-3 font-semibold">Модель и провайдер</th>
                        <th className="px-3 py-3 font-semibold">Категория</th>
                        <th className="px-3 py-3 font-semibold text-right">Вызовы</th>
                        <th className="px-3 py-3 font-semibold text-right">Объём</th>
                        <th className="px-4 py-3 font-semibold text-right">Расходы (кр.)</th>
                        <th className="px-4 py-3 font-semibold text-left">Доля в расходах</th>
                        {isAdmin && (
                          <th className="px-3 py-3 font-semibold text-right">Себест. ($)</th>
                        )}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/[0.06]">
                      {filteredModels.map((m) => {
                        const pct =
                          totalSpentMicro > 0
                            ? Math.round((m.credits_spent_micro / totalSpentMicro) * 100)
                            : 0;
                        const relPct = Math.min(
                          100,
                          Math.max(2, Math.round((m.credits_spent_micro / maxModelMicro) * 100)),
                        );

                        return (
                          <tr
                            key={`${m.kind}-${m.model}`}
                            className="group transition-colors hover:bg-white/[0.03]"
                          >
                            {/* Название и провайдер */}
                            <td className="px-4 py-3">
                              <div className="flex items-center gap-2.5">
                                <div className="flex h-7 w-7 items-center justify-center rounded-md border border-white/10 bg-zinc-950/80">
                                  <KindIcon kind={m.kind} className="h-4 w-4" />
                                </div>
                                <div>
                                  <div className="font-semibold text-zinc-100 group-hover:text-primary transition-colors">
                                    {m.display_name}
                                  </div>
                                  <div className="text-[11px] text-zinc-500 flex items-center gap-1.5">
                                    <span className="font-mono text-[10px] uppercase text-zinc-400">
                                      {m.provider}
                                    </span>
                                    <span>•</span>
                                    <span>{m.model}</span>
                                  </div>
                                </div>
                              </div>
                            </td>

                            {/* Категория */}
                            <td className="px-3 py-3 whitespace-nowrap">
                              <KindBadge kind={m.kind} />
                            </td>

                            {/* Вызовы */}
                            <td className="px-3 py-3 text-right whitespace-nowrap">
                              <div className="font-medium text-zinc-200 tabular-nums">{m.calls}</div>
                              {m.error_calls > 0 ? (
                                <div className="text-[10px] text-rose-400 tabular-nums">
                                  {m.error_calls} ош.
                                </div>
                              ) : (
                                <div className="text-[10px] text-emerald-400">все успешно</div>
                              )}
                            </td>

                            {/* Объём */}
                            <td className="px-3 py-3 text-right whitespace-nowrap tabular-nums text-zinc-300">
                              <span className="font-semibold text-zinc-100">
                                {m.kind === "llm" ? tok(m.units) : m.units.toLocaleString("ru-RU")}
                              </span>{" "}
                              <span className="text-[11px] text-zinc-500">{m.unit_label}</span>
                            </td>

                            {/* Расходы в кредитах */}
                            <td className="px-4 py-3 text-right whitespace-nowrap">
                              <span className="font-bold text-amber-300 tabular-nums">
                                {m.credits_spent}
                              </span>{" "}
                              <span className="text-[11px] text-amber-500/80 font-normal">кр.</span>
                            </td>

                            {/* Прогресс-бар доли */}
                            <td className="w-44 px-4 py-3 whitespace-nowrap">
                              <div className="flex items-center gap-2">
                                <div className="h-2 flex-1 rounded-full bg-white/[0.06] overflow-hidden">
                                  <div
                                    className="h-full rounded-full bg-gradient-to-r from-amber-500 to-amber-300"
                                    style={{ width: `${relPct}%` }}
                                  />
                                </div>
                                <span className="w-8 text-right text-[11px] font-mono text-zinc-400 tabular-nums">
                                  {pct}%
                                </span>
                              </div>
                            </td>

                            {/* Себестоимость для админа */}
                            {isAdmin && (
                              <td className="px-3 py-3 text-right whitespace-nowrap font-mono text-zinc-400 tabular-nums">
                                {usd(m.cost_usd)}
                              </td>
                            )}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* ═══════════════════════════════════════════════════════════════ */}
          {/* ВКЛАДКА 2: История операций (Детальный журнал)                 */}
          {/* ═══════════════════════════════════════════════════════════════ */}
          {activeTab === "recent" && (
            <div className="space-y-2">
              {filteredRecent.length === 0 ? (
                <div className="rounded-xl border border-white/10 bg-zinc-900/30 py-12 text-center text-xs text-zinc-500">
                  {loading ? "Загрузка журнала операций…" : "Операций не найдено по заданным фильтрам."}
                </div>
              ) : (
                <div className="divide-y divide-white/[0.06] rounded-xl border border-white/10 bg-zinc-900/40 overflow-hidden">
                  {filteredRecent.map((r) => {
                    const isDebit = r.credits_delta_micro <= 0;
                    const isPositive = r.credits_delta_micro > 0;

                    return (
                      <div
                        key={r.id}
                        className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 transition-colors hover:bg-white/[0.03]"
                      >
                        {/* Левая часть: дата, бейдж, модель, описание */}
                        <div className="flex items-center gap-3 min-w-0 flex-1">
                          {/* Превью если есть медиа-файл */}
                          {r.preview_url ? (
                            <a
                              href={r.preview_url}
                              target="_blank"
                              rel="noreferrer"
                              className="relative flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-md border border-white/15 bg-zinc-950 hover:border-primary transition-colors group/preview"
                              title="Нажмите для открытия файла"
                            >
                              {r.kind === "video" ? (
                                <video
                                  src={r.preview_url}
                                  className="h-full w-full object-cover"
                                  muted
                                />
                              ) : (
                                // eslint-disable-next-line @next/next/no-img-element
                                <img
                                  src={r.preview_url}
                                  alt=""
                                  className="h-full w-full object-cover"
                                />
                              )}
                              <div className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 group-hover/preview:opacity-100 transition-opacity">
                                <ExternalLink className="h-3 w-3 text-white" />
                              </div>
                            </a>
                          ) : (
                            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-white/10 bg-zinc-950/80">
                              <KindIcon kind={r.kind} className="h-5 w-5" />
                            </div>
                          )}

                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="font-semibold text-zinc-100">{r.model}</span>
                              <KindBadge kind={r.kind} />
                              <span className="font-mono text-[10px] text-zinc-500 uppercase">
                                {r.provider}
                              </span>
                            </div>

                            <div className="mt-0.5 text-xs text-zinc-400 truncate max-w-xl">
                              {r.description}
                            </div>

                            <div className="mt-1 flex items-center gap-3 text-[11px] text-zinc-500">
                              <span>{formatDate(r.created_at)}</span>
                              {r.duration_sec != null && (
                                <span className="flex items-center gap-1 text-zinc-400">
                                  <Clock className="h-3 w-3" />
                                  {formatSec(r.duration_sec)}
                                </span>
                              )}
                              {r.units_label && (
                                <span className="text-zinc-400">• {r.units_label}</span>
                              )}
                            </div>
                          </div>
                        </div>

                        {/* Правая часть: статус и изменение кредитов */}
                        <div className="flex items-center gap-4 text-right">
                          <div className="flex items-center gap-1.5 text-xs">
                            {r.status === "ok" ? (
                              <span className="flex items-center gap-1 text-emerald-400 font-medium">
                                <CheckCircle2 className="h-3.5 w-3.5" />
                                Ок
                              </span>
                            ) : (
                              <span className="flex items-center gap-1 text-rose-400 font-medium">
                                <XCircle className="h-3.5 w-3.5" />
                                Ошибка
                              </span>
                            )}
                          </div>

                          <div className="min-w-[90px] text-right">
                            <div
                              className={cn(
                                "text-sm font-bold tabular-nums",
                                isPositive && "text-emerald-400",
                                isDebit && r.credits_delta_micro !== 0 && "text-zinc-100",
                                r.credits_delta_micro === 0 && "text-zinc-400",
                              )}
                            >
                              {r.credits_delta}{" "}
                              <span className="text-[10px] font-normal text-zinc-500">кр.</span>
                            </div>

                            {isAdmin && r.cost_usd != null && (
                              <div className="font-mono text-[10px] text-zinc-500 tabular-nums">
                                {usd(r.cost_usd)}
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* ═══════════════════════════════════════════════════════════════ */}
          {/* ВКЛАДКА 3: Бюджет проекта (Legacy LLM Budget & Nodes)           */}
          {/* ═══════════════════════════════════════════════════════════════ */}
          {activeTab === "budget" && projectId != null && (
            <div className="space-y-4">
              {detail == null ? (
                <div className="py-8 text-center text-xs text-zinc-500">
                  Загрузка данных проекта #{projectId}…
                </div>
              ) : (
                <>
                  <div className="rounded-xl border border-white/10 bg-zinc-900/60 p-4">
                    <div className="flex flex-wrap items-end justify-between gap-4">
                      <div>
                        <div className="text-xs uppercase tracking-wider text-zinc-400 font-semibold">
                          Бюджет проекта #{detail.project_id}
                        </div>
                        <div className="mt-1 text-2xl font-bold tabular-nums text-zinc-100">
                          {usd(detail.total.cost_usd)}
                        </div>
                        <div className="text-xs text-zinc-400">
                          {detail.total.calls} вызовов · неуспешных {detail.total.failed} · unbilled{" "}
                          {detail.total.unbilled}
                        </div>
                      </div>

                      <div className="flex items-end gap-2">
                        <label className="flex flex-col gap-1 text-xs text-zinc-400">
                          Лимит бюджета, $
                          <Input
                            value={budgetInput}
                            onChange={(e) => setBudgetInput(e.target.value)}
                            className="h-8 w-32 text-xs bg-zinc-950 border-white/10"
                            inputMode="decimal"
                          />
                        </label>
                        <Button size="sm" onClick={saveBudget} disabled={busyBudget} className="h-8 text-xs">
                          {detail.budget.paused_for_budget
                            ? "Поднять и снять паузу"
                            : "Сохранить"}
                        </Button>
                      </div>
                    </div>
                  </div>

                  {/* Ноды проекта */}
                  <div className="rounded-xl border border-white/10 bg-zinc-900/40 p-3">
                    <h4 className="mb-2 text-xs font-semibold uppercase text-zinc-400">
                      Разбивка LLM по нодам конвейера
                    </h4>
                    <table className="w-full border-collapse text-xs">
                      <thead>
                        <tr className="border-b border-white/10 text-zinc-400">
                          <th className="px-2 py-1.5 text-left font-medium">Нода</th>
                          <th className="px-2 py-1.5 text-right font-medium">Вызовы</th>
                          <th className="px-2 py-1.5 text-right font-medium">Токены in / out</th>
                          <th className="px-2 py-1.5 text-right font-medium">$ USD</th>
                          <th className="px-2 py-1.5 text-right font-medium">Ошибки</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-white/[0.04]">
                        {detail.nodes.map((n) => (
                          <tr key={n.node_key} className="hover:bg-white/[0.02]">
                            <td className="px-2 py-1.5 font-mono text-zinc-200">{n.node_key}</td>
                            <td className="px-2 py-1.5 text-right tabular-nums text-zinc-300">
                              {n.calls}
                            </td>
                            <td className="px-2 py-1.5 text-right tabular-nums text-zinc-400">
                              {tok(n.prompt_tokens)} / {tok(n.completion_tokens)}
                            </td>
                            <td className="px-2 py-1.5 text-right font-semibold tabular-nums text-zinc-100">
                              {usd(n.cost_usd)}
                            </td>
                            <td
                              className={cn(
                                "px-2 py-1.5 text-right tabular-nums",
                                n.failed > 0 ? "text-rose-400 font-semibold" : "text-zinc-500",
                              )}
                            >
                              {n.failed}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
