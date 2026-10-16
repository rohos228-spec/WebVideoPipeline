"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Loader2,
  Users,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

/**
 * Панель данных героев прямо в ноде «Персонажи».
 *
 * Приоритетно показывает персонажей из реестра проекта (Entity / c01, c02...).
 * Также позволяет просматривать характеристики и редактировать/добавлять вручную.
 */
export function HeroConfigPanel({
  projectId,
  inStudio = false,
}: {
  projectId: number;
  inStudio?: boolean;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [showManual, setShowManual] = useState(false);

  const projectQ = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
    enabled: true,
  });

  const excelQ = useQuery({
    queryKey: ["excel-hero", projectId],
    queryFn: () => api.getExcelHero(projectId),
    enabled: true,
  });

  const excelChars = useMemo(() => excelQ.data?.characters ?? [], [excelQ.data?.characters]);
  const excelLoaded = excelQ.data?.loaded ?? false;

  // ── Загрузка из базы ──────────────────────────────────────────────
  const loadExcel = useMutation({
    mutationFn: () => api.loadExcelHero(projectId),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["excel-hero", projectId] });
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      toast.success(`Синхронизировано персонажей: ${r.count}`);
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const clearExcel = useMutation({
    mutationFn: () => api.clearExcelHero(projectId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["excel-hero", projectId] });
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      toast.success("Персонажи отвязаны от базы");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  // ── Ручной режим ──────────────────────────────────────────────────
  const [count, setCount] = useState<number>(1);
  const [descriptions, setDescriptions] = useState<string[]>([""]);
  const [variations, setVariations] = useState<number[]>([1]);

  const projectHash = useMemo(() => {
    if (!projectQ.data) return "";
    return JSON.stringify({
      c: projectQ.data.hero_count,
      d: projectQ.data.hero_descriptions,
      v: projectQ.data.hero_variations,
    });
  }, [projectQ.data]);

  useEffect(() => {
    if (!projectQ.data) return;
    const apiCount = projectQ.data.hero_count ?? 0;
    const apiDescs = projectQ.data.hero_descriptions ?? [];
    const apiVars = projectQ.data.hero_variations ?? [];

    const fallbackDescs = excelChars
      .map((c) => {
        const parts: string[] = [];
        if (c.name) parts.push(`Имя: ${c.name}`);
        if (c.look) parts.push(`Внешность: ${c.look}`);
        if (c.clothes) parts.push(`Одежда: ${c.clothes}`);
        if (c.char) parts.push(`Характер: ${c.char}`);
        return parts.join(". ") || c.look || c.name || "";
      })
      .filter((s) => s.trim().length >= 5);

    const hasApiDescs = apiDescs.some((d) => d && d.trim().length >= 5);
    const effectiveDescs = hasApiDescs
      ? apiDescs
      : fallbackDescs.length > 0
        ? fallbackDescs
        : [""];

    const n = Math.max(1, Math.min(5, (hasApiDescs ? apiCount : 0) || effectiveDescs.length || 1));
    setCount(n);
    setDescriptions(
      Array.from({ length: n }, (_, i) => effectiveDescs[i] ?? "")
    );
    setVariations(
      Array.from({ length: n }, (_, i) => apiVars[i] ?? 1)
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectHash, excelChars]);

  const setN = (n: number) => {
    const clamped = Math.max(1, Math.min(5, Math.floor(n) || 1));
    setCount(clamped);
    setDescriptions((prev) =>
      Array.from({ length: clamped }, (_, i) => prev[i] ?? "")
    );
    setVariations((prev) =>
      Array.from({ length: clamped }, (_, i) => prev[i] ?? 1)
    );
  };

  const saveManual = useMutation({
    mutationFn: () =>
      api.patchProject(projectId, {
        hero_count: count,
        hero_descriptions: descriptions.map((s) => s.trim()),
        hero_variations: variations.map((v) =>
          Math.max(1, Math.min(5, Math.floor(v) || 1))
        ),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      toast.success("Описания героев сохранены");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const canSaveManual =
    count >= 1 && descriptions.every((d) => d.trim().length >= 5);

  // ── РЕЖИМ СТУДИИ (в инспекторе ноды) ──────────────────────────────
  if (inStudio) {
    return (
      <div className="flex flex-col gap-3 rounded-xl border border-cyan-500/20 bg-cyan-500/[0.03] p-3.5">
        {/* Шапка блока */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-cyan-500/10 border border-cyan-500/25 text-cyan-400">
              <Users className="h-4 w-4" />
            </div>
            <div className="flex items-center gap-2">
              <span className="font-semibold text-xs text-foreground">Персонажи проекта</span>
              {excelChars.length > 0 ? (
                <span className="rounded-full bg-cyan-500/15 border border-cyan-500/30 px-2 py-0.5 text-[10px] font-medium text-cyan-300">
                  В базе: {excelChars.length} {excelChars.length === 1 ? "персонаж" : "персонажей"}
                </span>
              ) : projectQ.data?.hero_count ? (
                <span className="rounded-full bg-white/10 border border-white/20 px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                  Ручной ввод: {projectQ.data.hero_count}
                </span>
              ) : (
                <span className="rounded-full bg-white/5 border border-white/10 px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                  Нет данных
                </span>
              )}
            </div>
          </div>

          <div className="flex items-center gap-1.5">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-7 px-2.5 text-[11px] text-muted-foreground hover:text-cyan-300 hover:bg-cyan-500/10"
              disabled={loadExcel.isPending}
              onClick={() => loadExcel.mutate()}
              title="Обновить список персонажей из базы проекта"
            >
              {loadExcel.isPending ? (
                <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
              ) : (
                <RefreshCw className="mr-1.5 h-3 w-3" />
              )}
              Обновить
            </Button>
            {excelLoaded && excelChars.length > 0 && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-[11px] text-muted-foreground hover:text-destructive"
                disabled={clearExcel.isPending}
                onClick={() => clearExcel.mutate()}
                title="Отвязать персонажей базы и переключиться на ручной ввод"
              >
                Сброс
              </Button>
            )}
          </div>
        </div>

        {/* Список карточек персонажей */}
        {excelChars.length > 0 ? (
          <div className="flex flex-col gap-2">
            {excelChars.map((c) => (
              <div
                key={c.id}
                className="flex flex-col gap-1.5 rounded-lg border border-white/10 bg-black/30 p-2.5 transition hover:border-cyan-500/30"
              >
                <div className="flex items-center gap-2">
                  <span className="px-1.5 py-0.5 text-[11px] font-bold rounded bg-cyan-500/15 text-cyan-300 border border-cyan-500/30">
                    {c.id}
                  </span>
                  <span className="font-medium text-xs text-foreground">
                    {c.name || "Без имени"}
                  </span>
                  {c.ref_ids && c.ref_ids.length > 0 && (
                    <span className="text-[10px] text-muted-foreground">
                      · вариация от {c.ref_ids.join(", ")}
                    </span>
                  )}
                </div>
                {c.look && (
                  <p className="text-[11px] text-muted-foreground leading-snug pl-0.5">
                    <span className="text-foreground/80 font-medium">Внешность:</span> {c.look}
                  </p>
                )}
                {c.clothes && (
                  <p className="text-[11px] text-muted-foreground leading-snug pl-0.5">
                    <span className="text-foreground/80 font-medium">Одежда:</span> {c.clothes}
                  </p>
                )}
                {c.char && (
                  <p className="text-[11px] text-muted-foreground/80 leading-snug pl-0.5">
                    <span className="text-foreground/70 font-medium">Характер:</span> {c.char}
                  </p>
                )}
              </div>
            ))}
          </div>
        ) : !showManual ? (
          <div className="flex items-center justify-between rounded-lg border border-dashed border-white/15 bg-white/[0.01] p-3 text-xs text-muted-foreground">
            <span>В базе проекта пока нет зарегистрированных персонажей.</span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 text-xs border-cyan-500/30 text-cyan-300 hover:bg-cyan-500/10"
              onClick={() => setShowManual(true)}
            >
              Ввести вручную
            </Button>
          </div>
        ) : null}

        {/* Раскрывающийся блок ручного редактирования */}
        <div className="pt-0.5">
          <button
            type="button"
            className="flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-cyan-300 transition"
            onClick={() => setShowManual((v) => !v)}
          >
            {showManual ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
            <span>
              {showManual
                ? "Скрыть ручное описание"
                : excelChars.length > 0
                ? "Ручное редактирование / добавить персонажа"
                : "Ввести описание вручную"}
            </span>
          </button>

          {showManual && (
            <div className="mt-2.5 flex flex-col gap-2.5 rounded-lg border border-white/10 bg-black/25 p-3">
              <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                <span>Количество персонажей:</span>
                <div className="flex items-center gap-1.5">
                  <Input
                    type="number"
                    min={1}
                    max={5}
                    value={count}
                    onChange={(e) => setN(Number(e.target.value))}
                    className="h-7 w-16 text-xs bg-black/40 border-white/15 text-center font-medium"
                  />
                  {count < 5 && (
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className="h-7 px-2.5 text-xs text-cyan-300 hover:bg-cyan-500/10"
                      onClick={() => setN(count + 1)}
                    >
                      + Добавить
                    </Button>
                  )}
                </div>
              </div>

              {descriptions.map((desc, i) => (
                <div
                  key={i}
                  className="flex flex-col gap-1.5 rounded-lg border border-white/10 bg-black/35 p-2.5"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[11px] font-semibold uppercase tracking-wider text-cyan-300">
                      Персонаж {i + 1}
                    </span>
                    <label className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                      вариаций:
                      <Input
                        type="number"
                        min={1}
                        max={5}
                        value={variations[i] ?? 1}
                        onChange={(e) => {
                          const v = Math.max(1, Math.min(5, Number(e.target.value) || 1));
                          setVariations((prev) => {
                            const next = [...prev];
                            next[i] = v;
                            return next;
                          });
                        }}
                        className="h-6 w-12 text-xs bg-black/40 border-white/15 text-center"
                      />
                    </label>
                  </div>
                  <Textarea
                    value={desc}
                    placeholder="Опиши героя: имя, возраст, внешность, одежда, детали лица…"
                    onChange={(e) =>
                      setDescriptions((prev) => {
                        const next = [...prev];
                        next[i] = e.target.value;
                        return next;
                      })
                    }
                    className="min-h-[50px] resize-y text-xs leading-relaxed bg-black/40 border-white/15"
                    rows={2}
                  />
                </div>
              ))}

              <div className="flex items-center justify-between pt-1">
                {!canSaveManual ? (
                  <p className="text-[10px] text-amber-400/90">
                    У каждого персонажа должно быть описание ≥ 5 символов
                  </p>
                ) : <span />}
                <Button
                  type="button"
                  size="sm"
                  className="h-7 px-4 text-xs bg-cyan-600 hover:bg-cyan-500 text-white font-medium"
                  disabled={!canSaveManual || saveManual.isPending}
                  onClick={() => saveManual.mutate()}
                >
                  {saveManual.isPending && (
                    <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                  )}
                  Сохранить
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── РЕЖИМ КАНВАСА (аккордеон внизу узла на холсте) ─────────────────
  return (
    <div
      className="nodrag nopan nowheel border-t border-cyan-500/20 bg-cyan-500/[0.04]"
      onMouseDown={(e) => e.stopPropagation()}
      onWheel={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-[10px] text-cyan-200/90 transition hover:bg-cyan-500/[0.08]"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="flex items-center gap-1.5">
          <Users className="h-3 w-3 text-cyan-400" />
          Персонажи
          {excelChars.length > 0 ? (
            <span className="ml-1 text-cyan-300 font-medium">
              ({excelChars.length})
            </span>
          ) : projectQ.data?.hero_count ? (
            <span className="ml-1 text-muted-foreground">
              ({projectQ.data.hero_count})
            </span>
          ) : null}
        </span>
        {open ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
      </button>

      {open && (
        <div className="flex flex-col gap-2 px-3 pb-2.5">
          {/* Базовый блок */}
          <div className="flex flex-col gap-1.5 rounded-md border border-cyan-500/20 bg-cyan-500/[0.05] p-2">
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1 text-[10px] text-cyan-300">
                <Sparkles className="h-3 w-3" />
                Персонажи из базы
              </span>
              {excelChars.length > 0 && (
                <span className="text-[9px] text-cyan-300/90">
                  ✓ {excelChars.length}
                </span>
              )}
            </div>

            {excelChars.length > 0 && (
              <ul className="max-h-[64px] overflow-y-auto text-[10px] leading-snug text-muted-foreground">
                {excelChars.slice(0, 6).map((c) => (
                  <li key={c.id} className="truncate">
                    <span className="text-cyan-300/90 font-medium">{c.id}</span>
                    {c.name ? ` · ${c.name}` : ""}
                    {c.ref_ids && c.ref_ids.length > 0
                      ? ` · ref ${c.ref_ids.join(",")}`
                      : ""}
                  </li>
                ))}
                {excelChars.length > 6 && (
                  <li className="text-muted-foreground/70">
                    …и ещё {excelChars.length - 6}
                  </li>
                )}
              </ul>
            )}

            <div className="flex gap-1.5 pt-0.5">
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-6 flex-1 text-[10px] border-cyan-500/30 text-cyan-200 hover:bg-cyan-500/10"
                disabled={loadExcel.isPending}
                onClick={() => loadExcel.mutate()}
              >
                {loadExcel.isPending ? (
                  <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                ) : (
                  <RefreshCw className="mr-1 h-3 w-3" />
                )}
                Обновить
              </Button>
              {excelChars.length > 0 && (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-6 text-[10px] text-muted-foreground hover:text-destructive"
                  disabled={clearExcel.isPending}
                  onClick={() => clearExcel.mutate()}
                  title="Отвязать персонажей"
                >
                  Сброс
                </Button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
