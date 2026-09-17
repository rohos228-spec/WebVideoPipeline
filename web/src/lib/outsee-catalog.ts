/**
 * Каталог outsee Create — дословная копия из JS:
 * - registry `o` + chip options `HH`/`d` — chunk 8152
 * - video Nn tables — chunk 517 module 90228
 * - picker meta ej/ek — create/image pages
 *
 * Не выдумывать опции: только то, что отдаёт HH(model, chip).
 */

export type OutseeMediaType = "image" | "video" | "audio";

export type OutseeFeedKind = "all" | "image" | "video" | "audio";

export type OutseeChip =
  | "aspect"
  | "resolution"
  | "detail"
  | "duration"
  | "audio"
  | "orientation"
  | "quality"
  | "instrumental"
  | "image-input";

export type OutseeAudioModel = {
  slug: string;
  studioId: string | null;
  displayName: string;
  description: string;
  icon: string;
  price: string;
  isTop?: boolean;
  isNew?: boolean;
  chips: OutseeChip[];
  defaults: { instrumental?: boolean; voice?: string; speed?: number };
};

export type OutseeImageModel = {
  slug: string;
  studioId: string | null;
  displayName: string;
  description: string;
  icon: string;
  price: string;
  isTop?: boolean;
  isNew?: boolean;
  /** registry.hidden — не в S0 picker */
  hidden?: boolean;
  advanced?: boolean;
  chips: OutseeChip[];
  defaults: {
    aspectRatio?: string;
    imageResolution?: string;
    detailLevel?: string;
  };
};

export type OutseeVideoModel = {
  slug: string;
  studioId: string | null;
  displayName: string;
  description: string;
  icon: string;
  price: string;
  isTop?: boolean;
  isNew?: boolean;
  hidden?: boolean;
  advanced?: boolean;
  chips: OutseeChip[];
  defaults: {
    aspectRatio?: string;
    resolution?: string;
    duration?: number;
    generateAudio?: boolean;
    motionQuality?: string;
    /** sora size small|large */
    soraSize?: string;
  };
  /** Nn table (если пусто — HH вернёт [] / override) */
  nn: {
    resolutions: string[];
    durations: number[];
    aspectRatios: string[];
  };
};

const OUTSEE_ORIGIN = "https://outsee.io";

/** gpt-image-2 aspects = n.P из module 20674 */
const GPT_IMAGE_2_ASPECTS = [
  "1:1",
  "16:9",
  "9:16",
  "4:3",
  "3:4",
  "3:2",
  "2:3",
  "21:9",
] as const;

/** nano-banana* — точный порядок из HH/d */
const NANO_BANANA_ASPECTS = [
  "16:9",
  "9:16",
  "1:1",
  "4:3",
  "5:4",
  "3:4",
  "4:5",
  "21:9",
] as const;

/** seedream / прочие image — из HH/d */
const SEEDREAM_ASPECTS = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"] as const;

export const OUTSEE_DETAIL_LEVELS = [
  { id: "low", label: "Низкое", hint: "дешевле" },
  { id: "medium", label: "Среднее", hint: "баланс" },
  { id: "high", label: "Высокое", hint: "детальнее" },
] as const;

export const OUTSEE_CHIP_LABELS: Record<string, string> = {
  aspect: "Соотношение сторон",
  resolution: "Разрешение",
  detail: "Детализация",
  duration: "Длительность",
  audio: "Звук",
  orientation: "Ориентация",
  quality: "Качество",
  instrumental: "Вокал",
  "image-input": "Кадры",
};

/** Вертикальный typetoggle create (ep=). */
export const OUTSEE_TYPE_TABS: { id: OutseeMediaType; label: string }[] = [
  { id: "image", label: "Фото" },
  { id: "video", label: "Видео" },
  { id: "audio", label: "Аудио" },
];

/** Фильтр истории create (aJ=). */
export const OUTSEE_FEED_TABS: { id: OutseeFeedKind; label: string }[] = [
  { id: "all", label: "Все" },
  { id: "image", label: "Фото" },
  { id: "video", label: "Видео" },
  { id: "audio", label: "Аудио" },
];

export const OUTSEE_ACCENT = "#22d3ee";

/**
 * Порядок picker = Object.values(o).filter(type && !hidden) из 8152.
 * Meta (price/TOP/NEW/icon/description) — из ej create page.
 */
export const OUTSEE_IMAGE_MODELS: OutseeImageModel[] = [
  {
    slug: "nano-banana-2",
    studioId: "nano_banana_2",
    displayName: "Nano Banana 2",
    description: "Google Gemini 3.1 Flash Image · ультрареализм и генерация текста.",
    icon: `${OUTSEE_ORIGIN}/imagemobilepreview/1.jpg`,
    price: "3",
    isTop: true,
    chips: ["aspect", "resolution", "image-input"],
    defaults: { aspectRatio: "16:9", imageResolution: "2K" },
  },
  {
    slug: "qwen3-image",
    studioId: null,
    displayName: "Qwen Image 3",
    description: "Alibaba Qwen Image 3 · превосходная детализация и типографика.",
    icon: `${OUTSEE_ORIGIN}/imagemobilepreview/3.jpg`,
    price: "от 2",
    chips: ["aspect", "image-input"],
    defaults: { aspectRatio: "16:9" },
  },
  {
    slug: "gpt-image-2",
    studioId: "gpt_image_2",
    displayName: "GPT Image 2",
    description: "OpenAI GPT Image · постеры и точный рендеринг текста.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/gptimage.webp`,
    price: "от 0.03",
    isTop: true,
    chips: ["aspect", "resolution", "detail", "image-input"],
    defaults: { aspectRatio: "16:9", imageResolution: "2K", detailLevel: "medium" },
  },
  {
    slug: "gpt-image-2-vip",
    studioId: "gpt_image_2_vip",
    displayName: "GPT Image 2 VIP",
    description: "OpenAI GPT Image · точный рендеринг текста.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/gptimage.webp`,
    price: "от 0.06",
    chips: ["aspect", "resolution", "image-input"],
    defaults: { aspectRatio: "16:9", imageResolution: "2K" },
  },
  {
    slug: "gpt-image-2.5",
    studioId: "gpt_image_2_5",
    displayName: "GPT Image 2.5",
    description: "Vibecode · GPT Image 2.5 (1K).",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/gptimage.webp`,
    price: "от 0.06",
    isNew: true,
    chips: ["aspect", "resolution", "image-input"],
    defaults: { aspectRatio: "16:9", imageResolution: "1K" },
  },
  {
    slug: "topaz-image-upscale",
    studioId: null,
    displayName: "Topaz Image Upscale",
    description:
      "Официальный Topaz Image API · 3 режима (Standard 2 / Wonder 2 / Bloom Realism)",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/topaz.webp`,
    price: "от 5",
    advanced: true,
    chips: [],
    defaults: {},
  },
];

/**
 * Порядок picker video = Object.values(o) type=video !hidden.
 */
export const OUTSEE_VIDEO_MODELS: OutseeVideoModel[] = [
  {
    slug: "grok-imagine-video-1.5",
    studioId: null,
    displayName: "Grok Imagine 1.5",
    description: "Новейшая модель от xAI, лучшая на рынке с русской речью.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/grok.webp`,
    price: "от 3.3",
    isNew: true,
    chips: ["aspect", "resolution", "duration", "image-input"],
    defaults: { aspectRatio: "16:9", resolution: "480p", duration: 8 },
    nn: {
      resolutions: ["480p", "720p"],
      durations: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
      aspectRatios: ["16:9", "9:16", "1:1", "3:2", "2:3"],
    },
  },
  {
    slug: "seedance-2-0-mini",
    studioId: null,
    displayName: "Seedance 2 Mini",
    description: "Новая, лёгкая версия Seedance 2.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/seedance.webp`,
    price: "от 8",
    isNew: true,
    chips: ["aspect", "resolution", "duration", "image-input"],
    defaults: { aspectRatio: "16:9", resolution: "720p", duration: 5 },
    nn: {
      resolutions: ["480p", "720p"],
      durations: [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
      aspectRatios: ["16:9", "9:16", "4:3", "3:4", "1:1", "21:9"],
    },
  },
  {
    slug: "veo-3-1-lite",
    studioId: "veo_3_1_lite",
    displayName: "Veo 3.1 Lite",
    description:
      "Outsee Veo 720p: звук/4–6с дожимаем локально; кадры — http URL (история → Старт).",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/google.webp`,
    price: "от 10",
    chips: ["aspect", "resolution", "duration", "audio", "image-input"],
    defaults: {
      aspectRatio: "16:9",
      resolution: "720p",
      duration: 8,
      generateAudio: false,
    },
    nn: {
      resolutions: ["720p"],
      durations: [4, 6, 8],
      aspectRatios: ["16:9", "9:16"],
    },
  },
  {
    slug: "omni-flash",
    studioId: null,
    displayName: "Omni Flash",
    description: "Новейшая модель Google. Аудио-нативная, до 5 голосов, редактирование видео.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/google.webp`,
    price: "от 14",
    isNew: true,
    chips: ["aspect", "resolution", "duration"],
    defaults: { aspectRatio: "16:9", resolution: "720p", duration: 4 },
    nn: {
      resolutions: ["720p", "1080p"],
      durations: [4, 6, 8, 10],
      aspectRatios: ["landscape", "portrait"],
    },
  },
  {
    slug: "kling-3-0-turbo",
    studioId: null,
    displayName: "Kling 3.0 Turbo",
    description: "Быстрая версия Kling 3.0.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/kling.webp`,
    price: "от 16",
    isNew: true,
    chips: ["aspect", "resolution", "duration", "image-input"],
    defaults: { aspectRatio: "16:9", resolution: "720p", duration: 5 },
    nn: {
      resolutions: ["720p", "1080p"],
      durations: [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
      aspectRatios: ["16:9", "9:16", "1:1"],
    },
  },
  {
    slug: "kling-2-6",
    studioId: "kling_2_6",
    displayName: "Kling 2.6",
    description: "Подходит для всего. Лучшее соотношение цена/качество среди Kling моделей.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/kling.webp`,
    price: "от 9",
    chips: ["aspect", "resolution", "duration", "audio", "image-input"],
    defaults: { aspectRatio: "16:9", resolution: "1080p", duration: 5, generateAudio: false },
    nn: {
      resolutions: ["720p", "1080p"],
      durations: [5, 10],
      aspectRatios: ["16:9", "9:16", "1:1"],
    },
  },
  {
    slug: "happyhorse-1-0",
    studioId: null,
    displayName: "HappyHorse 1.0",
    description:
      "Новейшая модель от Alibaba. Реалистичное движение, мульти-референс, редактирование видео.",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/happyhorse.webp`,
    price: "от 15",
    isNew: true,
    chips: ["aspect", "resolution", "duration", "image-input"],
    defaults: { aspectRatio: "16:9", resolution: "720P", duration: 5 },
    nn: {
      resolutions: ["720P", "1080P"],
      durations: [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
      aspectRatios: ["16:9", "9:16", "1:1", "4:3", "3:4"],
    },
  },
  {
    slug: "topaz-video-upscale",
    studioId: null,
    displayName: "Topaz Video Upscale",
    description: "AI-апскейл видео до 4K · Starlight · Proteus · Astra · от 5 ток",
    icon: `${OUTSEE_ORIGIN}/videomobilepreview/topaz.webp`,
    price: "от 5",
    advanced: true,
    chips: [],
    defaults: {},
    nn: { resolutions: ["1080p", "4k"], durations: [1], aspectRatios: [] },
  },
];

/**
 * Аудио — все модели Create (Suno / ElevenLabs).
 */
export const OUTSEE_AUDIO_MODELS: OutseeAudioModel[] = [
  {
    slug: "suno-5-5",
    studioId: null,
    displayName: "Suno 5.5",
    description: "Улучшенное качество и персонализация.",
    icon: `${OUTSEE_ORIGIN}/imagemobilepreview/suno.webp`,
    price: "2.5",
    chips: ["instrumental"],
    defaults: { instrumental: false },
  },
  {
    slug: "elevenlabs-v3",
    studioId: null,
    displayName: "ElevenLabs",
    description: "Реалистичная озвучка текста. Сотни голосов, десятки языков.",
    icon: `${OUTSEE_ORIGIN}/imagemobilepreview/elevenlabs.webp`,
    price: "от 0.1",
    isNew: true,
    chips: [],
    defaults: { voice: "Rachel", speed: 1 },
  },
];

/**
 * Create-пикер (после объединения с KIE): outsee-секция — только
 * GPT Image 2, Nano Banana 2, Veo 3.1 Lite. Остальное — секция KIE.
 * Аудио outsee не дублируем: Suno/ElevenLabs живут в KIE-секции.
 */
const CREATE_PICKER_IMAGE_SLUGS = ["gpt-image-2", "nano-banana-2"] as const;
const CREATE_PICKER_VIDEO_SLUGS = ["veo-3-1-lite"] as const;

export function pickerImageModels(): OutseeImageModel[] {
  const by = new Map(OUTSEE_IMAGE_MODELS.map((m) => [m.slug, m]));
  return CREATE_PICKER_IMAGE_SLUGS.map((s) => by.get(s)).filter(
    Boolean,
  ) as OutseeImageModel[];
}

export function pickerVideoModels(): OutseeVideoModel[] {
  return OUTSEE_VIDEO_MODELS.filter((m) => !m.hidden);
}

export function pickerVideoModelsAll(): OutseeVideoModel[] {
  const by = new Map(OUTSEE_VIDEO_MODELS.map((m) => [m.slug, m]));
  return CREATE_PICKER_VIDEO_SLUGS.map((s) => by.get(s)).filter(
    Boolean,
  ) as OutseeVideoModel[];
}

export function pickerAudioModels(): OutseeAudioModel[] {
  return [];
}

export function pickerModelsForType(type: OutseeMediaType) {
  if (type === "image") return pickerImageModels();
  if (type === "video") return pickerVideoModelsAll();
  return pickerAudioModels();
}

/**
 * Копия HH/d(model, chip) из chunk 8152.
 */
export function chipOptions(slug: string, chip: OutseeChip): string[] {
  const image = OUTSEE_IMAGE_MODELS.find((m) => m.slug === slug);
  const video = OUTSEE_VIDEO_MODELS.find((m) => m.slug === slug);

  if (chip === "quality") {
    if (
      slug === "kling-motion-control" ||
      slug === "kling-3-0-motion-control" ||
      slug === "kling-lip-sync"
    ) {
      return video?.nn.resolutions?.length ? video.nn.resolutions : ["std", "pro"];
    }
    return ["std", "pro"];
  }
  if (chip === "orientation") return ["video", "image"];
  if (chip === "detail") return slug.includes("gpt-image") ? ["low", "medium", "high"] : [];

  if (video) {
    if (chip === "aspect") {
      // HH override: veo / omni → 16:9 / 9:16 (не portrait/landscape)
      if (slug === "veo-3-fast" || slug === "veo-3-1-lite" || slug === "omni-flash") {
        return ["16:9", "9:16"];
      }
      return [...video.nn.aspectRatios];
    }
    if (chip === "resolution") {
      if (slug === "veo-3-1-lite") return ["720p"];
      return [...video.nn.resolutions];
    }
    if (chip === "duration") return video.nn.durations.map(String);
    return [];
  }

  if (image) {
    if (chip === "aspect") {
      if (slug === "gpt-image-1.5") return ["1:1", "3:2", "2:3"];
      if (slug === "gpt-image-2" || slug === "gpt-image-2-vip") return [...GPT_IMAGE_2_ASPECTS];
      if (slug.startsWith("nano-banana")) return [...NANO_BANANA_ASPECTS];
      return [...SEEDREAM_ASPECTS];
    }
    if (chip === "resolution") {
      if (slug === "gpt-image-2" || slug === "gpt-image-2-vip") return ["2K"];
      if (slug.startsWith("nano-banana")) return ["2K"];
      if (slug === "seedream-4.5") return ["2K", "4K"];
      if (slug === "seedream-5-pro") return ["1K", "2K"];
      if (slug === "seedream-5-lite") return ["2K", "3K"];
      if (slug === "gpt-image-1.5") return ["2K"];
      return ["1K", "2K"];
    }
  }
  return [];
}

/** Chip order на create: aspect → resolution → detail → duration → audio */
export const DOCK_CHIP_ORDER: OutseeChip[] = [
  "aspect",
  "resolution",
  "detail",
  "duration",
  "audio",
  "image-input",
];

export function dockChipsForModel(slug: string, mediaType: OutseeMediaType): OutseeChip[] {
  if (mediaType === "audio") {
    const model = OUTSEE_AUDIO_MODELS.find((m) => m.slug === slug);
    if (!model) return [];
    return (["instrumental"] as OutseeChip[]).filter((c) => model.chips.includes(c));
  }
  const model =
    mediaType === "image"
      ? OUTSEE_IMAGE_MODELS.find((m) => m.slug === slug)
      : OUTSEE_VIDEO_MODELS.find((m) => m.slug === slug);
  if (!model) return [];
  return DOCK_CHIP_ORDER.filter((c) => model.chips.includes(c));
}

export function getImageModel(slug: string): OutseeImageModel {
  return (
    OUTSEE_IMAGE_MODELS.find((m) => m.slug === slug) ??
    OUTSEE_IMAGE_MODELS.find((m) => m.slug === "gpt-image-2")!
  );
}

export function getVideoModel(slug: string): OutseeVideoModel {
  return (
    OUTSEE_VIDEO_MODELS.find((m) => m.slug === slug) ??
    OUTSEE_VIDEO_MODELS.find((m) => m.slug === "veo-3-1-lite")!
  );
}

export function getAudioModel(slug: string): OutseeAudioModel {
  return OUTSEE_AUDIO_MODELS.find((m) => m.slug === slug) ?? OUTSEE_AUDIO_MODELS[0]!;
}

export function studioIdToSlug(studioId: string | null | undefined, kind: OutseeMediaType): string {
  if (!studioId) {
    if (kind === "image") return "gpt-image-2";
    if (kind === "audio") return "suno-5-5";
    return "veo-3-1-lite";
  }
  if (kind === "audio") return studioId.replace(/_/g, "-");
  const list = kind === "image" ? OUTSEE_IMAGE_MODELS : OUTSEE_VIDEO_MODELS;
  const hit = list.find((m) => m.studioId === studioId);
  if (hit) return hit.slug;
  if (studioId === "veo_3_1_fast") return "veo-3-1-lite";
  return studioId.replace(/_/g, "-");
}

export function slugToStudioId(slug: string, kind: OutseeMediaType): string | null {
  if (kind === "audio") return null;
  const list = kind === "image" ? OUTSEE_IMAGE_MODELS : OUTSEE_VIDEO_MODELS;
  return list.find((m) => m.slug === slug)?.studioId ?? null;
}

export function aspectToStudioId(label: string): string {
  if (label === "portrait") return "9_16";
  if (label === "landscape") return "16_9";
  return label.replace(":", "_");
}

export function studioAspectToLabel(id: string | null | undefined): string {
  if (!id) return "16:9";
  return id.replace("_", ":");
}

export function resToStudioId(label: string): string {
  return label.toLowerCase();
}

export function studioResToLabel(id: string | null | undefined, slug?: string): string {
  if (!id) return "2K";
  if (slug === "happyhorse-1-0") {
    const u = id.toUpperCase();
    return u.endsWith("P") ? u : `${u}P`;
  }
  if (id === "std" || id === "pro" || id === "4k") return id;
  if (/^\d+p$/i.test(id)) return id.toLowerCase();
  return id.toUpperCase();
}

export function outseeCreateUrl(type: OutseeMediaType, slug: string): string {
  const t = type === "audio" ? "audio" : type;
  return `${OUTSEE_ORIGIN}/create?type=${t}&model=${encodeURIComponent(slug)}`;
}

export function outseeImageUrl(slug: string): string {
  return `${OUTSEE_ORIGIN}/image?model=${encodeURIComponent(slug)}`;
}

export function clampToOptions(value: string, options: string[], fallback?: string): string {
  if (!options.length) return value;
  if (options.includes(value)) return value;
  if (fallback && options.includes(fallback)) return fallback;
  return options[0]!;
}

export function detailLabel(id: string): string {
  return OUTSEE_DETAIL_LEVELS.find((d) => d.id === id)?.label ?? id;
}

export function supportsRelax(slug: string, mediaType: OutseeMediaType): boolean {
  // на create: безлимит-чип если у юзера есть; в Studio — как в пайплайне
  if (mediaType === "image") return true;
  return slug === "veo-3-1-lite" || slug === "veo-3-fast" || slug.includes("veo");
}
