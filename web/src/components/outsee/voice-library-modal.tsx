"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Mic, Pause, Play, Search, Volume2, X } from "lucide-react";
import RU_VOICES_DATA from "@/lib/ru-voices.json";

export type RuVoice = {
  id: string;
  index: number;
  name: string;
  description: string;
  gender: "female" | "male";
  filename: string;
  sample_url: string;
};

const ALL_VOICES: RuVoice[] = RU_VOICES_DATA as RuVoice[];

export function getVoiceById(voiceId: string): RuVoice | undefined {
  if (!voiceId) return undefined;
  return ALL_VOICES.find((v) => v.id === voiceId.trim());
}

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  selectedVoiceId: string;
  onSelectVoice: (voiceId: string, voiceName: string) => void;
};

export function VoiceLibraryModal({
  open,
  onOpenChange,
  selectedVoiceId,
  onSelectVoice,
}: Props) {
  const [search, setSearch] = useState("");
  const [genderFilter, setGenderFilter] = useState<"all" | "female" | "male">("all");
  const [playingVoiceId, setPlayingVoiceId] = useState<string | null>(null);

  const audioRef = useRef<HTMLAudioElement | null>(null);

  // Stop audio on unmount or when modal closes
  useEffect(() => {
    if (!open && audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
      setPlayingVoiceId(null);
    }
  }, [open]);

  // Handle ESC key
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onOpenChange(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onOpenChange]);

  const togglePlay = (voice: RuVoice, e: React.MouseEvent) => {
    e.stopPropagation();

    if (playingVoiceId === voice.id) {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
      setPlayingVoiceId(null);
      return;
    }

    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }

    const audioUrl = voice.sample_url || `/api/voices/${voice.id}/sample`;
    const audio = new Audio(audioUrl);
    audioRef.current = audio;
    setPlayingVoiceId(voice.id);

    audio.play().catch((err) => {
      console.warn("Failed to play voice sample:", err);
      setPlayingVoiceId(null);
    });

    audio.onended = () => {
      setPlayingVoiceId(null);
      audioRef.current = null;
    };

    audio.onerror = () => {
      setPlayingVoiceId(null);
      audioRef.current = null;
    };
  };

  const filteredVoices = useMemo(() => {
    let list = ALL_VOICES;
    if (genderFilter !== "all") {
      list = list.filter((v) => v.gender === genderFilter);
    }
    const q = search.trim().toLowerCase();
    if (q) {
      list = list.filter(
        (v) =>
          v.name.toLowerCase().includes(q) ||
          v.description.toLowerCase().includes(q) ||
          v.id.toLowerCase().includes(q) ||
          String(v.index).includes(q)
      );
    }
    return list;
  }, [genderFilter, search]);

  const femaleCount = useMemo(() => ALL_VOICES.filter((v) => v.gender === "female").length, []);
  const maleCount = useMemo(() => ALL_VOICES.filter((v) => v.gender === "male").length, []);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm animate-in fade-in duration-200"
      onClick={() => onOpenChange(false)}
    >
      <div
        className="relative flex h-[88vh] max-h-[860px] w-full max-w-4xl flex-col rounded-2xl border border-white/10 bg-[#0f1015] text-white shadow-2xl shadow-black/80"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-white/10 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#22d3ee]/10 text-[#22d3ee] ring-1 ring-[#22d3ee]/25">
              <Mic className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-semibold tracking-wide text-white">
                  Библиотека русских голосов
                </h2>
                <span className="rounded-full bg-[#22d3ee]/20 px-2 py-0.5 text-[11px] font-medium text-[#22d3ee]">
                  ElevenLabs v4
                </span>
              </div>
              <p className="text-[12px] text-white/50">
                200 отобранных русских дикторских голосов с образцами звучания
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-white/40 transition hover:bg-white/10 hover:text-white"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Filter bar: Search + Gender Tabs */}
        <div className="flex flex-col gap-3 border-b border-white/10 bg-white/[0.02] px-6 py-3 sm:flex-row sm:items-center sm:justify-between">
          {/* Search Input */}
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-white/35" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Поиск по имени, тембру, характеру..."
              className="w-full rounded-xl border border-white/10 bg-[#161720] py-2 pl-9 pr-3 text-[13px] text-white placeholder-white/35 outline-none transition focus:border-[#22d3ee]/50 focus:ring-1 focus:ring-[#22d3ee]/30"
              autoFocus
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch("")}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-white/40 hover:text-white"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          {/* Gender Filter Buttons */}
          <div className="flex items-center gap-1 rounded-xl border border-white/10 bg-[#161720] p-1">
            <button
              type="button"
              onClick={() => setGenderFilter("all")}
              className={`rounded-lg px-3 py-1 text-[12px] font-medium transition ${
                genderFilter === "all"
                  ? "bg-[#22d3ee]/20 text-[#22d3ee]"
                  : "text-white/60 hover:text-white"
              }`}
            >
              Все ({ALL_VOICES.length})
            </button>
            <button
              type="button"
              onClick={() => setGenderFilter("female")}
              className={`rounded-lg px-3 py-1 text-[12px] font-medium transition ${
                genderFilter === "female"
                  ? "bg-rose-500/25 text-rose-300"
                  : "text-white/60 hover:text-white"
              }`}
            >
              Женские ({femaleCount})
            </button>
            <button
              type="button"
              onClick={() => setGenderFilter("male")}
              className={`rounded-lg px-3 py-1 text-[12px] font-medium transition ${
                genderFilter === "male"
                  ? "bg-blue-500/25 text-blue-300"
                  : "text-white/60 hover:text-white"
              }`}
            >
              Мужские ({maleCount})
            </button>
          </div>
        </div>

        {/* Voices Grid */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {filteredVoices.length === 0 ? (
            <div className="flex h-64 flex-col items-center justify-center text-center">
              <Volume2 className="h-10 w-10 text-white/20 mb-2" />
              <p className="text-[14px] text-white/60">Ничего не найдено</p>
              <p className="text-[12px] text-white/35">Попробуйте изменить поисковый запрос</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-2">
              {filteredVoices.map((voice) => {
                const isSelected = selectedVoiceId === voice.id;
                const isPlaying = playingVoiceId === voice.id;
                const isFemale = voice.gender === "female";

                return (
                  <div
                    key={voice.id}
                    onClick={() => {
                      onSelectVoice(voice.id, voice.name);
                      onOpenChange(false);
                    }}
                    className={`group relative flex cursor-pointer items-center justify-between rounded-xl border p-3 transition duration-150 ${
                      isSelected
                        ? "border-[#22d3ee] bg-[#22d3ee]/10 shadow-md shadow-[#22d3ee]/10"
                        : "border-white/[0.08] bg-[#14151c]/90 hover:border-white/20 hover:bg-[#1a1b24]"
                    }`}
                  >
                    {/* Left: Avatar + Play button + Info */}
                    <div className="flex items-center gap-3 overflow-hidden">
                      {/* Play/Pause Button */}
                      <button
                        type="button"
                        onClick={(e) => togglePlay(voice, e)}
                        title={isPlaying ? "Пауза" : "Прослушать образец"}
                        className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl transition ${
                          isPlaying
                            ? "bg-[#22d3ee] text-black shadow-lg shadow-[#22d3ee]/30 animate-pulse"
                            : isFemale
                              ? "bg-rose-500/15 text-rose-300 group-hover:bg-rose-500/25"
                              : "bg-blue-500/15 text-blue-300 group-hover:bg-blue-500/25"
                        }`}
                      >
                        {isPlaying ? (
                          <Pause className="h-4 w-4 fill-current" />
                        ) : (
                          <Play className="h-4 w-4 fill-current ml-0.5" />
                        )}
                      </button>

                      {/* Info */}
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="truncate text-[13px] font-semibold text-white">
                            {voice.name}
                          </span>
                          <span
                            className={`rounded px-1.5 py-0.2 text-[10px] font-medium uppercase ${
                              isFemale
                                ? "bg-rose-500/20 text-rose-300"
                                : "bg-blue-500/20 text-blue-300"
                            }`}
                          >
                            {isFemale ? "Ж" : "М"}
                          </span>
                          <span className="font-mono text-[10px] text-white/30">
                            #{voice.index}
                          </span>
                        </div>
                        <p className="truncate text-[11px] text-white/55">
                          {voice.description || "Натуральный чистый голос"}
                        </p>
                      </div>
                    </div>

                    {/* Right: Select indicator */}
                    <div className="ml-2 shrink-0">
                      {isSelected ? (
                        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#22d3ee] text-black shadow-sm">
                          <Check className="h-4 w-4 stroke-[2.5]" />
                        </div>
                      ) : (
                        <button
                          type="button"
                          className="rounded-lg border border-white/10 px-2.5 py-1 text-[11px] font-medium text-white/50 opacity-0 transition group-hover:opacity-100 hover:border-white/30 hover:text-white"
                        >
                          Выбрать
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-white/10 bg-[#0d0e13] px-6 py-3 text-[12px] text-white/45">
          <span>
            Показано: {filteredVoices.length} из {ALL_VOICES.length} голосов
          </span>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="rounded-lg border border-white/10 bg-white/5 px-4 py-1.5 text-[12px] font-medium text-white transition hover:bg-white/10"
          >
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
}
