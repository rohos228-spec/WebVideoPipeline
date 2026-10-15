"use client";

/**
 * Каталог промтов в стиле «Каталог стилей» (Генерация → Помощник):
 * тёмная сетка карточек с превью, бейдж сверху-слева, заголовок + краткое
 * описание снизу, фильтр-чипы со счётчиками, поиск, бирюзовая рамка «Выбран»,
 * нижняя панель выбранного. Клик по карточке — окно с крупным изображением,
 * описанием и полным текстом промта (копирование).
 */

import type React from "react";
import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { Check, Copy, Eye, ExternalLink, ImageOff, Search, Sparkles, X } from "lucide-react";
import { toast } from "sonner";
import type { NodeResultItem as BaseItem } from "@/lib/node-result-resolver";

export type NodeResultItem = BaseItem & { category?: string; description?: string };
import { cn } from "@/lib/utils";

type Filter = string;

const chipCls = (active: boolean) =>
  cn(
    "rounded-full px-3 py-1 text-[11px] font-medium transition",
    active
      ? "bg-[#22d3ee] font-semibold text-black shadow-[0_0_12px_rgba(34,211,238,0.3)]"
      : "bg-white/[0.05] text-white/60 hover:bg-white/[0.08] hover:text-white",
  );

/** Короткое описание: первое предложение промта, до ~140 символов. */
export function promptShortDesc(text: string | null | undefined): string {
  const t = (text ?? "").replace(/\s+/g, " ").trim();
  if (!t) return "Пустой промт";
  const m = t.match(/^(.{20,160}?[.!?…])(\s|$)/);
  const first = m ? m[1] : t;
  return first.length > 140 ? `${first.slice(0, 137).trimEnd()}…` : first;
}

function catSeed(c: string) {
  let h = 0;
  for (const ch of c) h = (h * 31 + ch.charCodeAt(0)) % 997;
  return h + 1;
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success("Промт скопирован");
  } catch {
    toast.error("Не удалось скопировать");
  }
}

function CardBg({ src, seed, label }: { src?: string | null; seed: number; label?: string }) {
  const [broken, setBroken] = useState(false);
  if (src && !broken) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={src}
        alt=""
        loading="lazy"
        onError={() => setBroken(true)}
        className="h-full w-full object-cover"
      />
    );
  }
  const hue = (seed * 47) % 360;
  return (
    <div
      className="flex h-full w-full items-center justify-center"
      style={{
        background: `radial-gradient(circle at 30% 20%, hsla(${hue},70%,45%,0.45), transparent 60%), linear-gradient(135deg, #17171f, #0d0d12)`,
      }}
    >
      <div className="flex flex-col items-center gap-1 -mt-6">
        <Sparkles className="h-6 w-6 text-white/25" />
        {label ? <span className="max-w-[90%] text-center text-[10px] font-semibold uppercase tracking-[0.18em] text-white/25">{label}</span> : null}
      </div>
    </div>
  );
}

export function PromptDetailModal({
  item,
  onClose,
  onPrev,
  onNext,
}: {
  item: NodeResultItem;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      } else if (e.key === "ArrowLeft" && onPrev) onPrev();
      else if (e.key === "ArrowRight" && onNext) onNext();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose, onPrev, onNext]);

  const content = item.content ?? "";
  const body = (
    <div
      data-prompt-detail-open=""
      className="fixed inset-0 z-[10000] flex items-center justify-center p-4"
      onPointerDown={(e) => e.stopPropagation()}
    >
      <div
        className="absolute inset-0 bg-black/75 backdrop-blur-[3px] animate-in fade-in duration-200"
        onClick={onClose}
      />
      <div className="relative z-10 flex max-h-[94vh] w-[min(96vw,1100px)] flex-col overflow-hidden rounded-2xl border border-white/12 bg-[#101015] shadow-[0_0_80px_rgba(0,0,0,0.9)] animate-in zoom-in-95 duration-200">
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-white/10 px-4">
          <div className="flex items-center gap-2">
            <span className="rounded bg-[#22d3ee]/15 px-1.5 py-0.5 text-[10px] font-semibold text-[#22d3ee]">
              {item.category ?? (item.frameNumber != null ? `Кадр ${item.frameNumber}` : "Промт")}
            </span>
            <span className="text-[14px] font-semibold text-white">{item.label}</span>
          </div>
          <div className="flex items-center gap-1">
            {onPrev && (
              <button type="button" onClick={onPrev} className="rounded-lg px-2 py-1 text-[12px] text-white/60 hover:bg-white/10 hover:text-white">
                ← Пред.
              </button>
            )}
            {onNext && (
              <button type="button" onClick={onNext} className="rounded-lg px-2 py-1 text-[12px] text-white/60 hover:bg-white/10 hover:text-white">
                След. →
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              title="Закрыть (Esc)"
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-white/50 hover:bg-white/10 hover:text-white"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {/* 1. Крупное изображение */}
          <div className="relative flex items-center justify-center bg-black">
            {item.previewUrl ? (
              <>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={item.previewUrl} alt={item.label} className="max-h-[62vh] w-full object-contain" />
                <a
                  href={item.previewUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="absolute right-3 top-3 inline-flex items-center gap-1 rounded-md bg-black/70 px-2 py-1 text-[11px] text-white/80 backdrop-blur hover:text-white"
                >
                  <ExternalLink className="h-3 w-3" /> Оригинал
                </a>
              </>
            ) : (
              <div className="flex h-[200px] w-full flex-col items-center justify-center gap-2 text-white/35">
                <ImageOff className="h-8 w-8" />
                <span className="text-[12px]">Изображение для этого промта ещё не сгенерировано</span>
              </div>
            )}
          </div>
          {/* 2. Описание */}
          <div className="space-y-1 border-b border-white/10 px-5 py-4">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-white/45">Описание</div>
            <div className="text-[13px] leading-relaxed text-white/85">{item.description || promptShortDesc(content)}</div>
          </div>
          {/* 3. Полный промт */}
          <div className="space-y-2 px-5 py-4">
            <div className="flex items-center justify-between">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-white/45">
                Промт · {content.length} симв.
              </div>
              <button
                type="button"
                onClick={() => void copyText(content)}
                className="inline-flex items-center gap-1.5 rounded-lg bg-[#22d3ee] px-3 py-1.5 text-[11px] font-semibold text-black hover:brightness-110"
              >
                <Copy className="h-3.5 w-3.5" /> Копировать
              </button>
            </div>
            <pre className="whitespace-pre-wrap break-words rounded-lg border border-white/10 bg-[#16161d] p-3 font-mono text-[12px] leading-relaxed text-white/85 select-text">
              {content || "—"}
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
  return typeof document !== "undefined" ? createPortal(body, document.body) : body;
}

export function PromptCardCatalog({
  items,
  title = "Каталог промтов",
  subtitle,
  selectedId: selectedIdProp,
  onSelect,
  heightClass = "h-[70vh]",
  badgeFor,
  headerExtra,
  mode = "browse",
  onDone,
  onClose,
  doneLabel = "Готово",
}: {
  items: NodeResultItem[];
  title?: string;
  subtitle?: string;
  /** Контролируемый выбор (например, выбранный файл промта ноды). */
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  heightClass?: string;
  /** Доп. бейдж справа сверху вместо точки (например «Активен»). */
  badgeFor?: (item: NodeResultItem) => React.ReactNode;
  headerExtra?: React.ReactNode;
  /** picker: клик выбирает, двойной клик / глаз — просмотр, «Готово» применяет. */
  mode?: "browse" | "picker";
  onDone?: (id: string) => void;
  onClose?: () => void;
  doneLabel?: string;
}) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [selectedIdState, setSelectedIdState] = useState(items[0]?.id ?? "");
  const selectedId = selectedIdProp ?? selectedIdState;
  const setSelectedId = (id: string) => {
    setSelectedIdState(id);
    onSelect?.(id);
  };
  const [openId, setOpenId] = useState<string | null>(null);

  useEffect(() => {
    if (selectedIdProp == null && !items.some((i) => i.id === selectedIdState)) {
      setSelectedIdState(items[0]?.id ?? "");
    }
  }, [items, selectedIdState, selectedIdProp]);

  const cats = useMemo(() => {
    const m = new Map<string, number>();
    for (const i of items) if (i.category) m.set(i.category, (m.get(i.category) ?? 0) + 1);
    return Array.from(m.entries());
  }, [items]);
  const withImage = useMemo(() => items.filter((i) => i.previewUrl).length, [items]);

  const displayed = useMemo(() => {
    const q = search.trim().toLowerCase();
    return items.filter((i) => {
      if (cats.length && filter !== "all" && i.category !== filter) return false;
      if (filter === "with_image" && !i.previewUrl) return false;
      if (filter === "no_image" && i.previewUrl) return false;
      if (!q) return true;
      return `${i.label} ${i.content ?? ""}`.toLowerCase().includes(q);
    });
  }, [items, search, filter, cats]);

  const selected = items.find((i) => i.id === selectedId);
  const openIdx = openId ? displayed.findIndex((i) => i.id === openId) : -1;
  const openItem = openIdx >= 0 ? displayed[openIdx] : items.find((i) => i.id === openId);

  return (
    <div className={cn("flex min-h-0 flex-1 flex-col", heightClass, "overflow-hidden rounded-xl border border-white/10 bg-[#101015]")}>
      <div className="flex shrink-0 items-center gap-2.5 border-b border-white/10 px-4 py-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#22d3ee]/10 text-[#22d3ee]">
          <Sparkles className="h-4 w-4" />
        </div>
        <div>
          <div className="text-[14px] font-semibold text-white">{title}</div>
          <div className="text-[11px] text-white/50">{subtitle ?? `${items.length} промтов${cats.length ? ` · ${cats.length} категорий` : ""}`}</div>
        </div>
        {headerExtra || onClose ? (
          <div className="ml-auto flex items-center gap-1.5">
            {headerExtra}
            {onClose ? (
              <button
                type="button"
                onClick={onClose}
                title="Закрыть (Esc)"
                className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-white/50 transition hover:bg-white/10 hover:text-white"
              >
                <X className="h-4 w-4" />
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="shrink-0 space-y-2.5 border-b border-white/10 bg-white/[0.015] px-4 py-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-white/40" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={cats.length ? "Поиск по промтам, описанию, тексту…" : "Поиск по кадрам и тексту промта…"}
            className="w-full rounded-lg border border-white/10 bg-[#16161d] py-1.5 pl-8 pr-7 text-[12px] text-white/90 placeholder-white/35 transition focus:border-[#22d3ee]/50 focus:outline-none focus:ring-1 focus:ring-[#22d3ee]/30"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-white/40 hover:text-white"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <button type="button" onClick={() => setFilter("all")} className={chipCls(filter === "all")}>
            Все ({items.length})
          </button>
          {cats.length ? cats.map(([c, n]) => (
            <button key={c} type="button" onClick={() => setFilter(c)} className={chipCls(filter === c)}>
              {c} ({n})
            </button>
          )) : (<>
          <button type="button" onClick={() => setFilter("with_image")} className={chipCls(filter === "with_image")}>
            С изображением ({withImage})
          </button>
          <button type="button" onClick={() => setFilter("no_image")} className={chipCls(filter === "no_image")}>
            Без изображения ({items.length - withImage})
          </button></>)}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {displayed.length === 0 ? (
          <p className="py-10 text-center text-[12px] text-white/45">Ничего не найдено</p>
        ) : (
          <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
            {displayed.map((it, idx) => {
              const isCurrent = it.id === selectedId;
              return (
                <div
                  key={it.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => {
                    setSelectedId(it.id);
                    if (mode !== "picker") setOpenId(it.id);
                  }}
                  onDoubleClick={() => setOpenId(it.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setSelectedId(it.id);
                      setOpenId(it.id);
                    }
                  }}
                  className={cn(
                    "group relative h-[152px] cursor-pointer select-none overflow-hidden rounded-xl border text-left transition-all duration-300",
                    isCurrent
                      ? "border-[#22d3ee] ring-2 ring-[#22d3ee]/50 shadow-[0_0_24px_rgba(34,211,238,0.35)]"
                      : "border-white/10 bg-[#16161c] hover:border-white/30 hover:shadow-[0_10px_30px_rgba(0,0,0,0.6)]",
                  )}
                >
                  <div className="absolute inset-0 h-full w-full overflow-hidden transition-transform duration-500 ease-out group-hover:scale-105">
                    <CardBg src={it.previewUrl} seed={it.category ? catSeed(it.category) : (it.frameNumber ?? idx + 1)} label={it.category} />
                  </div>
                  <div className="pointer-events-none absolute inset-x-0 top-0 z-20 flex items-center justify-between p-2">
                    <span className="rounded border border-white/10 bg-black/65 px-1.5 py-0.5 text-[9px] font-medium tracking-wide text-white/85 shadow-sm backdrop-blur-md">
                      {it.category ?? (it.frameNumber != null ? `Кадр ${it.frameNumber}` : "Промт")}
                    </span>
                    {badgeFor?.(it) ?? (isCurrent ? (
                      <span className="flex items-center gap-1 rounded bg-[#22d3ee] px-1.5 py-0.5 text-[9.5px] font-bold text-black shadow-md">
                        <Check className="h-3 w-3" /> Выбран
                      </span>
                    ) : (
                      <span
                        className={cn(
                          "h-2.5 w-2.5 rounded-full shadow-[0_0_8px_currentColor]",
                          it.previewUrl ? "bg-[#22d3ee] text-[#22d3ee]" : "bg-white/30 text-white/30",
                        )}
                      />
                    ))}
                  </div>
                  <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 flex flex-col justify-end bg-gradient-to-t from-black/95 via-black/70 to-transparent p-2.5 pt-10 transition-opacity duration-200 group-hover:opacity-0">
                    <div className="line-clamp-1 text-[13px] font-bold text-white drop-shadow-md">{it.label}</div>
                    <div className="mt-0.5 line-clamp-1 text-[10.5px] text-white/60">{(it.description || promptShortDesc(it.content))}</div>
                  </div>
                  <div className="absolute inset-0 z-30 flex flex-col justify-between bg-black/90 p-2.5 opacity-0 backdrop-blur-[3px] transition-all duration-200 group-hover:opacity-100">
                    <div className="min-w-0">
                      <div className="line-clamp-1 text-[13px] font-bold text-[#22d3ee]">{it.label}</div>
                      <div className="mt-1 line-clamp-5 font-mono text-[10px] leading-snug text-white/80">
                        {it.content}
                      </div>
                    </div>
                    <div className="flex items-center justify-between gap-1">
                      <span className="text-[10px] text-white/45">{mode === "picker" ? "Клик — выбрать" : "Клик — открыть"}</span>
                      <button
                        type="button"
                        title="Просмотр: изображение, описание, полный промт"
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenId(it.id);
                        }}
                        className="ml-auto rounded p-1 text-white/70 transition hover:bg-white/20 hover:text-white"
                      >
                        <Eye className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        title="Копировать промт"
                        onClick={(e) => {
                          e.stopPropagation();
                          void copyText(it.content ?? "");
                        }}
                        className="rounded p-1 text-white/70 transition hover:bg-white/20 hover:text-white"
                      >
                        <Copy className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {selected && (
        <div className="flex shrink-0 items-center justify-between gap-3 border-t border-white/10 bg-[#0c0c10] px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-2">
            <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-[#22d3ee] shadow-[0_0_8px_#22d3ee]" />
            <div className="min-w-0">
              <div className="truncate text-[12px] font-semibold text-white">{selected.label}</div>
              <div className="truncate text-[10.5px] text-white/50">{(selected.description || promptShortDesc(selected.content))}</div>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <button
              type="button"
              onClick={() => void copyText(selected.content ?? "")}
              className="inline-flex items-center gap-1.5 rounded-lg border border-white/12 bg-white/[0.04] px-3 py-1.5 text-[11px] font-medium text-white/70 hover:border-white/25 hover:text-white"
            >
              <Copy className="h-3.5 w-3.5" /> Копировать
            </button>
            {mode === "picker" ? (
              <>
                <button
                  type="button"
                  onClick={() => setOpenId(selected.id)}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-white/12 bg-white/[0.04] px-3 py-1.5 text-[11px] font-medium text-white/70 hover:border-white/25 hover:text-white"
                >
                  <Eye className="h-3.5 w-3.5" /> Просмотр
                </button>
                <button
                  type="button"
                  onClick={() => onDone?.(selected.id)}
                  className="rounded-lg bg-[#22d3ee] px-4 py-1.5 text-[11px] font-semibold text-black hover:brightness-110"
                >
                  {doneLabel}
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={() => setOpenId(selected.id)}
                className="rounded-lg bg-[#22d3ee] px-4 py-1.5 text-[11px] font-semibold text-black hover:brightness-110"
              >
                Открыть
              </button>
            )}
          </div>
        </div>
      )}

      {openItem && (
        <PromptDetailModal
          item={openItem}
          onClose={() => setOpenId(null)}
          onPrev={openIdx > 0 ? () => { setOpenId(displayed[openIdx - 1].id); setSelectedId(displayed[openIdx - 1].id); } : undefined}
          onNext={
            openIdx >= 0 && openIdx < displayed.length - 1
              ? () => { setOpenId(displayed[openIdx + 1].id); setSelectedId(displayed[openIdx + 1].id); }
              : undefined
          }
        />
      )}
    </div>
  );
}


/** Окно-выборщик промта в стиле «Каталог стилей» (шторка справа на всю высоту). */
export function PromptCatalogPicker({
  open,
  onOpenChange,
  items,
  title,
  subtitle,
  selectedId,
  onDone,
  doneLabel,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  items: NodeResultItem[];
  title: string;
  subtitle?: string;
  selectedId?: string | null;
  onDone: (id: string) => void;
  doneLabel?: string;
}) {
  const [pick, setPick] = useState<string | null>(selectedId ?? null);
  useEffect(() => {
    if (open) setPick(selectedId ?? items[0]?.id ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, selectedId]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !document.querySelector("[data-prompt-detail-open]")) onOpenChange(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);
  if (!open || typeof document === "undefined") return null;
  return createPortal(
    <div className="fixed inset-0 z-[9999] flex justify-end" onPointerDown={(e) => e.stopPropagation()}>
      <div
        className="fixed inset-0 bg-black/65 backdrop-blur-[3px] animate-in fade-in duration-200"
        onClick={() => onOpenChange(false)}
      />
      <aside
        className="relative z-10 flex h-full w-[min(96vw,1260px)] flex-col shadow-[0_0_80px_rgba(0,0,0,0.9)] animate-in slide-in-from-right duration-250"
        aria-label={title}
      >
        <PromptCardCatalog
          mode="picker"
          items={items}
          title={title}
          subtitle={subtitle}
          selectedId={pick}
          onSelect={setPick}
          heightClass="h-full rounded-none border-0 border-l"
          onClose={() => onOpenChange(false)}
          doneLabel={doneLabel}
          onDone={(id) => {
            onDone(id);
            onOpenChange(false);
          }}
        />
      </aside>
    </div>,
    document.body,
  );
}
