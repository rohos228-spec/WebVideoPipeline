"use client";

import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Chip, Empty, Label } from "@/components/ui/bits";
import { PromptEditor } from "@/components/editors/prompt-editor";
import { StepParamsPanel, stepHasParams } from "./step-params";
import { HitlPanel, hitlKindForNodeType } from "./hitl-panel";
import { StoragePanel } from "./storage-panel";
import { OperatorPanel } from "./operator-panel";
import { NodeResult } from "./node-result";
import { NodePrompt } from "./node-prompt";
import type {
  ElevenLabsVoice,
  GenerationOptions,
  GraphNode,
  GraphSettings,
  ModelChoice,
  NodeKindInfo,
  NodeState,
  SceneAgentChoice,
} from "@/lib/types";

/**
 * Инспектор выбранного узла: всё, что у узла можно настроить, — в одном
 * столбце, разделами.
 *
 * Разделы появляются по типу узла и по режиму. Всегда: подпись, вкл/выкл,
 * промт. В режиме проекта: запуск и сброс, модель (и качество/формат для
 * картинок и видео), параметры шага, проверка человеком и GPT, результат;
 * у «Хранилища» — файлы, у «Работы с GPT» — пульт оператора.
 *
 * Промт правится здесь тем же компонентом, что и в ленте стадий. Один
 * редактор на два места, потому что это буквально один и тот же файл: правка
 * из схемы обязана быть видна в стадии и наоборот, а две реализации разошлись
 * бы на первой же доработке.
 *
 * Модель, качество, формат — на узле, а не на проекте, потому что «сценарий
 * — дешёвой моделью, картинки — дорогой» — обычное желание, и одна модель
 * на проект его не выражает. Проектные значения остаются умолчанием.
 */

const STATE_CHIP: Partial<Record<NodeState, { tone: "neutral" | "ok" | "warn" | "danger" | "accent"; text: string }>> = {
  running: { tone: "accent", text: "идёт" },
  queued: { tone: "accent", text: "в очереди" },
  done: { tone: "ok", text: "готово" },
  failed: { tone: "danger", text: "сбой" },
  waiting_hitl: { tone: "warn", text: "ждёт проверки" },
  skipped: { tone: "neutral", text: "пропущен" },
};

const IMAGE_TYPES = new Set(["images", "hero", "items"]);
const VIDEO_TYPES = new Set(["videos"]);
const QUALITY: { id: string; label: string }[] = [
  { id: "low", label: "низкая" },
  { id: "medium", label: "средняя" },
  { id: "high", label: "высокая" },
];

export type InspectorProject = {
  id: number;
  state?: NodeState;
  price?: string;
  models: { text: ModelChoice[]; image: ModelChoice[]; video: ModelChoice[] };
  voices: ElevenLabsVoice[];
  settings: GraphSettings;
  /** Варианты формата/разрешения — те же, что в параметрах ролика. */
  options?: GenerationOptions;
  busy: boolean;
  onRun: (stepCode: string, nodeKey: string) => void;
  onReset: (stepCode: string) => void;
  /** Патч `meta.node_step_params[step]`. */
  onStepParams: (step: string, patch: Record<string, unknown>) => void;
  /** Патч верхнего уровня `meta` (auto_review_kinds и т.п.). */
  onMeta: (patch: Record<string, unknown>) => void;
};

export function NodeInspector({
  node,
  count,
  catalog,
  sceneAgents,
  project,
  onChange,
  onRemove,
  onDetach,
  onDuplicate,
}: {
  node: GraphNode | null;
  /** Сколько узлов выделено — при нескольких инспектор показывает групповые действия. */
  count: number;
  catalog: NodeKindInfo[];
  /** Роли веера сцен для «Работы с GPT»; список приезжает с сервера. */
  sceneAgents?: SceneAgentChoice[];
  /** Режим проекта: состояние, цена, запуск, сброс, модель, параметры. */
  project?: InspectorProject;
  onChange: (next: GraphNode) => void;
  onRemove: () => void;
  onDetach: () => void;
  onDuplicate: () => void;
}) {
  const info = catalog.find((n) => n.type === node?.type);

  if (count > 1) {
    return (
      <div className="space-y-3 p-4">
        <h2 className="font-display text-[16px] text-content">Выделено узлов: {count}</h2>
        <p className="text-[12px] text-content-muted">
          Двигаются вместе. Ctrl+C — скопировать, Ctrl+D — продублировать, Delete — убрать. Правой кнопкой по узлу —
          меню, в палитре — «сохранить как группу».
        </p>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={onDuplicate}>
            Дублировать
          </Button>
          <Button size="sm" variant="secondary" onClick={onDetach}>
            Снять связи
          </Button>
          <Button size="sm" variant="danger" onClick={onRemove}>
            Убрать из схемы
          </Button>
        </div>
      </div>
    );
  }

  if (!node) {
    return (
      <div className="space-y-3 p-4">
        <Empty>Выберите узел на схеме — здесь появятся его промт и настройки.</Empty>
        <ul className="space-y-1 text-[12px] text-content-faint">
          <li>Связь: потяните от правой точки узла к левой точке другого.</li>
          <li>Вид связи: клик по подписи стрелки — по кругу, правый клик — выбрать.</li>
          <li>Рамка выделения: Shift + протяжка. Несколько узлов: Ctrl + клик.</li>
          <li>Правая кнопка — меню узла, связи или холста.</li>
          <li>Ctrl+Z / Ctrl+Shift+Z — отмена и возврат, Ctrl+C / Ctrl+V — копирование.</li>
        </ul>
      </div>
    );
  }

  const data = node.data ?? {};
  const disabled = data.disabled === true;
  const label = (data.label as string) || "";
  const modelId = (data.modelId as string) || "";
  const step = info?.step_code ?? null;
  const chip = project?.state ? STATE_CHIP[project.state] : undefined;
  const isImage = IMAGE_TYPES.has(node.type);
  const isVideo = VIDEO_TYPES.has(node.type);
  const models = project ? (isImage ? project.models.image : isVideo ? project.models.video : project.models.text) : [];
  const canRun = Boolean(project && step && !disabled && project.state !== "running");
  const canReset = Boolean(project && step && (project.state === "done" || project.state === "failed"));
  const hitlKind = hitlKindForNodeType(node.type);
  // Маркер веера: `agent` — ключ старых канвасов, читается наравне с новым.
  const marker = (data.sd_agent as string) || (data.agent as string) || "";
  const setData = (patch: Record<string, unknown>) => {
    const next = { ...data, ...patch };
    for (const k of Object.keys(patch)) if (patch[k] === undefined || patch[k] === "") delete next[k];
    onChange({ ...node, data: next });
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 border-b border-border px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="truncate font-display text-[16px] text-content">{label || info?.label || node.type}</h2>
            <p className="mt-0.5 font-mono text-[11px] text-content-faint">
              {node.type}
              {step ? ` · ${step}` : ""}
              {` · ${node.id}`}
            </p>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1">
            {disabled ? <Chip tone="warn">выключен</Chip> : chip?.text ? <Chip tone={chip.tone}>{chip.text}</Chip> : null}
            {project?.price && <span className="font-mono text-[11px] tabular-nums text-content-muted">{project.price}</span>}
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          {project && step && (
            <Button
              size="sm"
              variant="primary"
              disabled={!canRun || project.busy}
              title={disabled ? "Узел выключен" : undefined}
              onClick={() => project.onRun(step, node.id)}
            >
              {project.state === "done" ? "Заново" : "Запустить шаг"}
            </Button>
          )}
          <Button size="sm" variant="secondary" onClick={() => setData({ disabled: !disabled })}>
            {disabled ? "Включить" : "Выключить"}
          </Button>
          {project && step && canReset && (
            <Button size="sm" variant="ghost" disabled={project.busy} onClick={() => project.onReset(step)}>
              Сбросить результат
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={onDuplicate} title="Копия узла рядом">
            Дублировать
          </Button>
          <Button size="sm" variant="ghost" onClick={onDetach} title="Снять все связи, узел оставить">
            Снять связи
          </Button>
          <Button size="sm" variant="ghost" onClick={onRemove}>
            Убрать из схемы
          </Button>
        </div>

        {disabled && (
          <p className="mt-2 text-[12px] text-content-muted">
            Выключенный узел конвейер пропускает — цепочка идёт дальше без него.
          </p>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-2">
        <Section title="Подпись" open>
          <input
            value={label}
            placeholder={info?.label ?? node.type}
            onChange={(e) => setData({ label: e.target.value || undefined })}
            className="w-full rounded-sm border border-border bg-surface-raised px-2 py-1 text-[13px] text-content outline-none focus:border-accent"
          />
        </Section>

        {project && step && models.length > 0 && (info?.has_prompt || isImage || isVideo) && (
          <Section title="Модель" open>
            <select
              value={modelId}
              onChange={(e) => setData({ modelId: e.target.value || undefined })}
              className="w-full rounded-sm border border-border bg-surface-raised px-2 py-1 text-[13px] text-content"
            >
              <option value="">по настройкам проекта</option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                  {m.vendor ? ` · ${m.vendor}` : ""}
                </option>
              ))}
            </select>
            {(isImage || isVideo) && (
              <div className="mt-2 grid grid-cols-3 gap-2">
                <SmallSelect
                  label={isVideo ? "Разрешение" : "Качество"}
                  value={(data.imageResolution as string) || ""}
                  choices={
                    (isVideo ? project.options?.video_resolutions : project.options?.image_resolutions)?.map((c) => ({
                      id: c.id,
                      label: c.label,
                    })) ?? []
                  }
                  onChange={(v) => setData({ imageResolution: v || undefined })}
                />
                {!isVideo && (
                  <SmallSelect
                    label="Детализация"
                    value={(data.imageQuality as string) || ""}
                    choices={QUALITY}
                    onChange={(v) => setData({ imageQuality: v || undefined })}
                  />
                )}
                <SmallSelect
                  label="Формат"
                  value={(data.aspectRatio as string) || ""}
                  choices={project.options?.aspect_ratios?.map((c) => ({ id: c.id.replace("_", ":"), label: c.label })) ?? []}
                  onChange={(v) => setData({ aspectRatio: v || undefined })}
                />
              </div>
            )}
            <p className="mt-1 text-[11px] text-content-faint">
              Пусто — как в параметрах ролика. Смена на пройденном узле сбросит его результат при сохранении.
            </p>
          </Section>
        )}

        {project && step && stepHasParams(step) && (
          <Section title="Параметры шага" open>
            <StepParamsPanel
              step={step}
              params={project.settings.step_params}
              voices={project.voices}
              bgmDefault={project.settings.bgm_level}
              busy={project.busy}
              onSave={project.onStepParams}
            />
          </Section>
        )}

        {project && hitlKind && (
          <Section title="Проверка" open={project.state === "waiting_hitl"}>
            <HitlPanel
              projectId={project.id}
              kind={hitlKind}
              autoReviewKinds={project.settings.auto_review_kinds}
              busy={project.busy}
              onAutoReviewChange={(kinds) => project.onMeta({ auto_review_kinds: kinds })}
            />
          </Section>
        )}

        {project && node.type === "storage" && (
          <Section title="Файлы хранилища" open>
            <StoragePanel projectId={project.id} nodeKey={node.id} />
          </Section>
        )}

        {project && node.type === "excel_gpt" && (
          <Section title="Пульт оператора" open>
            <OperatorPanel projectId={project.id} nodeKey={node.id} onLabel={(l) => setData({ label: l })} />
          </Section>
        )}

        {project && step && (
          <Section title="Результат" open={project.state === "done"}>
            <NodeResult projectId={project.id} type={node.type} state={project.state} />
          </Section>
        )}

        {node.type === "excel_gpt" && (sceneAgents?.length ?? 0) > 0 && (
          <Section title="Веер сцен" open={Boolean(marker)}>
            <select
              value={marker}
              // `agent` снимаем вместе с новым ключом: иначе «обычная нода»
              // на старом канвасе не снимала бы роль — маркер остался бы в нём.
              onChange={(e) => setData({ sd_agent: e.target.value || undefined, agent: undefined })}
              className="w-full rounded-sm border border-border bg-surface-raised px-2 py-1 text-[13px] text-content"
            >
              <option value="">обычная нода</option>
              {(sceneAgents ?? []).map((a) => (
                <option key={a.id} value={a.id}>
                  {a.label}
                </option>
              ))}
            </select>
            <p className="mt-1 text-[11px] text-content-faint">
              {marker
                ? `Ответ узла уйдёт сборщику сцен как срез этого агента. Промт выберите парный — обычно sd_${marker}.`
                : "Обычная нода работает по своему промту сама по себе. Роль делает её частью веера сцен: срез уходит сборщику, а шаг ищет узел именно по ней."}
            </p>
          </Section>
        )}

        <Section title="Промт" open={!project}>
          {info?.has_prompt && step ? (
            project ? (
              // В ролике у узла есть свой выбор варианта — им и собран веер
              // агентов. Редактор внутри тот же, что в ленте стадий: файл
              // один, и правка из схемы обязана быть видна в стадии.
              <NodePrompt
                projectId={project.id}
                step={step}
                nodeKey={node.id}
                variant={project.settings.prompt_variants?.[node.id] ?? ""}
                perNode={node.type === "excel_gpt"}
                onVariant={(name) => project.onMeta({ prompt_slot_variants: { [node.id]: { main: name } } })}
              />
            ) : (
              // Без проекта привязывать нечего: в шаблоне нет ни meta, ни
              // прогонов — правится общий промт шага.
              <PromptEditor prompts={[{ step, label: "Промт узла" }]} projectId={0} />
            )
          ) : (
            <Empty>У этого узла нет промта — он не обращается к модели, а делает работу кодом.</Empty>
          )}
        </Section>
      </div>
    </div>
  );
}

/** Раздел инспектора: заголовок-подпись, сворачивается. */
function Section({ title, open, children }: { title: string; open?: boolean; children: ReactNode }) {
  return (
    <details open={open} className="group/s border-b border-border py-3 last:border-b-0">
      <summary className="flex cursor-pointer list-none items-center justify-between [&::-webkit-details-marker]:hidden">
        <Label>{title}</Label>
        <span className="text-[10px] text-content-faint transition-transform group-open/s:rotate-90">▶</span>
      </summary>
      <div className="mt-2">{children}</div>
    </details>
  );
}

function SmallSelect({
  label,
  value,
  choices,
  onChange,
}: {
  label: string;
  value: string;
  choices: { id: string; label: string }[];
  onChange: (v: string) => void;
}) {
  return (
    <label className="text-[11px] text-content-muted">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-0.5 w-full rounded-sm border border-border bg-surface-raised px-1.5 py-1 text-[12px] text-content"
      >
        <option value="">—</option>
        {choices.map((c) => (
          <option key={c.id} value={c.id}>
            {c.label}
          </option>
        ))}
        {value && !choices.some((c) => c.id === value) && <option value={value}>{value}</option>}
      </select>
    </label>
  );
}
