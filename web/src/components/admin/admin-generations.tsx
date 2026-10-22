"use client";

/**
 * Вкладка «Генерации» админ-панели VIA: все файлы, которые студия создала
 * для проектов — кадры, видео, персонажи, предметы, озвучка, музыка, финалы.
 */
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileImage } from "lucide-react";
import { GENERATION_KINDS, adminApi, type AdminGeneration } from "@/lib/admin-api";
import {
  A,
  Badge,
  Card,
  Empty,
  ErrorBox,
  Field,
  Pager,
  Seg,
  Select,
  Skel,
  TextInput,
  fmtBytes,
  fmtDateTime,
  fmtNum,
} from "@/components/admin/admin-kit";

const LIMIT = 24;

const KIND_RU: Record<string, string> = {
  scene_image: "Кадр",
  scene_video: "Видео",
  hero_reference: "Персонаж",
  item_reference: "Предмет",
  audio: "Озвучка",
  music: "Музыка",
  final_video: "Финал",
};

const VIDEO_KINDS = new Set(["scene_video", "final_video"]);

/** Превью: видео для видео-генераций, картинка для остальных (с фолбэком). */
function Preview({ g }: { g: AdminGeneration }) {
  const [failed, setFailed] = useState(false);

  if (VIDEO_KINDS.has(g.kind)) {
    return (
      <video
        src={g.file_url}
        preload="metadata"
        muted
        playsInline
        className="h-full w-full object-cover"
      />
    );
  }

  if (failed) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-1.5 text-zinc-600">
        <FileImage className="h-6 w-6" />
        <span className="text-[12px]">{KIND_RU[g.kind] ?? g.kind}</span>
      </div>
    );
  }

  return (
    <img
      src={g.file_url}
      alt={g.project_title || KIND_RU[g.kind] || "Генерация"}
      loading="lazy"
      onError={() => setFailed(true)}
      className="h-full w-full object-cover"
    />
  );
}

function GenerationCard({ g }: { g: AdminGeneration }) {
  return (
    <article className="overflow-hidden rounded-[16px] border border-white/[0.07] bg-white/[0.025]">
      <a
        href={g.file_url}
        target="_blank"
        rel="noreferrer"
        title="Открыть файл в новой вкладке"
        className="group relative block aspect-video overflow-hidden bg-black/40"
      >
        <Preview g={g} />
        <span className="absolute left-2 top-2 flex flex-wrap gap-1">
          <Badge tone="neutral">{KIND_RU[g.kind] ?? g.kind}</Badge>
          {g.approved ? <Badge tone="ok">approved</Badge> : null}
        </span>
        <span className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/35 to-transparent opacity-0 transition-opacity duration-200 group-hover:opacity-100" />
      </a>
      <div className="space-y-1.5 p-3">
        <div className="truncate text-[14px] font-medium text-white" title={g.project_title}>
          {g.project_title || "Без проекта"}
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-zinc-500">
          <span style={{ color: A }}>{KIND_RU[g.kind] ?? g.kind}</span>
          <span className="truncate" title={g.owner_name}>
            {g.owner_name || "—"}
          </span>
          {g.frame_number != null ? <span>· кадр {fmtNum(g.frame_number)}</span> : null}
        </div>
        <div className="flex items-center justify-between gap-2 text-[12px] text-zinc-600">
          <span>{fmtDateTime(g.created_at)}</span>
          <span title={g.size_bytes ? `${fmtNum(g.size_bytes)} Б` : undefined}>
            {fmtBytes(g.size_bytes)}
          </span>
        </div>
      </div>
    </article>
  );
}

export function AdminGenerationsTab() {
  const [kind, setKind] = useState("");
  const [days, setDays] = useState(30);
  const [q, setQ] = useState("");
  const [qDebounced, setQDebounced] = useState("");
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    const t = setTimeout(() => setQDebounced(q.trim()), 350);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    setOffset(0);
  }, [qDebounced]);

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["admin", "generations", kind, days, qDebounced, offset],
    queryFn: () =>
      adminApi.generations({
        kind: kind || undefined,
        days,
        q: qDebounced || undefined,
        limit: LIMIT,
        offset,
      }),
  });

  const items = data?.items ?? [];

  return (
    <div className="flex flex-col gap-3">
      <Card>
        <div className="flex flex-wrap items-end gap-2">
          <div className="w-[180px]">
            <Field label="Тип">
              <Select
                value={kind}
                onChange={(e) => {
                  setKind(e.target.value);
                  setOffset(0);
                }}
              >
                {GENERATION_KINDS.map((k) => (
                  <option key={k.id || "all"} value={k.id}>
                    {k.label}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <div className="w-[300px]">
            <Field label="Период">
              <Seg
                value={days}
                options={[
                  { v: 7, t: "7 дн" },
                  { v: 30, t: "30 дн" },
                  { v: 90, t: "90 дн" },
                  { v: 0, t: "Всё" },
                ]}
                onChange={(v) => {
                  setDays(v);
                  setOffset(0);
                }}
              />
            </Field>
          </div>
          <div className="min-w-[200px] flex-1">
            <Field label="Поиск">
              <TextInput
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Название проекта или владелец"
              />
            </Field>
          </div>
          <div className="pb-2 text-[13px] text-zinc-500">
            Найдено {data ? fmtNum(data.total) : "—"}
          </div>
        </div>
      </Card>

      {isLoading ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skel key={i} h={228} />
          ))}
        </div>
      ) : error ? (
        <ErrorBox err={error} retry={() => void refetch()} />
      ) : items.length === 0 ? (
        <Empty text="Генераций не найдено. Поменяй фильтры или период." />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {items.map((g) => (
            <GenerationCard key={g.id} g={g} />
          ))}
        </div>
      )}

      {data ? (
        <Pager total={data.total} limit={data.limit || LIMIT} offset={data.offset} onChange={setOffset} />
      ) : null}
    </div>
  );
}
