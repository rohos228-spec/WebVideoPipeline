"use client";

import { useEffect, useRef, useState } from "react";
import { Download, Mic, Music2, Pause, Play, RotateCcw, RotateCw, Volume2, VolumeX } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

function formatTime(sec: number): string {
  if (!isFinite(sec) || isNaN(sec) || sec < 0) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s < 10 ? "0" : ""}${s}`;
}

/**
 * Статический профиль высоты (в %) для 32 полос визуализатора волны.
 */
const WAVE_BARS = [
  24, 38, 55, 42, 68, 85, 48, 72, 95, 60, 40, 78, 88, 52, 65, 92,
  70, 45, 82, 98, 64, 48, 76, 84, 58, 36, 68, 74, 50, 62, 38, 22,
];

/**
 * Компактный мини-плеер для списков артефактов (например, в SFX).
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

  const togglePlay = (e: React.SyntheticEvent) => {
    e.stopPropagation();
    e.preventDefault();
    const audio = audioRef.current;
    if (!audio) return;
    if (isPlaying) {
      audio.pause();
      setIsPlaying(false);
    } else {
      if (audio.ended || (audio.duration && Math.abs(audio.currentTime - audio.duration) < 0.2)) {
        audio.currentTime = 0;
      }
      audio.play().catch((err) => {
        console.warn("Audio play failed:", err);
        setIsPlaying(false);
      });
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
      data-keep-bg="true"
      className={cn(
        "nodrag nopan relative flex flex-col gap-1.5 rounded-xl border border-white/10 bg-black/85 p-2 shadow-2xl backdrop-blur-md transition-all hover:border-cyan-500/40",
        className
      )}
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <audio ref={audioRef} src={src} preload="metadata" />

      <div className="flex items-center gap-2 min-w-0">
        <button
          type="button"
          data-keep-bg="true"
          onClick={togglePlay}
          style={{ backgroundColor: "#22d3ee", color: "#000000" }}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full shadow-[0_0_12px_rgba(34,211,238,0.4)] transition hover:brightness-110 active:scale-95"
          title={isPlaying ? "Пауза" : "Воспроизвести"}
        >
          {isPlaying ? (
            <Pause className="h-3.5 w-3.5 fill-black text-black" />
          ) : (
            <Play className="h-3.5 w-3.5 fill-black text-black translate-x-0.5" />
          )}
        </button>

        <div className="min-w-0 flex-1 flex flex-col justify-center">
          <span className="truncate text-[11px] font-medium text-white/95 leading-tight">
            {label || "audio.mp3"}
          </span>
          <span className="text-[10px] font-mono text-cyan-300/80 leading-tight">
            {formatTime(currentTime)} / {formatTime(duration)}
          </span>
        </div>

        <div className="flex items-end gap-0.5 h-3 shrink-0 px-1">
          <span className={cn("w-0.5 rounded-full bg-cyan-400 transition-all", isPlaying ? "h-2.5 animate-pulse" : "h-1 opacity-40")} />
          <span className={cn("w-0.5 rounded-full bg-cyan-400 transition-all", isPlaying ? "h-3 animate-pulse delay-75" : "h-1.5 opacity-40")} />
          <span className={cn("w-0.5 rounded-full bg-cyan-400 transition-all", isPlaying ? "h-1.5 animate-pulse delay-150" : "h-1 opacity-40")} />
          <span className={cn("w-0.5 rounded-full bg-cyan-400 transition-all", isPlaying ? "h-2 animate-pulse delay-100" : "h-1 opacity-40")} />
        </div>
      </div>

      <div
        data-keep-bg="true"
        className="relative h-1.5 w-full cursor-pointer rounded-full bg-white/15 overflow-hidden group/track"
        onClick={handleSeek}
        title="Перемотать"
      >
        <div
          data-keep-bg="true"
          className="h-full rounded-full bg-gradient-to-r from-cyan-400 to-teal-400 transition-all group-hover/track:brightness-125"
          style={{ width: `${progressPercent}%` }}
        />
      </div>
    </div>
  );
}

/**
 * Профессиональный стильный аудиоплеер VIA для вкладки «Результаты».
 * Обладает яркой подсветкой, визуализатором эквалайзера, перемоткой и регулятором громкости.
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
  const [volume, setVolume] = useState(1);
  const [playbackRate, setPlaybackRate] = useState(1);

  const isMusic = Boolean(label && /music|музыка/i.test(label));

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
    const onError = () => {
      console.warn("Audio element error on src:", src);
      setIsPlaying(false);
    };

    audio.addEventListener("timeupdate", onTimeUpdate);
    audio.addEventListener("loadedmetadata", onLoadedMetadata);
    audio.addEventListener("durationchange", onDurationChange);
    audio.addEventListener("ended", onEnded);
    audio.addEventListener("pause", onPause);
    audio.addEventListener("play", onPlay);
    audio.addEventListener("error", onError);

    return () => {
      audio.removeEventListener("timeupdate", onTimeUpdate);
      audio.removeEventListener("loadedmetadata", onLoadedMetadata);
      audio.removeEventListener("durationchange", onDurationChange);
      audio.removeEventListener("ended", onEnded);
      audio.removeEventListener("pause", onPause);
      audio.removeEventListener("play", onPlay);
      audio.removeEventListener("error", onError);
      audio.pause();
    };
  }, [src]);

  const togglePlay = (e?: React.SyntheticEvent) => {
    if (e) {
      e.stopPropagation();
      e.preventDefault();
    }
    const audio = audioRef.current;
    if (!audio) return;

    if (isPlaying) {
      audio.pause();
      setIsPlaying(false);
    } else {
      if (audio.ended || (audio.duration && Math.abs(audio.currentTime - audio.duration) < 0.2)) {
        audio.currentTime = 0;
      }
      const playPromise = audio.play();
      if (playPromise !== undefined) {
        playPromise
          .then(() => setIsPlaying(true))
          .catch((err: unknown) => {
            console.error("Audio playback failed:", err);
            setIsPlaying(false);
            const msg = err instanceof Error ? err.message : String(err);
            toast.error(`Не удалось воспроизвести аудио: ${msg}`);
          });
      }
    }
  };

  const seekTo = (seconds: number) => {
    const audio = audioRef.current;
    if (!audio || !duration || !isFinite(duration)) return;
    const clamped = Math.max(0, Math.min(seconds, duration));
    audio.currentTime = clamped;
    setCurrentTime(clamped);
  };

  const handleSeek = (e: React.MouseEvent<HTMLDivElement>) => {
    e.stopPropagation();
    e.preventDefault();
    const rect = e.currentTarget.getBoundingClientRect();
    const clickX = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
    const pct = clickX / rect.width;
    seekTo(pct * (duration || 1));
  };

  const skipSeconds = (delta: number, e: React.SyntheticEvent) => {
    e.stopPropagation();
    e.preventDefault();
    seekTo(currentTime + delta);
  };

  const toggleSpeed = (e: React.SyntheticEvent) => {
    e.stopPropagation();
    e.preventDefault();
    const speeds = [1, 1.25, 1.5, 0.8];
    const nextIdx = (speeds.indexOf(playbackRate) + 1) % speeds.length;
    const nextSpeed = speeds[nextIdx] ?? 1;
    setPlaybackRate(nextSpeed);
    if (audioRef.current) {
      audioRef.current.playbackRate = nextSpeed;
    }
  };

  const toggleMute = (e: React.SyntheticEvent) => {
    e.stopPropagation();
    e.preventDefault();
    const audio = audioRef.current;
    if (!audio) return;
    audio.muted = !audio.muted;
    setIsMuted(audio.muted);
  };

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    e.stopPropagation();
    const val = parseFloat(e.target.value);
    setVolume(val);
    if (audioRef.current) {
      audioRef.current.volume = val;
      audioRef.current.muted = val === 0;
      setIsMuted(val === 0);
    }
  };

  const progressPercent = duration > 0 ? Math.min(100, (currentTime / duration) * 100) : 0;

  const displayTitle =
    label === "audio"
      ? "Озвучка (диктор)"
      : label === "music"
      ? "Фоновая музыка"
      : label || "Сгенерированная дорожка";

  const displaySub = isMusic
    ? "Сгенерированная музыкальная композиция для видеомонтажа"
    : "Полная аудиодорожка диктора с естественными интонациями";

  return (
    <div
      data-keep-bg="true"
      className={cn(
        "relative flex flex-col gap-4 rounded-2xl border border-white/15 bg-gradient-to-b from-[#131317] to-[#0a0a0c] p-5 shadow-2xl backdrop-blur-xl",
        className
      )}
      onClick={(e) => e.stopPropagation()}
    >
      <audio ref={audioRef} src={src} preload="metadata" />

      {/* Верхняя строка: иконка, название, теги и кнопка скачивания */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3.5 min-w-0">
          <div
            data-keep-bg="true"
            style={{
              backgroundColor: isMusic ? "rgba(168, 85, 247, 0.12)" : "rgba(34, 211, 238, 0.12)",
              borderColor: isMusic ? "rgba(168, 85, 247, 0.35)" : "rgba(34, 211, 238, 0.35)",
            }}
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border shadow-inner"
          >
            {isMusic ? (
              <Music2 className="h-5 w-5 text-purple-400" />
            ) : (
              <Mic className="h-5 w-5 text-cyan-400" />
            )}
          </div>

          <div className="min-w-0 flex flex-col">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold text-white tracking-wide">
                {displayTitle}
              </span>
              <span
                data-keep-bg="true"
                style={{
                  backgroundColor: "rgba(34, 211, 238, 0.12)",
                  borderColor: "rgba(34, 211, 238, 0.35)",
                  color: "#67e8f9",
                }}
                className="rounded border px-2 py-0.5 text-[10px] font-mono font-medium"
              >
                MP3 • HD
              </span>
            </div>
            <span className="text-xs text-white/55 truncate">{displaySub}</span>
          </div>
        </div>

        {downloadUrl ? (
          <a
            href={downloadUrl}
            download
            data-keep-bg="true"
            onClick={(e) => e.stopPropagation()}
            style={{ backgroundColor: "rgba(255, 255, 255, 0.05)", borderColor: "rgba(255, 255, 255, 0.15)" }}
            className="inline-flex h-9 items-center gap-2 rounded-xl border px-3 text-xs font-medium text-white shadow-sm transition hover:border-cyan-400/50 hover:bg-cyan-500/10 hover:text-cyan-300 shrink-0"
          >
            <Download className="h-3.5 w-3.5" />
            Скачать MP3
          </a>
        ) : null}
      </div>

      {/* Интерактивный эквалайзер / спектрограмма */}
      <div
        data-keep-bg="true"
        onClick={handleSeek}
        className="group/wave relative flex h-14 w-full cursor-pointer items-end justify-between gap-[3px] rounded-xl border border-white/[0.06] bg-black/40 px-3 py-2 transition hover:border-cyan-500/30"
        title="Кликните в любое место волны для перемотки"
      >
        {WAVE_BARS.map((heightPercent, idx) => {
          const barProgress = (idx / WAVE_BARS.length) * 100;
          const isPassed = barProgress <= progressPercent;
          return (
            <div
              key={idx}
              data-keep-bg="true"
              style={{
                height: `${heightPercent}%`,
                backgroundColor: isPassed ? "#22d3ee" : "rgba(255, 255, 255, 0.18)",
                boxShadow: isPassed ? "0 0 8px rgba(34, 211, 238, 0.5)" : "none",
                transformOrigin: "bottom",
              }}
              className={cn(
                "w-full rounded-full transition-all duration-150",
                isPlaying && isPassed && "animate-pulse"
              )}
            />
          );
        })}
      </div>

      {/* Таймлайн и перемотка */}
      <div className="flex flex-col gap-1.5">
        <div
          data-keep-bg="true"
          onClick={handleSeek}
          className="group/seek relative flex h-4 w-full cursor-pointer items-center"
          title="Перемотать"
        >
          {/* Фоновая линия */}
          <div
            data-keep-bg="true"
            style={{ backgroundColor: "rgba(255, 255, 255, 0.12)" }}
            className="h-2 w-full rounded-full overflow-hidden"
          >
            {/* Заполненная часть */}
            <div
              data-keep-bg="true"
              style={{
                width: `${progressPercent}%`,
                background: "linear-gradient(90deg, #06b6d4 0%, #14b8a6 60%, #10b981 100%)",
                boxShadow: "0 0 12px rgba(6, 182, 212, 0.6)",
              }}
              className="h-full rounded-full transition-all duration-75"
            />
          </div>

          {/* Бегунок scrubber */}
          <div
            data-keep-bg="true"
            style={{
              left: `${progressPercent}%`,
              backgroundColor: "#ffffff",
              boxShadow: "0 0 12px rgba(34, 211, 238, 0.9)",
            }}
            className="pointer-events-none absolute h-4 w-4 -translate-x-1/2 rounded-full border-2 border-cyan-400 transition-all group-hover/seek:scale-125"
          />
        </div>

        {/* Хронометраж */}
        <div className="flex items-center justify-between text-xs font-mono">
          <span className="font-semibold text-cyan-300">
            {formatTime(currentTime)}
          </span>
          <span className="text-white/45">
            {formatTime(duration)}
          </span>
        </div>
      </div>

      {/* Главная панель управления воспроизведением */}
      <div className="flex flex-wrap items-center justify-between gap-3 pt-1 border-t border-white/[0.08]">
        {/* Кнопка Play/Pause и статус */}
        <div className="flex items-center gap-3">
          <button
            type="button"
            data-keep-bg="true"
            onClick={togglePlay}
            style={{
              backgroundColor: "#22d3ee",
              color: "#000000",
              boxShadow: isPlaying
                ? "0 0 25px rgba(34, 211, 238, 0.65)"
                : "0 0 16px rgba(34, 211, 238, 0.4)",
            }}
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full transition-transform hover:scale-105 hover:brightness-110 active:scale-95 cursor-pointer"
            title={isPlaying ? "Пауза" : "Воспроизвести"}
          >
            {isPlaying ? (
              <Pause className="h-5 w-5 fill-black text-black" />
            ) : (
              <Play className="h-5 w-5 fill-black text-black translate-x-0.5" />
            )}
          </button>

          {/* Кликабельный лейбл воспроизведения */}
          <button
            type="button"
            data-keep-bg="true"
            onClick={togglePlay}
            className="flex items-center gap-2 text-left cursor-pointer transition hover:opacity-90"
          >
            <span className={cn("text-xs font-medium transition", isPlaying ? "text-cyan-300 font-semibold" : "text-white/80")}>
              {isPlaying ? "Воспроизведение..." : "Слушать запись"}
            </span>

            {isPlaying && (
              <div className="flex items-end gap-0.5 h-3">
                <span className="w-0.5 h-3 rounded-full bg-cyan-400 animate-pulse" />
                <span className="w-0.5 h-2 rounded-full bg-cyan-400 animate-pulse delay-100" />
                <span className="w-0.5 h-3.5 rounded-full bg-cyan-400 animate-pulse delay-75" />
              </div>
            )}
          </button>
        </div>

        {/* Вспомогательные контролы: перемотка -5s/+5s, скорость, громкость */}
        <div className="flex items-center gap-2">
          {/* -5 сек */}
          <button
            type="button"
            data-keep-bg="true"
            onClick={(e) => skipSeconds(-5, e)}
            style={{ backgroundColor: "rgba(255, 255, 255, 0.05)" }}
            className="flex h-8 items-center gap-1 rounded-lg border border-white/10 px-2 text-[11px] font-mono text-white/70 transition hover:border-cyan-500/40 hover:text-white"
            title="Назад на 5 секунд"
          >
            <RotateCcw className="h-3 w-3" />
            -5с
          </button>

          {/* +5 сек */}
          <button
            type="button"
            data-keep-bg="true"
            onClick={(e) => skipSeconds(5, e)}
            style={{ backgroundColor: "rgba(255, 255, 255, 0.05)" }}
            className="flex h-8 items-center gap-1 rounded-lg border border-white/10 px-2 text-[11px] font-mono text-white/70 transition hover:border-cyan-500/40 hover:text-white"
            title="Вперёд на 5 секунд"
          >
            <RotateCw className="h-3 w-3" />
            +5с
          </button>

          {/* Скорость */}
          <button
            type="button"
            data-keep-bg="true"
            onClick={toggleSpeed}
            style={{ backgroundColor: "rgba(255, 255, 255, 0.05)" }}
            className="flex h-8 w-11 items-center justify-center rounded-lg border border-white/10 text-[11px] font-mono text-cyan-300 transition hover:border-cyan-400/50 hover:bg-cyan-500/10"
            title="Скорость воспроизведения"
          >
            {playbackRate}x
          </button>

          {/* Громкость */}
          <div className="flex items-center gap-1.5 pl-1">
            <button
              type="button"
              data-keep-bg="true"
              onClick={toggleMute}
              className="flex h-8 w-8 items-center justify-center rounded-lg text-white/65 transition hover:bg-white/10 hover:text-white"
              title={isMuted ? "Включить звук" : "Без звука"}
            >
              {isMuted ? (
                <VolumeX className="h-4 w-4 text-rose-400" />
              ) : (
                <Volume2 className="h-4 w-4 text-white/80" />
              )}
            </button>

            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={isMuted ? 0 : volume}
              onChange={handleVolumeChange}
              className="h-1.5 w-16 cursor-pointer appearance-none rounded-full bg-white/20 accent-cyan-400"
              title={`Громкость: ${Math.round((isMuted ? 0 : volume) * 100)}%`}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
