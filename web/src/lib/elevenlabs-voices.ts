/** Каталог голосов 11Labs для ноды «Озвучка» (включая 200 русских голосов ElevenLabs v4). */

import RU_VOICES_DATA from "@/lib/ru-voices.json";

export type ElevenLabsVoice = {
  id: string;
  name: string;
  description: string;
  gender?: "female" | "male";
  index?: number;
  sample_url?: string;
};

export const STANDARD_ELEVENLABS_VOICES: ElevenLabsVoice[] = [
  {
    id: "pNInz6obpgDQGcFmaJgB",
    name: "Адам (Adam)",
    description: "глубокий, эпичный голос рассказчика (рекомендуется)",
    gender: "male",
  },
  {
    id: "JBFqnCBsd6RMkjVDRZzb",
    name: "Джордж (George)",
    description: "тёплый, харизматичный сторителлер",
    gender: "male",
  },
  {
    id: "nPczCjzI2devNBz1zQrb",
    name: "Брайан (Brian)",
    description: "бархатный, солидный диктор",
    gender: "male",
  },
  {
    id: "IKne3meq5aSn9XLyUdCD",
    name: "Чарли (Charlie)",
    description: "уверенный, энергичный голос",
    gender: "male",
  },
  {
    id: "TX3LPaxmHKxFdv7VOQHJ",
    name: "Лиам (Liam)",
    description: "молодой, современный голос",
    gender: "male",
  },
  {
    id: "Xb7hH8MSUJpSbSDYk0k2",
    name: "Алиса (Alice)",
    description: "чёткий, выразительный женский голос",
    gender: "female",
  },
  {
    id: "EXAVITQu4vr4xnSDxMaL",
    name: "Сара (Sarah)",
    description: "уверенный, зрелый женский голос",
    gender: "female",
  },
  {
    id: "hpp4J3VqNfWAUOO0d1Us",
    name: "Белла (Bella)",
    description: "тёплый, яркий женский голос",
    gender: "female",
  },
  {
    id: "pFZP5JQG7iQjIQuC4Bku",
    name: "Лили (Lily)",
    description: "бархатный, кинематографичный женский голос",
    gender: "female",
  },
];

type RawRuVoice = {
  id: string;
  index: number;
  name: string;
  description: string;
  gender: "female" | "male";
  sample_url?: string;
};

export const RU_VOICES: ElevenLabsVoice[] = (RU_VOICES_DATA as RawRuVoice[]).map((v) => ({
  id: v.id,
  index: v.index,
  name: v.name,
  description: v.description,
  gender: v.gender,
  sample_url: v.sample_url || `/api/voices/${v.id}/sample`,
}));

export const FEMALE_RU_VOICES: ElevenLabsVoice[] = RU_VOICES.filter((v) => v.gender === "female");
export const MALE_RU_VOICES: ElevenLabsVoice[] = RU_VOICES.filter((v) => v.gender === "male");

export const ELEVENLABS_VOICES: ElevenLabsVoice[] = [
  ...STANDARD_ELEVENLABS_VOICES,
  ...RU_VOICES,
];

export const DEFAULT_ELEVENLABS_VOICE_ID = "pNInz6obpgDQGcFmaJgB";

export function elevenLabsVoiceLabel(v: ElevenLabsVoice): string {
  const prefix = v.index ? `#${v.index} ` : "";
  return `${prefix}${v.name} — ${v.description}`;
}

export function findElevenLabsVoice(id: string | null | undefined): ElevenLabsVoice | undefined {
  if (!id) return undefined;
  const clean = id.trim();
  return ELEVENLABS_VOICES.find((v) => v.id === clean);
}
