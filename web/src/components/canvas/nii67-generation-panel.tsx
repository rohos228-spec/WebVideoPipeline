"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Image as ImageIcon, Loader2, Play, ScrollText, Sparkles, Video, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import type { Nii67Card, Nii67Door } from "@/lib/nii67";
import type { Nii67Frame } from "./nii67-frame-menu";

type ModelKind = "text" | "image" | "video";
type CatalogModel = {
  id: string;
  label: string;
  kind?: string;
  provider?: string;
  vendor?: string;
};
type CatalogVendor = { id: string; label: string; models: CatalogModel[] };

const DEFAULT_LLM = "gpt-5.6-sol";
const DEFAULT_IMAGE = "gpt-image-2-vip";
const DEFAULT_VIDEO = "veo-3-1-lite";

type Stage = 1 | 2 | 3;

function kindOf(model: CatalogModel): ModelKind {
  const raw = String(model.kind || "").toLowerCase();
  if (raw === "image" || raw === "video") return raw;
  return "text";
}

function modelLabel(models: CatalogModel[], id: string): string {
  return models.find((m) => m.id === id)?.label || id;
}

/** Модели и три этапа генерации. Пока этап 1 работает на существующей сборке кадров. */
export function Nii67GenerationPanel({
  card,
  frames,
  busy,
  matrixPending,
  onCardChange,
  onFramesChange,
  onSaveFrames,
  onBuild,
  onSaveCard,
}: {
  card: Nii67Card;
  frames: Nii67Frame[];
  busy: boolean;
  matrixPending: boolean;
  onCardChange: (next: Nii67Card) => void;
  onFramesChange: (next: Nii67Frame[]) => void;
  onSaveFrames: (next: Nii67Frame[]) => void;
  onBuild: () => void;
  onSaveCard: () => void;
}) {
  const catalogQuery = useQuery({
    queryKey: ["text-llm-catalog"],
    queryFn: async () => {
      const r = await fetch("/api/text-llm", { cache: "no-store" });
      if (!r.ok) throw new Error("catalog");
      return (await r.json()) as { catalog?: { vendors?: CatalogVendor[] } };
    },
    staleTime: 60_000,
  });

  const vendors = catalogQuery.data?.catalog?.vendors ?? [];
  const allModels = useMemo(() => vendors.flatMap((v) => v.models ?? []), [vendors]);
  const textModels = allModels.filter((m) => kindOf(m) === "text");
  const imageModels = allModels.filter((m) => kindOf(m) === "image");
  const videoModels = allModels.filter((m) => kindOf(m) === "video");

  const saved = card.generation?.models ?? {};
  const [llmId, setLlmId] = useState(saved.llm_model_id || DEFAULT_LLM);
  const [imageId, setImageId] = useState(saved.image_model_id || DEFAULT_IMAGE);
  const [videoId, setVideoId] = useState(saved.video_model_id || DEFAULT_VIDEO);
  const [promptsBlock, setPromptsBlock] = useState<{ doorId: string; index: number } | null>(null);

  useEffect(() => {
    setLlmId(saved.llm_model_id || DEFAULT_LLM);
    setImageId(saved.image_model_id || DEFAULT_IMAGE);
    setVideoId(saved.video_model_id || DEFAULT_VIDEO);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- только при смене карточки/проекта
  }, [card.saved_at, card.generation?.models?.llm_model_id, card.generation?.models?.image_model_id, card.generation?.models?.video_model_id]);

  function selectModel(kind: ModelKind, id: string) {
    const next: Nii67Card = {
      ...card,
      generation: {
        ...(card.generation ?? {}),
        models: {
          ...(card.generation?.models ?? {}),
          llm_model_id: kind === "text" ? id : llmId,
          image_model_id: kind === "image" ? id : imageId,
          video_model_id: kind === "video" ? id : videoId,
        },
      },
    };
    if (kind === "text") setLlmId(id);
    if (kind === "image") setImageId(id);
    if (kind === "video") setVideoId(id);
    onCardChange(next);
  }

  const stage1Ready = frames.length > 0;
  const textFrames = frames.filter((f) => (f as Nii67Frame & { role?: string }).role === "text").length;
  const imageFrames = frames.filter((f) => (f as Nii67Frame & { role?: string }).role !== "text").length;

  function blockFrames(door: Nii67Door, index: number): Nii67Frame[] {
    const prefix = `${door.id}.${index}.`;
    const scoped = frames.filter((f) => String(f.slot_id || "").startsWith(prefix));
    if (scoped.length) return scoped;
    if (index === 0) {
      // старый путь без цепочки: кадры двери не имеют номера блока
      return frames.filter((f) => f.door === door.id);
    }
    return [];
  }

  return (
    <section
      data-testid="nii67-generation"
      className="mb-3 rounded-2xl border border-teal-400/35 bg-gradient-to-b from-teal-950/50 to-[#0c161e] p-3"
    >
      <div className="mb-2 flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-teal-300" />
        <h3 className="text-[12px] font-semibold uppercase tracking-[0.12em] text-teal-100">Генерация</h3>
        <span className="rounded-full border border-teal-400/30 px-2 py-0.5 text-[10px] text-teal-200/80">
          этап 1 из 3
        </span>
      </div>

      <div className="mb-3 grid gap-2 sm:grid-cols-3">
        <ModelSelect
          icon={<Sparkles className="h-3 w-3" />}
          label="LLM-система"
          value={llmId}
          models={textModels}
          onChange={(id) => selectModel("text", id)}
        />
        <ModelSelect
          icon={<ImageIcon className="h-3 w-3" />}
          label="Модель картинки"
          value={imageId}
          models={imageModels}
          onChange={(id) => selectModel("image", id)}
        />
        <ModelSelect
          icon={<Video className="h-3 w-3" />}
          label="Видеомодель"
          value={videoId}
          models={videoModels}
          onChange={(id) => selectModel("video", id)}
        />
      </div>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] text-zinc-500">
        <span>Выбор сохраняется в карточке.</span>
        <Button type="button" size="sm" variant="outline" className="h-6 px-2 text-[10px]" onClick={onSaveCard}>
          Сохранить выбор
        </Button>
      </div>

      <div className="grid gap-2 lg:grid-cols-3">
        <StageCard
          n="1"
          title="Описания и промты"
          status={matrixPending ? "running" : stage1Ready ? "ready" : "idle"}
          line={
            stage1Ready
              ? `${frames.length} кадров · ${textFrames} текстовых · ${imageFrames} с картинкой`
              : "Соберёт кадры, промты и связи по карточке"
          }
          action={
            <Button
              type="button"
              size="sm"
              disabled={busy}
              className="w-full bg-teal-400 text-zinc-950 hover:bg-teal-300"
              onClick={onBuild}
            >
              {matrixPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
              Собрать
            </Button>
          }
        />
        <StageCard
          n="2"
          title="Картинки"
          status="idle"
          line="Будет генерировать по выбранной модели картинки"
          action={
            <Button type="button" size="sm" variant="outline" className="w-full" disabled title="Подключается следующим пакетом">
              Не подключено
            </Button>
          }
        />
        <StageCard
          n="3"
          title="Видео"
          status="idle"
          line="Будет собирать клипы из пар кадров"
          action={
            <Button type="button" size="sm" variant="outline" className="w-full" disabled title="Подключается следующим пакетом">
              Не подключено
            </Button>
          }
        />
      </div>

      <div className="mt-3 rounded-xl border border-white/10 bg-black/20 p-2">
        <div className="mb-1.5 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-400">
          <ScrollText className="h-3.5 w-3.5" />
          Промты по блокам
        </div>
        <div className="grid gap-1.5">
          {card.doors.map((door) =>
            door.blocks.map((block, index) => {
              const list = blockFrames(door, index);
              return (
                <button
                  key={block.id}
                  type="button"
                  data-testid={`nii67-block-prompts-${block.id}`}
                  className="flex items-center justify-between gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-left text-[12px] text-zinc-300 hover:border-teal-400/40 hover:text-teal-100"
                  onClick={() => setPromptsBlock({ doorId: door.id, index })}
                >
                  <span className="truncate">
                    {door.title || door.id} · блок {index + 1}
                    {block.text ? ` — ${block.text.slice(0, 42)}` : " — пусто"}
                  </span>
                  <span className="shrink-0 text-[10px] text-zinc-500">{list.length} промтов</span>
                </button>
              );
            }),
          )}
        </div>
      </div>

      <PromptsDialog
        block={promptsBlock}
        card={card}
        frames={frames}
        onClose={() => setPromptsBlock(null)}
        onFramesChange={onFramesChange}
        onSaveFrames={onSaveFrames}
        blockFrames={blockFrames}
      />
    </section>
  );
}

function ModelSelect({
  icon,
  label,
  value,
  models,
  onChange,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  models: CatalogModel[];
  onChange: (id: string) => void;
}) {
  return (
    <label className="block rounded-xl border border-white/10 bg-black/30 p-2">
      <span className="mb-1 flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-zinc-400">
        {icon}
        {label}
      </span>
      <div className="relative">
        <select
          className="w-full appearance-none rounded-lg border border-white/15 bg-[#11181d] px-2 py-1.5 pr-7 text-[12px] text-zinc-100 outline-none focus:border-teal-400/60"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        >
          {!models.some((m) => m.id === value) ? <option value={value}>{value}</option> : null}
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label} · {m.provider || m.vendor || m.kind}
            </option>
          ))}
        </select>
        <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3 w-3 -translate-y-1/2 text-zinc-500" />
      </div>
    </label>
  );
}

function StageCard({
  n,
  title,
  status,
  line,
  action,
}: {
  n: string;
  title: string;
  status: "idle" | "running" | "ready" | "error";
  line: string;
  action: React.ReactNode;
}) {
  const badge =
    status === "ready"
      ? "готов"
      : status === "running"
        ? "идёт"
        : status === "error"
          ? "ошибка"
          : "не запущен";
  return (
    <div className="rounded-xl border border-white/10 bg-black/25 p-2.5">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold text-zinc-200">
          Этап {n}. {title}
        </span>
        <span
          className={
            status === "ready"
              ? "rounded-full border border-teal-400/40 bg-teal-400/10 px-1.5 py-0.5 text-[10px] text-teal-200"
              : status === "running"
                ? "rounded-full border border-amber-400/40 bg-amber-400/10 px-1.5 py-0.5 text-[10px] text-amber-200"
                : "rounded-full border border-white/10 px-1.5 py-0.5 text-[10px] text-zinc-500"
          }
        >
          {badge}
        </span>
      </div>
      <p className="mb-2 min-h-[30px] text-[11px] leading-snug text-zinc-500">{line}</p>
      {action}
    </div>
  );
}

function PromptsDialog({
  block,
  card,
  frames,
  onClose,
  onFramesChange,
  onSaveFrames,
  blockFrames,
}: {
  block: { doorId: string; index: number } | null;
  card: Nii67Card;
  frames: Nii67Frame[];
  onClose: () => void;
  onFramesChange: (next: Nii67Frame[]) => void;
  onSaveFrames: (next: Nii67Frame[]) => void;
  blockFrames: (door: Nii67Door, index: number) => Nii67Frame[];
}) {
  const door = block ? card.doors.find((d) => d.id === block.doorId) : null;
  const list = block && door ? blockFrames(door, block.index) : [];
  const [draft, setDraft] = useState<Record<string, string>>({});

  useEffect(() => {
    setDraft({});
  }, [block?.doorId, block?.index, frames.length]);

  if (!block || !door) return null;

  function promptOf(frame: Nii67Frame): string {
    const raw = frame as Nii67Frame & { prompt?: string; image_prompt?: string; video_prompt?: string };
    return raw.video_prompt || raw.prompt || raw.image_prompt || "";
  }

  function save() {
    const next = frames.map((frame) => {
      const value = draft[frame.slot_id];
      if (value === undefined) return frame;
      return { ...frame, prompt: value } as Nii67Frame;
    });
    onFramesChange(next);
    onSaveFrames(next);
    setDraft({});
    toast.success("Промты блока сохранены");
  }

  return (
    <Dialog open onOpenChange={(value) => (!value ? onClose() : null)}>
      <DialogContent
        overlayClassName="z-[170]"
        className="z-[180] flex h-[min(760px,88vh)] w-[min(920px,94vw)] max-w-none flex-col gap-0 overflow-hidden border-teal-500/25 bg-[#071018] p-0 text-zinc-100"
      >
        <header className="flex shrink-0 items-center justify-between border-b border-white/10 px-4 py-3">
          <div>
            <DialogTitle className="text-sm font-semibold">
              Промты блока · {door.title || door.id} · {block.index + 1}
            </DialogTitle>
            <p className="text-[11px] text-zinc-500">
              Показаны все кадры блока. Правка меняет промт и требует перегенерации картинок/видео.
            </p>
          </div>
          <button type="button" className="rounded-md p-1.5 text-zinc-400 hover:bg-white/10 hover:text-white" onClick={onClose}>
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
          {!list.length ? (
            <p className="text-[12px] text-zinc-500">Для этого блока ещё нет кадров. Сначала нажми «Собрать» на этапе 1.</p>
          ) : null}
          {list.map((frame, i) => {
            const raw = frame as Nii67Frame & { prompt?: string; role?: string; block_index?: number };
            const text = draft[frame.slot_id] ?? promptOf(frame);
            const prev = list[i - 1];
            return (
              <div key={frame.slot_id} className="rounded-xl border border-white/10 bg-black/25 p-2.5">
                <div className="mb-1 flex flex-wrap items-center gap-2 text-[11px]">
                  <span className="font-mono text-teal-200">{frame.slot_id}</span>
                  <span className="rounded border border-white/10 px-1.5 py-0.5 text-zinc-400">{raw.role || frame.beat || "кадр"}</span>
                  {prev ? <span className="text-emerald-300/80">начало = конец предыдущего</span> : <span className="text-zinc-500">старт ветки</span>}
                  <span className="text-zinc-500">refs: {frame.refs?.length ? frame.refs.map((r) => r.name || r.type).filter(Boolean).join(", ") : "нет"}</span>
                </div>
                <textarea
                  className="min-h-[72px] w-full resize-y rounded-lg border border-white/10 bg-[#0b1217] px-2 py-1.5 font-mono text-[11px] leading-snug text-zinc-200 outline-none focus:border-teal-400/60"
                  value={text}
                  placeholder="Промт не собран"
                  onChange={(e) => setDraft((prevDraft) => ({ ...prevDraft, [frame.slot_id]: e.target.value }))}
                />
              </div>
            );
          })}
        </div>
        <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-white/10 px-4 py-2.5">
          <Button type="button" size="sm" variant="outline" onClick={onClose}>
            Закрыть
          </Button>
          <Button type="button" size="sm" disabled={!list.length || !Object.keys(draft).length} onClick={save}>
            Сохранить промты
          </Button>
        </footer>
      </DialogContent>
    </Dialog>
  );
}
