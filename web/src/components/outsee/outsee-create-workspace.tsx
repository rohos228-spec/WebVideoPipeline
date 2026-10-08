"use client";

/**
 * Outsee Create (глобально):
 * — настройки общие (data/outsee_create_settings.json), не project
 * — история общая по всем проектам
 * — typetoggle Фото / Видео / Аудио + feed Все/Фото/Видео/Аудио
 * — полный picker моделей как на outsee.io/create
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ChevronDown,
  Clock,
  Coins,
  Copy,
  CornerDownLeft,
  Dices,
  Disc,
  Download,
  ExternalLink,
  FileText,
  History,
  ImageIcon,
  Layers,
  Link2,
  Loader2,
  Maximize2,
  Mic,
  Music,
  Paperclip,
  Pause,
  Play,
  Radio,
  Repeat,
  RotateCcw,
  RotateCw,
  Scissors,
  Search,
  Send,
  Sparkles,
  Square,
  Trash2,
  Video,
  Volume2,
  VolumeX,
  X,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { KieField, KieModelSpec } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { isUnfilledAssistantPrompt } from "@/lib/gen-assistant-styles";
import { cn } from "@/lib/utils";
import {
  OUTSEE_ACCENT,
  OUTSEE_CHIP_LABELS,
  OUTSEE_DETAIL_LEVELS,
  OUTSEE_FEED_TABS,
  OUTSEE_ORIGIN,
  OUTSEE_TYPE_TABS,
  chipOptions,
  clampToOptions,
  detailLabel,
  dockChipsForModel,
  getAudioModel,
  getImageModel,
  getVideoModel,
  outseeCreateUrl,
  pickerModelsForType,
  slugToStudioId,
  type OutseeChip,
  type OutseeFeedKind,
  type OutseeMediaType,
} from "@/lib/outsee-catalog";
import { estimateCreatePrice } from "@/lib/create-pricing";
import { GenAssistantPanel } from "@/components/outsee/gen-assistant-panel";
import { VoiceLibraryModal, getVoiceById } from "./voice-library-modal";
import { AudioTagsBar } from "./audio-tags-bar";
import {
  estimateKie,
  kieChipFields,
  kieFileFields,
  kieMainTextField,
} from "@/lib/kie-pricing";

// Kie-каталог включен по умолчанию; выключить: NEXT_PUBLIC_KIE_CREATE=0.
// (Сравнение с "1" ломалось: Next компилировал флаг в runtime-доступ
// к process.env вместо baked-значения, и в браузере он был выключен.)
const KIE_CREATE_ENABLED = process.env.NEXT_PUBLIC_KIE_CREATE !== "0";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Опционально: «применить к проекту» — не источник настроек. */
  projectId: number | null;
};

type RefImage = { id: string; url: string; name: string; file?: File };

type DraftJob = {
  job_id: string;
  history_id: string;
  status: "processing";
  media: "image";
  model: string;
  prompt_preview: string;
  provider: "draft";
  created_at: string;
  started_at: string;
};

type HistoryItem = {
  id: string;
  kind: string;
  preview_url: string | null;
  raw_url?: string | null;
  path?: string | null;
  label: string;
  project_id: number | null;
  project_slug: string | null;
  prompt: string | null;
  status?: string | null;
  job_id?: string | null;
  error?: string | null;
  model?: string | null;
  elapsed_sec?: number | null;
  elapsed_label?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  mtime?: number | null;
  params?: Record<string, unknown> | null;
  reference_images?: string[] | null;
  first_frame_url?: string | null;
  provider?: "outsee" | "kie" | string | null;
};

function makeRefFromFile(file: File): RefImage {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    url: URL.createObjectURL(file),
    name: file.name,
    file,
  };
}

function revokeRefUrl(url: string) {
  if (url.startsWith("blob:")) URL.revokeObjectURL(url);
}

async function resolveReferenceUrls(refs: RefImage[]): Promise<string[]> {
  const out: string[] = [];
  for (const r of refs) {
    if (r.file) out.push(await readFileAsDataUrl(r.file));
    else out.push(r.url);
  }
  return out;
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("read failed"));
    reader.readAsDataURL(file);
  });
}

function formatElapsedMinSec(totalSec: number | null | undefined): string {
  const n = Math.max(0, Math.round(Number(totalSec) || 0));
  const m = Math.floor(n / 60);
  const s = n % 60;
  return `${m} мин ${s} сек`;
}


export const RANDOM_PROMPTS = [
  // Кинематографичные сюжеты и кинокадры
  "Cinematic film still of a detective in a trench coat standing under a flickering street lamp on a rainy night in 1950s Chicago, dramatic shadows, 35mm film look",
  "A dusty desert highway at sunset with a classic vintage muscle car parked on the roadside, golden hour light, anamorphic lens flare",
  "Astronaut sitting on a rocky cliff overlooking a vast crimson Martian canyon, twin moons in the starry night sky, cinematic lighting",
  "Close-up portrait of an old weathered sea captain looking into a fierce ocean storm, sea salt in his gray beard, intense dramatic gaze",
  "A neon-lit ramen bar in downtown Tokyo during a heavy downpour, steam rising from fresh bowls, reflections on wet asphalt, moody atmosphere",
  "A lone mountaineer reaching the summit of a snowy Alpine peak at sunrise, sea of clouds below, crisp clear mountain air, wide angle lens",
  "Dark gothic ballroom with grand chandeliers, mysterious masquerade dancers in elaborate dark attire, candlelit ambience, deep shadows",
  "Cyberpunk courier speeding through a rainy futuristic megacity on an illuminated hoverbike, holographic signs reflecting on helmet visor",
  "Retro 1980s synthwave night drive, sports car dashboard view, purple and teal sunset over distant palm trees and grid skyline",
  "A medieval blacksmith hammering a glowing red sword blade in a dim stone forge, bright flying sparks, fiery rim lighting",

  // Уют, быт и атмосфера
  "Cozy rustic kitchen in morning sunlight, fresh warm croissants on a wooden board, steam rising from ceramic coffee cup, soft dust motes",
  "Rainy afternoon in an old bookstore, stacks of antique books reaching the ceiling, a cat napping on a green velvet armchair by the window",
  "A serene wooden cabin on the edge of a misty pine lake, warm amber glow in the windows, smoking chimney, autumn dawn reflection",
  "A vinyl record spinning on a vintage turntable, warm amber lamp glow, soft bokeh lights, cozy evening living room",
  "Sunny glass greenhouse conservatory overflowing with exotic tropical monstera and ferns, hanging brass lanterns, golden sunbeams",
  "A street artist painting a colorful mural on an old brick wall in a sunlit European alley, paint splatters, authentic candid moment",
  "A camper van parked on an ocean cliff edge with the back doors open, two cups of tea, overlooking crashing waves at twilight",
  "An artisan pottery workshop, potter's hands shaping wet clay on a spinning wheel, natural window light, rustic ceramics on wooden shelves",

  // Животные и дикая природа
  "A cute fluffy red fox curled up asleep on a blanket of freshly fallen snow in a quiet winter birch forest",
  "Charming capybara relaxing in an outdoor Japanese hot spring bath with a small yuzu fruit balanced on its head, gentle rising steam",
  "Macro close-up shot of a chameleon with vibrant neon scales and an iridescent eye perched on a lush tropical branch",
  "A majestic humpback whale breaching out of calm Arctic waters, dramatic golden sunset sky, glistening water splash",
  "A wise barn owl perched on a moss-covered oak branch in twilight mist, soft detailed feathers, striking amber eyes",
  "Playful golden retriever puppy running through a vibrant meadow of wild blooming poppies, sunny summer afternoon",
  "A tiny colorful tree frog resting inside a wet exotic jungle flower, glistening translucent water dewdrops, shallow focus",

  // Еда, напитки и коммерческий предметный стиль
  "Gourmet smash burger with melting aged cheddar, crispy bacon, caramelized onions and sauce dripping onto craft paper, mouthwatering food photography",
  "Crystal cocktail glass with an artisan amber whiskey, spherical clear ice cube, orange peel garnish, moody speakeasy bar lighting",
  "A slice of decadent dark chocolate cake with glossy dripping ganache and fresh ripe raspberries on a matte ceramic plate",
  "Overhead flat lay of an authentic Italian Neapolitan pizza fresh from a wood-fired oven, blistered crust, creamy mozzarella and fresh basil leaves",
  "A matcha latte in a minimalist ceramic cup with delicate foam leaf latte art, bamboo whisk and green powder on a raw stone slate",
  "Fresh chilled glass bottle of sparkling soda with ice condensation droplets, floating lime slices and fresh mint leaves, bright summer sunlight",

  // Фэнтези, мистика и Sci-Fi
  "Ancient colossal stone temple ruins hidden deep inside a bioluminescent jungle, glowing vines, cascading emerald waterfalls",
  "Majestic celestial dragon with shimmering pearl scales soaring through pastel sunset clouds above floating mountain islands",
  "Enchanted library where glowing magical origami birds fly between high bookshelves, magical stardust swirling in the air",
  "A futuristic botanical dome on the moon, lush green trees inside glass dome with Earth rising above the barren lunar landscape",
  "Ethereal underwater crystal palace with glowing jellyfish drifting past carved coral arches, tranquil turquoise atmosphere",
  "A friendly small maintenance robot tending to bonsai trees in a minimalist futuristic apartment, soft daylight, warm feeling",
  "A solitary wizard tower on a sharp sea cliff during an epic thunderstorm, glowing blue runes carved into dark stone, violent lightning",
  "A mysterious alchemist workstation with glass retorts bubbling with glowing luminescent liquids, ancient scrolls, dried lavender bunches",

  // Архитектура и дизайн
  "Minimalist brutalist villa made of raw concrete and warm cedar wood, floor-to-ceiling glass windows facing a serene misty pine forest",
  "Cozy Scandinavian interior living room with a crackling fireplace, beige linen sofa, wool knit throw, and large window with mountain view",
  "Futuristic organic architecture city with flowing white curves, green sky terraces, elevated pedestrian bridges and clean blue sky",
  "Traditional Kyoto machiya courtyard garden with smooth stepping stones, bamboo fountain, and vibrant red Japanese maple leaves in autumn",
  "Modern luxury penthouse bedroom at night overlooking Manhattan skyline, dark moody tones, plush king bed, floor-to-ceiling city panorama",

  // Графика, 3D и креативные концепты
  "Vibrant 3D isometric cutaway illustration of a cozy gamer room with dual monitors, glowing RGB lights, mini fridge, and posters",
  "Retro travel poster illustration of a futuristic vacation to Saturn's rings, bold vintage typography, stylish mid-century palette",
  "Whimsical miniature clay diorama of a tiny bakery run by mice, micro loaves of bread, flour dusting, handcrafted stop-motion look",
  "Cute cartoon astronaut cat exploring an alien planet covered in candy-colored giant mushrooms, playful vibrant colors",
  "Editorial fashion studio portrait of a woman wearing a holographic geometric dress, high-fashion makeup, dramatic studio split lighting",
  "A vintage steam locomotive rushing through a snowy mountain gorge over an arched stone viaduct, billowing white steam clouds",
];

function downloadMediaFile(
  url: string,
  filename: string,
  format: "png" | "jpg" | "webp" | "mp4" | "mp3" | string = "png",
  path?: string | null,
) {
  const params = new URLSearchParams();
  if (path) params.set("path", path);
  if (url) params.set("url", url);
  params.set("format", format);
  params.set("filename", filename);

  const downloadUrl = `/api/outsee-create/download?${params.toString()}`;
  const a = document.createElement("a");
  a.href = downloadUrl;
  a.download = `${filename.replace(/\.[^/.]+$/, "")}.${format}`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

function AudioStudioPlayer({
  item,
  onInspect,
}: {
  item: HistoryItem;
  onInspect?: () => void;
}) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const progressBarRef = useRef<HTMLDivElement>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [isLooping, setIsLooping] = useState(false);
  const [copied, setCopied] = useState(false);
  const speedRef = useRef<HTMLDivElement>(null);
  const [speedOpen, setSpeedOpen] = useState(false);

  useEffect(() => {
    if (!speedOpen) return;
    const onDown = (e: MouseEvent) => {
      if (speedRef.current && !speedRef.current.contains(e.target as Node)) {
        setSpeedOpen(false);
      }
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [speedOpen]);

  useEffect(() => {
    setIsPlaying(false);
    setCurrentTime(0);
    setDuration(0);
    if (audioRef.current) {
      audioRef.current.currentTime = 0;
      audioRef.current.playbackRate = playbackRate;
      audioRef.current.loop = isLooping;
    }
  }, [item.preview_url, item.id]);

  const togglePlay = () => {
    if (!audioRef.current) return;
    if (isPlaying) {
      audioRef.current.pause();
    } else {
      audioRef.current.play().catch((err) => {
        console.warn("Audio playback error:", err);
      });
    }
  };

  const seekBy = (sec: number) => {
    if (!audioRef.current) return;
    const target = Math.max(0, Math.min(duration || 0, audioRef.current.currentTime + sec));
    audioRef.current.currentTime = target;
    setCurrentTime(target);
  };

  const handleSeek = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!progressBarRef.current || !audioRef.current || !duration) return;
    const rect = progressBarRef.current.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const percent = Math.max(0, Math.min(1, clickX / rect.width));
    const target = percent * duration;
    audioRef.current.currentTime = target;
    setCurrentTime(target);
  };

  const toggleMute = () => {
    if (!audioRef.current) return;
    if (isMuted) {
      audioRef.current.muted = false;
      setIsMuted(false);
    } else {
      audioRef.current.muted = true;
      setIsMuted(true);
    }
  };

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = Number(e.target.value);
    setVolume(val);
    if (audioRef.current) {
      audioRef.current.volume = val;
      if (val === 0) {
        audioRef.current.muted = true;
        setIsMuted(true);
      } else if (isMuted) {
        audioRef.current.muted = false;
        setIsMuted(false);
      }
    }
  };

  const cyclePlaybackRate = () => {
    const rates = [1, 1.25, 1.5, 2, 0.75];
    const nextIdx = (rates.indexOf(playbackRate) + 1) % rates.length;
    const nextRate = rates[nextIdx];
    setPlaybackRate(nextRate);
    if (audioRef.current) {
      audioRef.current.playbackRate = nextRate;
    }
  };

  const toggleLoop = () => {
    const nextLoop = !isLooping;
    setIsLooping(nextLoop);
    if (audioRef.current) {
      audioRef.current.loop = nextLoop;
    }
  };

  const handleCopyLink = () => {
    const url = item.preview_url || item.raw_url || "";
    if (!url) return;
    navigator.clipboard.writeText(url).then(() => {
      setCopied(true);
      toast.success("Ссылка на аудио скопирована");
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const handleDownload = () => {
    const url = item.preview_url || item.raw_url || "";
    if (!url) return;
    const title = (item.params?.title as string) || item.label || "audio_track";
    void downloadMediaFile(url, title, "mp3", item.path);
  };

  const formatTime = (seconds: number): string => {
    if (isNaN(seconds) || seconds < 0) return "00:00";
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  };

  const progressPercent = duration > 0 ? (currentTime / duration) * 100 : 0;

  const trackTitle =
    (item.params?.title as string) ||
    item.label ||
    "Сгенерированный аудио трек";

  const trackStyle =
    (item.params?.style as string) ||
    item.prompt ||
    "";

  const modelBadge =
    (item.params?.model as string) ||
    (item.model?.includes("ElevenLabs") || item.model?.includes("elevenlabs")
      ? "ElevenLabs v4"
      : item.model?.includes("V5_5")
        ? "V5.5"
        : item.model?.includes("V5")
          ? "V5"
          : "Suno AI");

  const WAVE_BARS = useMemo(
    () => [
      22, 38, 55, 78, 48, 92, 68, 54, 82, 96, 64, 46, 76, 92, 58, 42,
      66, 86, 98, 74, 52, 82, 100, 88, 64, 48, 72, 88, 96, 72, 48, 64,
      86, 76, 54, 72, 92, 82, 58, 76, 92, 64, 44, 62, 82, 96, 58, 34,
    ],
    [],
  );

  return (
    <div className="relative flex w-full max-w-3xl lg:max-w-4xl flex-col gap-5 rounded-3xl border border-white/15 bg-[#121216]/95 p-6 md:p-8 backdrop-blur-2xl shadow-[0_30px_90px_rgba(0,0,0,0.9)] ring-1 ring-white/10 animate-in fade-in duration-300">
      <audio
        ref={audioRef}
        src={item.preview_url || undefined}
        preload="metadata"
        onPlay={() => setIsPlaying(true)}
        onPause={() => setIsPlaying(false)}
        onEnded={() => {
          if (!isLooping) setIsPlaying(false);
        }}
        onTimeUpdate={() => {
          if (audioRef.current) setCurrentTime(audioRef.current.currentTime);
        }}
        onLoadedMetadata={() => {
          if (audioRef.current) {
            setDuration(audioRef.current.duration);
            audioRef.current.playbackRate = playbackRate;
            audioRef.current.loop = isLooping;
          }
        }}
      />

      {/* Top: Vinyl + Track info + Actions */}
      <div className="flex items-center gap-4 md:gap-5">
        <div className="relative flex h-20 w-20 md:h-24 md:w-24 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-[#1a1a24] to-[#0a0a0f] ring-1 ring-white/15 shadow-2xl overflow-hidden">
          <div
            className={cn(
              "absolute inset-1.5 rounded-full border border-white/10 bg-gradient-to-tr from-black via-zinc-900 to-black transition-transform duration-700",
              isPlaying && "animate-[spin_4s_linear_infinite]",
            )}
          >
            <div className="absolute inset-2.5 rounded-full border border-white/5" />
            <div className="absolute inset-5 rounded-full border border-white/5" />
            <div className="absolute inset-0 m-auto h-6 w-6 md:h-7 md:w-7 rounded-full bg-gradient-to-br from-[#22d3ee] to-[#38bdf8] shadow-[0_0_12px_rgba(34,211,238,0.6)] flex items-center justify-center">
              <div className="h-2 w-2 rounded-full bg-black" />
            </div>
          </div>
          <Disc className={cn("h-8 w-8 md:h-9 md:w-9 text-white/70 relative z-10 transition-opacity", isPlaying ? "opacity-0" : "opacity-80")} />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2.5">
            <h3 className="truncate text-base md:text-xl font-bold text-white tracking-tight" title={trackTitle}>
              {trackTitle}
            </h3>
            <span className="shrink-0 rounded-md bg-[#22d3ee]/15 px-2 py-0.5 font-mono text-[10px] md:text-xs font-bold text-[#22d3ee] ring-1 ring-[#22d3ee]/30">
              {modelBadge}
            </span>
          </div>
          {trackStyle && (
            <p className="mt-1 line-clamp-2 text-xs md:text-sm text-white/60 leading-relaxed" title={trackStyle}>
              {trackStyle}
            </p>
          )}
          <div className="mt-1.5 flex items-center gap-2 text-[11px] text-white/40 font-mono">
            <span>ID: {item.id ? String(item.id).slice(0, 8) : "—"}</span>
            {item.elapsed_sec && (
              <>
                <span>•</span>
                <span>ген: {Math.round(item.elapsed_sec)}с</span>
              </>
            )}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={handleCopyLink}
            className="flex h-9 w-9 md:h-10 md:w-10 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-white/60 transition hover:border-white/20 hover:bg-white/[0.08] hover:text-white"
            title="Скопировать ссылку на аудио"
          >
            {copied ? <Check className="h-4 w-4 text-[#22d3ee]" /> : <Copy className="h-4 w-4" />}
          </button>
          <button
            type="button"
            onClick={handleDownload}
            className="flex h-9 w-9 md:h-10 md:w-10 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-white/60 transition hover:border-[#22d3ee]/40 hover:bg-[#22d3ee]/10 hover:text-[#22d3ee]"
            title="Скачать трек (MP3)"
          >
            <Download className="h-4 w-4" />
          </button>
          {onInspect && (
            <button
              type="button"
              onClick={onInspect}
              className="flex h-9 w-9 md:h-10 md:w-10 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-white/60 transition hover:border-white/20 hover:bg-white/[0.08] hover:text-white"
              title="Открыть инспектор и детали промпта"
            >
              <Maximize2 className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>

      {/* Waveform Visualizer & Seek Area */}
      <div
        ref={progressBarRef}
        onClick={handleSeek}
        className="group relative flex h-16 md:h-22 w-full cursor-pointer items-end justify-between gap-1 md:gap-1.5 rounded-2xl bg-black/45 px-4 py-3 ring-1 ring-white/10 transition hover:ring-[#22d3ee]/40 overflow-hidden"
      >
        {isPlaying && (
          <div className="absolute inset-0 bg-gradient-to-t from-[#22d3ee]/[0.08] to-transparent pointer-events-none" />
        )}

        {WAVE_BARS.map((heightPercent, idx) => {
          const barProgress = idx / (WAVE_BARS.length - 1);
          const currentProgress = duration > 0 ? currentTime / duration : 0;
          const isPassed = barProgress <= currentProgress;

          return (
            <div
              key={idx}
              className="relative flex h-full flex-1 items-end justify-center"
            >
              <div
                style={{ height: `${heightPercent}%` }}
                className={cn(
                  "w-1.5 md:w-2 rounded-full transition-all duration-150",
                  isPassed
                    ? "bg-[#22d3ee] shadow-[0_0_10px_rgba(34,211,238,0.55)]"
                    : "bg-white/15 group-hover:bg-white/25",
                  isPlaying && isPassed && "brightness-125",
                )}
              />
            </div>
          );
        })}

        <div
          style={{ left: `${progressPercent}%` }}
          className="pointer-events-none absolute top-0 bottom-0 w-1 bg-white shadow-[0_0_14px_#22d3ee] transition-all"
        />
      </div>

      {/* Scrubber slider and time display */}
      <div className="space-y-2">
        <div
          onClick={handleSeek}
          className="group relative flex h-2.5 w-full cursor-pointer items-center rounded-full bg-white/10"
        >
          <div
            style={{ width: `${progressPercent}%` }}
            className="h-full rounded-full bg-gradient-to-r from-[#22d3ee] to-[#38bdf8] shadow-[0_0_14px_rgba(34,211,238,0.5)]"
          />
          <div
            style={{ left: `calc(${progressPercent}% - 7px)` }}
            className="absolute h-3.5 w-3.5 rounded-full bg-white ring-2 ring-[#22d3ee] shadow-[0_0_10px_#22d3ee] opacity-0 transition-opacity group-hover:opacity-100"
          />
        </div>

        <div className="flex items-center justify-between font-mono text-xs md:text-sm text-white/50">
          <span className="text-white/90 font-semibold">{formatTime(currentTime)}</span>
          <span>{formatTime(duration)}</span>
        </div>
      </div>

      {/* Controls Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={toggleLoop}
            className={cn(
              "flex h-9 md:h-10 items-center gap-1.5 rounded-xl px-3 text-xs font-mono transition ring-1",
              isLooping
                ? "bg-[#22d3ee]/20 text-[#22d3ee] ring-[#22d3ee]/40 shadow-[0_0_14px_rgba(34,211,238,0.25)]"
                : "bg-white/[0.04] text-white/50 ring-white/10 hover:bg-white/[0.08] hover:text-white",
            )}
            title="Зациклить трек (Loop)"
          >
            <Repeat className="h-4 w-4" />
            <span>Loop</span>
          </button>

          <div className="relative" ref={speedRef}>
            <button
              type="button"
              onClick={() => setSpeedOpen((v) => !v)}
              className={cn(
                "flex h-9 md:h-10 items-center gap-1.5 rounded-xl px-3 font-mono text-xs ring-1 transition",
                speedOpen
                  ? "bg-[#22d3ee]/20 text-[#22d3ee] ring-[#22d3ee]/40 shadow-[0_0_14px_rgba(34,211,238,0.25)]"
                  : "bg-white/[0.04] text-white/70 ring-white/10 hover:bg-white/[0.08] hover:text-white",
              )}
              title="Выбрать скорость воспроизведения"
            >
              <span>{playbackRate}x</span>
              <ChevronDown className={cn("h-3.5 w-3.5 text-white/50 transition-transform duration-200", speedOpen && "rotate-180")} />
            </button>

            {speedOpen && (
              <div className="absolute bottom-full left-0 mb-2 z-50 flex flex-col min-w-[130px] rounded-xl border border-white/15 bg-[#121216]/98 p-1 backdrop-blur-2xl shadow-[0_15px_40px_rgba(0,0,0,0.85)] ring-1 ring-white/10 animate-in fade-in zoom-in-95 duration-150">
                <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-white/40 border-b border-white/[0.08] mb-1">
                  Скорость
                </div>
                {[0.5, 0.75, 1, 1.25, 1.5, 1.75, 2].map((rate) => {
                  const isActive = playbackRate === rate;
                  return (
                    <button
                      key={rate}
                      type="button"
                      onClick={() => {
                        setPlaybackRate(rate);
                        if (audioRef.current) audioRef.current.playbackRate = rate;
                        setSpeedOpen(false);
                      }}
                      className={cn(
                        "flex items-center justify-between rounded-lg px-2.5 py-1.5 text-left font-mono text-xs transition",
                        isActive
                          ? "bg-[#22d3ee]/20 text-[#22d3ee] font-bold"
                          : "text-white/75 hover:bg-white/[0.08] hover:text-white",
                      )}
                    >
                      <span>{rate}x {rate === 1 ? "(1.0)" : ""}</span>
                      {isActive && <Check className="h-3.5 w-3.5 text-[#22d3ee]" />}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* Center: Rewind / Play / Forward */}
        <div className="flex items-center gap-3 md:gap-4">
          <button
            type="button"
            onClick={() => seekBy(-10)}
            className="flex h-10 w-10 md:h-11 md:w-11 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-white/70 transition hover:scale-105 hover:border-white/20 hover:bg-white/[0.08] hover:text-white active:scale-95"
            title="Перемотать назад на 10 сек"
          >
            <RotateCcw className="h-4.5 w-4.5" />
          </button>

          <button
            type="button"
            onClick={togglePlay}
            className="flex h-13 w-13 md:h-15 md:w-15 items-center justify-center rounded-2xl bg-gradient-to-r from-[#22d3ee] to-[#0ea5e9] text-black shadow-[0_0_30px_rgba(34,211,238,0.45)] transition hover:scale-105 hover:brightness-110 active:scale-95"
            title={isPlaying ? "Пауза" : "Воспроизвести"}
          >
            {isPlaying ? (
              <Pause className="h-6 w-6 md:h-7 md:w-7 fill-current" />
            ) : (
              <Play className="h-6 w-6 md:h-7 md:w-7 fill-current translate-x-0.5" />
            )}
          </button>

          <button
            type="button"
            onClick={() => seekBy(10)}
            className="flex h-10 w-10 md:h-11 md:w-11 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-white/70 transition hover:scale-105 hover:border-white/20 hover:bg-white/[0.08] hover:text-white active:scale-95"
            title="Перемотать вперёд на 10 сек"
          >
            <RotateCw className="h-4.5 w-4.5" />
          </button>
        </div>

        {/* Volume */}
        <div className="flex items-center gap-2.5">
          <button
            type="button"
            onClick={toggleMute}
            className="text-white/60 transition hover:text-white"
            title={isMuted ? "Включить звук" : "Выключить звук"}
          >
            {isMuted || volume === 0 ? (
              <VolumeX className="h-4.5 w-4.5 text-rose-400" />
            ) : (
              <Volume2 className="h-4.5 w-4.5" />
            )}
          </button>
          <input
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={isMuted ? 0 : volume}
            onChange={handleVolumeChange}
            className="h-1.5 w-20 md:w-28 cursor-pointer appearance-none rounded-lg bg-white/20 accent-[#22d3ee]"
            title={`Громкость: ${Math.round((isMuted ? 0 : volume) * 100)}%`}
          />
        </div>
      </div>
    </div>
  );
}

export function OutseeCreateWorkspace({ open, onOpenChange, projectId }: Props) {
  const qc = useQueryClient();
  const [mediaType, setMediaType] = useState<OutseeMediaType>("image");
  const [feedKind, setFeedKind] = useState<OutseeFeedKind>("all");
  const [imageSlug, setImageSlug] = useState("gpt-image-2");
  const [videoSlug, setVideoSlug] = useState("veo-3-1-lite");
  const [audioSlug, setAudioSlug] = useState("kie:suno-music");
  const [aspect, setAspect] = useState("16:9");
  const [resolution, setResolution] = useState("2K");
  const [detail, setDetail] = useState("medium");
  const [videoResolution, setVideoResolution] = useState("720p");
  const [duration, setDuration] = useState("5");
  const [generateAudio, setGenerateAudio] = useState(false);
  const [orientation, setOrientation] = useState<"video" | "image">("video");
  const [motionQuality, setMotionQuality] = useState("std");
  const [instrumental, setInstrumental] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [negativePrompt, setNegativePrompt] = useState("");
  const [showNegativePrompt, setShowNegativePrompt] = useState(false);
  const [downloadFormat, setDownloadFormat] = useState<"png" | "jpg" | "webp">("png");
  const [batchCount, setBatchCount] = useState<1 | 2 | 4>(1);
  const [isEnhancingPrompt, setIsEnhancingPrompt] = useState(false);
  const [soraSize, setSoraSize] = useState<"small" | "large">("small");
  const [firstFrameDataUrl, setFirstFrameDataUrl] = useState<string | null>(null);
  const [lastFrameDataUrl, setLastFrameDataUrl] = useState<string | null>(null);
  const [firstFrameName, setFirstFrameName] = useState<string | null>(null);
  const [lastFrameName, setLastFrameName] = useState<string | null>(null);
  const [referenceImages, setReferenceImages] = useState<RefImage[]>([]);
  const [draftJobs, setDraftJobs] = useState<DraftJob[]>([]);
  const [modelOpen, setModelOpen] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [assistantExpanded, setAssistantExpanded] = useState(true);
  const [appliedPrompt, setAppliedPrompt] = useState<{ text: string; ts: number } | null>(null);
  const [openChip, setOpenChip] = useState<string | null>(null);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [kieValues, setKieValues] = useState<Record<string, unknown>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [settingsHydrated, setSettingsHydrated] = useState(false);
  const modelRef = useRef<HTMLDivElement>(null);
  // Якорь выбора модели, когда открыт помощник промпта (док скрыт)
  const modelRef2 = useRef<HTMLDivElement>(null);
  const firstFrameInputRef = useRef<HTMLInputElement>(null);
  const lastFrameInputRef = useRef<HTMLInputElement>(null);
  const multiRefInputRef = useRef<HTMLInputElement>(null);
  const [voiceLibraryOpen, setVoiceLibraryOpen] = useState(false);
  const promptTextareaRef = useRef<HTMLTextAreaElement>(null);

  const handleInsertAudioTag = (tag: string) => {
    const textarea = promptTextareaRef.current;
    if (!textarea) {
      setPrompt((prev) => (prev ? `${prev} ${tag} ` : `${tag} `));
      return;
    }
    const start = textarea.selectionStart ?? prompt.length;
    const end = textarea.selectionEnd ?? prompt.length;
    const before = prompt.slice(0, start);
    const after = prompt.slice(end);
    const spacerBefore = before.length > 0 && !before.endsWith(" ") ? " " : "";
    const spacerAfter = after.length > 0 && !after.startsWith(" ") ? " " : "";
    const inserted = `${spacerBefore}${tag}${spacerAfter}`;
    const nextPrompt = `${before}${inserted}${after}`;
    setPrompt(nextPrompt);
    requestAnimationFrame(() => {
      textarea.focus();
      const newPos = start + inserted.length;
      textarea.setSelectionRange(newPos, newPos);
    });
  };
  const referenceImagesRef = useRef<RefImage[]>([]);

  const settingsQ = useQuery({
    queryKey: ["outsee-create-settings"],
    queryFn: api.getOutseeCreateSettings,
    enabled: open && KIE_CREATE_ENABLED,
  });

  const outseeStatusQ = useQuery({
    queryKey: ["outsee-status"],
    queryFn: api.outseeStatus,
    enabled: open && KIE_CREATE_ENABLED,
    staleTime: 30_000,
  });

  const createQueueQ = useQuery({
    queryKey: ["create-queue"],
    queryFn: api.createQueue,
    enabled: open && KIE_CREATE_ENABLED,
    refetchInterval: open ? 1200 : false,
  });

  const kieCatalogQ = useQuery({
    queryKey: ["kie-catalog"],
    queryFn: api.kieCatalog,
    enabled: open && KIE_CREATE_ENABLED,
    staleTime: 60_000,
  });
  const kieCreditsQ = useQuery({
    queryKey: ["kie-credits"],
    queryFn: api.kieCredits,
    enabled: open && KIE_CREATE_ENABLED,
    refetchInterval: open ? 60_000 : false,
  });

  const runningJobs = [...draftJobs, ...(createQueueQ.data?.running ?? [])];
  const waitingJobs = createQueueQ.data?.waiting ?? [];
  const queueCount =
    (createQueueQ.data?.total_active ?? 0) ||
    runningJobs.length + waitingJobs.length;
  const historyBusy = queueCount > 0 || draftJobs.length > 0;

  const historyQ = useQuery({
    queryKey: ["outsee-create-history", feedKind],
    queryFn: () =>
      api.listOutseeCreateHistory(feedKind, { scope: "create", limit: 60 }),
    enabled: open && KIE_CREATE_ENABLED,
    // Не долбим диск/сеть: часто только пока есть очередь, иначе редко.
    refetchInterval: open ? (historyBusy ? 3000 : 12_000) : false,
  });

  useEffect(() => {
    if (!open || !settingsQ.data || settingsHydrated) return;
    const s = settingsQ.data;
    const mt = (s.media_type as OutseeMediaType) || "image";
    const rawImg = String(s.image_slug || "gpt-image-2").replace("kie:", "");
    if (rawImg === "gpt-image-2" || rawImg === "nano-banana-2") {
      setImageSlug(rawImg);
    } else {
      setImageSlug(rawImg.startsWith("kie:") ? rawImg : `kie:${rawImg}`);
    }
    const rawVid = String(s.video_slug || "veo-3-1-lite").replace("kie:", "");
    if (rawVid === "veo-3-1-lite" || rawVid === "veo-3-1") {
      setVideoSlug("veo-3-1-lite");
    } else {
      setVideoSlug(rawVid.startsWith("kie:") ? rawVid : `kie:${rawVid}`);
    }
    const rawAud = String(s.audio_slug || "kie:suno-music");
    setAudioSlug(
      rawAud.startsWith("kie:")
        ? rawAud
        : rawAud === "suno-5-5"
          ? "kie:suno-music"
          : rawAud === "elevenlabs-v3"
            ? "kie:elevenlabs-tts-multilingual"
            : `kie:${rawAud}`,
    );
    setAspect(String(s.aspect || "16:9"));
    setResolution(String(s.image_resolution || "2K"));
    setDetail(String(s.image_quality || "medium"));
    setVideoResolution(String(s.video_resolution || "720p"));
    setDuration(String(s.duration || "5"));
    const restoredVideo = rawVid;
    setGenerateAudio(
      restoredVideo === "veo-3-1-lite" ? false : Boolean(s.generate_audio),
    );
    setOrientation(s.orientation === "image" ? "image" : "video");
    setMotionQuality(String(s.motion_quality || "std"));
    setInstrumental(Boolean(s.instrumental));
    setPrompt(String(s.prompt || ""));
    setSoraSize(s.sora_size === "large" ? "large" : "small");
    setSettingsHydrated(true);
  }, [open, settingsQ.data, settingsHydrated]);

  useEffect(() => {
    if (!open) {
      setModelOpen(false);
      setOpenChip(null);
      setSettingsHydrated(false);
    }
  }, [open]);

  useEffect(() => {
    referenceImagesRef.current = referenceImages;
  }, [referenceImages]);
  useEffect(() => {
    return () => {
      referenceImagesRef.current.forEach((r) => revokeRefUrl(r.url));
    };
  }, []);

  useEffect(() => {
    if (!modelOpen) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (modelRef.current?.contains(t) || modelRef2.current?.contains(t)) return;
      setModelOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setModelOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [modelOpen]);

  useEffect(() => {
    if (!lightboxOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setLightboxOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lightboxOpen]);

  const [nowTs, setNowTs] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNowTs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const getLiveElapsed = (
    item?:
      | HistoryItem
      | {
          id?: string;
          job_id?: string | null;
          created_at?: string | null;
          started_at?: string | null;
          elapsed_sec?: number | null;
          elapsed_label?: string | null;
          status?: string | null;
        }
      | null,
  ) => {
    if (!item) return "0 мин 00 сек";
    const isPending = item.status === "queued" || item.status === "processing";
    if (!isPending) {
      if (item.elapsed_label) return item.elapsed_label;
      if (item.elapsed_sec != null) return formatElapsedMinSec(item.elapsed_sec);
    }
    // Проверяем соответствующую задачу в активной очереди
    const activeJob =
      runningJobs.find(
        (j) =>
          (item.id && j.history_id === item.id) ||
          (item.job_id && j.job_id === item.job_id),
      ) ||
      waitingJobs.find(
        (j) =>
          (item.id && j.history_id === item.id) ||
          (item.job_id && j.job_id === item.job_id),
      );
    const iso =
      activeJob?.created_at ||
      activeJob?.started_at ||
      item.created_at ||
      item.started_at;
    if (!iso) {
      if (item.elapsed_sec != null && item.elapsed_sec > 0) {
        return formatElapsedMinSec(item.elapsed_sec);
      }
      return "0 мин 01 сек";
    }
    try {
      const t0 = new Date(iso).getTime();
      const diffSec = Math.max(1, Math.floor((nowTs - t0) / 1000));
      return formatElapsedMinSec(diffSec);
    } catch {
      return item.elapsed_label || (item.elapsed_sec != null ? formatElapsedMinSec(item.elapsed_sec) : "0 мин 01 сек");
    }
  };

  const activeSlug =
    mediaType === "image" ? imageSlug : mediaType === "video" ? videoSlug : audioSlug;
  const imageModel = getImageModel(imageSlug);
  const videoModel = getVideoModel(videoSlug);
  const audioModel = getAudioModel(audioSlug);
  const dockChips = dockChipsForModel(activeSlug, mediaType);

  // ---- KIE: модель выбрана из общего пикера (slug "kie:<id>") ----
  const kieModels = useMemo(
    () =>
      (kieCatalogQ.data?.models ?? []).filter((m) => {
        const id = m.id.toLowerCase();
        // GPT Image 2, Nano Banana 2, Veo 3.1 Lite — строго Outsee
        if (id.includes("veo") || id.includes("gpt-image") || id.includes("banana")) {
          return false;
        }
        return true;
      }),
    [kieCatalogQ.data],
  );
  const kieModel = useMemo(() => {
    if (!activeSlug.startsWith("kie:")) return null;
    return kieModels.find((m) => m.id === activeSlug.slice(4)) ?? null;
  }, [activeSlug, kieModels]);
  const kieActive = kieModel != null;
  const kieTextField = kieModel ? kieMainTextField(kieModel) : null;

  const maxReferences = useMemo(() => {
    if (mediaType !== "image") return 0;
    const slug = activeSlug.toLowerCase();
    if (slug.includes("z-image")) return 0;
    if (kieActive && kieModel) {
      const refField = kieModel.fields.find(
        (f) =>
          f.kind === "images" ||
          ["image_urls", "image_input", "imageUrls", "images", "image_url", "imageUrl"].includes(f.name),
      );
      if (refField) return refField.max_items || (refField.kind === "images" ? 8 : 1);
      return 0;
    }
    return 8;
  }, [mediaType, activeSlug, kieActive, kieModel]);

  const kiePrice = useMemo(() => {
    if (!kieModel || !kieCatalogQ.data) return null;
    const vals = { ...kieValues };
    if (kieTextField) vals[kieTextField] = prompt;
    return estimateKie(kieModel, vals, kieCatalogQ.data.credit_usd);
  }, [kieModel, kieValues, prompt, kieTextField, kieCatalogQ.data]);

  useEffect(() => {
    // Сохраняем совместимые поля при смене модели KIE
    setKieValues((prev) => {
      const next: Record<string, unknown> = {};
      if (prev.aspect_ratio) next.aspect_ratio = prev.aspect_ratio;
      if (prev.resolution) next.resolution = prev.resolution;
      if (prev.quality) next.quality = prev.quality;
      return next;
    });
  }, [activeSlug]);

  // Трим каталога Create: outsee — только GPT Image 2 / Nano Banana 2 /
  // Veo 3.1 Lite; аудио — только KIE (Suno/ElevenLabs не дублируются).
  useEffect(() => {
    if (!kieCatalogQ.data) return;
    const isKie = (s: string) => kieModels.some((m) => `kie:${m.id}` === s);
    if (
      mediaType === "image" &&
      !isKie(imageSlug) &&
      !["gpt-image-2", "nano-banana-2"].includes(imageSlug)
    ) {
      setImageSlug("gpt-image-2");
    }
    if (mediaType === "video" && !isKie(videoSlug) && videoSlug !== "veo-3-1-lite") {
      setVideoSlug("veo-3-1-lite");
    }
    if (mediaType === "audio" && !isKie(audioSlug)) {
      setAudioSlug(
        audioSlug === "suno-5-5"
          ? "kie:suno-music"
          : audioSlug === "elevenlabs-v4" || audioSlug === "elevenlabs-v3"
            ? "kie:elevenlabs-v4"
            : "kie:suno-music",
      );
    }
  }, [kieCatalogQ.data, kieModels, mediaType, imageSlug, videoSlug, audioSlug]);

  const currentName = kieActive
    ? (kieModel.label ?? kieModel.id)
    : mediaType === "image"
      ? imageModel.displayName
      : mediaType === "video"
        ? videoModel.displayName
        : audioModel.displayName;
  const currentWired = false;
  const outseeConfigured = Boolean(outseeStatusQ.data?.configured);
  const kieConfigured = Boolean(kieCatalogQ.data?.configured);

  const autoProvider: "outsee" | null = useMemo(() => {
    if (kieActive) return null;
    if (mediaType === "audio") return null;
    if (outseeConfigured) return "outsee";
    return null;
  }, [kieActive, mediaType, outseeConfigured]);

  const maxParallel =
    autoProvider === "outsee"
      ? (createQueueQ.data?.max_parallel_outsee ?? 5)
      : (createQueueQ.data?.max_parallel ?? 5);

  const canApiDirect = kieActive ? kieConfigured : autoProvider != null;
  const currentIcon = kieActive
    ? kieModel?.id.includes("suno")
      ? `${OUTSEE_ORIGIN}/imagemobilepreview/suno.webp`
      : kieModel?.id.includes("elevenlabs")
        ? `${OUTSEE_ORIGIN}/imagemobilepreview/elevenlabs.webp`
        : null
    : mediaType === "image"
      ? imageModel.icon
      : mediaType === "video"
        ? videoModel.icon
        : audioModel.icon;
  const currentCatalogPrice =
    mediaType === "image"
      ? imageModel.price
      : mediaType === "video"
        ? videoModel.price
        : audioModel.price;

  const basePriceLabel = kieActive
    ? kiePrice
      ? `$${kiePrice.usd.toFixed(3)} · ${kiePrice.credits} кр`
      : "—"
    : estimateCreatePrice({
        media: mediaType,
        model: activeSlug,
        resolution,
        duration: Number(duration) || 10,
        size: soraSize,
        catalogPrice: currentCatalogPrice,
      }).label;

  const priceLabel = useMemo(() => {
    if ((mediaType === "image" || mediaType === "video") && batchCount > 1) {
      if (kieActive && kiePrice) {
        return `$${(kiePrice.usd * batchCount).toFixed(3)} · ${kiePrice.credits * batchCount} кр`;
      }
      return `${basePriceLabel} (x${batchCount})`;
    }
    return basePriceLabel;
  }, [basePriceLabel, mediaType, batchCount, kieActive, kiePrice]);

  useEffect(() => {
    if (mediaType === "image") {
      const aspects = chipOptions(imageSlug, "aspect");
      const resolutions = chipOptions(imageSlug, "resolution");
      if (aspects.length) setAspect((a) => clampToOptions(a, aspects, "16:9"));
      if (resolutions.length) setResolution((r) => clampToOptions(r, resolutions, "2K"));
      return;
    }
    if (mediaType === "video") {
      const aspects = chipOptions(videoSlug, "aspect");
      const resolutions = chipOptions(videoSlug, "resolution");
      const durations = chipOptions(videoSlug, "duration");
      if (aspects.length) setAspect((a) => clampToOptions(a, aspects, "16:9"));
      if (resolutions.length) {
        setVideoResolution((r) => clampToOptions(r, resolutions, resolutions[0]));
      }
      if (durations.length) {
        setDuration((d) => clampToOptions(d, durations, durations[0]));
      }
    }
  }, [imageSlug, videoSlug, mediaType]);

  const applyFrameFromHistory = async (item: HistoryItem, slot: "first" | "last") => {
    // Предпочитаем публичный raw_url (Outsee CDN) — data:/local Outsee игнорит.
    const httpUrl = item.raw_url && item.raw_url.startsWith("http") ? item.raw_url : null;
    if (httpUrl) {
      if (slot === "first") {
        setFirstFrameDataUrl(httpUrl);
        setFirstFrameName(item.label || "история");
      } else {
        setLastFrameDataUrl(httpUrl);
        setLastFrameName(item.label || "история");
      }
      toast.success(slot === "first" ? "Стартовый кадр из истории" : "Конечный кадр из истории");
      return;
    }
    if (!item.preview_url) {
      toast.error("Нет URL картинки для кадра");
      return;
    }
    try {
      const res = await fetch(item.preview_url);
      const blob = await res.blob();
      const file = new File([blob], `${item.id}.png`, { type: blob.type || "image/png" });
      const dataUrl = await readFileAsDataUrl(file);
      if (slot === "first") {
        setFirstFrameDataUrl(dataUrl);
        setFirstFrameName(item.label || item.id);
      } else {
        setLastFrameDataUrl(dataUrl);
        setLastFrameName(item.label || item.id);
      }
      toast.success(slot === "first" ? "Стартовый кадр" : "Конечный кадр");
    } catch {
      toast.error("Не удалось взять кадр из истории");
    }
  };

  const addReferenceFromHistory = async (item: HistoryItem) => {
    if (maxReferences <= 0) return;
    if (referenceImages.length >= maxReferences) {
      toast.error(`Достигнут лимит референсов (${maxReferences})`);
      return;
    }
    const httpUrl = item.raw_url && item.raw_url.startsWith("http") ? item.raw_url : null;
    if (httpUrl) {
      setReferenceImages((prev) => [
        ...prev,
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          url: httpUrl,
          name: item.label || "история",
        },
      ]);
      toast.success("Референс взят из истории");
      return;
    }
    if (!item.preview_url) {
      toast.error("Нет URL картинки для референса");
      return;
    }
    try {
      const res = await fetch(item.preview_url);
      const blob = await res.blob();
      const file = new File([blob], `${item.id}.png`, { type: blob.type || "image/png" });
      setReferenceImages((prev) => [...prev, makeRefFromFile(file)]);
      toast.success("Референс добавлен из истории");
    } catch {
      toast.error("Не удалось взять референс из истории");
    }
  };

  const addReferenceFiles = (files: File[]) => {
    if (maxReferences <= 0) {
      toast.error("Эта модель не принимает референсы");
      return;
    }
    const remaining = maxReferences - referenceImages.length;
    if (remaining <= 0) {
      toast.error(`Достигнут лимит референсов (${maxReferences})`);
      return;
    }
    const newRefs = files.slice(0, remaining).map(makeRefFromFile);
    if (!newRefs.length) return;
    setReferenceImages((prev) => [...prev, ...newRefs]);
    toast.success(`Добавлено ${newRefs.length} референс(ов)`);
  };

  const applyModelDefaults = (slug: string, kind: OutseeMediaType) => {
    if (kind === "image") {
      const m = getImageModel(slug);
      const d = m.defaults;
      const aspects = chipOptions(slug, "aspect");
      const resolutions = chipOptions(slug, "resolution");
      if (aspects.length) {
        setAspect((current) => clampToOptions(current, aspects, d.aspectRatio || "16:9"));
      }
      if (resolutions.length) {
        setResolution((current) => clampToOptions(current, resolutions, d.imageResolution || "2K"));
      }
      if (m.chips.includes("detail")) {
        setDetail((current) => current || d.detailLevel || "medium");
      }
      return;
    }
    if (kind === "audio") {
      return;
    }
    const m = getVideoModel(slug);
    const d = m.defaults;
    const aspects = chipOptions(slug, "aspect");
    const resolutions = chipOptions(slug, "resolution");
    const durations = chipOptions(slug, "duration");
    if (aspects.length) {
      setAspect((current) => clampToOptions(current, aspects, d.aspectRatio || "16:9"));
    }
    if (resolutions.length) {
      setVideoResolution((current) => clampToOptions(current, resolutions, d.resolution || resolutions[0]));
    }
    if (durations.length) {
      setDuration((current) => clampToOptions(current, durations, d.duration != null ? String(d.duration) : durations[0]));
    }
    if (m.chips.includes("quality")) {
      setMotionQuality((current) => current || d.motionQuality || "std");
    }
    // Сохраняем вложения и референсы пользователя при смене моделей!
  };

  const settingsPayload = (): Record<string, unknown> => ({
    media_type: mediaType,
    image_slug: imageSlug,
    video_slug: videoSlug,
    audio_slug: audioSlug,
    aspect,
    image_resolution: resolution,
    image_quality: detail,
    image_relax: false,
    video_resolution: videoResolution,
    video_relax: false,
    duration,
    generate_audio: generateAudio,
    orientation,
    motion_quality: motionQuality,
    instrumental,
    prompt,
    image_provider: "outsee",
    video_provider: "outsee",
    sora_size: soraSize,
  });

  const saveGlobal = useMutation({
    mutationFn: () => api.putOutseeCreateSettings(settingsPayload()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["outsee-create-settings"] });
      toast.success("Глобальные настройки Create сохранены");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const applyToProject = useMutation({
    mutationFn: async () => {
      if (projectId == null) throw new Error("Выберите проект слева");
      await api.putOutseeCreateSettings(settingsPayload());
      const body: Record<string, unknown> = {};
      const imgStudio = slugToStudioId(imageSlug, "image");
      const vidStudio = slugToStudioId(videoSlug, "video");
      if (imgStudio) {
        body.image_generator = imgStudio;
        body.aspect_ratio = aspect.replace(":", "_");
        body.image_resolution = resolution.toLowerCase();
        if (imageModel.chips.includes("detail")) body.image_quality = detail;
        body.image_relax = false;
      }
      if (vidStudio) {
        body.video_generator = vidStudio;
        const vr = videoResolution.toLowerCase();
        if (vr === "720p" || vr === "1080p") body.video_resolution = vr;
        body.video_relax = false;
      }
      return api.patchProject(projectId, body);
    },
    onSuccess: () => {
      if (projectId != null) qc.invalidateQueries({ queryKey: ["project", projectId] });
      toast.success("Настройки применены к проекту");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const deleteItem = useMutation({
    mutationFn: (item: HistoryItem) =>
      api.deleteOutseeCreateHistoryItem({ path: item.path || undefined, itemId: item.id }),
    onSuccess: (_, item) => {
      toast.success("Удалено из истории");
      if (selectedId === item.id) setSelectedId(null);
      qc.invalidateQueries({ queryKey: ["outsee-create-history"] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const cancelJobMut = useMutation({
    mutationFn: (jobId: string) => api.cancelCreateJob(jobId),
    onSuccess: () => {
      toast.success("Генерация остановлена");
      qc.invalidateQueries({ queryKey: ["create-queue"] });
      qc.invalidateQueries({ queryKey: ["outsee-create-history"] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const [trackingJobs, setTrackingJobs] = useState<
    { provider: "outsee" | "kie"; jobId: string; historyId: string }[]
  >([]);

  useEffect(() => {
    if (!trackingJobs.length) return;
    let cancelled = false;
    const tick = async () => {
      for (const t of [...trackingJobs]) {
        try {
          const job = await api.createJob(t.jobId);
          if (cancelled) return;
          qc.invalidateQueries({ queryKey: ["create-queue"] });
          if (job.status === "done") {
            const took =
              job.elapsed_label ||
              (job.elapsed_sec != null ? formatElapsedMinSec(job.elapsed_sec) : null);
            toast.success(
              took
                ? `Готово · ${job.model || "файл"} · ${took}`
                : `Готово · ${job.model || "файл"}`,
            );
            if (job.history_id) setSelectedId(job.history_id);
            setTrackingJobs((prev) => prev.filter((x) => x.jobId !== t.jobId));
            qc.invalidateQueries({ queryKey: ["outsee-create-history"] });
          } else if (job.status === "failed") {
            const took =
              job.elapsed_label ||
              (job.elapsed_sec != null ? formatElapsedMinSec(job.elapsed_sec) : null);
            toast.error(
              took
                ? `${job.error || "Генерация не удалась"} · ${took}`
                : job.error || "Генерация не удалась",
            );
            setTrackingJobs((prev) => prev.filter((x) => x.jobId !== t.jobId));
            qc.invalidateQueries({ queryKey: ["outsee-create-history"] });
          }
        } catch {
          /* job may not be ready yet */
        }
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 1500);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [trackingJobs, qc]);

  const handleRandomPrompt = () => {
    const available = RANDOM_PROMPTS.filter((p) => p !== prompt);
    const chosen = available[Math.floor(Math.random() * available.length)];
    setPrompt(chosen);
    toast.success("Случайный промпт подставлен 🎲");
  };

  /**
   * Двойной клик по картинке в истории: применить её конфигурацию
   * (модель / формат / разрешение / детализация из sidecar-params + промпт)
   * к окну и открыть панель «Помощник» с этим промптом.
   */
  const applyHistoryConfig = (item: HistoryItem) => {
    if (item.kind !== "image") return;
    const p = (item.params ?? {}) as Record<string, unknown>;
    const rawModel = item.model ? String(item.model) : "";
    const candidates = [rawModel, slugToStudioId(rawModel, "image") ?? ""];
    const slug = candidates.find((c) => c && chipOptions(c, "aspect").length > 0);
    const effSlug = slug ?? imageSlug;
    if (slug && slug !== imageSlug) setImageSlug(slug);
    if (typeof p.aspect === "string" && p.aspect) {
      setAspect(clampToOptions(p.aspect, chipOptions(effSlug, "aspect"), aspect));
    }
    if (typeof p.resolution === "string" && p.resolution) {
      setResolution(clampToOptions(p.resolution, chipOptions(effSlug, "resolution"), resolution));
    }
    if (typeof p.detail_level === "string" && p.detail_level) {
      const dOpts = chipOptions(effSlug, "detail");
      if (dOpts.length) setDetail(clampToOptions(p.detail_level, dOpts, detail));
    }
    if (item.prompt) {
      setPrompt(item.prompt);
      setAppliedPrompt({ text: item.prompt, ts: Date.now() });
    }
    setAssistantOpen(true);
    toast.success("Конфигурация изображения применена к панели снизу");
  };

  const handleEnhancePrompt = async () => {
    const text = prompt.trim();
    if (!text) {
      toast.error("Сначала напишите краткую идею в поле ввода");
      return;
    }
    setIsEnhancingPrompt(true);
    try {
      const res = await api.enhanceOutseeCreatePrompt({
        prompt: text,
      });
      if (res?.enhanced_prompt) {
        setPrompt(res.enhanced_prompt);
        toast.success("Промпт улучшен ИИ ✨");
      }
    } catch {
      toast.error("Не удалось улучшить промпт");
    } finally {
      setIsEnhancingPrompt(false);
    }
  };

  const createGenerate = useMutation({
    mutationFn: async (
      arg?:
        | string
        | {
            prompt?: string;
            forceSingle?: boolean;
            draftId?: string;
            retryItem?: HistoryItem;
          },
    ) => {
      // forceSingle — помощник промптов: ровно 1 картинка на каждый промпт агента,
      // без умножения на batchCount.
      const retry = typeof arg === "object" ? arg?.retryItem : undefined;
      const promptOverride = typeof arg === "string" ? arg : (arg?.prompt ?? retry?.prompt ?? undefined);
      const forceSingle = (typeof arg === "object" && arg?.forceSingle === true) || Boolean(retry);
      const draftId = typeof arg === "object" ? arg?.draftId : undefined;
      let text = (promptOverride ?? prompt).trim();
      if (!text) throw new Error("Введите промпт");
      if (text.toLowerCase().includes("not example objects from the style guide")) {
        throw new Error("Промпт не собран агентом — генерация не запущена");
      }
      const targetMediaType: OutseeMediaType = retry
        ? ((retry.kind as OutseeMediaType) || "image")
        : mediaType;
      if (text && targetMediaType === "image" && negativePrompt.trim() && !retry) {
        text += `\nAvoid: ${negativePrompt.trim()}`;
      }
      const refUrls = await resolveReferenceUrls(referenceImages);

      const executeSingle = async (index: number) => {
        const nonce = `${Date.now()}-${index}-${Math.random().toString(36).slice(2, 7)}`;
        const retryParams = (retry?.params ?? {}) as Record<string, unknown>;
        const rawModel = retry?.model ? String(retry.model) : "";
        const paramModelId = typeof retryParams.model_id === "string" ? retryParams.model_id : "";

        let normalizedModel = rawModel;
        if (!normalizedModel && targetMediaType === "audio") {
          normalizedModel = audioSlug;
        }
        if (normalizedModel === "suno-5-5") normalizedModel = "kie:suno-music";
        if (normalizedModel === "elevenlabs-v4" || normalizedModel === "elevenlabs-v3") normalizedModel = "kie:elevenlabs-v4";

        const matchedKie =
          kieModels.find(
            (m) =>
              m.id === paramModelId ||
              `kie:${m.id}` === normalizedModel ||
              m.id === normalizedModel ||
              m.label.toLowerCase() === normalizedModel.toLowerCase(),
          ) ||
          (retry?.provider === "kie" || retry?.project_slug === "kie"
            ? kieModels.find((m) => m.id === paramModelId)
            : null);

        // ---- KIE: динамическая модель из каталога kie.ai ----
        const isKie =
          targetMediaType === "audio" ||
          Boolean(matchedKie) ||
          normalizedModel.startsWith("kie:") ||
          Boolean(retryParams.model_id) ||
          retry?.provider === "kie" ||
          retry?.project_slug === "kie" ||
          (Boolean(kieActive && kieModel) && !retry);
        if (isKie) {
          if (!kieConfigured) {
            throw new Error("KIE_API_KEY не задан в .env");
          }
          const effectiveKieModel =
            matchedKie ??
            (normalizedModel.startsWith("kie:") ? kieModels.find((m) => m.id === normalizedModel.slice(4)) : null) ??
            kieModel ??
            (targetMediaType === "audio" ? kieModels.find((m) => m.id === "suno-music") : null);
          const modelId =
            effectiveKieModel?.id ||
            paramModelId ||
            (normalizedModel.startsWith("kie:") ? normalizedModel.slice(4) : null) ||
            kieModel?.id ||
            (targetMediaType === "audio" ? "suno-music" : null);
          if (!modelId) {
            throw new Error("Не удалось определить модель KIE");
          }
          const storedVals = (retryParams.values && typeof retryParams.values === "object"
            ? retryParams.values
            : retryParams) as Record<string, unknown>;
          const vals: Record<string, unknown> = retry
            ? { ...storedVals, _nonce: nonce }
            : { ...kieValues, _nonce: nonce };
          delete vals.model_id;
          delete vals.values;

          const textField = effectiveKieModel ? kieMainTextField(effectiveKieModel) : kieTextField;
          if (textField) vals[textField] = text;
          else if (retry) {
            const pField = Object.keys(vals).find((k) => k.toLowerCase().includes("prompt")) || "prompt";
            vals[pField] = text;
          }
          if (negativePrompt.trim() && !retry) {
            const negField = (effectiveKieModel || kieModel)?.fields.find((f) =>
              f.name.toLowerCase().includes("neg"),
            );
            if (negField) vals[negField.name] = negativePrompt.trim();
          }

          if (modelId === "suno-music") {
            const isInst = Boolean(vals.instrumental ?? instrumental);
            vals.instrumental = isInst;
            const isCustom = vals.customMode !== false;
            if (!vals.style || typeof vals.style !== "string" || !vals.style.trim()) {
              vals.style = text.slice(0, 500);
            }
            if (!vals.title || typeof vals.title !== "string" || !vals.title.trim()) {
              vals.title = text.slice(0, 80);
            }
            if (isInst) {
              vals.prompt = isCustom ? "" : text;
            } else if (!vals.prompt) {
              vals.prompt = text;
            }
          }

          // Автоматическая передача референсов и стартовых кадров в поля модели KIE
          const kieRefUrls =
            retry?.reference_images && retry.reference_images.length > 0
              ? retry.reference_images
              : refUrls.length > 0
                ? refUrls
                : firstFrameDataUrl
                  ? [firstFrameDataUrl]
                  : [];
          if (kieRefUrls.length > 0 && effectiveKieModel) {
            const imageField = effectiveKieModel.fields.find(
              (f) =>
                f.kind === "images" ||
                ["image_urls", "image_input", "imageUrls", "images", "image_url", "imageUrl"].includes(f.name),
            );
            if (imageField) {
              if (
                imageField.kind === "images" ||
                imageField.name.endsWith("s") ||
                imageField.name === "image_input" ||
                (imageField.max_items && imageField.max_items > 1)
              ) {
                vals[imageField.name] = kieRefUrls.slice(0, imageField.max_items || 8);
              } else {
                vals[imageField.name] = kieRefUrls[0];
              }
            }
          }
          if (effectiveKieModel) {
            const isInst = Boolean(vals.instrumental ?? instrumental);
            const missing = effectiveKieModel.fields
              .filter((f) => f.required)
              .filter((f) => kieFieldVisible(f, vals))
              .filter((f) => {
                if (f.name === "prompt" && (isInst || modelId === "suno-music")) return false;
                const v = vals[f.name] ?? f.default;
                if (v === undefined || v === null) return true;
                if (typeof v === "string") return v.trim() === "";
                if (Array.isArray(v)) return v.length === 0;
                return false;
              });
            if (missing.length) {
              throw new Error(`Заполни: ${missing.map((f) => f.label).join(", ")}`);
            }
          }
          void api.putOutseeCreateSettings(settingsPayload()).catch(() => undefined);
          const res = await api.kieGenerate({ model_id: modelId, values: vals });
          return {
            job_id: res.job.job_id,
            history_id: res.job.history_id,
            status: res.job.status,
            queue_position: res.job.queue_position,
            provider: "kie" as const,
            draftId,
          };
        }
        if (!text) throw new Error("Введите промпт");
        if (!outseeConfigured) {
          throw new Error("OUTSEE_API_KEY не задан в .env");
        }
        // Settings не блокируют enqueue: параллельные клики иначе ломаются
        // на гонке записи outsee_create_settings.json.
        void api.putOutseeCreateSettings(settingsPayload()).catch(() => undefined);

        const targetAspect =
          typeof retryParams.aspect === "string" && retryParams.aspect
            ? retryParams.aspect
            : aspect;
        const targetResolution =
          typeof retryParams.resolution === "string" && retryParams.resolution
            ? retryParams.resolution
            : targetMediaType === "video"
              ? videoResolution
              : resolution;
        const targetDetail =
          typeof retryParams.detail_level === "string" && retryParams.detail_level
            ? retryParams.detail_level
            : imageModel.chips.includes("detail")
              ? detail
              : undefined;
        const targetDuration =
          typeof retryParams.duration === "number"
            ? retryParams.duration
            : Number(duration) || 5;
        const targetFirstFrame = retry?.first_frame_url || firstFrameDataUrl;
        const targetLastFrame =
          (typeof retryParams.last_frame_url === "string"
            ? retryParams.last_frame_url
            : undefined) || lastFrameDataUrl;
        const targetRefs =
          retry?.reference_images && retry.reference_images.length > 0
            ? retry.reference_images
            : refUrls.length > 0
              ? refUrls
              : undefined;
        const targetModel =
          rawModel || (targetMediaType === "video" ? videoSlug : imageSlug);
        const targetProjectId = retry ? (retry.project_id ?? projectId) : projectId;

        const enqueued =
          targetMediaType === "video"
            ? await api.outseeGenerate({
                prompt: text,
                media: "video",
                model: targetModel,
                aspect: targetAspect,
                resolution: targetResolution,
                duration: targetDuration,
                generate_audio: videoModel.chips.includes("audio") ? generateAudio : null,
                first_frame_url: targetFirstFrame,
                last_frame_url: targetLastFrame,
                project_id: targetProjectId,
                nonce,
                batch_index: index,
              })
            : await api.outseeGenerate({
                prompt: text,
                media: "image",
                model: targetModel,
                aspect: targetAspect,
                resolution: targetResolution,
                detail_level: targetDetail,
                first_frame_url: targetFirstFrame,
                reference_images: targetRefs,
                project_id: targetProjectId,
                nonce,
                batch_index: index,
              });
        return { ...enqueued, provider: "outsee" as const, draftId };
      };

      const count = forceSingle
        ? 1
        : targetMediaType === "image" || targetMediaType === "video"
          ? batchCount
          : 1;
      if (count > 1) {
        const results = await Promise.all(
          Array.from({ length: count }, (_, i) => executeSingle(i))
        );
        return { batch: true, count, results, draftId };
      }
      return executeSingle(0);
    },
    onSuccess: (res) => {
      const doneDraft =
        res && typeof res === "object" && "draftId" in res
          ? (res as { draftId?: string }).draftId
          : undefined;
      if (doneDraft) {
        setDraftJobs((prev) => prev.filter((d) => d.job_id !== doneDraft));
      }
      if (res && typeof res === "object" && "batch" in res && Array.isArray((res as any).results)) {
        const batchRes = (res as any).results as any[];
        const newTrackers: { provider: "outsee" | "kie"; jobId: string; historyId: string }[] = [];
        let firstHistId: string | null = null;
        for (const r of batchRes) {
          if (r && typeof r === "object" && "job_id" in r && r.job_id) {
            if (!firstHistId && r.history_id) firstHistId = r.history_id;
            newTrackers.push({ provider: r.provider, jobId: r.job_id, historyId: r.history_id });
          }
        }
        if (firstHistId) setSelectedId(firstHistId);
        setTrackingJobs((prev) => [
          ...prev.filter((x) => !newTrackers.some((n) => n.jobId === x.jobId)),
          ...newTrackers,
        ]);
        qc.invalidateQueries({ queryKey: ["outsee-create-history"] });
        qc.invalidateQueries({ queryKey: ["create-queue"] });
        toast.success(`Запущено ${batchRes.length} генерации 🚀`);
        return;
      }
      if (res && typeof res === "object" && "job_id" in res && res.job_id) {
        const r = res as {
          job_id: string;
          history_id: string;
          queue?: number;
          waiting_count?: number;
          running_count?: number;
          status?: string;
          queue_position?: number | null;
          provider: "outsee" | "kie";
        };
        if (r.history_id) setSelectedId(r.history_id);
        setTrackingJobs((prev) => [
          ...prev.filter((x) => x.jobId !== r.job_id),
          { provider: r.provider, jobId: r.job_id, historyId: r.history_id },
        ]);
        qc.invalidateQueries({ queryKey: ["outsee-create-history"] });
        qc.invalidateQueries({ queryKey: ["create-queue"] });
        const wait = r.waiting_count ?? 0;
        const run = r.running_count ?? 0;
        if (r.status === "queued" || (r.queue_position != null && r.queue_position > 0)) {
          toast.message(
            `Ожидание #${r.queue_position ?? wait} · в работе ${run}/${maxParallel}`,
          );
        } else {
          toast.message(`В работе · ${run}/${maxParallel}`);
        }
        return;
      }
      toast.success("Шаг запущен");
      qc.invalidateQueries({ queryKey: ["outsee-create-history"] });
    },
    onError: (e, arg) => {
      const draftId = typeof arg === "object" ? arg?.draftId : undefined;
      if (draftId) {
        setDraftJobs((prev) => prev.filter((d) => d.job_id !== draftId));
      }
      toast.error(errorMessageFromUnknown(e));
    },
  });

  const handleRetry = (item: HistoryItem) => {
    if (!item.prompt) {
      toast.error("У этой генерации нет текста промпта");
      return;
    }
    const p = (item.params ?? {}) as Record<string, unknown>;
    const rawModel = item.model ? String(item.model) : "";
    const paramModelId = typeof p.model_id === "string" ? p.model_id : "";

    const matchedKie =
      kieModels.find(
        (m) =>
          m.id === paramModelId ||
          `kie:${m.id}` === rawModel ||
          m.id === rawModel ||
          m.label.toLowerCase() === rawModel.toLowerCase(),
      ) ||
      (item.provider === "kie" || item.project_slug === "kie"
        ? kieModels.find((m) => m.id === paramModelId)
        : null);

    if (matchedKie) {
      const isKieVideo = matchedKie.result === "video" || matchedKie.media === "video" || item.kind === "video";
      const isKieAudio = matchedKie.result === "audio" || matchedKie.media === "audio" || item.kind === "audio";
      if (isKieVideo) {
        setMediaType("video");
        setVideoSlug(`kie:${matchedKie.id}`);
      } else if (isKieAudio) {
        setMediaType("audio");
        setAudioSlug(`kie:${matchedKie.id}`);
      } else {
        setMediaType("image");
        setImageSlug(`kie:${matchedKie.id}`);
      }
      const vals = (p.values && typeof p.values === "object" ? p.values : p) as Record<string, unknown>;
      setKieValues({ ...vals });
      if (typeof vals.aspect_ratio === "string") setAspect(vals.aspect_ratio);
      if (typeof vals.resolution === "string") setResolution(vals.resolution);
      if (typeof vals.quality === "string") setDetail(vals.quality);
      setPrompt(item.prompt);
    } else if (item.kind === "image") {
      setMediaType("image");
      const candidates = [rawModel, slugToStudioId(rawModel, "image") ?? ""];
      const slug = candidates.find((c) => c && chipOptions(c, "aspect").length > 0);
      const effSlug = slug ?? imageSlug;
      if (slug && slug !== imageSlug) setImageSlug(slug);
      if (typeof p.aspect === "string" && p.aspect) {
        setAspect(clampToOptions(p.aspect, chipOptions(effSlug, "aspect"), aspect));
      }
      if (typeof p.resolution === "string" && p.resolution) {
        setResolution(clampToOptions(p.resolution, chipOptions(effSlug, "resolution"), resolution));
      }
      if (typeof p.detail_level === "string" && p.detail_level) {
        const dOpts = chipOptions(effSlug, "detail");
        if (dOpts.length) setDetail(clampToOptions(p.detail_level, dOpts, detail));
      }
      setPrompt(item.prompt);
      setAppliedPrompt({ text: item.prompt, ts: Date.now() });
    } else if (item.kind === "audio") {
      setMediaType("audio");
      const targetSlug =
        rawModel === "suno-5-5"
          ? "kie:suno-music"
          : rawModel === "elevenlabs-v3"
            ? "kie:elevenlabs-tts-multilingual"
            : rawModel.startsWith("kie:")
              ? rawModel
              : `kie:${rawModel || "suno-music"}`;
      setAudioSlug(targetSlug);
      const vals = (p.values && typeof p.values === "object" ? p.values : p) as Record<string, unknown>;
      setKieValues({ ...vals });
      if (typeof vals.instrumental === "boolean") setInstrumental(vals.instrumental);
      else if (typeof p.instrumental === "boolean") setInstrumental(p.instrumental);
      setPrompt(item.prompt);
    }
    createGenerate.mutate({ retryItem: item, forceSingle: true });
  };

  const historyItems: HistoryItem[] = useMemo(() => {
    const real = (historyQ.data as HistoryItem[] | undefined) ?? [];
    const drafts =
      feedKind === "all" || feedKind === "image"
        ? draftJobs.map((d) => ({
            id: d.history_id,
            kind: "image",
            preview_url: null,
            label: d.prompt_preview || "генерация",
            project_id: null,
            project_slug: null,
            prompt: null,
            status: "processing",
            job_id: d.job_id,
            created_at: d.created_at,
            started_at: d.started_at,
          }))
        : [];
    return [...drafts, ...real];
  }, [historyQ.data, draftJobs, feedKind]);

  const selected = useMemo(() => {
    let item: HistoryItem | null = null;
    if (!historyItems.length) return null;
    if (selectedId) {
      item = historyItems.find((h) => h.id === selectedId) ?? historyItems[0] ?? null;
    } else {
      item = historyItems[0] ?? null;
    }
    if (!item) return null;
    const activeJob =
      runningJobs.find((j) => j.history_id === item?.id || (item?.job_id && j.job_id === item.job_id)) ||
      waitingJobs.find((j) => j.history_id === item?.id || (item?.job_id && j.job_id === item.job_id));
    if (activeJob) {
      return {
        ...item,
        status: activeJob.status || item.status,
        created_at: activeJob.created_at || item.created_at,
        started_at: activeJob.started_at || item.started_at,
      };
    }
    return item;
  }, [historyItems, selectedId, runningJobs, waitingJobs]);

  if (!open) return null;

  const TypeIcon = ({ id }: { id: OutseeMediaType }) => {
    if (id === "video") return <Video className="h-4 w-4" strokeWidth={1.7} />;
    if (id === "audio") return <Music className="h-4 w-4" strokeWidth={1.7} />;
    return <ImageIcon className="h-4 w-4" strokeWidth={1.7} />;
  };

  return (
    <div className="fixed inset-0 z-[80] flex flex-col bg-[#0a0a0a] text-white">
      <header className="flex h-[52px] shrink-0 items-center justify-between border-b border-white/10 bg-[#0d0d11]/90 px-4 backdrop-blur-xl">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-white/10 bg-white/[0.03] text-white/70 transition hover:bg-white/[0.08] hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
          <div className="flex items-center gap-2">
            <Layers className="h-4 w-4" style={{ color: OUTSEE_ACCENT }} />
            <div className="leading-tight">
              <div className="text-sm font-bold tracking-tight text-white/95">Генерация</div>
              <div className="text-[10px] uppercase tracking-[0.16em] text-white/40">
                outsee create · глобально
              </div>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2.5">
          <span className="hidden text-[11px] text-white/40 sm:inline">
            настройки и история общие для Studio
          </span>
          {projectId != null && (
            <span className="rounded-full border border-[#22d3ee]/30 bg-[#22d3ee]/10 px-2.5 py-0.5 font-mono text-[10px] font-semibold text-[#22d3ee]">
              проект #{projectId}
            </span>
          )}
          {mediaType === "image" && (
            <button
              type="button"
              onClick={() => setAssistantOpen((v) => !v)}
              title="Помощник промпта: стиль → запрос → собранный промпт"
              className={cn(
                "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11px] font-semibold transition",
                assistantOpen
                  ? "border-[#22d3ee]/50 bg-[#22d3ee]/15 text-[#22d3ee]"
                  : "border-white/10 bg-white/[0.03] text-white/60 hover:border-[#22d3ee]/40 hover:bg-[#22d3ee]/10 hover:text-white",
              )}
            >
              <Sparkles className="h-3 w-3" />
              Помощник
            </button>
          )}
          <a
            href={outseeCreateUrl(mediaType, activeSlug)}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-[11px] text-white/60 transition hover:border-white/25 hover:bg-white/[0.07] hover:text-white"
          >
            outsee.io
            <ExternalLink className="h-3 w-3" />
          </a>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* History + feed filter */}
        <aside className="flex w-[250px] shrink-0 flex-col border-r border-white/10 bg-[#0a0a0d]/95 backdrop-blur-xl lg:w-[290px]">
          <div className="flex items-center gap-2 border-b border-white/[0.08] px-3.5 py-2.5">
            <History className="h-3.5 w-3.5 text-white/50" />
            <span className="text-[11px] font-bold uppercase tracking-[0.14em] text-white/50">
              История
            </span>
            {queueCount > 0 && (
              <span
                className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[9px] font-extrabold uppercase tracking-wider text-black shadow-sm"
                style={{ backgroundColor: OUTSEE_ACCENT }}
                title={`В работе ${runningJobs.length}/${maxParallel}, ожидание ${waitingJobs.length}`}
              >
                <Loader2 className="h-2.5 w-2.5 animate-spin" />
                {runningJobs.length}·{waitingJobs.length}
              </span>
            )}
            <span className="ml-auto font-mono text-[10px] text-white/40 font-semibold">
              {historyItems.length}
            </span>
          </div>
          <div className="space-y-2 border-b border-white/[0.08] px-2.5 py-2.5">
            <div>
              <div className="mb-1.5 px-1 text-[9px] font-bold uppercase tracking-wider text-white/40">
                В работе · {runningJobs.length}/{maxParallel}
              </div>
              {runningJobs.length === 0 ? (
                <div className="rounded-lg border border-dashed border-white/10 px-2 py-2 text-[9px] text-white/30">
                  нет активных
                </div>
              ) : (
                <div className="space-y-1.5">
                  {runningJobs.map((j) => (
                    <button
                      key={j.job_id}
                      type="button"
                      onClick={() => j.history_id && setSelectedId(j.history_id)}
                      className="flex w-full items-center gap-2 rounded-xl border border-[#22d3ee]/40 bg-[#22d3ee]/10 px-2.5 py-2 text-left shadow-[0_0_15px_rgba(34,211,238,0.15)] transition"
                    >
                      <Loader2
                        className="h-3.5 w-3.5 shrink-0 animate-spin text-[#22d3ee]"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-1">
                          <div className="truncate font-mono text-[10px] font-semibold text-white/90">
                            {j.model || j.media}
                          </div>
                          <span className="shrink-0 font-mono text-[9px] font-semibold text-[#22d3ee]">
                            {getLiveElapsed(j)}
                          </span>
                        </div>
                        <div className="truncate text-[9px] text-white/50">
                          {j.prompt_preview || "генерация…"}
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div>
              <div className="mb-1.5 px-1 text-[9px] font-bold uppercase tracking-wider text-white/40">
                Ожидание · {waitingJobs.length}
              </div>
              {waitingJobs.length === 0 ? (
                <div className="rounded-lg border border-dashed border-white/10 px-2 py-2 text-[9px] text-white/30">
                  очередь пуста
                </div>
              ) : (
                <div className="space-y-1.5">
                  {waitingJobs.map((j) => (
                    <button
                      key={j.job_id}
                      type="button"
                      onClick={() => j.history_id && setSelectedId(j.history_id)}
                      className="flex w-full items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-2.5 py-2 text-left transition hover:border-white/20 hover:bg-white/[0.06]"
                    >
                      <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-white/10 font-mono text-[9px] font-bold text-white/70">
                        #{j.queue_position ?? "—"}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-1">
                          <div className="truncate font-mono text-[10px] font-semibold text-white/80">
                            {j.model || j.media}
                          </div>
                          <span className="shrink-0 font-mono text-[9px] font-medium text-white/50">
                            {getLiveElapsed(j)}
                          </span>
                        </div>
                        <div className="truncate text-[9px] text-white/40">
                          {j.prompt_preview || "в очереди"}
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
          <div className="flex flex-wrap gap-1 border-b border-white/[0.08] p-2">
            {OUTSEE_FEED_TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setFeedKind(t.id)}
                className={cn(
                  "rounded-lg px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider transition-all duration-150",
                  feedKind === t.id
                    ? "bg-[#22d3ee] text-black font-extrabold shadow-[0_0_15px_rgba(34,211,238,0.3)]"
                    : "bg-white/[0.04] text-white/50 hover:bg-white/[0.08] hover:text-white",
                )}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            {historyQ.isLoading ? (
              <div className="flex items-center gap-2 px-2 py-6 text-[11px] text-white/40">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                загрузка…
              </div>
            ) : historyItems.length === 0 ? (
              <div className="px-2 py-8 text-center text-[11px] text-white/35">
                Пока нет файлов. Результаты сохраняются в{" "}
                <span className="font-mono text-white/50">data/generations/</span>
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-2">
                {historyItems.map((item) => {
                  const active = selected?.id === item.id;
                  const isVideo = item.kind === "video";
                  const isAudio = item.kind === "audio";
                  const waitJob = waitingJobs.find((j) => j.history_id === item.id);
                  const runJob = runningJobs.find((j) => j.history_id === item.id);
                  const pending =
                    item.status === "queued" ||
                    item.status === "processing" ||
                    Boolean(waitJob || runJob);
                  const failed = item.status === "failed";
                  const pendingLabel = waitJob
                    ? `ожидание #${waitJob.queue_position ?? "—"}`
                    : runJob || item.status === "processing"
                      ? "в работе"
                      : item.status === "queued"
                        ? "в очереди"
                        : "генерация";
                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => setSelectedId(item.id)}
                      onDoubleClick={() => applyHistoryConfig(item)}
                      className={cn(
                        "group relative aspect-square overflow-hidden rounded-xl border bg-[#121216] transition-all duration-200",
                        active
                          ? "border-[#22d3ee] ring-2 ring-[#22d3ee]/40 shadow-[0_0_20px_rgba(34,211,238,0.25)]"
                          : "border-white/[0.08] hover:border-white/25 hover:bg-[#18181f]",
                      )}
                      title={`${item.label}${item.project_slug ? ` · ${item.project_slug}` : ""} · двойной клик — применить конфигурацию`}
                    >
                      {item.preview_url && !pending ? (
                        isVideo ? (
                          <video
                            src={item.preview_url}
                            muted
                            playsInline
                            preload="metadata"
                            className="h-full w-full object-cover"
                          />
                        ) : isAudio ? (
                          <div className="flex h-full flex-col items-center justify-center gap-1 bg-white/[0.03]">
                            <Music className="h-6 w-6 text-white/40" />
                          </div>
                        ) : (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img
                            src={item.preview_url}
                            alt=""
                            loading="lazy"
                            decoding="async"
                            className="h-full w-full object-cover"
                          />
                        )
                      ) : (
                        <div className="flex h-full flex-col items-center justify-center gap-1.5 px-2 text-center">
                          {pending ? (
                            <Loader2
                              className="h-5 w-5 animate-spin text-[#22d3ee]"
                            />
                          ) : failed ? (
                            <div className="flex flex-col items-center gap-1.5">
                              <span className="text-[9px] font-semibold uppercase tracking-wider text-red-400">
                                ошибка
                              </span>
                              {item.prompt && (
                                <span
                                  role="button"
                                  tabIndex={0}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleRetry(item);
                                  }}
                                  onKeyDown={(e) => {
                                    if (e.key === "Enter" || e.key === " ") {
                                      e.stopPropagation();
                                      handleRetry(item);
                                    }
                                  }}
                                  className="inline-flex items-center gap-1 rounded-full bg-red-500/15 border border-red-500/30 px-2 py-0.5 text-[9px] font-medium text-red-200 hover:bg-red-500/30 hover:border-red-400 hover:text-white transition-all cursor-pointer shadow-sm active:scale-95"
                                  title="Повторить"
                                >
                                  <RotateCw className={cn("h-2.5 w-2.5", createGenerate.isPending && "animate-spin")} />
                                  <span>Повтор</span>
                                </span>
                              )}
                            </div>
                          ) : (
                            <span className="text-[9px] text-white/25">{item.kind}</span>
                          )}
                          {pending && (
                            <div className="flex flex-col items-center gap-0.5">
                              <span className="text-[9px] font-semibold uppercase tracking-wider text-white/55">
                                {pendingLabel}
                              </span>
                              <span className="font-mono text-[9px] font-bold text-[#22d3ee]">
                                {getLiveElapsed(item)}
                              </span>
                            </div>
                          )}
                        </div>
                      )}
                      {((item.reference_images && item.reference_images.length > 0) || item.first_frame_url) && !pending && (
                        <div className="absolute top-1.5 left-1.5 z-20 flex items-center gap-0.5 rounded-md bg-black/75 px-1.5 py-0.5 text-[8px] font-bold text-[#22d3ee] backdrop-blur">
                          <Paperclip className="h-2.5 w-2.5" />
                          <span>{item.reference_images?.length || 1}</span>
                        </div>
                      )}
                      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 via-black/50 to-transparent px-2 py-1.5">
                        <div className="truncate font-mono text-[9px] font-semibold text-white/80">{item.label}</div>
                        {item.project_slug && (
                          <div className="truncate text-[8px] text-white/45">{item.project_slug}</div>
                        )}
                      </div>
                      {pending && (item.job_id || item.id) && !String(item.job_id || item.id).startsWith("draft-") && (
                        <div className="absolute top-1.5 right-1.5 z-20 flex items-center gap-1 opacity-0 transition group-hover:opacity-100">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              const jid = item.job_id || (item.id.startsWith("gen-") ? item.id.slice(4) : item.id);
                              cancelJobMut.mutate(jid);
                            }}
                            className="flex h-6 w-6 items-center justify-center rounded-md bg-red-950/80 text-red-300 backdrop-blur transition hover:bg-red-600 hover:text-white shadow-md ring-1 ring-red-500/40"
                            title="Остановить генерацию"
                          >
                            <XCircle className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      )}
                      {!pending && (
                        <div className="absolute top-1.5 right-1.5 z-20 flex items-center gap-1 opacity-0 transition group-hover:opacity-100">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              deleteItem.mutate(item);
                            }}
                            className="flex h-6 w-6 items-center justify-center rounded-md bg-black/75 text-white/70 backdrop-blur transition hover:bg-red-600 hover:text-white shadow-md"
                            title="Удалить из истории"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                          {(item.preview_url || item.raw_url) && (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                downloadMediaFile(
                                  item.preview_url || item.raw_url || "",
                                  item.label || "generation",
                                  item.kind === "video" ? "mp4" : item.kind === "audio" ? "mp3" : "png",
                                  item.path,
                                );
                              }}
                              className="flex h-6 w-6 items-center justify-center rounded-md bg-black/75 text-white/80 backdrop-blur transition hover:bg-[#22d3ee] hover:text-black shadow-md"
                              title="Скачать файл"
                            >
                              <Download className="h-3.5 w-3.5" />
                            </button>
                          )}
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </aside>

        {/* Result + dock */}
        <section className="relative flex min-w-0 flex-1 flex-col">
          {/* Ambient glow backlight */}
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center -z-0">
            <div className="h-80 w-80 rounded-full bg-[#22d3ee]/10 blur-[110px]" />
            <div className="h-60 w-60 rounded-full bg-purple-500/10 blur-[90px]" />
          </div>

          {/* Header - float top-left so media starts at the very top */}
          <div className="pointer-events-none absolute top-3 left-4 z-20 flex flex-col gap-0.5 lg:left-6">
            <h2 className="text-sm font-bold text-white lg:text-base">
              Результат генерации
            </h2>
            {selected && (
              <div className="flex flex-col text-[11px] font-medium text-white/50">
                {selected.elapsed_label ||
                (selected.elapsed_sec != null && selected.elapsed_sec >= 0) ? (
                  <div>
                    Время генерации:{" "}
                    <span className="font-mono font-semibold text-[#22d3ee]">
                      {getLiveElapsed(selected)}
                    </span>
                  </div>
                ) : null}
                {selected.model && (
                  <div className="text-white/45">
                    Модель:{" "}
                    <span className="font-mono font-semibold text-white/75">{selected.model}</span>
                  </div>
                )}
              </div>
            )}
          </div>

          <div
            className={cn(
              "relative z-10 flex min-h-0 flex-1 flex-col items-center px-4 pb-[260px] lg:px-6 w-full",
              selected?.kind === "audio" || !selected?.preview_url || selected?.status === "failed" || selected?.status === "queued" || selected?.status === "processing"
                ? "justify-center my-auto"
                : "justify-start pt-2",
            )}
          >
            {selected?.preview_url &&
            selected.status !== "queued" &&
            selected.status !== "processing" ? (
              <div className="flex flex-col items-center w-full my-auto">
                <div className="group relative flex max-h-[calc(100vh-360px)] max-w-full items-center justify-center w-full">
                  {selected.kind === "video" ? (
                    <video
                      src={selected.preview_url}
                      controls
                      className="max-h-[calc(100vh-360px)] max-w-full rounded-2xl border border-white/15 bg-black/80 shadow-[0_20px_50px_rgba(0,0,0,0.8)]"
                    />
                  ) : selected.kind === "audio" ? (
                    <div className="flex w-full items-center justify-center py-4 my-auto">
                      <AudioStudioPlayer
                        item={selected}
                        onInspect={() => setLightboxOpen(true)}
                      />
                    </div>
                  ) : (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={selected.preview_url}
                      alt=""
                      onClick={() => setLightboxOpen(true)}
                      className="max-h-[calc(100vh-360px)] max-w-full cursor-zoom-in rounded-2xl border border-white/15 bg-black/80 object-contain shadow-[0_20px_50px_rgba(0,0,0,0.8)] transition hover:brightness-105"
                    />
                  )}
                  {selected.kind !== "audio" && (
                    <button
                      type="button"
                      onClick={() => setLightboxOpen(true)}
                      className="absolute top-3 right-3 z-30 flex items-center gap-1.5 rounded-xl border border-white/20 bg-black/70 px-3 py-1.5 text-[11px] font-medium text-white/90 opacity-0 backdrop-blur-md transition hover:scale-105 hover:border-[#22d3ee]/60 hover:bg-[#22d3ee]/20 hover:text-white group-hover:opacity-100 shadow-2xl"
                      title="Инспектор промпта / Во весь экран"
                    >
                      <Maximize2 className="h-3.5 w-3.5" />
                      <span>Инспектор</span>
                    </button>
                  )}
                </div>

                {/* Отображение использованных референсов (если были) */}
                {((selected.reference_images && selected.reference_images.length > 0) || selected.first_frame_url) && (
                  <div className="mt-2.5 flex flex-wrap items-center gap-2 rounded-xl border border-white/10 bg-black/50 px-3 py-1.5 backdrop-blur-md shadow-md">
                    <span className="text-[10px] font-semibold uppercase tracking-wider text-white/50">
                      Использованные референсы ({selected.reference_images?.length || 1}):
                    </span>
                    <div className="flex items-center gap-1.5">
                      {(selected.reference_images && selected.reference_images.length > 0
                        ? selected.reference_images
                        : [selected.first_frame_url!]
                      ).map((u, i) => (
                        <a
                          key={i}
                          href={u}
                          target="_blank"
                          rel="noreferrer"
                          className="group/ref relative block h-8 w-8 overflow-hidden rounded-lg border border-white/20 transition hover:scale-110 hover:border-[#22d3ee]"
                          title="Открыть референс в новой вкладке"
                        >
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={u} alt="" className="h-full w-full object-cover" />
                        </a>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : selected &&
              (selected.status === "queued" || selected.status === "processing") ? (
              <div className="my-auto flex w-full max-w-sm flex-col items-center gap-3.5 rounded-3xl border border-white/15 bg-[#121216]/95 px-6 py-10 text-center backdrop-blur-2xl shadow-[0_20px_50px_rgba(0,0,0,0.8)]">
                <Loader2
                  className="h-9 w-9 animate-spin text-[#22d3ee]"
                />
                <div className="text-sm font-bold text-white/90">
                  {selected.status === "queued" ? "В очереди ожидания" : "Идёт генерация…"}
                </div>
                <div className="inline-flex items-center gap-1.5 rounded-full border border-[#22d3ee]/30 bg-[#22d3ee]/10 px-3 py-1 font-mono text-[12px] font-bold text-[#22d3ee]">
                  <Clock className="h-3.5 w-3.5" />
                  <span>{getLiveElapsed(selected)}</span>
                </div>
                <div className="text-[12px] text-white/50">
                  {selected.model || selected.label}
                  {queueCount > 1 ? ` · очередь ${queueCount}` : ""}
                </div>
                {selected.prompt && (
                  <div className="line-clamp-3 max-w-full text-[11px] text-white/40">
                    {selected.prompt}
                  </div>
                )}
                <button
                  type="button"
                  onClick={() => {
                    const jid = selected.job_id || (selected.id.startsWith("gen-") ? selected.id.slice(4) : selected.id);
                    cancelJobMut.mutate(jid);
                  }}
                  disabled={cancelJobMut.isPending}
                  className="mt-2 inline-flex items-center gap-1.5 rounded-xl border border-red-500/40 bg-red-500/15 px-4 py-2 text-[12px] font-semibold text-red-300 backdrop-blur transition hover:border-red-500/60 hover:bg-red-500/30 hover:text-white disabled:opacity-50 shadow-lg"
                  title="Остановить выполнение генерации"
                >
                  <XCircle className="h-4 w-4" />
                  <span>{cancelJobMut.isPending ? "Останавливаем…" : "Остановить генерацию"}</span>
                </button>
              </div>
            ) : selected?.status === "failed" ? (
              <div className="my-auto flex w-full max-w-md flex-col items-center gap-3 rounded-2xl border border-red-500/30 bg-red-500/10 px-6 py-8 text-center backdrop-blur-2xl shadow-[0_20px_50px_rgba(0,0,0,0.8)]">
                <div className="text-sm font-bold text-red-300">Ошибка генерации</div>
                <div className="text-[12px] text-white/60">
                  {selected.error || "Не удалось получить файл"}
                </div>
                <div className="text-[12px] font-medium text-white/55">
                  Результат ·{" "}
                  {selected.elapsed_label ||
                    formatElapsedMinSec(selected.elapsed_sec)}
                </div>
                <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
                  {selected.prompt && (
                    <button
                      type="button"
                      onClick={() => handleRetry(selected)}
                      disabled={createGenerate.isPending}
                      className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-[#22d3ee] to-[#06b6d4] px-4 py-2 text-[12px] font-bold text-black shadow-[0_0_20px_rgba(34,211,238,0.4)] transition hover:brightness-110 active:scale-95 disabled:opacity-50"
                      title="Повторить генерацию с этим промптом"
                    >
                      <RotateCw className={cn("h-4 w-4", createGenerate.isPending && "animate-spin")} />
                      <span>{createGenerate.isPending ? "Запуск…" : "Повторить"}</span>
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => deleteItem.mutate(selected)}
                    disabled={deleteItem.isPending}
                    className="inline-flex items-center gap-1.5 rounded-xl border border-red-500/30 bg-red-500/20 px-3.5 py-2 text-[11px] font-semibold text-red-300 backdrop-blur transition hover:border-red-500/50 hover:bg-red-500/30 hover:text-white shadow-lg active:scale-95 disabled:opacity-50"
                    title="Удалить ошибочную запись из истории"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    <span>Удалить из истории</span>
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex w-full max-w-xs flex-col items-center gap-4 rounded-2xl border border-white/10 bg-[#121216]/70 px-6 py-10 text-center backdrop-blur-xl">
                <ImageIcon className="h-8 w-8 text-white/30" />
                <div className="text-sm font-medium text-white/70">Нет результата</div>
                <div className="text-[12px] text-white/40">
                  Файлы пишутся в{" "}
                  <span className="font-mono text-white/60">data/generations/</span> на этом
                  компьютере.
                </div>
              </div>
            )}
          </div>

          {/* prompt dock + vertical type toggle */}
          <div className="absolute bottom-0 left-0 right-0 z-10 px-3 pb-3 lg:px-5 lg:pb-4">
            <div className="flex items-end gap-2">
              {/* cs-typetoggle */}
              <div className="flex shrink-0 flex-col gap-2">
                {OUTSEE_TYPE_TABS.map((t) => {
                  const active = mediaType === t.id;
                  return (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => {
                        setMediaType(t.id);
                        setFeedKind(t.id);
                        setModelOpen(false);
                      }}
                      aria-pressed={active}
                      className={cn(
                        "flex min-w-[76px] flex-col items-center gap-1.5 rounded-xl border px-3 py-2.5 transition-all duration-200",
                        active
                          ? "border-[#22d3ee] bg-[#22d3ee]/15 text-[#22d3ee] shadow-[0_0_18px_rgba(34,211,238,0.25)]"
                          : "border-white/10 bg-[#16161b]/90 text-white/45 hover:border-white/20 hover:bg-[#1e1e24] hover:text-white",
                      )}
                    >
                      <TypeIcon id={t.id} />
                      <span className="font-mono text-[10px] font-bold uppercase tracking-[0.08em]">
                        {t.label}
                      </span>
                    </button>
                  );
                })}
              </div>

              {/* Помощник промпта: заменяет док генерации (те же размеры) */}
              {assistantOpen && mediaType === "image" && (
                <GenAssistantPanel
                  onClose={() => setAssistantOpen(false)}
                  appliedPrompt={appliedPrompt}
                  imageSlug={imageSlug}
                  modelName={currentName}
                  aspect={aspect}
                  resolution={resolution}
                  detail={detail}
                  generating={createGenerate.isPending}
                  onAspectChange={setAspect}
                  onResolutionChange={setResolution}
                  onDetailChange={setDetail}
                  onOpenModelPicker={() => {
                    setModelOpen(true);
                    setOpenChip(null);
                  }}
                  onApplyPrompt={(t) => setPrompt(t)}
                  onGenerate={(t) => {
                    if (isUnfilledAssistantPrompt(t)) {
                      toast.error("Промпт не собран агентом — генерация не запущена", {
                        duration: 12_000,
                        position: "top-center",
                      });
                      return;
                    }
                    createGenerate.mutate({ prompt: t, forceSingle: true });
                    setPrompt("");
                  }}
                  onPrepareGenerate={(preview, n) => {
                    setPrompt("");
                    const now = new Date().toISOString();
                    const ids: string[] = [];
                    const extra: DraftJob[] = [];
                    for (let i = 0; i < Math.max(1, n); i += 1) {
                      const id = `draft-${Date.now()}-${i}-${Math.random().toString(36).slice(2, 6)}`;
                      ids.push(id);
                      extra.push({
                        job_id: id,
                        history_id: id,
                        status: "processing",
                        media: "image",
                        model: imageSlug,
                        prompt_preview: preview.slice(0, 80) || "собираю промпт…",
                        provider: "draft",
                        created_at: now,
                        started_at: now,
                      });
                    }
                    setDraftJobs((prev) => [...extra, ...prev]);
                    setSelectedId(ids[0] ?? null);
                    return ids;
                  }}
                  onFailGenerate={(ids) => {
                    if (!ids.length) return;
                    setDraftJobs((prev) => prev.filter((d) => !ids.includes(d.job_id)));
                  }}
                  onGenerateAll={(texts, draftIds) => {
                    const ready = texts
                      .map((x) => x.trim())
                      .filter((t) => !isUnfilledAssistantPrompt(t));
                    const unused = (draftIds || []).slice(ready.length);
                    if (unused.length) {
                      setDraftJobs((prev) => prev.filter((d) => !unused.includes(d.job_id)));
                    }
                    if (!ready.length) {
                      if (draftIds?.length) {
                        setDraftJobs((prev) => prev.filter((d) => !draftIds.includes(d.job_id)));
                      }
                      toast.error("Промпт не собран агентом — генерация не запущена", {
                        duration: 12_000,
                        position: "top-center",
                      });
                      return;
                    }
                    ready.forEach((t, i) => {
                      createGenerate.mutate({
                        prompt: t,
                        forceSingle: true,
                        draftId: draftIds?.[i],
                      });
                    });
                    setPrompt("");
                  }}
                  expanded={assistantExpanded}
                  onExpandedChange={setAssistantExpanded}
                  modelIcon={currentIcon}
                  references={referenceImages}
                  maxReferences={maxReferences}
                  onAddReferenceFiles={(files) => addReferenceFiles(files)}
                  onRemoveReference={(id) =>
                    setReferenceImages((prev) => {
                      const hit = prev.find((r) => r.id === id);
                      if (hit) revokeRefUrl(hit.url);
                      return prev.filter((r) => r.id !== id);
                    })
                  }
                />
              )}
              {/* выбор модели при открытом помощнике: док скрыт, поэтому отдельный якорь у правой панели */}
              {assistantOpen && mediaType === "image" && modelOpen && (
                <div
                  className="absolute bottom-full right-3 z-50 mb-2 w-[520px] lg:right-5"
                  ref={modelRef2}
                >
                  <ModelPickerPopover
                    mediaType={mediaType}
                    selectedSlug={activeSlug}
                    kieModels={kieModels}
                    creditUsd={kieCatalogQ.data?.credit_usd ?? 0.005}
                    onSelect={(slug) => {
                      if (mediaType === "image") setImageSlug(slug);
                      else if (mediaType === "video") setVideoSlug(slug);
                      else setAudioSlug(slug);
                      if (!slug.startsWith("kie:")) applyModelDefaults(slug, mediaType);
                      setModelOpen(false);
                    }}
                  />
                </div>
              )}
              <div
                className={cn(
                  "min-w-0 flex-1 rounded-2xl border border-white/15 bg-[#121216]/95 backdrop-blur-2xl shadow-[0_20px_60px_rgba(0,0,0,0.85)] ring-1 ring-white/10",
                  assistantOpen && mediaType === "image" && "hidden",
                )}
              >
                {/* KIE: вложения для аудио/видео (голос, донор движения, аудиофайл) */}
                {kieActive &&
                  kieModel &&
                  kieFileFields(kieModel).filter((f) => f.kind !== "images").length > 0 && (
                  <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.08] px-3 py-2.5 lg:px-4">
                    {kieFileFields(kieModel)
                      .filter((f) => f.kind !== "images")
                      .map((f) => (
                        <KieAttachButton
                          key={f.name}
                          field={f}
                          values={kieValues}
                          onChange={(name, items) =>
                            setKieValues((prev) => ({ ...prev, [name]: items }))
                          }
                        />
                      ))}
                    <span className="text-[10px] text-white/35">
                      файл грузится в kie → публичный URL; или вставь свой URL в поле
                    </span>
                  </div>
                )}
                {/* Унифицированная зона вложений и референсов (Outsee + KIE) */}
                {((mediaType === "video" &&
                  (videoModel.chips.includes("image-input") ||
                    (kieActive &&
                      Boolean(
                        kieModel?.fields.some(
                          (f) => f.kind === "images" || f.name.toLowerCase().includes("image"),
                        ),
                      )))) ||
                  (mediaType === "image" && maxReferences > 0)) && (
                  <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.08] px-3 py-2.5 lg:px-4">
                    {mediaType === "image" ? (
                      <>
                        <input
                          ref={multiRefInputRef}
                          type="file"
                          multiple
                          accept="image/png,image/jpeg,image/webp"
                          className="hidden"
                          onChange={(e) => {
                            const files = Array.from(e.target.files || []);
                            if (files.length) addReferenceFiles(files);
                            e.target.value = "";
                          }}
                        />
                        <button
                          type="button"
                          onClick={() => multiRefInputRef.current?.click()}
                          disabled={referenceImages.length >= maxReferences}
                          className={cn(
                            "inline-flex h-9 items-center gap-2 rounded-xl border px-3 text-[11px] font-bold uppercase tracking-wider transition",
                            referenceImages.length > 0
                              ? "border-[#22d3ee]/40 bg-[#22d3ee]/10 text-[#22d3ee]"
                              : "border-dashed border-white/20 bg-white/[0.03] text-white/70 hover:border-white/40 hover:text-white",
                          )}
                        >
                          <Paperclip className="h-3.5 w-3.5" />
                          <span>+ Референс</span>
                          <span className="rounded-md bg-white/10 px-1.5 py-0.5 font-mono text-[10px]">
                            {referenceImages.length}/{maxReferences}
                          </span>
                        </button>
                        {referenceImages.map((ref, idx) => (
                          <div
                            key={ref.id}
                            className="group flex items-center gap-1.5 rounded-xl border border-white/15 bg-white/[0.05] py-1 pl-1 pr-2 text-[11px] text-white/90"
                          >
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img
                              src={ref.url}
                              alt=""
                              className="h-6 w-6 rounded-lg object-cover ring-1 ring-white/15"
                            />
                            <span className="max-w-[90px] truncate font-mono text-[10px] text-white/75">
                              {ref.name || `Реф #${idx + 1}`}
                            </span>
                            <button
                              type="button"
                              onClick={() =>
                                setReferenceImages((prev) => {
                                  revokeRefUrl(ref.url);
                                  return prev.filter((r) => r.id !== ref.id);
                                })
                              }
                              className="ml-0.5 text-white/40 transition hover:text-red-400"
                              title="Удалить"
                            >
                              <X className="h-3 w-3" />
                            </button>
                          </div>
                        ))}
                        {maxReferences > 0 &&
                          selected?.kind === "image" &&
                          selected.status === "done" &&
                          referenceImages.length < maxReferences && (
                            <button
                              type="button"
                              onClick={() => void addReferenceFromHistory(selected)}
                              className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-[#22d3ee]/40 bg-[#22d3ee]/10 px-3 text-[11px] font-semibold text-[#22d3ee] transition hover:bg-[#22d3ee]/20"
                              title="Добавить текущий результат в референсы"
                            >
                              + В референсы ({referenceImages.length}/{maxReferences})
                            </button>
                          )}
                      </>
                    ) : (
                      <>
                        <input
                          ref={firstFrameInputRef}
                          type="file"
                          accept="image/png,image/jpeg,image/webp"
                          className="hidden"
                          onChange={(e) => {
                            const f = e.target.files?.[0];
                            if (!f) return;
                            void readFileAsDataUrl(f).then((dataUrl) => {
                              setFirstFrameDataUrl(dataUrl);
                              setFirstFrameName(f.name);
                              toast.success("Стартовый кадр добавлен");
                            });
                            e.target.value = "";
                          }}
                        />
                        <input
                          ref={lastFrameInputRef}
                          type="file"
                          accept="image/png,image/jpeg,image/webp"
                          className="hidden"
                          onChange={(e) => {
                            const f = e.target.files?.[0];
                            if (!f) return;
                            void readFileAsDataUrl(f).then((dataUrl) => {
                              setLastFrameDataUrl(dataUrl);
                              setLastFrameName(f.name);
                              toast.success("Конечный кадр добавлен");
                            });
                            e.target.value = "";
                          }}
                        />
                        <button
                          type="button"
                          onClick={() => firstFrameInputRef.current?.click()}
                          className={cn(
                            "inline-flex h-9 items-center gap-2 rounded-xl border px-3 text-[11px] font-bold uppercase tracking-wider transition",
                            firstFrameDataUrl
                              ? "border-[#22d3ee]/40 bg-[#22d3ee]/10 text-[#22d3ee]"
                              : "border-dashed border-white/20 bg-white/[0.03] text-white/70 hover:border-white/40 hover:text-white",
                          )}
                        >
                          {firstFrameDataUrl ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                              src={firstFrameDataUrl}
                              alt=""
                              className="h-6 w-6 rounded-lg object-cover ring-1 ring-white/15"
                            />
                          ) : (
                            <Paperclip className="h-3.5 w-3.5" />
                          )}
                          <span>Стартовый кадр</span>
                          {firstFrameDataUrl && (
                            <span
                              className="text-white/45 hover:text-white"
                              onClick={(ev) => {
                                ev.stopPropagation();
                                setFirstFrameDataUrl(null);
                                setFirstFrameName(null);
                              }}
                            >
                              <X className="h-3.5 w-3.5" />
                            </span>
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={() => lastFrameInputRef.current?.click()}
                          className={cn(
                            "inline-flex h-9 items-center gap-2 rounded-xl border px-3 text-[11px] font-bold uppercase tracking-wider transition",
                            lastFrameDataUrl
                              ? "border-[#22d3ee]/40 bg-[#22d3ee]/10 text-[#22d3ee]"
                              : "border-dashed border-white/20 bg-white/[0.03] text-white/70 hover:border-white/40 hover:text-white",
                          )}
                        >
                          {lastFrameDataUrl ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                              src={lastFrameDataUrl}
                              alt=""
                              className="h-6 w-6 rounded-lg object-cover ring-1 ring-white/15"
                            />
                          ) : (
                            <Paperclip className="h-3.5 w-3.5" />
                          )}
                          <span>Конечный кадр</span>
                          {lastFrameDataUrl && (
                            <span
                              className="text-white/45 hover:text-white"
                              onClick={(ev) => {
                                ev.stopPropagation();
                                setLastFrameDataUrl(null);
                                setLastFrameName(null);
                              }}
                            >
                              <X className="h-3.5 w-3.5" />
                            </span>
                          )}
                        </button>
                        {mediaType === "video" &&
                          videoModel.chips.includes("image-input") &&
                          selected?.kind === "image" &&
                          selected.status === "done" && (
                            <>
                              <button
                                type="button"
                                onClick={() => void applyFrameFromHistory(selected, "first")}
                                className="inline-flex h-9 items-center gap-1 rounded-xl border border-[#22d3ee]/40 bg-[#22d3ee]/10 px-2.5 text-[11px] font-semibold text-[#22d3ee] transition hover:bg-[#22d3ee]/20"
                                title="Текущее фото → Стартовый кадр"
                              >
                                → В старт
                              </button>
                              <button
                                type="button"
                                onClick={() => void applyFrameFromHistory(selected, "last")}
                                className="inline-flex h-9 items-center gap-1 rounded-xl border border-white/20 bg-white/[0.04] px-2.5 text-[11px] font-medium text-white/75 transition hover:border-white/30 hover:bg-white/[0.08]"
                                title="Текущее фото → Конечный кадр"
                              >
                                → В финиш
                              </button>
                            </>
                          )}
                      </>
                    )}
                    <span className="text-[10px] text-white/35">
                      файл с диска или выбор из истории слева
                    </span>
                  </div>
                )}
                {/* Actions and Negative Prompt for image mode */}
                {mediaType === "image" && (
                  <div className="flex flex-wrap items-center justify-end gap-1.5 border-b border-white/[0.06] bg-white/[0.015] px-3 py-1.5 lg:px-4">
                    <div className="flex items-center gap-1.5 shrink-0">
                      <button
                        type="button"
                        onClick={handleRandomPrompt}
                        className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[11px] font-medium text-white/70 transition hover:border-[#22d3ee]/40 hover:bg-[#22d3ee]/10 hover:text-white"
                        title="Подставить готовый красивый пример промпта"
                      >
                        <Dices className="h-3 w-3 text-[#22d3ee]" />
                        <span>Случайный</span>
                      </button>
                      <button
                        type="button"
                        disabled={isEnhancingPrompt}
                        onClick={handleEnhancePrompt}
                        className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-[#22d3ee]/30 bg-[#22d3ee]/10 px-2 py-0.5 text-[11px] font-semibold text-[#22d3ee] transition hover:bg-[#22d3ee]/20 disabled:opacity-50"
                        title="Улучшить и детализировать текущий промпт с помощью ИИ"
                      >
                        {isEnhancingPrompt ? (
                          <Loader2 className="h-3 w-3 animate-spin text-[#22d3ee]" />
                        ) : (
                          <FileText className="h-3 w-3 text-[#22d3ee]" />
                        )}
                        <span>{isEnhancingPrompt ? "Улучшаем…" : "Улучшить"}</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => setShowNegativePrompt((v) => !v)}
                        className={cn(
                          "inline-flex shrink-0 items-center gap-1 rounded-lg px-2 py-0.5 font-mono text-[10px] transition",
                          showNegativePrompt || negativePrompt
                            ? "bg-purple-500/20 text-purple-300 ring-1 ring-purple-500/30"
                            : "bg-white/[0.04] text-white/45 hover:text-white",
                        )}
                      >
                        <span>⛔ Негативный</span>
                        {negativePrompt && <span className="h-1.5 w-1.5 rounded-full bg-purple-400" />}
                      </button>
                    </div>
                  </div>
                )}

                {/* Expandable Negative Prompt input */}
                {mediaType === "image" && showNegativePrompt && (
                  <div className="border-b border-white/[0.06] bg-black/20 px-3 py-2 lg:px-4">
                    <input
                      type="text"
                      value={negativePrompt}
                      onChange={(e) => setNegativePrompt(e.target.value)}
                      placeholder="Отрицательный промпт: чего НЕ должно быть на картинке (напр. размытие, лишние пальцы, текст, мусор)..."
                      className="w-full rounded-lg border border-white/10 bg-[#16161b] px-3 py-1.5 text-[12px] text-white placeholder-white/30 focus:border-purple-400 focus:outline-none selection:bg-[#22d3ee]/40 selection:text-white"
                    />
                  </div>
                )}
                {mediaType === "audio" &&
                  (kieModel?.id === "elevenlabs-v4" || audioSlug.includes("elevenlabs-v4")) && (
                    <div className="px-3 pt-3 lg:px-4">
                      <AudioTagsBar onInsertTag={handleInsertAudioTag} />
                    </div>
                  )}
                {(!kieActive || kieTextField) && (
                  <div className="px-3 pt-3 lg:px-4">
                    <textarea
                      ref={promptTextareaRef}
                      value={prompt}
                      onChange={(e) => setPrompt(e.target.value)}
                      placeholder={
                        mediaType === "audio"
                          ? kieModel?.id === "elevenlabs-v4" || audioSlug.includes("elevenlabs-v4")
                            ? "Введите текст для озвучки (1–10 000 символов)... Используйте кнопки аудио-тегов выше для добавления эмоций, смеха, пауз и акцентов."
                            : "Текст / описание трека…"
                          : mediaType === "video"
                            ? "Опишите видео…"
                            : "Опишите изображение…"
                      }
                      rows={
                        mediaType === "audio" &&
                        (kieModel?.id === "elevenlabs-v4" || audioSlug.includes("elevenlabs-v4"))
                          ? 5
                          : 3
                      }
                      style={{ outline: "none" }}
                      className="w-full resize-none bg-transparent text-[13px] leading-relaxed text-white/90 placeholder:text-white/30 border-0 outline-none ring-0 focus:border-0 focus:outline-none focus:ring-0 selection:bg-[#22d3ee]/40 selection:text-white"
                    />
                  </div>
                )}
                {kieActive && kieModel && !kieTextField && (
                  <div className="px-3 pt-3 lg:px-4">
                    <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] px-3 py-2.5 text-[12px] leading-relaxed text-white/60">
                      {kieModel.hint || kieModel.desc}
                    </div>
                  </div>
                )}

                <div className="flex flex-wrap items-end gap-2 border-t border-white/[0.08] px-3 py-2.5 lg:px-4">
                  <div className="relative" ref={modelRef}>
                    <ChipButton
                      active={modelOpen}
                      onClick={() => {
                        setModelOpen((v) => !v);
                        setOpenChip(null);
                      }}
                    >
                      {currentIcon ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={currentIcon}
                          alt=""
                          width={18}
                          height={18}
                          className="h-[18px] w-[18px] shrink-0 rounded-md object-cover ring-1 ring-white/10"
                        />
                      ) : (
                        <span className="inline-flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-md bg-[#38bdf8]/20 font-mono text-[10px] font-bold text-[#38bdf8] ring-1 ring-white/10">
                          K
                        </span>
                      )}
                      <span className="font-medium">
                        {currentWired ? (
                          <span className="mr-1 font-mono text-[#22d3ee]">+</span>
                        ) : null}
                        {currentName}
                      </span>
                      <ChevronDown className="h-3 w-3 opacity-60" />
                    </ChipButton>
                    {modelOpen && (
                      <ModelPickerPopover
                        mediaType={mediaType}
                        selectedSlug={activeSlug}
                        kieModels={kieModels}
                        creditUsd={kieCatalogQ.data?.credit_usd ?? 0.005}
                        onSelect={(slug) => {
                          if (mediaType === "image") setImageSlug(slug);
                          else if (mediaType === "video") setVideoSlug(slug);
                          else setAudioSlug(slug);
                          if (!slug.startsWith("kie:")) applyModelDefaults(slug, mediaType);
                          setModelOpen(false);
                        }}
                      />
                    )}
                  </div>

                  {!kieActive && mediaType === "video" && videoModel.chips.includes("orientation") && (
                    <div className="inline-flex gap-0.5 rounded-full border border-white/10 bg-[#16161b] p-0.5">
                      {(["video", "image"] as const).map((o) => (
                        <button
                          key={o}
                          type="button"
                          onClick={() => setOrientation(o)}
                          className={cn(
                            "rounded-full px-2.5 py-1 text-[11px] font-medium transition",
                            orientation === o
                              ? "bg-[#22d3ee]/20 text-[#22d3ee] font-semibold"
                              : "text-white/45 hover:text-white",
                          )}
                        >
                          {o === "video" ? "По видео" : "По картинке"}
                        </button>
                      ))}
                    </div>
                  )}

                  {!kieActive && mediaType === "video" && videoModel.chips.includes("quality") && (
                    <div className="inline-flex gap-0.5 rounded-full border border-white/10 bg-[#16161b] p-0.5">
                      {chipOptions(videoSlug, "quality").map((q) => (
                        <button
                          key={q}
                          type="button"
                          onClick={() => setMotionQuality(q)}
                          className={cn(
                            "rounded-full px-2.5 py-1 font-mono text-[11px] uppercase transition",
                            motionQuality === q
                              ? "bg-[#22d3ee]/20 text-[#22d3ee] font-semibold"
                              : "text-white/45 hover:text-white",
                          )}
                        >
                          {q}
                        </button>
                      ))}
                    </div>
                  )}

                  {!kieActive && dockChips.map((chip) => {
                    if (chip === "audio") {
                      return (
                        <button
                          key="audio"
                          type="button"
                          onClick={() => setGenerateAudio((v) => !v)}
                          className={cn(
                            "inline-flex h-9 items-center gap-1.5 rounded-xl border px-3 text-[12px] font-medium transition",
                            generateAudio
                              ? "border-[#22d3ee]/40 bg-[#22d3ee]/15 text-[#22d3ee]"
                              : "border-white/10 bg-[#16161b] text-white/70 hover:border-white/20 hover:text-white",
                          )}
                          title={generateAudio ? "Со звуком" : "Без звука"}
                        >
                          {OUTSEE_CHIP_LABELS.audio}
                          <span className="font-mono text-[10px] text-white/45">
                            {generateAudio ? "on" : "off"}
                          </span>
                        </button>
                      );
                    }
                    if (chip === "image-input") {
                      return null;
                    }
                    if (chip === "instrumental") {
                      return (
                        <button
                          key="instrumental"
                          type="button"
                          onClick={() => setInstrumental((v) => !v)}
                          className={cn(
                            "inline-flex h-9 items-center gap-1.5 rounded-xl border px-3 text-[12px] font-medium transition",
                            !instrumental
                              ? "border-[#22d3ee]/40 bg-[#22d3ee]/15 text-[#22d3ee]"
                              : "border-white/10 bg-[#16161b] text-white/70 hover:border-white/20 hover:text-white",
                          )}
                          title="Вокал on = не instrumental"
                        >
                          {OUTSEE_CHIP_LABELS.instrumental}
                          <span className="font-mono text-[10px] text-white/40">
                            {instrumental ? "off" : "on"}
                          </span>
                        </button>
                      );
                    }

                    const opts = chipOptions(activeSlug, chip);
                    if (!opts.length) return null;

                    let display = aspect;
                    let onSelect = setAspect;
                    let selectedVal = aspect;
                    if (chip === "resolution") {
                      display = mediaType === "image" ? resolution : videoResolution;
                      onSelect = mediaType === "image" ? setResolution : setVideoResolution;
                      selectedVal = display;
                    } else if (chip === "detail") {
                      display = `Детализация: ${detailLabel(detail)}`;
                      onSelect = setDetail;
                      selectedVal = detail;
                    } else if (chip === "duration") {
                      display = `${duration}с`;
                      onSelect = setDuration;
                      selectedVal = duration;
                    }

                    const options =
                      chip === "detail"
                        ? OUTSEE_DETAIL_LEVELS.map((d) => ({
                            id: d.id,
                            label: d.label,
                            hint: d.hint,
                          }))
                        : chip === "duration"
                          ? opts.map((d) => ({ id: d, label: `${d}с` }))
                          : opts.map((o) => ({ id: o, label: o }));

                    return (
                      <OptionDropdown
                        key={chip}
                        label={OUTSEE_CHIP_LABELS[chip] || chip}
                        value={display}
                        selectedValue={selectedVal}
                        open={openChip === chip}
                        onOpenChange={(v) => {
                          setOpenChip(v ? chip : null);
                          if (v) setModelOpen(false);
                        }}
                        options={options}
                        onSelect={onSelect}
                        mono={chip !== "detail"}
                      />
                    );
                  })}

                  {/* KIE: динамические настройки модели из каталога */}
                  {kieActive &&
                    kieModel &&
                    kieChipFields(kieModel)
                      .filter((f) => kieFieldVisible(f, kieValues))
                      .map((f) => (
                        <KieFieldChip
                          key={f.name}
                          field={f}
                          values={kieValues}
                          openChip={openChip}
                          setOpenChip={setOpenChip}
                          setModelOpen={setModelOpen}
                          onOpenVoiceModal={() => setVoiceLibraryOpen(true)}
                          onChange={(name, v) =>
                            setKieValues((prev) => ({ ...prev, [name]: v }))
                          }
                        />
                      ))}

                  <div className="ml-auto flex flex-wrap items-center gap-2">
                    {!kieActive && mediaType === "video" &&
                      (videoSlug === "sora-2" ||
                        videoSlug === "sora2-portrait" ||
                        videoSlug === "sora2-landscape") && (
                        <div className="inline-flex gap-0.5 rounded-xl border border-white/10 bg-[#16161b] p-0.5">
                          {(["small", "large"] as const).map((sz) => (
                            <button
                              key={sz}
                              type="button"
                              onClick={() => setSoraSize(sz)}
                              className={cn(
                                "rounded-lg px-2.5 py-1.5 font-mono text-[10px] uppercase transition",
                                soraSize === sz
                                  ? "bg-[#22d3ee]/20 text-[#22d3ee] font-semibold"
                                  : "text-white/40 hover:text-white",
                              )}
                            >
                              {sz}
                            </button>
                          ))}
                        </div>
                      )}
                    <button
                      type="button"
                      disabled={saveGlobal.isPending}
                      onClick={() => saveGlobal.mutate()}
                      className="rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-[11px] font-medium text-white/70 transition hover:border-white/25 hover:bg-white/[0.08] hover:text-white disabled:opacity-40"
                    >
                      {saveGlobal.isPending ? "…" : "Сохранить"}
                    </button>
                    {!kieActive && (
                      <button
                        type="button"
                        disabled={applyToProject.isPending || projectId == null}
                        onClick={() => applyToProject.mutate()}
                        className="rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-[11px] font-medium text-white/70 transition hover:border-white/25 hover:bg-white/[0.08] hover:text-white disabled:opacity-40"
                        title="Скопировать глобальные настройки в выбранный проект"
                      >
                        В проект
                      </button>
                    )}
                    {kieActive && kieCreditsQ.data?.credits != null && (
                      <div
                        className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-white/10 bg-[#16161b] px-2.5 font-mono text-[11px] text-white/60"
                        title="Баланс kie.ai"
                      >
                        {kieCreditsQ.data.credits.toFixed(0)} кр
                      </div>
                    )}
                    <div
                      className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-white/10 bg-[#16161b] px-2.5 font-mono text-[11px] text-white/80"
                      title={
                        kieActive
                          ? "kie.ai: 1 кр = $0.005. Цена за выбранные параметры."
                          : "1 токен = $0.10 (10¢). Цена за выбранные параметры."
                      }
                    >
                      <Coins className="h-3 w-3 text-[#22d3ee]" strokeWidth={2.5} />
                      <span>{priceLabel}</span>
                    </div>
                    {(mediaType === "image" || mediaType === "video") && (
                      <div
                        className="inline-flex h-9 items-center gap-0.5 rounded-xl border border-white/10 bg-[#16161b] p-0.5"
                        title={`Пакетная генерация: ${mediaType === "image" ? "1, 2 или 4 фото" : "1 или 2 видео"}`}
                      >
                        {(mediaType === "image" ? ([1, 2, 4] as const) : ([1, 2] as const)).map(
                          (cnt) => (
                            <button
                              key={cnt}
                              type="button"
                              onClick={() => setBatchCount(cnt)}
                              className={cn(
                                "rounded-lg px-2 py-1 font-mono text-[11px] font-bold transition",
                                batchCount === cnt
                                  ? "bg-[#22d3ee]/20 text-[#22d3ee] ring-1 ring-[#22d3ee]/40"
                                  : "text-white/45 hover:text-white",
                              )}
                            >
                              {cnt}x
                            </button>
                          ),
                        )}
                      </div>
                    )}
                    <button
                      type="button"
                      disabled={
                        createGenerate.isPending ||
                        (kieActive
                          ? (kieTextField && !prompt.trim()) || !kieConfigured
                          : !prompt.trim() || !canApiDirect)
                      }
                      onClick={() => {
                        if (createGenerate.isPending) return;
                        createGenerate.mutate(undefined);
                      }}
                      className={cn(
                        "inline-flex min-w-[145px] items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-[#22d3ee] to-[#0ea5e9] px-4 py-2 text-[12px] font-extrabold uppercase tracking-wider text-black shadow-[0_0_20px_rgba(34,211,238,0.3)] transition-all duration-200 hover:brightness-110 hover:shadow-[0_0_25px_rgba(34,211,238,0.45)] disabled:opacity-40 disabled:pointer-events-none",
                      )}
                      title={
                        createGenerate.isPending
                          ? "Уже ставится в очередь…"
                          : !canApiDirect
                            ? "Нужен OUTSEE_API_KEY или KIE_API_KEY в .env"
                            : `Сгенерировать (${batchCount > 1 ? `${batchCount} шт` : "1 шт"}, лимит ${maxParallel}) · ${priceLabel}`
                      }
                    >
                      {createGenerate.isPending ? (
                        <>
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          <span>Запуск…</span>
                        </>
                      ) : (
                        <>
                          <Play className="h-3.5 w-3.5 fill-current" />
                          <span>
                            Генерировать
                            {batchCount > 1 ? ` (${batchCount}x)` : ""}
                          </span>
                        </>
                      )}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>

      {/* Lightbox full screen modal & Prompt Inspector */}
      {lightboxOpen && selected?.preview_url && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/95 p-3 md:p-6 backdrop-blur-2xl animate-in fade-in duration-200"
          onClick={() => setLightboxOpen(false)}
        >
          <div
            className="flex flex-col max-h-[96vh] max-w-[98vw] w-full gap-3 md:gap-3.5"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Top Bar: Media Info & Action buttons (Download, Delete, Close) */}
            <div className="flex items-center justify-between gap-3 px-1 sm:px-2 shrink-0">
              <div className="flex items-center gap-2.5 min-w-0">
                <span className="truncate text-sm sm:text-base font-semibold text-white/90">
                  {selected.label || (selected.kind === "audio" ? "Аудиотрек" : selected.kind === "video" ? "Видео" : "Изображение")}
                </span>
                {selected.model && (
                  <span className="shrink-0 rounded-md border border-[#22d3ee]/30 bg-[#22d3ee]/10 px-2 py-0.5 font-mono text-[10px] font-semibold text-[#22d3ee]">
                    {selected.model}
                  </span>
                )}
              </div>

              <div className="flex items-center gap-2 shrink-0">
                <div className="inline-flex items-center rounded-xl border border-white/20 bg-black/80 p-0.5 backdrop-blur-md shadow-2xl">
                  <button
                    type="button"
                    onClick={() =>
                      void downloadMediaFile(
                        selected.preview_url || selected.raw_url || "",
                        selected.label || "generation",
                        selected.kind === "video"
                          ? "mp4"
                          : selected.kind === "audio"
                            ? "mp3"
                            : downloadFormat,
                        selected.path,
                      )
                    }
                    className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12px] font-semibold text-white/90 transition hover:bg-white/[0.12] hover:text-white"
                  >
                    <Download className="h-4 w-4 text-[#22d3ee]" />
                    <span>Скачать</span>
                  </button>
                  {selected.kind === "image" && (
                    <div className="flex items-center border-l border-white/20 pl-1 pr-1 font-mono text-[11px]">
                      {(["png", "jpg", "webp"] as const).map((fmt) => (
                        <button
                          key={fmt}
                          type="button"
                          onClick={() => setDownloadFormat(fmt)}
                          className={cn(
                            "rounded px-2 py-0.5 uppercase transition",
                            downloadFormat === fmt
                              ? "bg-[#22d3ee]/25 font-bold text-[#22d3ee]"
                              : "text-white/50 hover:text-white",
                          )}
                        >
                          {fmt}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                <button
                  type="button"
                  onClick={() => {
                    setLightboxOpen(false);
                    deleteItem.mutate(selected);
                  }}
                  className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-red-500/30 bg-black/80 px-3 text-[12px] font-medium text-red-400 backdrop-blur transition hover:border-red-500/50 hover:bg-red-500/20 hover:text-red-300 shadow-2xl"
                  title="Удалить из истории"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  <span className="hidden sm:inline">Удалить</span>
                </button>

                <button
                  type="button"
                  onClick={() => setLightboxOpen(false)}
                  className="flex h-9 w-9 items-center justify-center rounded-xl border border-white/20 bg-black/80 text-white/80 backdrop-blur transition hover:bg-white/20 hover:text-white shadow-2xl"
                  title="Закрыть (Esc)"
                >
                  <X className="h-5 w-5" />
                </button>
              </div>
            </div>

            {/* Content Row: Media Area + Prompt Inspector Panel */}
            <div className="flex flex-1 flex-col md:flex-row items-center md:items-start justify-center gap-4 min-h-0 overflow-hidden">
              {/* Media Area */}
              <div className="flex flex-1 items-center justify-center max-h-[84vh] max-w-full min-w-0">
                {selected.kind === "video" ? (
                  <video
                    src={selected.preview_url}
                    controls
                    autoPlay
                    className="max-h-[84vh] max-w-full rounded-2xl border border-white/15 bg-black object-contain shadow-[0_0_80px_rgba(0,0,0,0.9)]"
                  />
                ) : selected.kind === "audio" ? (
                  <div className="w-full max-w-3xl lg:max-w-4xl py-4">
                    <AudioStudioPlayer item={selected} />
                  </div>
                ) : (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={selected.preview_url}
                    alt=""
                    className="max-h-[84vh] max-w-full rounded-2xl border border-white/15 bg-black object-contain shadow-[0_0_80px_rgba(0,0,0,0.9)]"
                  />
                )}
              </div>

              {/* Prompt Inspector Panel */}
              <div className="flex w-full md:w-84 shrink-0 flex-col gap-3 rounded-2xl border border-white/15 bg-[#121216]/95 p-4 backdrop-blur-2xl shadow-[0_20px_60px_rgba(0,0,0,0.9)] max-h-[84vh] overflow-y-auto ring-1 ring-white/10">
                <div className="flex items-center justify-between border-b border-white/10 pb-2.5">
                  <div className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-[#22d3ee]">
                    <FileText className="h-4 w-4" />
                    <span>Инспектор</span>
                  </div>
                  {selected.model && (
                    <span className="rounded-md border border-white/10 bg-white/[0.04] px-2 py-0.5 font-mono text-[10px] text-white/70">
                      {selected.model}
                    </span>
                  )}
                </div>

                {/* Prompt Text Box */}
                <div>
                  <div className="mb-1 text-[11px] font-semibold text-white/50">Промпт:</div>
                  <div className="max-h-52 overflow-y-auto rounded-xl border border-white/10 bg-black/40 p-3 text-[12px] leading-relaxed text-white/90 select-text">
                    {selected.prompt || "Без текстового описания"}
                  </div>
                </div>

                {/* Quick Actions: Copy & Insert into Prompt Dock */}
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      if (selected.prompt) {
                        void navigator.clipboard.writeText(selected.prompt);
                        toast.success("Промпт скопирован в буфер 📋");
                      }
                    }}
                    disabled={!selected.prompt}
                    className="inline-flex h-9 items-center justify-center gap-1.5 rounded-xl border border-white/15 bg-white/[0.05] px-3 text-[11px] font-semibold text-white/85 transition hover:border-white/30 hover:bg-white/[0.1] hover:text-white disabled:opacity-40"
                  >
                    <Copy className="h-3.5 w-3.5" />
                    <span>Скопировать</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (selected.prompt) {
                        setPrompt(selected.prompt);
                        setLightboxOpen(false);
                        toast.success("Промпт подставлен в поле ввода ✍️");
                      }
                    }}
                    disabled={!selected.prompt}
                    className="inline-flex h-9 items-center justify-center gap-1.5 rounded-xl border border-[#22d3ee]/40 bg-[#22d3ee]/15 px-3 text-[11px] font-bold text-[#22d3ee] transition hover:bg-[#22d3ee]/25 disabled:opacity-40"
                  >
                    <CornerDownLeft className="h-3.5 w-3.5" />
                    <span>Вставить в чат</span>
                  </button>
                </div>

                {selected.prompt && (
                  <button
                    type="button"
                    onClick={() => {
                      setLightboxOpen(false);
                      handleRetry(selected);
                    }}
                    disabled={createGenerate.isPending}
                    className="inline-flex h-9 items-center justify-center gap-1.5 rounded-xl bg-gradient-to-r from-[#22d3ee] to-[#06b6d4] px-3 text-[11px] font-bold text-black shadow-[0_0_15px_rgba(34,211,238,0.3)] transition hover:brightness-110 active:scale-95 disabled:opacity-50"
                    title="Повторить генерацию с теми же параметрами"
                  >
                    <RotateCw className={cn("h-3.5 w-3.5", createGenerate.isPending && "animate-spin")} />
                    <span>{createGenerate.isPending ? "Запуск…" : "Сгенерировать заново"}</span>
                  </button>
                )}

                {/* References Strip (if any) */}
                {((selected.reference_images && selected.reference_images.length > 0) || selected.first_frame_url) && (
                  <div className="border-t border-white/10 pt-2.5">
                    <div className="mb-1.5 text-[11px] font-semibold text-white/50">
                      Использованные референсы ({selected.reference_images?.length || 1}):
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {(selected.reference_images && selected.reference_images.length > 0
                        ? selected.reference_images
                        : [selected.first_frame_url!]
                      ).map((u, i) => (
                        <a
                          key={i}
                          href={u}
                          target="_blank"
                          rel="noreferrer"
                          className="group/ref relative block h-12 w-12 overflow-hidden rounded-lg border border-white/20 transition hover:scale-105 hover:border-[#22d3ee]"
                          title="Открыть референс в новой вкладке"
                        >
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={u} alt="" className="h-full w-full object-cover" />
                        </a>
                      ))}
                    </div>
                  </div>
                )}

                {/* Meta details */}
                <div className="space-y-1 border-t border-white/10 pt-2.5 font-mono text-[10px] text-white/45">
                  {selected.elapsed_label || selected.elapsed_sec != null ? (
                    <div>
                      Время генерации:{" "}
                      <span className="font-semibold text-white/70">
                        {selected.elapsed_label || formatElapsedMinSec(selected.elapsed_sec)}
                      </span>
                    </div>
                  ) : null}
                  <div>
                    ID: <span className="text-white/60">{selected.id}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Voice Library Modal */}
      <VoiceLibraryModal
        open={voiceLibraryOpen}
        onOpenChange={setVoiceLibraryOpen}
        selectedVoiceId={String(kieValues["voice_id"] || "ymDCYd8puC7gYjxIamPt")}
        onSelectVoice={(voiceId, voiceName) => {
          setKieValues((prev) => ({ ...prev, voice_id: voiceId }));
          toast.success(`Выбран голос: ${voiceName}`);
        }}
      />
    </div>
  );
}

function kieFieldVisible(f: KieField, values: Record<string, unknown>): boolean {
  if (!f.show_if) return true;
  return Object.entries(f.show_if).every(([k, want]) => {
    const got = values[k];
    if (typeof want === "boolean") {
      const truthy = got === true || String(got).toLowerCase() === "true";
      return truthy === want;
    }
    return String(got ?? "") === String(want);
  });
}

/** KIE: одна настройка модели в виде чипа (select/toggle/number/text). */
function KieFieldChip({
  field,
  values,
  openChip,
  setOpenChip,
  setModelOpen,
  onOpenVoiceModal,
  onChange,
}: {
  field: KieField;
  values: Record<string, unknown>;
  openChip?: string | null;
  setOpenChip?: (v: string | null) => void;
  setModelOpen?: (v: boolean) => void;
  onOpenVoiceModal?: () => void;
  onChange: (name: string, v: unknown) => void;
}) {
  const v = values[field.name] ?? field.default;

  if (field.name === "voice_id" || field.name === "voice") {
    const voice = getVoiceById(String(v || ""));
    const displayName = voice
      ? `${voice.name} (${voice.gender === "female" ? "Ж" : "М"})`
      : String(v || "Выбрать голос");
    return (
      <button
        type="button"
        onClick={onOpenVoiceModal}
        className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-[#22d3ee]/40 bg-[#22d3ee]/10 px-3 text-[12px] font-medium text-white transition hover:bg-[#22d3ee]/20 hover:border-[#22d3ee]/60"
        title="Открыть библиотеку русских голосов (200)"
      >
        <Mic className="h-3.5 w-3.5 text-[#22d3ee]" />
        <span className="text-white/60">Голос:</span>
        <span className="font-semibold text-[#22d3ee]">{displayName}</span>
      </button>
    );
  }

  if (field.name === "stability" || field.name === "similarity") {
    const numVal =
      typeof v === "number"
        ? v
        : Number(v ?? field.default ?? (field.name === "stability" ? 0.5 : 0.75));
    return (
      <div
        className="inline-flex h-9 items-center gap-2 rounded-xl border border-white/10 bg-[#16161b] px-3"
        title={field.desc || field.label}
      >
        <span className="text-[11px] text-white/55">{field.label}:</span>
        <span className="font-mono text-[11px] font-semibold text-[#22d3ee]">{numVal.toFixed(2)}</span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={numVal}
          onChange={(e) => onChange(field.name, parseFloat(e.target.value))}
          className="h-1.5 w-16 cursor-pointer appearance-none rounded-lg bg-white/20 accent-[#22d3ee]"
        />
      </div>
    );
  }

  if (field.kind === "toggle") {
    const on = v === true || String(v).toLowerCase() === "true";
    return (
      <button
        type="button"
        onClick={() => onChange(field.name, !on)}
        title={field.desc || field.label}
        className={cn(
          "inline-flex h-9 items-center gap-1.5 rounded-xl border px-3 text-[12px] font-medium transition",
          on
            ? "border-[#22d3ee]/40 bg-[#22d3ee]/15 text-[#22d3ee]"
            : "border-white/10 bg-[#16161b] text-white/70 hover:border-white/20 hover:text-white",
        )}
      >
        {field.label}
        <span className="font-mono text-[10px] text-white/45">{on ? "on" : "off"}</span>
      </button>
    );
  }

  if (field.kind === "select") {
    const formatOption = (opt: string) => {
      const fn = field.name.toLowerCase();
      const o = opt.toLowerCase();
      if (fn === "quality") {
        if (o === "basic") return { label: "1K", hint: "Базовое" };
        if (o === "high") return { label: "2K", hint: "Высокое" };
        if (o === "std" || o === "standard") return { label: "Standard", hint: "720p" };
        if (o === "pro") return { label: "Pro", hint: "1080p" };
      }
      if (fn === "vocalgender") {
        if (o === "m") return { label: "Мужской вокал", hint: "Мужской тембр" };
        if (o === "f") return { label: "Женский вокал", hint: "Женский тембр" };
        return { label: "Вокал: Любой", hint: "Без предпочтений" };
      }
      if (fn === "output_format" || fn === "format") {
        return { label: opt.toUpperCase() };
      }
      return { label: opt || "—" };
    };

    const options = (field.options || []).map((o) => {
      const meta = formatOption(o);
      return { id: o, label: meta.label, hint: meta.hint };
    });
    const currentVal = String(v ?? field.options?.[0] ?? "—");
    const selectedOpt = options.find((o) => o.id === currentVal);
    const displayVal = selectedOpt ? selectedOpt.label : currentVal;

    return (
      <OptionDropdown
        label={field.label}
        value={displayVal}
        selectedValue={currentVal}
        open={openChip === field.name}
        onOpenChange={(isOpen) => {
          if (setOpenChip) setOpenChip(isOpen ? field.name : null);
          if (isOpen && setModelOpen) setModelOpen(false);
        }}
        options={options}
        onSelect={(optId) => onChange(field.name, optId)}
        mono
      />
    );
  }

  if (field.kind === "number") {
    return (
      <div
        className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-white/10 bg-[#16161b] px-2.5"
        title={field.desc || field.label}
      >
        <span className="text-[11px] text-white/55">{field.label}</span>
        <input
          type="number"
          value={v === undefined || v === null ? "" : String(v)}
          min={field.min}
          max={field.max}
          step={field.step ?? 1}
          onChange={(e) =>
            onChange(field.name, e.target.value === "" ? undefined : Number(e.target.value))
          }
          className="w-16 bg-transparent font-mono text-[12px] text-white/90 outline-none"
        />
      </div>
    );
  }

  // text
  return (
    <div
      className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-white/10 bg-[#16161b] px-2.5"
      title={field.desc || field.label}
    >
      <span className="text-[11px] text-white/55">{field.label}</span>
      <input
        value={String(v ?? "")}
        onChange={(e) => onChange(field.name, e.target.value)}
        placeholder="—"
        className="w-28 bg-transparent text-[12px] text-white/90 outline-none placeholder:text-white/25"
      />
    </div>
  );
}

const KIE_FILE_ACCEPT: Record<string, string> = {
  images: "image/png,image/jpeg,image/webp,image/bmp,image/gif",
  videos: "video/mp4,video/quicktime,video/webm",
  audios: "audio/mpeg,audio/wav,audio/mp4,audio/x-m4a",
};

/** KIE: кнопка вложения в стиле «Стартовый кадр» — загрузка в kie или URL. */
function KieAttachButton({
  field,
  values,
  onChange,
}: {
  field: KieField;
  values: Record<string, unknown>;
  onChange: (name: string, items: string[]) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [urlMode, setUrlMode] = useState(false);
  const [urlDraft, setUrlDraft] = useState("");
  const items: string[] = Array.isArray(values[field.name])
    ? (values[field.name] as string[])
    : [];
  const max = field.max_items ?? 99;
  const canAdd = items.length < max;

  const addUrl = () => {
    const u = urlDraft.trim();
    if (!u) return;
    if (!/^https?:\/\//.test(u)) {
      toast.error("Нужен http(s) URL");
      return;
    }
    onChange(field.name, [...items, u]);
    setUrlDraft("");
    setUrlMode(false);
  };

  return (
    <>
      {items.map((u, i) => (
        <span
          key={`${u}-${i}`}
          className="inline-flex h-10 items-center gap-2 rounded-xl border border-[#22d3ee]/40 bg-[#22d3ee]/10 px-3 text-[12px] font-medium text-[#22d3ee]"
        >
          {field.kind === "images" ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={u} alt="" className="h-7 w-7 rounded-md object-cover ring-1 ring-white/15" />
          ) : (
            <Paperclip className="h-4 w-4" />
          )}
          <span className="max-w-[120px] truncate font-mono text-[10px] text-white/70">
            {u.split("/").pop()}
          </span>
          <span
            className="cursor-pointer text-white/45 hover:text-white"
            onClick={() => onChange(field.name, items.filter((_, j) => j !== i))}
          >
            <X className="h-3.5 w-3.5" />
          </span>
        </span>
      ))}
      {canAdd && (
        <span className="inline-flex items-center gap-1">
          <input
            ref={inputRef}
            type="file"
            accept={KIE_FILE_ACCEPT[field.kind] || "*/*"}
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (!f) return;
              if (f.size === 0) {
                toast.error("Выбран пустой файл (0 байт)");
                e.target.value = "";
                return;
              }
              if (field.kind === "audios" && f.size < 1000) {
                toast.error("Файл слишком мал или не содержит аудиоданных (минимум 1 КБ)");
                e.target.value = "";
                return;
              }
              if (field.kind === "videos" && f.size < 2000) {
                toast.error("Файл слишком мал или не содержит видеоданных");
                e.target.value = "";
                return;
              }
              setBusy(true);
              api
                .kieUpload(f)
                .then((r) => {
                  onChange(field.name, [...items, r.url]);
                  toast.success(`${field.label}: файл загружен`);
                })
                .catch((err) => toast.error(errorMessageFromUnknown(err)))
                .finally(() => setBusy(false));
              e.target.value = "";
            }}
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => inputRef.current?.click()}
            title={field.desc || field.label}
            className="inline-flex h-10 items-center gap-2 rounded-xl border border-dashed border-white/25 bg-white/[0.03] px-3 text-[12px] font-medium text-white/70 transition hover:border-white/40 hover:text-white disabled:opacity-50"
          >
            {busy ? (
              <Loader2 className="h-4 w-4 animate-spin text-[#22d3ee]" />
            ) : (
              <Paperclip className="h-4 w-4" />
            )}
            {field.label}
            {field.required ? <span className="text-red-400">*</span> : null}
          </button>
          <button
            type="button"
            onClick={() => setUrlMode((v) => !v)}
            title="Вставить URL вместо загрузки"
            className="inline-flex h-10 w-8 items-center justify-center rounded-xl border border-white/10 bg-white/[0.03] text-white/50 transition hover:border-white/25 hover:text-white"
          >
            <Link2 className="h-3.5 w-3.5" />
          </button>
          {urlMode && (
            <span className="inline-flex items-center gap-1">
              <input
                value={urlDraft}
                onChange={(e) => setUrlDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addUrl();
                  }
                }}
                placeholder="https://…"
                className="h-10 w-48 rounded-xl border border-white/10 bg-black/40 px-2.5 text-[11px] text-white/80 outline-none placeholder:text-white/25 focus:border-[#22d3ee]/50"
              />
              <button
                type="button"
                onClick={addUrl}
                className="h-10 rounded-xl border border-white/10 px-2.5 text-[11px] text-white/60 transition hover:border-white/25 hover:text-white"
              >
                +
              </button>
            </span>
          )}
        </span>
      )}
    </>
  );
}

function ChipButton({
  children,
  onClick,
  active,
  title,
}: {
  children: React.ReactNode;
  onClick: () => void;
  active?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={cn(
        "inline-flex h-9 items-center gap-1.5 rounded-xl border px-3 text-[12px] font-medium transition-all duration-200",
        active
          ? "border-[#22d3ee] bg-[#22d3ee]/15 text-[#22d3ee] shadow-[0_0_15px_rgba(34,211,238,0.2)]"
          : "border-white/10 bg-[#16161b] text-white/80 hover:border-white/20 hover:bg-[#1f1f26] hover:text-white",
      )}
    >
      {children}
    </button>
  );
}

function OptionDropdown({
  label,
  value,
  selectedValue,
  open,
  onOpenChange,
  options,
  onSelect,
  mono,
}: {
  label: string;
  value: string;
  selectedValue?: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  options: { id: string; label: string; hint?: string }[];
  onSelect: (id: string) => void;
  mono?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onOpenChange(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open, onOpenChange]);

  const currentVal = selectedValue ?? value;

  return (
    <div className="relative" ref={ref}>
      <ChipButton active={open} onClick={() => onOpenChange(!open)} title={label}>
        <span className={cn(mono && "font-mono tabular-nums")}>{value}</span>
        <ChevronDown className="h-3 w-3 opacity-60" />
      </ChipButton>
      {open && (
        <div
          className="absolute bottom-full left-0 z-[1000] mb-2 max-h-60 overflow-y-auto rounded-xl border border-white/15 bg-[#121216]/95 backdrop-blur-2xl p-1.5 shadow-[0_15px_40px_rgba(0,0,0,0.85)] ring-1 ring-white/10"
          style={{ minWidth: 140 }}
        >
          {options.map((opt) => {
            const active = opt.id === currentVal || opt.label === currentVal || opt.id === value || opt.label === value;
            return (
              <button
                key={opt.id}
                type="button"
                onClick={() => {
                  onSelect(opt.id);
                  onOpenChange(false);
                }}
                className={cn(
                  "flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-[12px] transition",
                  active
                    ? "bg-[#22d3ee]/15 text-[#22d3ee] font-semibold"
                    : "text-white/80 hover:bg-white/[0.08] hover:text-white",
                )}
              >
                <span className={cn(mono && "font-mono")}>{opt.label}</span>
                {opt.hint && <span className="text-[10px] text-white/40">{opt.hint}</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
function ModelCardIcon({
  icon,
  label,
  letter,
  isTopCard,
}: {
  icon?: string | null;
  label: string;
  letter?: string | null;
  isTopCard?: boolean;
}) {
  const [error, setError] = useState(false);
  if (!icon || error) {
    return (
      <span
        className={cn(
          "inline-flex h-10 w-10 items-center justify-center rounded-lg font-mono text-[14px] font-bold ring-1 transition",
          isTopCard
            ? "bg-[#22d3ee]/15 text-[#22d3ee] ring-[#22d3ee]/30 group-hover:ring-[#22d3ee]/60"
            : "bg-[#38bdf8]/15 text-[#38bdf8] ring-white/10 group-hover:ring-[#38bdf8]/40",
        )}
      >
        {letter || label.slice(0, 1)}
      </span>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={icon}
      alt={label}
      onError={() => setError(true)}
      className="h-10 w-10 rounded-lg object-cover ring-1 ring-white/10 transition group-hover:ring-[#22d3ee]/40"
    />
  );
}

interface AudioActionSpec {
  id: string;
  slug: string;
  name: string;
  desc: string;
  badge?: string;
  iconType: "music" | "cover" | "vocal" | "split" | "sfx" | "sounds" | "extend" | "lyrics" | "speech" | "turbo" | "dialogue" | "isolate";
}

const SUNO_AUDIO_ACTIONS: AudioActionSpec[] = [
  {
    id: "suno-music",
    slug: "kie:suno-music",
    name: "Создание трека",
    desc: "Полная песня или инструментал по стилю / тексту (V5.5 / V5)",
    badge: "ТОП",
    iconType: "music",
  },
  {
    id: "suno-upload-cover",
    slug: "kie:suno-upload-cover",
    name: "Кавер на своё аудио",
    desc: "Загрузи аудио → Suno создаст трек в новом стиле",
    iconType: "cover",
  },
  {
    id: "suno-add-vocals",
    slug: "kie:suno-add-vocals",
    name: "Добавить вокал",
    desc: "Инструментал + текст песни → готовый трек с вокалом",
    iconType: "vocal",
  },
  {
    id: "suno-separate-vocals",
    slug: "kie:suno-separate-vocals",
    name: "Разделить вокал / минус",
    desc: "Стем-сплиттер: разделение трека на вокал и музыку",
    iconType: "split",
  },
  {
    id: "elevenlabs-sfx",
    slug: "kie:elevenlabs-sfx",
    name: "Звуковые эффекты (SFX)",
    desc: "Foley-эффекты: шаги, взрывы, удары, окружение (без музыки)",
    iconType: "sfx",
  },
  {
    id: "suno-sounds",
    slug: "kie:suno-sounds",
    name: "Саундскейп и петли (Loops)",
    desc: "Короткие атмосферные фоны, петли и гармонии",
    iconType: "sounds",
  },
  {
    id: "suno-extend",
    slug: "kie:suno-extend",
    name: "Продлить трек",
    desc: "Продление уже созданного трека Suno по audioId",
    iconType: "extend",
  },
  {
    id: "suno-lyrics",
    slug: "kie:suno-lyrics",
    name: "Текст песни",
    desc: "Генерация текста и структуры куплетов/припевов по теме",
    iconType: "lyrics",
  },
];

const ELEVENLABS_AUDIO_ACTIONS: AudioActionSpec[] = [
  {
    id: "elevenlabs-v4",
    slug: "kie:elevenlabs-v4",
    name: "Озвучка ElevenLabs v4 (WaveSpeed)",
    desc: "Новейшая модель v4, библиотека 200 русских голосов, аудио-теги эмоций",
    badge: "NEW",
    iconType: "speech",
  },
];

function AudioActionIcon({ type }: { type: AudioActionSpec["iconType"] }) {
  switch (type) {
    case "music":
      return <Music className="h-4 w-4 text-[#22d3ee]" />;
    case "cover":
      return <RotateCw className="h-4 w-4 text-amber-400" />;
    case "vocal":
      return <Mic className="h-4 w-4 text-rose-400" />;
    case "split":
      return <Scissors className="h-4 w-4 text-purple-400" />;
    case "sfx":
      return <Volume2 className="h-4 w-4 text-emerald-400" />;
    case "sounds":
      return <Radio className="h-4 w-4 text-teal-400" />;
    case "extend":
      return <Clock className="h-4 w-4 text-blue-400" />;
    case "lyrics":
      return <FileText className="h-4 w-4 text-pink-400" />;
    case "speech":
      return <Mic className="h-4 w-4 text-[#22d3ee]" />;
    case "turbo":
      return <Sparkles className="h-4 w-4 text-amber-400" />;
    case "dialogue":
      return <Layers className="h-4 w-4 text-violet-400" />;
    case "isolate":
      return <Volume2 className="h-4 w-4 text-cyan-400" />;
    default:
      return <Music className="h-4 w-4 text-white/60" />;
  }
}

function ModelPickerPopover({
  mediaType,
  selectedSlug,
  kieModels = [],
  creditUsd = 0.005,
  onSelect,
}: {
  mediaType: OutseeMediaType;
  selectedSlug: string;
  kieModels?: KieModelSpec[];
  creditUsd?: number;
  onSelect: (slug: string) => void;
}) {
  const [search, setSearch] = useState("");
  const isAudio = mediaType === "audio";
  const [sunoOpen, setSunoOpen] = useState(() => {
    const isEl = selectedSlug.toLowerCase().includes("elevenlabs") && !selectedSlug.toLowerCase().includes("elevenlabs-sfx");
    return !isEl;
  });
  const [elevenlabsOpen, setElevenlabsOpen] = useState(() => {
    const isEl = selectedSlug.toLowerCase().includes("elevenlabs") && !selectedSlug.toLowerCase().includes("elevenlabs-sfx");
    return isEl;
  });

  const title =
    mediaType === "image"
      ? "Модели изображений"
      : mediaType === "video"
        ? "Модели видео"
        : "Аудио движки и режимы";
  const models = pickerModelsForType(mediaType);
  const kieForType = kieModels.filter((m) => {
    const id = m.id.toLowerCase();
    // GPT Image 2, Nano Banana 2, Veo 3.1 Lite — строго через Outsee!
    if (id.includes("veo") || id.includes("gpt-image") || id.includes("banana")) {
      return false;
    }
    const media =
      m.media || (m.category === "video" ? "video" : m.category === "image" ? "image" : "audio");
    return media === mediaType;
  });

  const q = search.trim().toLowerCase();

  const filteredSunoActions = useMemo(() => {
    if (!q) return SUNO_AUDIO_ACTIONS;
    return SUNO_AUDIO_ACTIONS.filter(
      (a) =>
        a.name.toLowerCase().includes(q) ||
        a.desc.toLowerCase().includes(q) ||
        a.id.toLowerCase().includes(q) ||
        "suno".includes(q),
    );
  }, [q]);

  const filteredElevenLabsActions = useMemo(() => {
    if (!q) return ELEVENLABS_AUDIO_ACTIONS;
    return ELEVENLABS_AUDIO_ACTIONS.filter(
      (a) =>
        a.name.toLowerCase().includes(q) ||
        a.desc.toLowerCase().includes(q) ||
        a.id.toLowerCase().includes(q) ||
        "elevenlabs".includes(q),
    );
  }, [q]);

  const filteredModels = useMemo(() => {
    if (!q) return models;
    return models.filter(
      (m) =>
        m.displayName.toLowerCase().includes(q) ||
        m.description.toLowerCase().includes(q) ||
        m.slug.toLowerCase().includes(q),
    );
  }, [models, q]);

  const filteredKie = useMemo(() => {
    if (!q) return kieForType;
    return kieForType.filter(
      (m) =>
        m.label.toLowerCase().includes(q) ||
        (m.desc || "").toLowerCase().includes(q) ||
        (m.hint || "").toLowerCase().includes(q) ||
        m.id.toLowerCase().includes(q),
    );
  }, [kieForType, q]);

  const unifiedItems = useMemo(() => {
    // Collect icons from base models for quick lookup
    const iconBySlug = new Map<string, string>();
    for (const m of filteredModels) {
      if (m.icon) {
        iconBySlug.set(m.slug.toLowerCase(), m.icon);
        iconBySlug.set(m.slug.toLowerCase().replace(/[-_]/g, ""), m.icon);
      }
    }

    // Build authoritative KIE items first
    const kieModelIds = new Set<string>();
    const kieItems = filteredKie.map((m) => {
      const rawId = m.id.toLowerCase();
      const cleanId = rawId.replace(/[-_]/g, "");
      kieModelIds.add(rawId);
      kieModelIds.add(cleanId);
      const est = estimateKie(m, {}, creditUsd);

      const icon =
        (rawId.includes("seedream") ? "/icons/bytedance.svg" : null) ||
        (rawId.includes("qwen") ? "/icons/qwen.svg" : null) ||
        iconBySlug.get(rawId) ||
        iconBySlug.get(cleanId) ||
        (rawId.includes("nano-banana") ? iconBySlug.get("nano-banana-2") : null) ||
        (rawId.includes("gpt-image") ? iconBySlug.get("gpt-image-2") : null) ||
        (rawId.includes("veo") ? iconBySlug.get("veo-3-1-lite") || iconBySlug.get("veo-3-1") : null) ||
        (rawId.includes("kling") ? iconBySlug.get("kling-2-6") : null) ||
        (rawId.includes("hailuo") ? iconBySlug.get("hailuo-02") : null) ||
        (rawId.includes("topaz") ? iconBySlug.get("topaz-video-upscale") || iconBySlug.get("topaz-image-upscale") : null) ||
        (rawId.includes("suno") ? iconBySlug.get("suno-5-5") : null) ||
        null;

      return {
        slug: `kie:${m.id}`,
        label: m.label,
        desc: m.hint || m.desc,
        icon,
        letter: m.label.slice(0, 1),
        isKie: true,
        isTop: Boolean(m.is_top || m.isTop),
        badge: m.badge || (m.is_top || m.isTop ? "ТОП" : undefined),
        priceLabel: null as string | null,
        priceUsd: est.usd > 0 ? est.usd : null,
      };
    });

    // Only include remaining base models that are NOT present in KIE
    const remainingBaseItems = filteredModels
      .filter((m) => {
        const s = m.slug.toLowerCase();
        const c = s.replace(/[-_]/g, "");
        return !kieModelIds.has(s) && !kieModelIds.has(c);
      })
      .map((m) => ({
        slug: m.slug,
        label: m.displayName,
        desc: m.description,
        icon: m.icon,
        letter: null as string | null,
        isKie: false,
        isTop: Boolean(m.isTop || m.slug === "veo-3-1-lite"),
        badge: m.isTop ? "ТОП" : m.isNew ? "НОВОЕ" : undefined,
        priceLabel: m.price ? m.price : null,
        priceUsd: null as number | null,
      }));

    return [...kieItems, ...remainingBaseItems];
  }, [filteredModels, filteredKie, creditUsd]);

  const topItems = useMemo(() => unifiedItems.filter((i) => i.isTop), [unifiedItems]);
  const otherItems = useMemo(() => unifiedItems.filter((i) => !i.isTop), [unifiedItems]);

  const totalCount = unifiedItems.length;

  const renderCard = (item: (typeof unifiedItems)[0]) => {
    const active = item.slug === selectedSlug;
    const isTopCard = item.isTop;
    return (
      <button
        key={item.slug}
        type="button"
        onClick={() => onSelect(item.slug)}
        className={cn(
          "group relative flex items-start gap-2.5 rounded-xl border p-2.5 text-left transition-all duration-200",
          active
            ? isTopCard
              ? "border-[#22d3ee] bg-[#22d3ee]/15 text-white shadow-[0_0_22px_rgba(34,211,238,0.25)] ring-1 ring-[#22d3ee]/50"
              : "border-[#38bdf8] bg-[#38bdf8]/10 text-white shadow-[0_0_20px_rgba(56,189,248,0.2)]"
            : isTopCard
              ? "border-[#22d3ee]/25 bg-[#22d3ee]/[0.03] hover:border-[#22d3ee]/50 hover:bg-[#22d3ee]/[0.08]"
              : "border-white/[0.08] bg-white/[0.03] hover:border-white/20 hover:bg-white/[0.06]",
        )}
      >
        {item.badge && (
          <span
            className={cn(
              "absolute top-2 right-2 rounded-md px-1.5 py-0.5 font-mono text-[9px] font-bold tracking-wider shadow-sm",
              item.badge === "ТОП"
                ? "bg-[#22d3ee] text-black font-extrabold"
                : item.badge === "1080p" || item.badge === "2K/4K"
                  ? "bg-indigo-500 text-white"
                  : item.badge.includes("Звук")
                    ? "bg-purple-500 text-white"
                    : item.badge.includes("с")
                      ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                      : "bg-white/10 text-white/60",
            )}
          >
            {item.badge}
          </span>
        )}
        <div className="flex shrink-0 flex-col items-center">
          <ModelCardIcon
            icon={item.icon}
            label={item.label}
            letter={item.letter}
            isTopCard={isTopCard}
          />
          {item.priceLabel && (
            <span className="mt-1 inline-flex items-center gap-0.5 font-mono text-[10px] text-white/60">
              <Coins className="h-2.5 w-2.5 text-[#22d3ee]" strokeWidth={2.5} />
              {item.priceLabel}
            </span>
          )}
          {item.priceUsd !== null && item.priceUsd !== undefined && (
            <span className="mt-1 inline-flex items-center gap-0.5 font-mono text-[10px] text-white/60">
              <Coins className="h-2.5 w-2.5 text-[#38bdf8]" strokeWidth={2.5} />
              {`$${item.priceUsd.toFixed(3)}`}
            </span>
          )}
        </div>
        <div className="min-w-0 flex-1 pr-7">
          <p
            className={cn(
              "truncate text-[12px] font-semibold",
              active
                ? "text-[#22d3ee]"
                : isTopCard
                  ? "text-white font-medium group-hover:text-[#22d3ee]"
                  : "text-white/90 group-hover:text-white",
            )}
          >
            {item.label}
          </p>
          <p className="mt-0.5 line-clamp-2 text-[10px] leading-snug text-white/45 transition group-hover:text-white/70">
            {item.desc}
          </p>
        </div>
      </button>
    );
  };

  const renderAudioActionCard = (action: AudioActionSpec) => {
    const active = selectedSlug === action.slug || selectedSlug.replace(/^kie:/, "") === action.id;
    const kieM = kieForType.find((m) => m.id === action.id);
    const est = kieM ? estimateKie(kieM, {}, creditUsd) : null;
    const priceNote = kieM?.pricing?.note;

    return (
      <button
        key={action.slug}
        type="button"
        onClick={() => onSelect(action.slug)}
        className={cn(
          "group relative flex w-full items-start gap-2.5 rounded-xl border p-2.5 text-left transition-all duration-200",
          active
            ? "border-[#22d3ee] bg-[#22d3ee]/15 text-white shadow-[0_0_18px_rgba(34,211,238,0.2)] ring-1 ring-[#22d3ee]/50"
            : "border-white/[0.08] bg-white/[0.025] hover:border-white/20 hover:bg-white/[0.06]",
        )}
      >
        {action.badge && (
          <span className="absolute top-2 right-2 rounded-md bg-[#22d3ee] px-1.5 py-0.5 font-mono text-[9px] font-extrabold text-black shadow-sm">
            {action.badge}
          </span>
        )}
        <div className="flex shrink-0 flex-col items-center pt-0.5">
          <div
            className={cn(
              "flex h-8 w-8 items-center justify-center rounded-lg ring-1 transition",
              active
                ? "bg-[#22d3ee]/20 text-[#22d3ee] ring-[#22d3ee]/40"
                : "bg-white/[0.05] text-white/75 ring-white/10 group-hover:text-white",
            )}
          >
            <AudioActionIcon type={action.iconType} />
          </div>
        </div>
        <div className="min-w-0 flex-1 pr-6">
          <p
            className={cn(
              "truncate text-[12px] font-semibold",
              active ? "text-[#22d3ee]" : "text-white/90 group-hover:text-white",
            )}
          >
            {action.name}
          </p>
          <p className="mt-0.5 line-clamp-2 text-[10px] leading-snug text-white/45 group-hover:text-white/70">
            {action.desc}
          </p>
          <div className="mt-1 flex items-center gap-2">
            {priceNote ? (
              <span className="inline-flex items-center gap-1 font-mono text-[10px] text-white/55">
                <Coins className="h-2.5 w-2.5 text-[#38bdf8]" />
                {priceNote}
              </span>
            ) : est?.usd ? (
              <span className="inline-flex items-center gap-1 font-mono text-[10px] text-white/55">
                <Coins className="h-2.5 w-2.5 text-[#38bdf8]" />
                {`$${est.usd.toFixed(3)}`}
              </span>
            ) : null}
          </div>
        </div>
        {active && (
          <span className="absolute bottom-2.5 right-2.5 flex h-4 w-4 items-center justify-center rounded-full bg-[#22d3ee]/20 text-[#22d3ee]">
            <Check className="h-3 w-3" />
          </span>
        )}
      </button>
    );
  };

  return (
    <div
      className="absolute bottom-full left-0 z-50 mb-3 flex max-h-[76vh] flex-col overflow-hidden rounded-2xl border border-white/15 bg-[#121216]/95 backdrop-blur-2xl shadow-[0_25px_70px_rgba(0,0,0,0.85)] ring-1 ring-white/10"
      style={{
        width: mediaType === "video" ? 620 : mediaType === "audio" ? 480 : 520,
      }}
      role="dialog"
      aria-label={title}
      onPointerDown={(e) => e.stopPropagation()}
    >
      {/* Sticky Header with Search */}
      <div className="shrink-0 border-b border-white/[0.08] bg-[#16161b]/95 p-3 space-y-2.5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-bold tracking-tight text-white/90">{title}</span>
            <span className="rounded-full bg-white/10 px-2 py-0.5 font-mono text-[10px] font-semibold text-[#22d3ee]">
              {isAudio ? `2 движка · ${filteredSunoActions.length + filteredElevenLabsActions.length} действ.` : totalCount}
            </span>
          </div>
        </div>
        <div className="relative flex items-center">
          <Search className="absolute left-2.5 h-3.5 w-3.5 text-white/40" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={
              isAudio
                ? "Поиск действия (трек, кавер, озвучка, диалог, SFX)..."
                : "Быстрый поиск модели (Kling, Nano, Flux, Veo, Sora...)"
            }
            className="h-8 w-full rounded-xl border border-white/10 bg-black/40 pl-8 pr-7 text-[11px] text-white/90 placeholder:text-white/30 transition focus:border-[#22d3ee]/60 focus:outline-none focus:ring-1 focus:ring-[#22d3ee]/30"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch("")}
              className="absolute right-2 text-white/40 hover:text-white"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>

      {/* Unified single scrollable body */}
      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {isAudio ? (
          <>
            {/* Engine 1: Suno */}
            {(q ? filteredSunoActions.length > 0 : true) && (
              <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-2 space-y-2">
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => setSunoOpen((prev) => !prev)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") setSunoOpen((prev) => !prev);
                  }}
                  className={cn(
                    "flex cursor-pointer items-center justify-between rounded-xl border p-2.5 transition-all",
                    SUNO_AUDIO_ACTIONS.some((a) => selectedSlug === a.slug || selectedSlug.replace(/^kie:/, "") === a.id)
                      ? "border-orange-500/40 bg-gradient-to-r from-orange-500/[0.12] to-amber-500/[0.04]"
                      : "border-white/10 bg-white/[0.03] hover:border-white/20 hover:bg-white/[0.05]",
                  )}
                >
                  <div className="flex items-center gap-2.5">
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-orange-500/20 text-orange-400 ring-1 ring-orange-500/30">
                      <Music className="h-5 w-5" />
                    </div>
                    <div>
                      <div className="flex items-center gap-1.5">
                        <span className="text-[13px] font-bold text-white">Suno AI</span>
                        <span className="rounded-md bg-orange-500/20 px-1.5 py-0.5 font-mono text-[9px] font-bold text-orange-300">
                          Музыка & Звуки
                        </span>
                      </div>
                      <p className="text-[10px] text-white/50">Треки, каверы, вокал, стем-сплиттер, SFX</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="rounded-full bg-white/10 px-2 py-0.5 font-mono text-[10px] font-semibold text-white/70">
                      {filteredSunoActions.length}
                    </span>
                    <ChevronDown
                      className={cn(
                        "h-4 w-4 text-white/50 transition-transform duration-200",
                        (q ? true : sunoOpen) && "rotate-180",
                      )}
                    />
                  </div>
                </div>

                {(q ? true : sunoOpen) && (
                  <div className="space-y-1.5 pt-1">
                    {filteredSunoActions.map((action) => renderAudioActionCard(action))}
                  </div>
                )}
              </div>
            )}

            {/* Engine 2: ElevenLabs */}
            {(q ? filteredElevenLabsActions.length > 0 : true) && (
              <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-2 space-y-2">
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => setElevenlabsOpen((prev) => !prev)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") setElevenlabsOpen((prev) => !prev);
                  }}
                  className={cn(
                    "flex cursor-pointer items-center justify-between rounded-xl border p-2.5 transition-all",
                    ELEVENLABS_AUDIO_ACTIONS.some((a) => selectedSlug === a.slug || selectedSlug.replace(/^kie:/, "") === a.id)
                      ? "border-indigo-500/40 bg-gradient-to-r from-indigo-500/[0.12] to-purple-500/[0.04]"
                      : "border-white/10 bg-white/[0.03] hover:border-white/20 hover:bg-white/[0.05]",
                  )}
                >
                  <div className="flex items-center gap-2.5">
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-indigo-500/20 text-indigo-400 ring-1 ring-indigo-500/30">
                      <Mic className="h-5 w-5" />
                    </div>
                    <div>
                      <div className="flex items-center gap-1.5">
                        <span className="text-[13px] font-bold text-white">ElevenLabs</span>
                        <span className="rounded-md bg-indigo-500/20 px-1.5 py-0.5 font-mono text-[9px] font-bold text-indigo-300">
                          Голос & Речь
                        </span>
                      </div>
                      <p className="text-[10px] text-white/50">Озвучка v4, 200 русских голосов, эмоции и теги</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="rounded-full bg-white/10 px-2 py-0.5 font-mono text-[10px] font-semibold text-white/70">
                      {filteredElevenLabsActions.length}
                    </span>
                    <ChevronDown
                      className={cn(
                        "h-4 w-4 text-white/50 transition-transform duration-200",
                        (q ? true : elevenlabsOpen) && "rotate-180",
                      )}
                    />
                  </div>
                </div>

                {(q ? true : elevenlabsOpen) && (
                  <div className="space-y-1.5 pt-1">
                    {filteredElevenLabsActions.map((action) => renderAudioActionCard(action))}
                  </div>
                )}
              </div>
            )}

            {filteredSunoActions.length === 0 && filteredElevenLabsActions.length === 0 && (
              <div className="py-12 text-center text-[12px] text-white/40">
                Действия по запросу «<span className="text-white/70">{search}</span>» не найдены
              </div>
            )}
          </>
        ) : (
          <>
            {/* Section 1: TOP Models */}
            {topItems.length > 0 && (
              <div>
                <div className="mb-2 flex items-center gap-1.5 px-1 text-[11px] font-bold uppercase tracking-[0.14em] text-[#22d3ee]">
                  <span className="flex items-center gap-1">
                    <span>🔥</span>
                    <span>ТОП МОДЕЛИ</span>
                  </span>
                  <span className="rounded-full bg-[#22d3ee]/15 px-1.5 py-0.5 font-mono text-[9px] font-bold text-[#22d3ee]">
                    {topItems.length}
                  </span>
                </div>
                <div
                  className="grid gap-2"
                  style={{
                    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
                  }}
                >
                  {topItems.map((item) => renderCard(item))}
                </div>
              </div>
            )}

            {/* Section 2: Other Models */}
            {otherItems.length > 0 && (
              <div>
                <div className="mb-2 flex items-center gap-1.5 px-1 text-[10px] font-bold uppercase tracking-[0.14em] text-white/45">
                  <span>Другие и специальные модели</span>
                  <span className="font-mono text-white/25">({otherItems.length})</span>
                </div>
                <div
                  className="grid gap-2"
                  style={{
                    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
                  }}
                >
                  {otherItems.map((item) => renderCard(item))}
                </div>
              </div>
            )}

            {unifiedItems.length === 0 && (
              <div className="py-12 text-center text-[12px] text-white/40">
                Модели по запросу «<span className="text-white/70">{search}</span>» не найдены
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
