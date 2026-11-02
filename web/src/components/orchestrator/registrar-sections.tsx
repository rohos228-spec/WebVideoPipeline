"use client";

/**
 * Служебные блоки оркестратора (вкладка «Служебное» дока): наблюдение, стоп, траты,
 * резервы, публикации, журнал, план действий, контроль изменений.
 * Перенос из video-pipeline int/chat-v1 (там жили в orchestrator-panel.tsx); старый чат
 * этой линии (orchestrator-panel.tsx, main) остаётся отдельным файлом.
 */

import { ChevronDown, ChevronUp } from "lucide-react";
import { useHideMoney } from "@/hooks/use-hide-money";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { AutonomySection } from "./autonomy-section";
import { ObservationSection } from "./observation-section";
import { KillSwitchSection } from "./kill-switch-section";
import { SpendSection } from "./spend-section";
import { ReservesSection } from "./reserves-section";
import { PublicationsSection } from "./publications-section";
import { JournalSection } from "./journal-section";
import { ActionLayerSection } from "../action-layer/action-layer-section";
import { ChangeControlSection } from "../change-control/change-control-section";

export const REGISTRAR_SECTIONS: { id: string; title: string }[] = [
  { id: "autonomy", title: "Автономия" },
  { id: "observation", title: "Наблюдение, автономный запуск и телеметрия" },
  { id: "kill", title: "Аварийный стоп" },
  { id: "spend", title: "Траты" },
  { id: "reserves", title: "Резервы бюджета" },
  { id: "publications", title: "Публикации" },
  { id: "journal", title: "Журнал" },
  { id: "actions", title: "Действия по плану" },
  { id: "changes", title: "Контроль изменений" },
];

function registrarSection(id: string, projectId: number | null) {
  if (id === "autonomy") return <AutonomySection />;
  if (id === "observation") return <ObservationSection projectId={projectId} />;
  if (id === "kill") return <KillSwitchSection projectId={projectId} />;
  if (id === "spend") return <SpendSection projectId={projectId} />;
  if (id === "reserves") return <ReservesSection />;
  if (id === "publications") return <PublicationsSection projectId={projectId} />;
  if (id === "journal") return <JournalSection projectId={projectId} />;
  if (id === "actions") return <ActionLayerSection projectId={projectId} />;
  if (id === "changes") return <ChangeControlSection projectId={projectId} />;
  return null;
}

// Траты (общая касса провайдеров, себестоимость) и реестр резервов — инструменты студии:
// участнику бэкенд отвечает 403 (engine_access.require_engine_admin, plan.ADMIN_ONLY_*).
const ADMIN_ONLY_SECTIONS = new Set(["spend", "reserves", "kill"]);
// Блоки, чьих ручек на этой линии нет: показывать их значит показать вечную ошибку загрузки.
// Наблюдение, стоп, траты и журнал перенесены (app/web/routers/observations.py); журнала
// публикаций с подтверждениями (роутер publications и шаг публикации с проверкой) нет.
const NOT_ON_THIS_LINE = new Set(["publications"]);

export function RegistrarSections({ projectId }: { projectId: number | null }) {
  const [openIds, setOpenIds] = usePersistedState<string[]>("vp-registrar-open", []);
  const hideAdmin = useHideMoney();
  const sections = REGISTRAR_SECTIONS.filter(
    (s) => !NOT_ON_THIS_LINE.has(s.id) && !(hideAdmin && ADMIN_ONLY_SECTIONS.has(s.id)),
  );
  return (
    <div className="space-y-1.5" data-testid="registrar-sections">
      {sections.map((section) => {
        const isOpen = openIds.includes(section.id);
        return (
          <div key={section.id} className="rounded-lg border border-white/[0.08]">
            <button
              type="button"
              className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-[12px] text-white/80 hover:bg-white/[0.03]"
              aria-expanded={isOpen}
              data-testid={`registrar-toggle-${section.id}`}
              onClick={() =>
                setOpenIds((cur) => (cur.includes(section.id) ? cur.filter((x) => x !== section.id) : [...cur, section.id]))
              }
            >
              <span>{section.title}</span>
              {isOpen ? <ChevronUp className="h-3.5 w-3.5 text-white/40" /> : <ChevronDown className="h-3.5 w-3.5 text-white/40" />}
            </button>
            {isOpen ? <div className="border-t border-white/[0.06] px-3 pb-2">{registrarSection(section.id, projectId)}</div> : null}
          </div>
        );
      })}
    </div>
  );
}
