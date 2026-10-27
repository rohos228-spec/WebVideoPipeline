"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp, Sparkles } from "lucide-react";
import { AUDIO_TAG_CATEGORIES } from "@/lib/audio-tags";

type Props = {
  onInsertTag: (tag: string) => void;
};

export function AudioTagsBar({ onInsertTag }: Props) {
  const [activeCategoryId, setActiveCategoryId] = useState("mood");
  const [isExpanded, setIsExpanded] = useState(true);

  const activeCategory =
    AUDIO_TAG_CATEGORIES.find((c) => c.id === activeCategoryId) ||
    AUDIO_TAG_CATEGORIES[0];

  return (
    <div className="rounded-xl border border-white/[0.08] bg-[#12131a]/80 p-2.5 backdrop-blur-sm">
      {/* Category header & toggle */}
      <div className="flex items-center justify-between pb-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider text-[#22d3ee]/80 mr-1">
            <Sparkles className="h-3 w-3" />
            Эмоции и аудио-теги:
          </span>
          {AUDIO_TAG_CATEGORIES.map((cat) => (
            <button
              key={cat.id}
              type="button"
              onClick={() => {
                setActiveCategoryId(cat.id);
                setIsExpanded(true);
              }}
              className={`rounded-lg px-2.5 py-1 text-[11px] font-medium transition ${
                activeCategoryId === cat.id && isExpanded
                  ? "bg-[#22d3ee]/20 text-[#22d3ee] font-semibold shadow-sm"
                  : "bg-white/[0.03] text-white/55 hover:bg-white/[0.07] hover:text-white"
              }`}
            >
              {cat.label} ({cat.tags.length})
            </button>
          ))}
        </div>

        <button
          type="button"
          onClick={() => setIsExpanded(!isExpanded)}
          className="flex h-6 w-6 items-center justify-center rounded-md text-white/40 transition hover:bg-white/10 hover:text-white"
          title={isExpanded ? "Свернуть теги" : "Развернуть теги"}
        >
          {isExpanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </button>
      </div>

      {/* Tags chips grid / row */}
      {isExpanded && (
        <div className="flex max-h-32 flex-wrap gap-1.5 overflow-y-auto pt-1 scrollbar-thin">
          {activeCategory.tags.map((tag) => (
            <button
              key={tag}
              type="button"
              onClick={() => onInsertTag(tag)}
              title={`Вставить ${tag} в позицию курсора`}
              className="inline-flex items-center rounded-md border border-white/10 bg-white/[0.04] px-2 py-0.5 font-mono text-[11px] text-white/80 transition hover:border-[#22d3ee]/60 hover:bg-[#22d3ee]/15 hover:text-[#22d3ee] active:scale-95"
            >
              {tag}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
