"use client";

import { useMemo, useState } from "react";
import { Empty } from "@/components/ui/bits";
import type { NodeCatalog, NodeGroupSummary } from "@/lib/types";

/**
 * Палитра узлов, сгруппированная по разделам, плюс готовые группы.
 *
 * Порядок внутри раздела — порядок появления в конвейере, а не алфавит:
 * палитрой пользуются, чтобы дособрать цепочку, и узел ищут там, где он стоит
 * по смыслу. Алфавит развёл бы «Картинки» и «Промты картинок» на разные концы.
 *
 * Поиск — по подписи и коду типа сразу: кто-то помнит «Доработка №3», кто-то
 * `enrich_3`. Группы — пресеты из нескольких узлов со связями (веер агентов
 * сцен и то, что человек сохранил сам): вставляются целиком, сервер сам
 * ставит позиции и вшивает в цепочку после узла нужного типа.
 */
export function NodePalette({
  catalog,
  groups,
  onAdd,
  onInsertGroup,
  onDeleteGroup,
}: {
  catalog: NodeCatalog | undefined;
  /** Группы узлов; `undefined` — ещё грузятся или в этом режиме их нет. */
  groups?: NodeGroupSummary[];
  onAdd: (type: string) => void;
  onInsertGroup?: (group: NodeGroupSummary) => void;
  onDeleteGroup?: (group: NodeGroupSummary) => void;
}) {
  const [q, setQ] = useState("");
  const needle = q.trim().toLowerCase();

  const visible = useMemo(() => {
    if (!catalog) return [];
    if (!needle) return catalog.nodes;
    return catalog.nodes.filter(
      (n) => n.label.toLowerCase().includes(needle) || n.type.toLowerCase().includes(needle),
    );
  }, [catalog, needle]);

  const visibleGroups = useMemo(() => {
    if (!groups) return [];
    if (!needle) return groups;
    return groups.filter(
      (g) =>
        g.title.toLowerCase().includes(needle) ||
        g.description.toLowerCase().includes(needle) ||
        g.nodes.some((n) => n.label.toLowerCase().includes(needle)),
    );
  }, [groups, needle]);

  if (!catalog) return null;
  if (catalog.nodes.length === 0) {
    return (
      <div className="p-4">
        <Empty>Каталог узлов пуст — сервер не отдал ни одного типа.</Empty>
      </div>
    );
  }

  return (
    <div className="space-y-5 p-4">
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="найти узел…"
        aria-label="Поиск по палитре"
        className="w-full rounded-sm border border-border bg-surface-raised px-2 py-1 text-[12px] text-content outline-none placeholder:text-content-faint focus:border-accent"
      />

      {Object.entries(catalog.kinds).map(([kind, title]) => {
        const items = visible.filter((n) => n.kind === kind);
        if (items.length === 0) return null;
        return (
          <section key={kind}>
            <h3 className="mb-1.5 text-[11px] uppercase tracking-wide text-content-faint">{title}</h3>
            <ul className="space-y-0.5">
              {items.map((n) => (
                <li key={n.type}>
                  <button
                    onClick={() => onAdd(n.type)}
                    title={`${n.type}${n.has_prompt ? " · у узла есть промт" : " · узел работает кодом, без модели"}`}
                    className="w-full rounded-sm px-2 py-1 text-left text-[13px] text-content-muted transition-colors hover:bg-surface-sunken hover:text-content"
                  >
                    {n.label}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        );
      })}

      {groups && (
        <section>
          <h3 className="mb-1.5 text-[11px] uppercase tracking-wide text-content-faint">Группы</h3>
          {visibleGroups.length === 0 ? (
            <p className="text-[12px] text-content-faint">
              {needle ? "Ничего не нашлось." : "Выделите несколько узлов и сохраните их как группу — она появится здесь."}
            </p>
          ) : (
            <ul className="space-y-1">
              {visibleGroups.map((g) => (
                <li key={g.id} className="group/g rounded-sm px-2 py-1 transition-colors hover:bg-surface-sunken">
                  <button
                    onClick={() => onInsertGroup?.(g)}
                    disabled={!onInsertGroup}
                    title={
                      onInsertGroup
                        ? `${g.description || g.title}${g.default_after_type ? ` — вставится после «${g.default_after_type}»` : ""}`
                        : "Группы вставляются в схему ролика, не в шаблон"
                    }
                    className="w-full text-left text-[13px] text-content-muted hover:text-content disabled:cursor-default disabled:opacity-60"
                  >
                    <span>{g.title}</span>
                    <span className="ml-1.5 font-mono text-[10px] text-content-faint">{g.node_count} узл.</span>
                  </button>
                  <div className="mt-0.5 flex items-center justify-between gap-2">
                    <span className="truncate text-[11px] text-content-faint" title={g.nodes.map((n) => n.label).join(" → ")}>
                      {g.nodes.map((n) => n.label).join(" → ")}
                    </span>
                    {!g.builtin && onDeleteGroup && (
                      <button
                        onClick={() => onDeleteGroup(g)}
                        className="hidden shrink-0 text-[11px] text-content-faint hover:text-danger group-hover/g:inline"
                      >
                        удалить
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {needle && visible.length === 0 && visibleGroups.length === 0 && (
        <Empty>По запросу «{q}» узлов нет.</Empty>
      )}
    </div>
  );
}
