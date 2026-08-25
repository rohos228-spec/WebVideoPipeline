"use client";

import { Empty } from "@/components/ui/bits";
import type { NodeCatalog } from "@/lib/types";

/**
 * Палитра узлов, сгруппированная по разделам.
 *
 * Порядок внутри раздела — порядок появления в конвейере, а не алфавит:
 * палитрой пользуются, чтобы дособрать цепочку, и узел ищут там, где он стоит
 * по смыслу. Алфавит развёл бы «Картинки» и «Промты картинок» на разные концы.
 */
export function NodePalette({
  catalog,
  onAdd,
}: {
  catalog: NodeCatalog | undefined;
  onAdd: (type: string) => void;
}) {
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
      {Object.entries(catalog.kinds).map(([kind, title]) => {
        const items = catalog.nodes.filter((n) => n.kind === kind);
        if (items.length === 0) return null;
        return (
          <section key={kind}>
            <h3 className="mb-1.5 text-[11px] uppercase tracking-wide text-content-faint">
              {title}
            </h3>
            <ul className="space-y-0.5">
              {items.map((n) => (
                <li key={n.type}>
                  <button
                    onClick={() => onAdd(n.type)}
                    title={n.has_prompt ? "у узла есть промт" : "узел работает кодом, без модели"}
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
    </div>
  );
}
