"use client";

import { useEffect, useRef, useState } from "react";
import type { Nii67Frame } from "./nii67-frame-menu";
import { Nii67GenerationPanel } from "./nii67-generation-panel";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ListChecks,
  ChevronUp,
  Copy,
  DoorOpen,
  Eye,
  Flag,
  GripVertical,
  ImageIcon,
  Loader2,
  Plus,
  RotateCcw,
  Save,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { clearNii67ProjectViews } from "@/lib/nii67-project-state";
import { errorMessageFromUnknown } from "@/lib/error-message";
import {
  BLOCK_KINDS,
  BLOCK_OUTCOME_OPTIONS,
  CREATURE_TYPES,
  FINALE_LIMIT,
  GENERATE_HINT,
  KIND_LABEL,
  KIND_MARK,
  MATRIX_CREATURE_ACTION_OPTIONS,
  MATRIX_MOVE_OPTIONS,
  MATRIX_STATE_FIELDS,
  TEMPERAMENTS,
  type BlockKind,
  type BlockOutcome,
  type MatrixCreatureAction,
  type MatrixMove,
  type Nii67Block,
  type Nii67Card,
  type Nii67Door,
  type Nii67MatrixState,
  cardFromUnknown,
  defaultCard,
  blockDoorPatch,
  addDoorBlock,
  removeDoorBlock,
  updateBlock,
  updateDoor,
  updateFinale,
  updateHandsImage,
  updateIntroVideo,
  updatePortal,
} from "@/lib/nii67";
import { cn } from "@/lib/utils";

const fieldClass =
  "w-full resize-y rounded-lg border border-white/10 bg-black/35 px-2.5 py-2 text-[13px] leading-snug text-zinc-100 placeholder:text-zinc-500 focus:border-teal-400/50 focus:outline-none";

const selectClass =
  "w-full rounded-lg border border-white/10 bg-black/35 px-2 py-1.5 text-[12px] text-zinc-100 focus:border-teal-400/50 focus:outline-none";

function Field({
  label,
  value,
  onChange,
  placeholder,
  maxLength,
  rows = 3,
  testId,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  maxLength?: number;
  rows?: number;
  testId?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center justify-between gap-2 text-[10px] font-medium uppercase tracking-wide text-zinc-400">
        <span>{label}</span>
        {maxLength ? (
          <span className="tabular-nums text-zinc-500">
            {value.length}/{maxLength}
          </span>
        ) : null}
      </span>
      <textarea
        data-testid={testId}
        value={value}
        rows={rows}
        maxLength={maxLength}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className={fieldClass}
      />
    </label>
  );
}

function ShortField({
  label,
  value,
  onChange,
  placeholder,
  testId,
  maxLength = 180,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  testId?: string;
  maxLength?: number;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[10px] font-medium uppercase tracking-wide text-zinc-400">
        {label}
      </span>
      <input
        data-testid={testId}
        value={value}
        maxLength={maxLength}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-lg border border-white/10 bg-black/35 px-2.5 py-1.5 text-[13px] text-zinc-100 placeholder:text-zinc-500 focus:border-teal-400/50 focus:outline-none"
      />
    </label>
  );
}

function ChipRow({
  label,
  options,
  value,
  onChange,
  testId,
}: {
  label: string;
  options: readonly { id: string; label: string }[];
  value: string;
  onChange: (id: string) => void;
  testId: string;
}) {
  return (
    <div>
      <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-zinc-400">{label}</div>
      <div className="flex flex-wrap gap-1">
        {options.map((option) => {
          const active = value === option.id;
          return (
            <button
              key={option.id}
              type="button"
              data-testid={`${testId}-${option.id}`}
              aria-pressed={active}
              className={cn(
                "rounded-full border px-2 py-0.5 text-[11px] transition",
                active
                  ? "border-teal-300/70 bg-teal-400/20 text-teal-50"
                  : "border-white/10 text-zinc-300 hover:border-white/30",
              )}
              onClick={() => onChange(active ? "" : option.id)}
            >
              {option.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function ImageSlot({
  prompt,
  fileName,
  previewUrl,
  onPrompt,
  onFile,
  testId,
  placeholder,
}: {
  prompt: string;
  fileName: string;
  previewUrl?: string;
  onPrompt: (value: string) => void;
  onFile: (file: File) => void;
  testId: string;
  placeholder: string;
}) {
  return (
    <div className="rounded-lg border border-dashed border-teal-400/30 bg-black/25 p-2">
      <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide text-teal-200/80">
        <ImageIcon className="h-3 w-3" />
        Слот картинки
      </div>
      <div className="mb-2 flex items-center gap-2">
        <div className="flex h-16 w-24 shrink-0 items-center justify-center overflow-hidden rounded-md border border-white/10 bg-zinc-950">
          {previewUrl ? (
            // локальный предпросмотр выбранного файла; в карточку пишется только имя
            <img src={previewUrl} alt="" className="h-full w-full object-cover" />
          ) : (
            <ImageIcon className="h-4 w-4 text-zinc-600" />
          )}
        </div>
        <label className="min-w-0 flex-1 text-[11px] text-zinc-400">
          <span className="mb-1 block truncate">{fileName || "Файл не выбран"}</span>
          <input
            data-testid={`${testId}-file`}
            type="file"
            accept="image/*"
            className="block w-full text-[11px] text-zinc-300 file:mr-2 file:rounded-md file:border-0 file:bg-teal-500/20 file:px-2 file:py-1 file:text-[11px] file:text-teal-100"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onFile(file);
            }}
          />
        </label>
      </div>
      <textarea
        data-testid={`${testId}-prompt`}
        value={prompt}
        rows={2}
        maxLength={4000}
        placeholder={placeholder}
        onChange={(event) => onPrompt(event.target.value)}
        className={fieldClass}
      />
    </div>
  );
}

function UploadSlot({
  label,
  accept,
  testId,
  fileName,
  previewUrl,
  busy,
  onFile,
}: {
  label: string;
  accept: string;
  testId: string;
  fileName: string;
  previewUrl?: string;
  busy: boolean;
  onFile: (file: File) => void;
}) {
  return (
    <label className="block rounded-lg border border-dashed border-teal-400/30 bg-black/25 p-2 text-[11px] text-zinc-400">
      <span className="mb-1 block font-medium uppercase tracking-wide text-teal-200/80">{label}</span>
      {previewUrl ? (
        <img src={previewUrl} alt="" className="mb-1.5 h-28 w-full rounded-md border border-white/10 object-cover" />
      ) : null}
      <span className="mb-1 block truncate">{busy ? "Загрузка…" : fileName || "Файл не выбран"}</span>
      <input
        data-testid={testId}
        type="file"
        accept={accept}
        disabled={busy}
        className="block w-full text-[11px] text-zinc-300 file:mr-2 file:rounded-md file:border-0 file:bg-teal-500/20 file:px-2 file:py-1 file:text-[11px] file:text-teal-100"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onFile(file);
        }}
      />
    </label>
  );
}

function InsertMenu({
  testId,
  label,
  onInsert,
}: {
  testId: string;
  label: string;
  onInsert: (kind: BlockKind) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="py-1">
      <button
        type="button"
        data-testid={testId}
        className="flex w-full items-center justify-center gap-1 rounded-lg border border-dashed border-teal-400/30 px-2 py-1.5 text-[11px] font-medium text-teal-200 hover:bg-teal-400/10"
        onClick={() => setOpen((value) => !value)}
      >
        <Plus className="h-3 w-3" />
        {label}
      </button>
      {open ? (
        <div className="mt-1 overflow-hidden rounded-lg border border-white/10 bg-zinc-950">
          {BLOCK_KINDS.map((kind) => (
            <button
              key={kind}
              type="button"
              data-testid={`${testId}-${kind}`}
              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[12px] text-zinc-200 hover:bg-white/[0.05]"
              onClick={() => {
                onInsert(kind);
                setOpen(false);
              }}
            >
              <span className="w-4 text-center font-semibold text-teal-300">{KIND_MARK[kind]}</span>
              {KIND_LABEL[kind]}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function BlockImage({
  label,
  testId,
  fileName,
  previewUrl,
  onFile,
}: {
  label: string;
  testId: string;
  fileName: string;
  previewUrl?: string;
  onFile: (file: File) => void;
}) {
  return (
    <div className="rounded-lg border border-dashed border-teal-400/30 bg-black/25 p-2">
      <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide text-teal-200/80">
        <ImageIcon className="h-3 w-3" />
        {label}
      </div>
      <div className="flex items-center gap-2">
        <div className="flex h-16 w-24 shrink-0 items-center justify-center overflow-hidden rounded-md border border-white/10 bg-zinc-950">
          {previewUrl ? (
            <img src={previewUrl} alt="" className="h-full w-full object-cover" />
          ) : (
            <ImageIcon className="h-4 w-4 text-zinc-600" />
          )}
        </div>
        <label className="min-w-0 flex-1 text-[11px] text-zinc-400">
          <span className="mb-1 block truncate">{fileName || "Файл не выбран"}</span>
          <input
            data-testid={`${testId}-file`}
            type="file"
            accept="image/*"
            className="block w-full text-[11px] text-zinc-300 file:mr-2 file:rounded-md file:border-0 file:bg-teal-500/20 file:px-2 file:py-1 file:text-[11px] file:text-teal-100"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onFile(file);
            }}
          />
        </label>
      </div>
    </div>
  );
}

function BlockCard({
  block,
  projectId,
  previewUrl,
  previewUrl2,
  onPatch,
  onDelete,
  onFile,
}: {
  block: Nii67Block;
  projectId: number;
  previewUrl?: string;
  previewUrl2?: string;
  onPatch: (patch: Parameters<typeof updateBlock>[3]) => void;
  onDelete: () => void;
  onFile: (slot: 1 | 2, file: File) => void;
}) {
  const finalPreview =
    previewUrl ?? (block.image_name ? api.nii67AssetUrl(projectId, block.image_name) : undefined);
  const deathPreview =
    previewUrl2 ?? (block.image_name2 ? api.nii67AssetUrl(projectId, block.image_name2) : undefined);
  return (
    <article
      data-testid={`nii67-block-${block.id}`}
      className="rounded-xl border border-white/10 bg-[#0d1720] shadow-sm"
    >
      <div className="flex flex-wrap items-center gap-1.5 px-2 py-2">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-teal-400/15 text-[12px] font-bold text-teal-200">
          {KIND_MARK[block.kind]}
        </span>
        <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-zinc-100">
          {KIND_LABEL[block.kind]}
        </span>
        <button
          type="button"
          data-testid={`nii67-delete-${block.id}`}
          className="rounded-md px-1.5 py-1 text-[11px] text-rose-300 hover:bg-rose-500/10"
          onClick={onDelete}
        >
          <Trash2 className="mr-1 inline h-3 w-3" />
          Удалить
        </button>
      </div>
      <div className="flex flex-col gap-2 border-t border-white/5 px-2.5 py-2.5">
        {block.kind === "death" ? (
          <>
            <BlockImage
              label="Финальное изображение"
              testId={`nii67-block-final-${block.id}`}
              fileName={block.image_name}
              previewUrl={finalPreview}
              onFile={(file) => onFile(1, file)}
            />
            <Field
              label="Описание действия"
              value={block.text}
              testId={`nii67-block-action-${block.id}`}
              placeholder="Что герой делает в момент смерти"
              onChange={(text) => onPatch({ text })}
            />
            <Field
              label="Описание иллюстрации смерти"
              value={block.text2}
              testId={`nii67-block-death-illustration-${block.id}`}
              placeholder="Что изображено на иллюстрации смерти"
              onChange={(text2) => onPatch({ text2 })}
            />
            <BlockImage
              label="Иллюстрация смерти"
              testId={`nii67-block-death-image-${block.id}`}
              fileName={block.image_name2}
              previewUrl={deathPreview}
              onFile={(file) => onFile(2, file)}
            />
          </>
        ) : null}
        {block.kind === "creature" ? (
          <>
            <Field
              label="Описание появления"
              value={block.character.position}
              testId={`nii67-block-creature-appear-${block.id}`}
              placeholder="Как существо появляется в кадре"
              onChange={(position) => onPatch({ character: { enabled: true, position } })}
            />
            <Field
              label="Описание действия"
              value={block.character.behavior}
              testId={`nii67-block-creature-action-${block.id}`}
              placeholder="Что существо делает"
              onChange={(behavior) => onPatch({ character: { enabled: true, behavior } })}
            />
            <label className="block">
              <span className="mb-1 block text-[10px] font-medium uppercase tracking-wide text-zinc-400">
                Исход существа
              </span>
              <select
                data-testid={`nii67-block-outcome-${block.id}`}
                value={block.outcome}
                onChange={(event) => onPatch({ outcome: event.target.value as BlockOutcome })}
                className={selectClass}
              >
                {BLOCK_OUTCOME_OPTIONS.map((option) => (
                  <option key={option.id} value={option.id} className="bg-zinc-900">
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </>
        ) : null}
        {block.kind === "neutral" ? (
          <>
            <BlockImage
              label="Кадр: где оказался герой"
              testId={`nii67-block-place-${block.id}`}
              fileName={block.image_name}
              previewUrl={finalPreview}
              onFile={(file) => onFile(1, file)}
            />
            <Field
              label="Что видит герой"
              value={block.text}
              testId={`nii67-block-place-text-${block.id}`}
              placeholder="Место, в котором оказался герой после двери"
              onChange={(text) => onPatch({ text })}
            />
          </>
        ) : null}
        {block.kind === "back" ? (
          <>
            <Field
              label="Описание мира"
              value={block.text}
              testId={`nii67-block-back-text-${block.id}`}
              placeholder="Что герой увидел за дверью и почему вернулся"
              onChange={(text) => onPatch({ text })}
            />
            <BlockImage
              label="Тематическая иллюстрация"
              testId={`nii67-block-back-image-${block.id}`}
              fileName={block.image_name}
              previewUrl={finalPreview}
              onFile={(file) => onFile(1, file)}
            />
          </>
        ) : null}
      </div>
    </article>
  );
}

function DoorMatrixFields({
  door,
  onChange,
  compact = false,
}: {
  door: Nii67Door;
  onChange: (mutator: (card: Nii67Card) => Nii67Card) => void;
  compact?: boolean;
}) {
  const filled = MATRIX_STATE_FIELDS.filter((item) => door.state[item.key].trim()).length;
  return (
    <div className={cn("flex flex-col gap-1.5", compact && "rounded-lg border border-white/10 bg-black/20 p-2")}>
      {compact ? (
        <div className="flex items-center justify-between text-[11px] font-semibold text-teal-100">
          <span>{door.title}</span>
          <span className="text-[10px] font-normal text-zinc-500">
            {filled}/{MATRIX_STATE_FIELDS.length}
          </span>
        </div>
      ) : null}
      <details
        data-testid={`nii67-matrix-${door.id}`}
        className="group rounded-lg border border-white/10 bg-black/20"
      >
        <summary className="flex cursor-pointer list-none items-center gap-1.5 px-2 py-1.5 text-[10px] font-medium uppercase tracking-wide text-zinc-400 transition hover:text-zinc-200">
          <ChevronDown className="h-3 w-3 transition group-open:rotate-180" />
          Доп. данные для промтов
          <span className="ml-auto tabular-nums text-zinc-500">
            {filled}/{MATRIX_STATE_FIELDS.length}
          </span>
        </summary>
        <div className={cn("overflow-y-auto border-t border-white/10 p-2", compact ? "max-h-64" : "max-h-72")}>
          <div className="grid grid-cols-1 gap-2">
            {MATRIX_STATE_FIELDS.map((item) => (
              <Field
                key={item.key}
                label={item.label}
                value={door.state[item.key]}
                rows={item.rows}
                placeholder={item.hint}
                testId={`nii67-state-${door.id}-${item.key}`}
                onChange={(value) =>
                  onChange((card) =>
                    updateDoor(card, door.id, {
                      state: { [item.key]: value } as Partial<Nii67MatrixState>,
                    }),
                  )
                }
              />
            ))}
          </div>
        </div>
      </details>
    </div>
  );
}

function DoorColumn({
  door,
  projectId,
  previews,
  onChange,
  onAddBlock,
  onFile,
}: {
  door: Nii67Door;
  projectId: number;
  previews: Record<string, string>;
  onChange: (mutator: (card: Nii67Card) => Nii67Card) => void;
  onAddBlock: (doorId: string, kind: BlockKind) => void;
  onFile: (key: string, file: File, applyName: (name: string) => void) => void;
}) {
  return (
    <section
      data-testid={`nii67-door-${door.id}`}
      className="flex min-w-0 flex-col rounded-2xl border border-teal-400/20 bg-[#0b141c] p-2.5"
    >
      <header className="mb-2 flex items-start justify-between gap-2 px-1">
        <div>
          <div className="flex items-center gap-1.5 text-sm font-semibold text-zinc-50">
            <DoorOpen className="h-4 w-4 text-teal-300" />
            {door.title}
          </div>
          <div className="text-[11px] text-zinc-400">{door.branch}</div>
        </div>
        <span className="rounded-full border border-white/10 px-2 py-0.5 text-[10px] uppercase tracking-wide text-zinc-400">
          {door.id === "door1" ? "Путь 1" : "Путь 2"}
        </span>
      </header>
      <div className="mb-2 flex flex-col gap-2">
        <BlockImage
          label="Мир при открытии двери"
          testId={`nii67-door-world-${door.id}`}
          fileName={door.image_name}
          previewUrl={
            previews[`door:${door.id}:image`] ??
            (door.image_name ? api.nii67AssetUrl(projectId, door.image_name) : undefined)
          }
          onFile={(file) =>
            onFile(`door:${door.id}:image`, file, (image_name) =>
              onChange((card) => updateDoor(card, door.id, { image_name })),
            )
          }
        />
        <ShortField
          label="Мир"
          value={door.theme}
          maxLength={240}
          testId={`nii67-theme-${door.id}`}
          placeholder="Опишите мир за этой дверью"
          onChange={(theme) => onChange((card) => updateDoor(card, door.id, { theme }))}
        />
        <Field
          label="Закон мира"
          value={door.theme_note}
          rows={2}
          testId={`nii67-law-${door.id}`}
          placeholder="Один закон мира"
          onChange={(theme_note) => onChange((card) => updateDoor(card, door.id, { theme_note }))}
        />
        <DoorMatrixFields door={door} onChange={onChange} />
      </div>
      <div className="flex flex-col gap-1.5">
        {door.blocks.map((block) => (
          <div key={block.id} className="flex flex-col gap-1.5">
            <BlockCard
              block={block}
              projectId={projectId}
              previewUrl={previews[`block:${block.id}:1`]}
              previewUrl2={previews[`block:${block.id}:2`]}
              onPatch={(patch) =>
                onChange((card) => {
                  const next = updateBlock(card, door.id, block.id, patch);
                  const updated = next.doors
                    .find((entry) => entry.id === door.id)
                    ?.blocks.find((item) => item.id === block.id);
                  return updated ? updateDoor(next, door.id, blockDoorPatch(updated)) : next;
                })
              }
              onDelete={() => onChange((card) => removeDoorBlock(card, door.id, block.id))}
              onFile={(slot, file) =>
                onFile(`block:${block.id}:${slot}`, file, (image_name) =>
                  onChange((card) =>
                    updateBlock(card, door.id, block.id, slot === 1 ? { image_name } : { image_name2: image_name }),
                  ),
                )
              }
            />
            <InsertMenu
              testId={`nii67-insert-${door.id}-${block.id}`}
              label="Добавить блок"
              onInsert={(kind) => onAddBlock(door.id, kind)}
            />
          </div>
        ))}
        {door.blocks.length === 0 ? (
          <>
            <InsertMenu
              testId={`nii67-insert-${door.id}`}
              label="Добавить блок"
              onInsert={(kind) => onAddBlock(door.id, kind)}
            />
            <p className="rounded-lg border border-dashed border-white/10 px-2 py-3 text-center text-[12px] leading-snug text-zinc-500">
              Смерть, Существо, Действие или Возвращается обратно.
            </p>
          </>
        ) : null}
      </div>
    </section>
  );
}

/** Редактор сценария NII 67 на ноде «Сценарий». */
export function Nii67CardPanel({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [card, setCard] = useState<Nii67Card>(() => defaultCard());
  const [dirty, setDirty] = useState(false);
  const [previewText, setPreviewText] = useState<string | null>(null);
  const [frames, setFrames] = useState<Nii67Frame[]>([]);
  const [previews, setPreviews] = useState<Record<string, string>>({});
  const dirtyRef = useRef(false);
  const framesDirtyRef = useRef(false);
  const cardRef = useRef(card);
  const editGen = useRef(0);
  const projectRef = useRef(projectId);
  projectRef.current = projectId;
  const previewsRef = useRef(previews);
  cardRef.current = card;
  previewsRef.current = previews;

  const cardQ = useQuery({
    queryKey: ["nii67", projectId],
    queryFn: () => api.getNii67(projectId),
    enabled: projectId > 0,
  });

  useEffect(() => {
    dirtyRef.current = false;
    framesDirtyRef.current = false;
    editGen.current += 1;
    setDirty(false);
    setCard(defaultCard());
    setPreviewText(null);
    const cleared = clearNii67ProjectViews(previewsRef.current);
    setFrames(cleared.frames);
    setPreviews(cleared.previews);
  }, [projectId]);

  useEffect(() => {
    if (!cardQ.data) return;
    if (!dirtyRef.current) setCard(cardFromUnknown(cardQ.data.card));
    if (!framesDirtyRef.current && Array.isArray(cardQ.data.frames)) setFrames(cardQ.data.frames);
  }, [cardQ.data]);

  useEffect(
    () => () => {
      Object.values(previewsRef.current).forEach((url) => URL.revokeObjectURL(url));
    },
    [],
  );

  function change(mutator: (current: Nii67Card) => Nii67Card) {
    editGen.current += 1;
    dirtyRef.current = true;
    setDirty(true);
    setCard((prev) => mutator(prev));
  }

  const [uploading, setUploading] = useState<string | null>(null);
  async function upload(
    slot: string,
    file: File,
    applyName: (name: string) => void,
  ) {
    setUploading(slot);
    const pid = projectId;
    const gen = editGen.current;
    try {
      const res = await api.uploadNii67(pid, slot, file);
      if (pid !== projectRef.current || gen !== editGen.current) return;
      applyName(res.name);
      toast.success("Файл сохранён в проект");
    } catch (error) {
      toast.error(errorMessageFromUnknown(error));
    } finally {
      setUploading(null);
    }
  }

  function fileSlot(key: string): string {
    const [head, id, tail] = key.split(":");
    if (head === "block") return `block_${id}_${tail}`;
    if (head === "door") return `door_${id}_${tail}`;
    return key;
  }

  function rememberFile(key: string, file: File, applyName: (name: string) => void) {
    const url = URL.createObjectURL(file);
    setPreviews((prev) => {
      if (prev[key]) URL.revokeObjectURL(prev[key]);
      return { ...prev, [key]: url };
    });
    const pid = projectId;
    const gen = editGen.current;
    const slot = fileSlot(key);
    void api
      .uploadNii67(pid, slot, file)
      .then((res) => {
        if (pid !== projectRef.current || gen !== editGen.current) return;
        applyName(res.name);
      })
      .catch(() => {
        if (pid !== projectRef.current || gen !== editGen.current) return;
        applyName(file.name);
      });
  }

  const save = useMutation({
    mutationFn: async (mode: "draft" | "save") => {
      const gen = editGen.current;
      const pid = projectId;
      const res = await api.putNii67(pid, cardRef.current, mode);
      return { res, gen, mode, pid };
    },
    onSuccess: ({ res, gen, mode, pid }) => {
      if (pid !== projectRef.current) return;
      if (editGen.current === gen) {
        dirtyRef.current = false;
        setDirty(false);
        setCard(cardFromUnknown(res.card));
      }
      toast.success(mode === "draft" ? "Черновик сохранён" : "Карточка сохранена");
      void qc.invalidateQueries({ queryKey: ["nii67", projectId] });
    },
    onError: (error) => toast.error(errorMessageFromUnknown(error)),
  });

  const preview = useMutation({
    mutationFn: () => api.previewNii67(projectId, cardRef.current),
    onSuccess: (res) => setPreviewText(res.text),
    onError: (error) => toast.error(errorMessageFromUnknown(error)),
  });

  const saveFrames = useMutation({
    mutationFn: async (list: Nii67Frame[]) => {
      const pid = projectId;
      const res = await api.putNii67Frames(pid, list);
      return { res, pid };
    },
    onSuccess: ({ res, pid }) => {
      if (pid !== projectRef.current) return;
      framesDirtyRef.current = false;
      setFrames(res.frames);
      toast.success("Кадры сохранены");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Не удалось сохранить кадры"),
  });

  const matrix = useMutation({
    mutationFn: async (next?: Nii67Card) => {
      const pid = projectId;
      const res = await api.matrixPlanNii67(pid, next ?? cardRef.current);
      return { res, pid };
    },
    onSuccess: ({ res, pid }) => {
      if (pid !== projectRef.current) return;
      dirtyRef.current = false;
      setDirty(false);
      setCard(cardFromUnknown(res.card));
      framesDirtyRef.current = true;
      if (Array.isArray(res.frames)) setFrames(res.frames);
      if (res.preview) {
        toast.info("Кадры черновые: заполните условия двери, чтобы сохранить раскадровку");
      } else {
        toast.success("Кадры матрицы собраны и записаны");
      }
      void qc.invalidateQueries({ queryKey: ["nii67", projectId] });
    },
    onError: (error) => toast.error(errorMessageFromUnknown(error)),
  });

  const reset = useMutation({
    mutationFn: () => api.resetNii67(projectId),
    onSuccess: (res) => {
      editGen.current += 1;
      dirtyRef.current = false;
      framesDirtyRef.current = false;
      setDirty(false);
      setCard(cardFromUnknown(res.card));
      setPreviewText(null);
      setFrames([]);
      setPreviews((prev) => clearNii67ProjectViews(prev).previews);
      toast.success("Карточка сброшена");
      void qc.invalidateQueries({ queryKey: ["nii67", projectId] });
    },
    onError: (error) => toast.error(errorMessageFromUnknown(error)),
  });

  const busy = save.isPending || preview.isPending || matrix.isPending || reset.isPending;
  const ggLabel = card.gg.name.trim() || "1 ГГ";

  function addBlock(doorId: string, kind: BlockKind) {
    editGen.current += 1;
    dirtyRef.current = true;
    setDirty(true);
    framesDirtyRef.current = true;
    const next = addDoorBlock(cardRef.current, doorId, kind);
    cardRef.current = next;
    setCard(next);
    setFrames([]);
    matrix.mutate(next);
  }

  return (
    <div
      className="nodrag nopan nowheel border-t border-teal-400/20 bg-teal-500/[0.04]"
      onMouseDown={(event) => event.stopPropagation()}
      onClick={(event) => event.stopPropagation()}
      onWheel={(event) => event.stopPropagation()}
    >
      <button
        type="button"
        data-testid="nii67-open"
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[11px] text-teal-100 transition hover:bg-teal-500/10"
        onClick={() => setOpen(true)}
      >
        <DoorOpen className="h-3.5 w-3.5 shrink-0 text-teal-300" />
        <span className="font-semibold">Карточка NII 67</span>
        <span className="ml-auto text-zinc-400">{dirty ? "не сохранено" : "две двери"}</span>
      </button>

      {!open ? (
        <section
          data-testid="nii67-matrix-inline"
          className="mx-2 mb-2 rounded-2xl border border-teal-400/30 bg-[#0c161e] p-2"
        >
          <div className="mb-1.5 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-teal-200/90">
            <ListChecks className="h-3.5 w-3.5" />
            Данные для промтов
          </div>
          <div className="grid gap-2">
            {card.doors.map((door) => (
              <DoorMatrixFields key={door.id} door={door} onChange={change} compact />
            ))}
          </div>
          <Button
            type="button"
            size="sm"
            data-testid="nii67-matrix-inline-btn"
            disabled={busy}
            className="mt-2 w-full border-teal-300/50 bg-teal-400 text-zinc-950 hover:bg-teal-300"
            onClick={() => matrix.mutate(undefined)}
          >
            {matrix.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ListChecks className="h-3.5 w-3.5" />}
            Собрать кадры
          </Button>
        </section>
      ) : null}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          overlayClassName="z-[150]"
          className="z-[160] flex h-[min(920px,92vh)] w-[min(1180px,96vw)] max-w-none flex-col gap-0 overflow-hidden border-teal-500/25 bg-[#071018] p-0 text-zinc-100"
        >
          <header className="flex shrink-0 items-start justify-between gap-3 border-b border-white/10 px-4 py-3 pr-12">
            <div className="min-w-0">
              <DialogTitle className="text-base font-semibold tracking-tight text-zinc-50">
                Видео Pipeline
              </DialogTitle>
              <p className="mt-0.5 text-[12px] text-zinc-400">
                Создайте сценарий для одного главного героя (1 ГГ)
              </p>
            </div>
            <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
              <Button
                type="button"
                size="sm"
                variant="outline"
                data-testid="nii67-save-draft"
                disabled={busy}
                onClick={() => save.mutate("draft")}
              >
                <Save className="h-3.5 w-3.5" />
                Сохранить черновик
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                data-testid="nii67-preview"
                disabled={busy}
                onClick={() => preview.mutate()}
              >
                {preview.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Eye className="h-3.5 w-3.5" />}
                Предпросмотр
              </Button>
              <span className="rounded-full border border-teal-300/40 bg-teal-400/10 px-2.5 py-1 text-[11px] font-semibold text-teal-100">
                {ggLabel}
              </span>
            </div>
          </header>

          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
            <Nii67GenerationPanel
              card={card}
              frames={frames}
              busy={busy}
              matrixPending={matrix.isPending}
              onCardChange={(next) => {
                change(() => next);
              }}
              onFramesChange={setFrames}
              onSaveFrames={(next) => saveFrames.mutate(next)}
              onBuild={() => matrix.mutate(undefined)}
              onSaveCard={() => save.mutate("save")}
            />
            {cardQ.isLoading ? (
              <p className="mb-2 text-[12px] text-zinc-500">Загружаю карточку…</p>
            ) : null}
            {dirty ? (
              <p className="mb-2 text-[12px] text-amber-200/90">Есть несохранённые правки</p>
            ) : null}
            {previewText !== null ? (
              <section className="mb-3 rounded-xl border border-teal-400/30 bg-black/40 p-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-teal-100">Предпросмотр сценария</span>
                  <button
                    type="button"
                    className="text-[11px] text-zinc-400 hover:text-zinc-200"
                    onClick={() => setPreviewText(null)}
                  >
                    Закрыть
                  </button>
                </div>
                <pre
                  data-testid="nii67-preview-text"
                  className="max-h-56 overflow-auto whitespace-pre-wrap text-[12px] leading-relaxed text-zinc-200"
                >
                  {previewText}
                </pre>
              </section>
            ) : null}

            <section data-testid="nii67-intro-block" className="mb-3 rounded-2xl border border-white/10 bg-[#0c161e] p-3">
              <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-teal-200/90">
                Интро
              </div>
              <UploadSlot
                label="Исходное видео"
                accept="video/*"
                testId="nii67-intro-video"
                fileName={card.intro.video_name}
                busy={uploading === "intro_video"}
                onFile={(file) =>
                  upload("intro_video", file, (name) => change((current) => updateIntroVideo(current, name)))
                }
              />
              <p className="mt-2 text-[11px] leading-snug text-zinc-500">{GENERATE_HINT}</p>
            </section>

            <section data-testid="nii67-portal-block" className="mb-3 rounded-2xl border border-white/10 bg-[#0c161e] p-3">
              <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-teal-200/90">
                Двери
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                <UploadSlot
                  label="Фото левой двери"
                  accept="image/*"
                  testId="nii67-portal-left-image"
                  fileName={card.portal.left_image_name}
                  previewUrl={
                    previews["portal:left"] ??
                    (card.portal.left_image_name
                      ? api.nii67AssetUrl(projectId, card.portal.left_image_name)
                      : undefined)
                  }
                  busy={uploading === "portal_left_image"}
                  onFile={(file) =>
                    upload("portal_left_image", file, (left_image_name) =>
                      change((current) => updatePortal(current, { left_image_name })),
                    )
                  }
                />
                <UploadSlot
                  label="Фото правой двери"
                  accept="image/*"
                  testId="nii67-portal-right-image"
                  fileName={card.portal.right_image_name}
                  previewUrl={
                    previews["portal:right"] ??
                    (card.portal.right_image_name
                      ? api.nii67AssetUrl(projectId, card.portal.right_image_name)
                      : undefined)
                  }
                  busy={uploading === "portal_right_image"}
                  onFile={(file) =>
                    upload("portal_right_image", file, (right_image_name) =>
                      change((current) => updatePortal(current, { right_image_name })),
                    )
                  }
                />
              </div>
              <div className="mt-2">
                <UploadSlot
                  label="Референс рук"
                  accept="image/*"
                  testId="nii67-hands-image"
                  fileName={card.hands_image_name}
                  previewUrl={
                    card.hands_image_name
                      ? api.nii67AssetUrl(projectId, card.hands_image_name)
                      : undefined
                  }
                  busy={uploading === "hands_image"}
                  onFile={(file) =>
                    upload("hands_image", file, (hands_image_name) =>
                      change((current) => updateHandsImage(current, hands_image_name)),
                    )
                  }
                />
              </div>
            </section>

            <div className="mb-3 text-center">
              <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-teal-300/90">
                Разделение
              </div>
              <p className="text-[12px] text-zinc-400">
                сценарий раздваивается и идёт параллельно
              </p>
            </div>

            <div className="grid items-start gap-3 lg:grid-cols-2">
              {card.doors.map((door) => (
                <DoorColumn
                  key={door.id}
                  door={door}
                  projectId={projectId}
                  previews={previews}
                  onChange={change}
                  onAddBlock={addBlock}
                  onFile={rememberFile}
                />
              ))}
            </div>

            <section className="mt-3 rounded-2xl border border-teal-400/25 bg-[#0c161e] p-3">
              <div className="mb-1 flex items-center gap-2 text-sm font-semibold text-zinc-50">
                <Flag className="h-4 w-4 text-teal-300" />
                Общий финал
              </div>
              <p className="mb-2 text-[12px] text-zinc-400">
                Финальная часть сценария (стандарт + заметки). Обе двери сходятся сюда.
              </p>
              <div className="grid gap-2 md:grid-cols-2">
                <Field
                  label="Стандарт финала"
                  value={card.finale.standard}
                  maxLength={FINALE_LIMIT}
                  testId="nii67-finale-standard"
                  placeholder="Финальный кадр, посыл, эмоциональная точка..."
                  onChange={(standard) => change((current) => updateFinale(current, { standard }))}
                />
                <Field
                  label="Заметки"
                  value={card.finale.notes}
                  maxLength={FINALE_LIMIT}
                  testId="nii67-finale-notes"
                  placeholder="Дополнительные идеи, важные детали, исключения..."
                  onChange={(notes) => change((current) => updateFinale(current, { notes }))}
                />
              </div>
            </section>
          </div>

          <footer className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-t border-white/10 px-4 py-3">
            <p className="max-w-md text-[11px] text-zinc-500">
              Блоки — последовательные элементы, вставляются между любыми другими.
            </p>
            <div className="flex flex-wrap items-center gap-1.5">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                data-testid="nii67-reset"
                disabled={busy}
                onClick={() => {
                  if (!window.confirm("Сбросить карточку NII 67 к пустому шаблону двух дверей?")) return;
                  reset.mutate();
                }}
              >
                <RotateCcw className="h-3.5 w-3.5" />
                Сбросить
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                data-testid="nii67-save"
                disabled={busy}
                onClick={() => save.mutate("save")}
              >
                {save.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                Сохранить
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                data-testid="nii67-matrix-plan"
                disabled={busy}
                title="Собрать кадры по полям матрицы (ход, состояние, существо) без GPT"
                onClick={() => matrix.mutate(undefined)}
              >
                {matrix.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ListChecks className="h-3.5 w-3.5" />}
                Матрица
              </Button>
            </div>
          </footer>
        </DialogContent>
      </Dialog>
    </div>
  );
}
