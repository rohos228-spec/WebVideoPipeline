"use client";

import { useEffect, useRef, useState } from "react";
import { Download, Mic, Pause, Play, Volume2, VolumeX } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

function formatTime(sec: number): string {
  if (!isFinite(sec) || isNaN(sec) || sec < 0) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s < 10 ? "0" : ""}${s}`;
}

/**
 * Компактный мини-плеер для карточки ноды на холсте.
 * Не закрывает фоновый арт, имеет стиль VIA с неоновым акцентом.
 */
export function NodeAudioMiniPlayer({
  src,
  label,
  className,
}: {
  src: string;
  label?: string;
  className?: string;
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const onTimeUpdate = () => setCurrentTime(audio.currentTime);
    const onLoadedMetadata = () => {
      if (isFinite(audio.duration)) setDuration(audio.duration);
    };
    const onDurationChange = () => {
      if (isFinite(audio.duration)) setDuration(audio.duration);
    };
    const onEnded = () => {
      setIsPlaying(false);
      setCurrentTime(0);
    };
    const onPause = () => setIsPlaying(false);
    const onPlay = () => setIsPlaying(true);

    audio.addEventListener("timeupdate", onTimeUpdate);
    audio.addEventListener("loadedmetadata", onLoadedMetadata);
    audio.addEventListener("durationchange", onDurationChange);
    audio.addEventListener("ended", onEnded);
    audio.addEventListener("pause", onPause);
    audio.addEventListener("play", onPlay);

    return () => {
      audio.removeEventListener("timeupdate", onTimeUpdate);
      audio.removeEventListener("loadedmetadata", onLoadedMetadata);
      audio.removeEventListener("durationchange", onDurationChange);
      audio.removeEventListener("ended", onEnded);
      audio.removeEventListener("pause", onPause);
      audio.removeEventListener("play", onPlay);
      audio.pause();
    };
  }, [src]);

  const togglePlay = (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    const audio = audioRef.current;
    if (!audio) return;
    if (isPlaying) {
      audio.pause();
    } else {
      audio.play().catch((err) => console.warn("Audio play failed:", err));
    }
  };

  const handleSeek = (e: React.MouseEvent<HTMLDivElement>) => {
    e.stopPropagation();
    e.preventDefault();
    const audio = audioRef.current;
    if (!audio || !duration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const pct = Math.max(0, Math.min(1, clickX / rect.width));
    audio.currentTime = pct * duration;
    setCurrentTime(audio.currentTime);
  };

  const progressPercent = duration > 0 ? Math.min(100, (currentTime / duration) * 100) : 0;

  return (
    <div
      className={cn(
        "nodrag nopan relative flex flex-col gap-1.5 rounded-xl border border-white/10 bg-black/75 p-2 shadow-2xl backdrop-blur-md transition-all hover:border-cyan-500/35",
        className
      )}
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <audio ref={audioRef} src={src} preload="metadata" />

      <div className="flex items-center gap-2 min-w-0">
        {/* Кнопка воспроизведения */}
        <button
          type="button"
          onClick={togglePlay}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-cyan-500 text-black shadow-md shadow-cyan-500/30 transition hover:bg-cyan-400 hover:scale-105 active:scale-95"
          title={isPlaying ? "Пауза" : "Воспроизвести"}
        >
          {isPlaying ? (
            <Pause className="h-3.5 w-3.5 fill-current" />
          ) : (
            <Play className="h-3.5 w-3.5 fill-current translate-x-0.5" />
          )}
        </button>

        {/* Название и хронометраж */}
        <div className="min-w-0 flex-1 flex flex-col justify-center">
          <span className="truncate text-[11px] font-medium text-white/95 leading-tight">
            {label || "voice_full.mp3"}
          </span>
          <span className="text-[10px] font-mono text-cyan-300/80 leading-tight">
            {formatTime(currentTime)} / {formatTime(duration)}
          </span>
        </div>

        {/* Индикатор эквалайзера */}
        <div className="flex items-end gap-0.5 h-3 shrink-0 px-1">
          <span className={cn("w-0.5 rounded-full bg-cyan-400 transition-all", isPlaying ? "h-2.5 animate-pulse" : "h-1 opacity-40")} />
          <span className={cn("w-0.5 rounded-full bg-cyan-400 transition-all", isPlaying ? "h-3 animate-pulse delay-75" : "h-1.5 opacity-40")} />
          <span className={cn("w-0.5 rounded-full bg-cyan-400 transition-all", isPlaying ? "h-1.5 animate-pulse delay-150" : "h-1 opacity-40")} />
          <span className={cn("w-0.5 rounded-full bg-cyan-400 transition-all", isPlaying ? "h-2 animate-pulse delay-100" : "h-1 opacity-40")} />
        </div>
      </div>

      {/* Полоса перемотки */}
      <div
        className="relative h-1.5 w-full cursor-pointer rounded-full bg-white/15 overflow-hidden group/track"
        onClick={handleSeek}
        title="Перемотать"
      >
        <div
          className="h-full rounded-full bg-gradient-to-r from-cyan-400 to-teal-400 transition-all group-hover/track:brightness-125"
          style={{ width: `${progressPercent}%` }}
        />
      </div>
    </div>
  );
}

/**
 * Полноценная карточка аудиоплеера для вкладки «Результаты».
 */
export function ResultAudioCard({
  src,
  label,
  downloadUrl,
  className,
}: {
  src: string;
  label?: string;
  downloadUrl?: string | null;
  className?: string;
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [isMuted, setIsMuted] = useState(false);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const onTimeUpdate = () => setCurrentTime(audio.currentTime);
    const onLoadedMetadata = () => {
      if (isFinite(audio.duration)) setDuration(audio.duration);
    };
    const onDurationChange = () => {
      if (isFinite(audio.duration)) setDuration(audio.duration);
    };
    const onEnded = () => {
      setIsPlaying(false);
      setCurrentTime(0);
    };
    const onPause = () => setIsPlaying(false);
    const onPlay = () => setIsPlaying(true);

    audio.addEventListener("timeupdate", onTimeUpdate);
    audio.addEventListener("loadedmetadata", onLoadedMetadata);
    audio.addEventListener("durationchange", onDurationChange);
    audio.addEventListener("ended", onEnded);
    audio.addEventListener("pause", onPause);
    audio.addEventListener("play", onPlay);

    return () => {
      audio.removeEventListener("timeupdate", onTimeUpdate);
      audio.removeEventListener("loadedmetadata", onLoadedMetadata);
      audio.removeEventListener("durationchange", onDurationChange);
      audio.removeEventListener("ended", onEnded);
      audio.removeEventListener("pause", onPause);
      audio.removeEventListener("play", onPlay);
      audio.pause();
    };
  }, [src]);

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (isPlaying) {
      audio.pause();
    } else {
      audio.play().catch((err) => console.warn("Audio play failed:", err));
    }
  };

  const toggleMute = () => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.muted = !audio.muted;
    setIsMuted(audio.muted);
  };

  const handleSeek = (e: React.MouseEvent<HTMLDivElement>) => {
    const audio = audioRef.current;
    if (!audio || !duration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const pct = Math.max(0, Math.min(1, clickX / rect.width));
    audio.currentTime = pct * duration;
    setCurrentTime(audio.currentTime);
  };

  const progressPercent = duration > 0 ? Math.min(100, (currentTime / duration) * 100) : 0;

  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-2xl border border-white/10 bg-gradient-to-b from-white/[0.04] to-black/40 p-4 shadow-xl backdrop-blur-md",
        className
      )}
    >
      <audio ref={audioRef} src={src} preload="metadata" />

      {/* Верхний ряд: иконка, название, бейджи, кнопка скачивания */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-cyan-500/25 bg-cyan-500/10 text-cyan-400 shadow-sm shadow-cyan-500/10">
            <Mic className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex flex-col">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold text-foreground">
                {label || "Сгенерированная озвучка"}
              </span>
              <span className="rounded bg-cyan-500/15 border border-cyan-500/30 px-1.5 py-0.5 text-[10px] text-cyan-300 font-mono">
                MP3
              </span>
            </div>
            <span className="text-xs text-muted-foreground">
              Полная аудиодорожка диктора для монтажа
            </span>
          </div>
        </div>

        {downloadUrl ? (
          <Button
            size="sm"
            variant="outline"
            className="h-8 gap-1.5 border-white/15 bg-white/[0.03] text-xs text-foreground hover:border-cyan-500/50 hover:bg-cyan-500/10 hover:text-cyan-300 shrink-0"
            asChild
          >
            <a href={downloadUrl} download>
              <Download className="h-3.5 w-3.5" />
              Скачать MP3
            </a>
          </Button>
        ) : null}
      </div>

      {/* Прогресс-бар и хронометраж */}
      <div className="flex flex-col gap-1.5 pt-1">
        <div
          className="relative h-2.5 w-full cursor-pointer rounded-full bg-white/10 overflow-hidden group/bar"
          onClick={handleSeek}
          title="Кликните для перемотки"
        >
          <div
            className="h-full rounded-full bg-gradient-to-r from-cyan-500 via-teal-400 to-emerald-400 transition-all group-hover/bar:brightness-110"
            style={{ width: `${progressPercent}%` }}
          />
        </div>
        <div className="flex items-center justify-between text-[11px] font-mono text-muted-foreground">
          <span>{formatTime(currentTime)}</span>
          <span className="text-cyan-400/80">{formatTime(duration)}</span>
        </div>
      </div>

      {/* Нижняя панель управления */}
      <div className="flex items-center justify-between pt-1">
        <div className="flex items-center gap-2.5">
          <button
            type="button"
            onClick={togglePlay}
            className="flex h-9 w-9 items-center justify-center rounded-full bg-cyan-500 text-black shadow-md shadow-cyan-500/30 transition hover:bg-cyan-400 hover:scale-105 active:scale-95"
            title={isPlaying ? "Пауза" : "Воспроизвести"}
          >
            {isPlaying ? (
              <Pause className="h-4 w-4 fill-current" />
            ) : (
              <Play className="h-4 w-4 fill-current translate-x-0.5" />
            )}
          </button>
          <span className="text-xs text-white/70 font-medium pl-1">
            {isPlaying ? "Воспроизведение…" : "Слушать запись"}
          </span>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={toggleMute}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-white/60 transition hover:bg-white/10 hover:text-white"
            title={isMuted ? "Включить звук" : "Без звука"}
          >
            {isMuted ? <VolumeX className="h-4 w-4 text-rose-400" /> : <Volume2 className="h-4 w-4" />}
          </button>
        </div>
      </div>
    </div>
  );
}
