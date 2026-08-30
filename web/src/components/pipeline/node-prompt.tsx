"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Chip, Working } from "@/components/ui/bits";
import { PromptEditor } from "@/components/editors/prompt-editor";

/**
 * Промт узла: сначала выбор файла, потом его текст.
 *
 * Выбор хранится в `meta.prompt_slot_variants[node][main]` — там же, куда его
 * пишет вставка группы узлов, поэтому вставленный веер агентов сразу виден
 * здесь выбранным. У «Работы с GPT» привязка живёт на каждом узле отдельно:
 * этим механизмом и сделан веер — четыре одинаковых по типу ноды работают
 * разными агентами, потому что у каждой свой файл.
 *
 * Раньше этого выбора в интерфейсе не было: раздел показывал библиотеку шага
 * целиком, и у «Работы с GPT» в него вываливались все sd_*-агенты сразу —
 * список, из которого нельзя было ничего назначить, только посмотреть.
 *
 * Текст файла правится общий: промт лежит в библиотеке, а не в ролике. Здесь
 * выбирают, ЧЕМ работает узел; редактор ниже правит выбранное — и говорит об
 * этом прямо.
 */
export function NodePrompt({
  projectId,
  step,
  nodeKey,
  variant,
  perNode,
  onVariant,
}: {
  projectId: number;
  step: string;
  nodeKey: string;
  /** Назначенный узлу вариант; пусто — узел берёт то, что решит шаг. */
  variant: string;
  /** true — привязка действует на этот узел, а не на весь ролик. */
  perNode: boolean;
  onVariant: (name: string) => void;
}) {
  const files = useQuery({ queryKey: ["prompt-files", step], queryFn: () => api.promptFiles(step) });
  const resolved = useQuery({
    queryKey: ["prompt-resolve", step, projectId, nodeKey],
    queryFn: () => api.promptResolve(step, projectId, nodeKey),
  });

  if (files.isLoading || resolved.isLoading) return <Working label="читаю промт" />;

  const names = (files.data ?? []).map((f) => f.name);
  // Вариант, которого нет в списке (файл переименовали или удалили), всё
  // равно показываем: иначе select молча встал бы на «по умолчанию», а на
  // деле узел ходит за пропавшим файлом.
  const options = variant && !names.includes(variant) ? [variant, ...names] : names;
  const active = variant || resolved.data?.name || "";

  return (
    <div className="space-y-2">
      <select
        value={variant}
        onChange={(e) => onVariant(e.target.value)}
        className="w-full rounded-sm border border-border bg-surface-raised px-2 py-1 text-[13px] text-content"
      >
        <option value="">по умолчанию</option>
        {options.map((n) => (
          <option key={n} value={n}>
            {n}
          </option>
        ))}
      </select>

      <div className="flex flex-wrap items-center gap-2 text-[11px] text-content-faint">
        <span>сейчас узел берёт</span>
        <span className="font-mono text-content-muted">{resolved.data?.name ?? "—"}</span>
        {resolved.data && <Chip tone={resolved.data.source === "slot" ? "ok" : "neutral"}>{resolved.data.source_label}</Chip>}
      </div>

      <p className="text-[11px] text-content-faint">
        {perNode
          ? "Выбор хранится на узле: несколько «Работ с GPT» могут работать разными агентами."
          : "Узел шага в схеме один, поэтому выбор действует на весь ролик."}
      </p>

      {active && (
        <PromptEditor
          prompts={[{ step, label: "Промт узла" }]}
          projectId={projectId}
          name={active}
          nodeKey={nodeKey}
        />
      )}
    </div>
  );
}
