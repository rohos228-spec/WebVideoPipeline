"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Loader2,
  Package,
  ChevronDown,
  ChevronRight,
  Save,
  Plus,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

/**
 * Панель конфигурации предметов (Items / Props) проекта.
 * Поддерживает полноразмерный режим студии (inStudio) и компактный режим на холсте.
 */
export function ItemsConfigPanel({
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

  const apiDescs: string[] = useMemo(() => {
    return (projectQ.data?.item_descriptions ?? []).filter(
      (d: string) => typeof d === "string" && d.trim().length > 0
    );
  }, [projectQ.data?.item_descriptions]);

  const [count, setCount] = useState<number>(1);
  const [descriptions, setDescriptions] = useState<string[]>([""]);

  const projectHash = useMemo(() => {
    if (!projectQ.data) return "";
    return JSON.stringify({
      d: projectQ.data.item_descriptions,
    });
  }, [projectQ.data]);

  useEffect(() => {
    if (!projectQ.data) return;
    const n = Math.max(1, Math.min(5, apiDescs.length || 1));
    setCount(n);
    setDescriptions(
      Array.from({ length: n }, (_, i) => apiDescs[i] ?? "")
    );
    if (apiDescs.length > 0) {
      setShowManual(false);
    }
  }, [projectHash, apiDescs]);

  const setN = (n: number) => {
    const clamped = Math.max(1, Math.min(5, Math.floor(n) || 1));
    setCount(clamped);
    setDescriptions((prev) =>
      Array.from({ length: clamped }, (_, i) => prev[i] ?? "")
    );
  };

  const saveManual = useMutation({
    mutationFn: () =>
      api.patchProject(projectId, {
        item_descriptions: descriptions.map((s) => s.trim()).filter(Boolean),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      toast.success("Описания предметов сохранены");
      setShowManual(false);
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const resetItems = useMutation({
    mutationFn: () =>
      api.patchProject(projectId, {
        item_descriptions: [],
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      setDescriptions([""]);
      setCount(1);
      setShowManual(false);
      toast.success("Предметы сброшены");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const canSaveManual =
    count >= 1 && descriptions.some((d) => d.trim().length >= 3);

  const savedCount = apiDescs.length;

  // ── РЕЖИМ СТУДИИ (в инспекторе ноды) ──────────────────────────────
  if (inStudio) {
    return (
      <div className="flex flex-col gap-3 rounded-xl border border-cyan-500/20 bg-cyan-500/[0.03] p-3.5">
        {/* Шапка блока */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-cyan-500/10 border border-cyan-500/25 text-cyan-400">
              <Package className="h-4 w-4" />
            </div>
            <div className="flex items-center gap-2">
              <span className="font-semibold text-xs text-foreground">
                Реквизит и ключевые предметы
              </span>
              {savedCount > 0 ? (
                <span className="rounded-full bg-cyan-500/15 border border-cyan-500/30 px-2 py-0.5 text-[10px] font-medium text-cyan-300">
                  Задано: {savedCount} {savedCount === 1 ? "предмет" : "предметов"}
                </span>
              ) : (
                <span className="rounded-full bg-white/5 border border-white/10 px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                  Шаг опционален
                </span>
              )}
            </div>
          </div>

          <div className="flex items-center gap-1.5">
            {savedCount > 0 && !showManual && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 px-2.5 text-[11px] text-cyan-300 hover:bg-cyan-500/10"
                onClick={() => setShowManual(true)}
              >
                Редактировать
              </Button>
            )}
            {savedCount > 0 && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-[11px] text-muted-foreground hover:text-destructive"
                disabled={resetItems.isPending}
                onClick={() => resetItems.mutate()}
                title="Очистить список предметов"
              >
                <Trash2 className="h-3 w-3 mr-1" />
                Сброс
              </Button>
            )}
          </div>
        </div>

        {/* Если предметы уже есть и не открыт редактор — показываем карточки */}
        {savedCount > 0 && !showManual ? (
          <div className="flex flex-col gap-2">
            {apiDescs.map((desc, idx) => (
              <div
                key={idx}
                className="flex flex-col gap-1 rounded-lg border border-white/10 bg-black/30 p-2.5 transition hover:border-cyan-500/30"
              >
                <div className="flex items-center gap-2">
                  <span className="px-1.5 py-0.5 text-[10px] font-bold rounded bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 uppercase">
                    Предмет {idx + 1}
                  </span>
                </div>
                <p className="text-xs text-foreground/90 leading-relaxed pl-0.5 pt-0.5">
                  {desc}
                </p>
              </div>
            ))}
          </div>
        ) : null}

        {/* Если предметов нет и не открыт редактор — дружелюбный баннер */}
        {savedCount === 0 && !showManual ? (
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-lg border border-cyan-500/20 bg-cyan-950/25 p-3 text-xs">
            <div className="flex items-start gap-2.5">
              <span className="text-base leading-none">💡</span>
              <div className="flex flex-col gap-0.5">
                <span className="font-semibold text-cyan-300">Шаг опционален</span>
                <p className="text-muted-foreground text-[11px] leading-relaxed">
                  Если в видео нет постоянного реквизита (оружие, артефакты, гаджеты, техника) — заполнять ничего не нужно. Нажмите «Запустить шаг», и он автоматически завершится за 1 секунду.
                </p>
              </div>
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 text-xs border-cyan-500/30 text-cyan-300 hover:bg-cyan-500/10 shrink-0 self-start sm:self-center"
              onClick={() => setShowManual(true)}
            >
              + Добавить предмет
            </Button>
          </div>
        ) : null}

        {/* Редактор предметов */}
        {showManual && (
          <div className="flex flex-col gap-2.5 rounded-lg border border-white/10 bg-black/25 p-3">
            <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
              <span>Количество предметов:</span>
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
                    <Plus className="h-3 w-3 mr-1" />
                    Добавить
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
                    Предмет {i + 1}
                  </span>
                </div>
                <Textarea
                  value={desc}
                  placeholder="Опиши предмет: название, форма, материал, детали, свечение, эпоха…"
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
                  Укажите описание хотя бы одного предмета (≥ 3 символов)
                </p>
              ) : (
                <span />
              )}
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-7 px-3 text-xs text-muted-foreground hover:text-foreground"
                  onClick={() => setShowManual(false)}
                >
                  Отмена
                </Button>
                <Button
                  type="button"
                  size="sm"
                  className="h-7 px-4 text-xs bg-cyan-600 hover:bg-cyan-500 text-white font-medium"
                  disabled={!canSaveManual || saveManual.isPending}
                  onClick={() => saveManual.mutate()}
                >
                  {saveManual.isPending ? (
                    <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                  ) : (
                    <Save className="mr-1.5 h-3 w-3" />
                  )}
                  Сохранить
                </Button>
              </div>
            </div>
          </div>
        )}
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
          <Package className="h-3 w-3 text-cyan-400" />
          Предметы
          {savedCount > 0 ? (
            <span className="ml-1 text-cyan-300 font-medium">
              ({savedCount})
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
          <div className="rounded-md border border-cyan-500/25 bg-cyan-950/30 p-2 text-[10px] text-cyan-200/90 leading-relaxed">
            <p className="font-semibold text-cyan-300 flex items-center gap-1">
              <span>💡</span> Шаг опционален
            </p>
            <p className="mt-0.5 text-muted-foreground text-[9.5px]">
              Если в видео нет постоянного реквизита — просто нажмите «Запустить шаг».
            </p>
          </div>

          <button
            type="button"
            className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-cyan-300 transition"
            onClick={() => setShowManual((v) => !v)}
          >
            {showManual ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
            <span>
              {showManual ? "Скрыть ручной ввод" : "Ввести описание вручную"}
            </span>
            {savedCount > 0 ? (
              <span className="text-cyan-300">· {savedCount}</span>
            ) : null}
          </button>

          {showManual && (
            <div className="flex flex-col gap-2 rounded-md border border-white/10 bg-black/25 p-2">
              <label className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
                <span>Сколько предметов</span>
                <Input
                  type="number"
                  min={1}
                  max={5}
                  value={count}
                  onChange={(e) => setN(Number(e.target.value))}
                  className="h-6 w-14 text-[11px] bg-black/40 border-white/15 text-center"
                />
              </label>

              {descriptions.map((desc, i) => (
                <div
                  key={i}
                  className="flex flex-col gap-1 rounded-md border border-white/10 bg-black/35 p-1.5"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[10px] font-semibold uppercase tracking-wider text-cyan-300">
                      Предмет {i + 1}
                    </span>
                  </div>
                  <Textarea
                    value={desc}
                    placeholder="Опиши предмет: материал, форма, детали, свечение, эпоха…"
                    onChange={(e) =>
                      setDescriptions((prev) => {
                        const next = [...prev];
                        next[i] = e.target.value;
                        return next;
                      })
                    }
                    className="min-h-[42px] resize-y text-[10.5px] leading-snug bg-black/40 border-white/15"
                    rows={2}
                  />
                </div>
              ))}

              <div className="flex items-center justify-between pt-1">
                <Button
                  type="button"
                  size="sm"
                  className="h-6 px-3 text-[10px] font-semibold bg-cyan-600 hover:bg-cyan-500 text-white"
                  disabled={!canSaveManual || saveManual.isPending}
                  onClick={() => saveManual.mutate()}
                >
                  {saveManual.isPending ? (
                    <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                  ) : (
                    <Save className="mr-1 h-3 w-3" />
                  )}
                  Сохранить
                </Button>

                {savedCount > 0 && (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="h-6 px-2 text-[10px] text-muted-foreground hover:text-destructive"
                    onClick={() => resetItems.mutate()}
                  >
                    Сброс
                  </Button>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}