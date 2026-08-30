"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Empty, Label, Working } from "@/components/ui/bits";
import type { StoredFile } from "@/lib/types";

/**
 * Узел «Хранилище»: папка файлов, в которую стекаются результаты по стрелкам.
 *
 * Показывает, что лежит и откуда пришло; умеет забрать свежее со входящих
 * связей, положить своё, скачать всё одним архивом и очистить. Файлы
 * читаются с диска сервера, поэтому опрос — раз в несколько секунд: узел
 * наполняется, пока идут соседние шаги.
 */

const KIND_FILTER: { id: string; label: string }[] = [
  { id: "", label: "Все" },
  { id: "image", label: "Картинки" },
  { id: "video", label: "Видео" },
  { id: "xlsx", label: "Excel" },
  { id: "text", label: "Текст" },
];

export function StoragePanel({ projectId, nodeKey }: { projectId: number; nodeKey: string }) {
  const qc = useQueryClient();
  const [filter, setFilter] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const key = ["storage", projectId, nodeKey];

  const resolve = useQuery({
    queryKey: key,
    queryFn: () => api.storageResolve(projectId, nodeKey),
    refetchInterval: 6_000,
  });
  const invalidate = () => qc.invalidateQueries({ queryKey: key });

  const sync = useMutation({
    mutationFn: () => api.storageSync(projectId, nodeKey),
    onSuccess: (r) => {
      invalidate();
      toast.success(`Забрано файлов: ${Array.isArray(r.copied) ? r.copied.length : 0}`);
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const uploadMany = useMutation({
    mutationFn: async (files: File[]) => {
      for (const f of files) await api.storageUpload(projectId, nodeKey, f);
      return files.length;
    },
    onSuccess: (n) => {
      invalidate();
      toast.success(`Загружено: ${n}`);
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const clear = useMutation({
    mutationFn: () => api.storageClear(projectId, nodeKey),
    onSuccess: (r) => {
      invalidate();
      toast.success(`Удалено файлов: ${r.removed}`);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  if (resolve.isLoading) return <Working label="читаю хранилище" />;
  if (resolve.isError || !resolve.data) {
    return (
      <div className="space-y-2">
        <p className="text-[12px] text-danger">Хранилище не читается: {resolve.error?.message}</p>
        <Button size="sm" variant="secondary" onClick={() => resolve.refetch()}>
          Ещё раз
        </Button>
      </div>
    );
  }

  const r = resolve.data;
  const files = r.files.filter((f) => !filter || f.kind === filter);
  const busy = sync.isPending || uploadMany.isPending || clear.isPending;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="secondary" disabled={busy} onClick={() => sync.mutate()} title="Забрать файлы со входящих стрелок">
          Обновить со стрелок
        </Button>
        <Button size="sm" variant="secondary" disabled={busy} onClick={() => fileInput.current?.click()}>
          Загрузить
        </Button>
        <a
          href={api.storageZipUrl(projectId, nodeKey)}
          className={`inline-flex h-7 items-center rounded-md px-2.5 text-[13px] text-content-muted hover:bg-surface-sunken hover:text-content ${
            r.files.length === 0 ? "pointer-events-none opacity-40" : ""
          }`}
        >
          Скачать всё .zip
        </a>
        <Button
          size="sm"
          variant="danger"
          disabled={busy || r.files.length === 0}
          onClick={() => {
            if (confirm(`Удалить все файлы хранилища (${r.files.length})?`)) clear.mutate();
          }}
        >
          Очистить
        </Button>
        <input
          ref={fileInput}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            const list = Array.from(e.target.files ?? []);
            e.target.value = "";
            if (list.length) uploadMany.mutate(list);
          }}
        />
      </div>

      {r.incomingSources.length > 0 && (
        <p className="text-[11px] text-content-faint">
          Входящие: {r.incomingSources.map((s) => s.label || s.source).filter(Boolean).join(", ")}
          {r.lastSyncAt ? ` · обновлено ${r.lastSyncAt}` : ""}
        </p>
      )}

      <div className="flex flex-wrap gap-1">
        {KIND_FILTER.map((k) => (
          <button
            key={k.id}
            onClick={() => setFilter(k.id)}
            className={`rounded-full border px-2 py-0.5 text-[11px] transition-colors ${
              filter === k.id ? "border-accent text-accent" : "border-border text-content-muted hover:text-content"
            }`}
          >
            {k.label}
          </button>
        ))}
      </div>

      {files.length === 0 ? (
        <Empty>
          {r.files.length === 0
            ? "Пока пусто. Файлы придут со стрелок, когда соседние узлы отработают, или загрузите свои."
            : "В этом виде файлов нет."}
        </Empty>
      ) : (
        <ul className="divide-y divide-border">
          {files.map((f) => (
            <FileRow key={f.path} file={f} />
          ))}
        </ul>
      )}

      <div>
        <Label>Папка на сервере</Label>
        <p className="mt-0.5 break-all font-mono text-[10px] text-content-faint">{r.storageDir}</p>
      </div>
    </div>
  );
}

function FileRow({ file }: { file: StoredFile }) {
  const isImage = file.kind === "image" && file.preview_url;
  return (
    <li className="flex items-center gap-2 py-1.5">
      {isImage ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={file.preview_url!} alt="" className="h-9 w-9 shrink-0 rounded-sm border border-border object-cover" />
      ) : (
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-sm border border-border font-mono text-[10px] text-content-faint">
          {file.kind}
        </span>
      )}
      <div className="min-w-0 flex-1">
        <div className="truncate text-[12px] text-content" title={file.name}>
          {file.name}
        </div>
        <div className="truncate text-[11px] text-content-faint">
          {file.fromLabel || file.fromNode ? `из «${file.fromLabel || file.fromNode}» · ` : ""}
          {bytes(file.size)}
          {file.originalName && file.originalName !== file.name ? ` · было: ${file.originalName}` : ""}
        </div>
      </div>
      <a href={file.download_url} className="shrink-0 text-[11px] text-content-faint hover:text-accent">
        скачать
      </a>
    </li>
  );
}

function bytes(n: number): string {
  if (n < 1024) return `${n} Б`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} КБ`;
  return `${(n / 1024 / 1024).toFixed(1).replace(".", ",")} МБ`;
}
