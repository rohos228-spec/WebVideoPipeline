/**
 * Помощник генерации: категории и стили с «агентами» (promptCore).
 * Источник ядер — prompts/blocks/visual_style/*.md (data/library/current).
 * Используется панелью gen-assistant-panel в окне «Генерация».
 */

export type GenStyleArt =
  | "polka"
  | "pixel"
  | "noir"
  | "clay"
  | "knit"
  | "infographic"
  | "photo"
  | "tutor"
  | "retro"
  | "concept"
  | "anime"
  | "fashion"
  | "nature"
  | "diagram";

export type GenStyleDef = {
  id: string;
  /** Ключ SVG-превью плитки. */
  art: GenStyleArt;
  /** Готовая картинка превью (`/gen-styles/...`). Если нет — SVG по `art`. */
  cover?: string;
  name: string;
  /** Путь блока в библиотеке промптов (справочно). */
  file: string;
  desc: string;
  /** Ключ цвета точки/рамки плитки. */
  color: "red" | "purple" | "gray" | "orange" | "cyan" | "blue" | "green" | "pink" | "yellow";
  tags: string[];
  promptCore: string;
};

export type GenCategoryDef = {
  id: string;
  /** Превью-представитель категории. */
  art: GenStyleArt;
  name: string;
  styles: GenStyleDef[];
};

export const GEN_ASSISTANT_CATEGORIES: GenCategoryDef[] = [
  {
    id: "cartoon",
    art: "polka",
    name: "Мульт/аниме",
    styles: [
      {
        id: "trash_polka_noir_short",
        art: "polka",
        name: "Треш-полька нуар",
        file: "visual_style/trash_polka_noir_short.md",
        desc: "Grunge poster, ink splash, blood-red акценты, distressed paper",
        color: "red",
        tags: ["true-crime", "драма", "история", "постер", "взрослые"],
        promptCore:
          "Trash Polka Noir Comic Grunge Poster Illustration: trash polka aesthetic, dark graphic novel art, high-contrast mixed media grunge poster. Palette: stark black, off-white, dirty cream, charcoal, and vivid blood-red splash accents. Raw ink splatters, energetic brush strokes, halftone dots, distressed vintage paper texture, gritty ink linework, single dramatic focal point.",
      },
      {
        id: "micro_pixelart",
        art: "pixel",
        name: "Микро-пиксельарт",
        file: "visual_style/micro_pixelart.md",
        desc: "16-bit pixel art, четкие пиксели, ретро-гейм эстетика",
        color: "purple",
        tags: ["игры", "технологии", "коты", "ночь", "кибер"],
        promptCore:
          "Detailed 16-bit pixel art illustration: authentic handcrafted pixel art aesthetic, crisp visible pixels, rich pixel shading, artistic color dithering, retro cinematic game atmosphere, vibrant color harmony, distinct pixel art sprites and textures. Pure 16-bit pixel art video game art style.",
      },
      {
        id: "anime_cyber_tokyo",
        art: "anime",
        name: "Киберпанк аниме",
        file: "visual_style/anime_cyber_tokyo.md",
        desc: "Неоновый Токио, детальные аниме-фоны, мокрый асфальт, вывески",
        color: "pink",
        tags: ["аниме", "киберпанк", "неон", "город", "ночь"],
        promptCore:
          "High-end anime film still, neo-tokyo cyberpunk aesthetic, intricate anime background art, dramatic wet reflections on streets, glowing holographic signage and neon lights, rich cinematic color palette, Studio Ghibli meets Ghost in the Shell background fidelity, crisp linework. No photorealism, no 3D render, no flat amateur art.",
      },
      {
        id: "animation_pixar_3d",
        art: "clay",
        name: "3D Мультфильм",
        file: "visual_style/animation_pixar_3d.md",
        desc: "Выразительные персонажи, мягкий студийный свет, объемный рендер",
        color: "orange",
        tags: ["3D", "персонажи", "сказка", "дети", "юмор"],
        promptCore:
          "Award-winning 3D animated feature film still: stylized expressive characters, warm volumetrics, soft studio key lighting, rich tactile subsurface scattering, tactile textures, Pixar and DreamWorks look, rich emotive atmosphere. Stylized 3D CGI animation render.",
      },
      {
        id: "comic_watercolor_ink",
        art: "polka",
        name: "Акварельный комикс",
        file: "visual_style/comic_watercolor_ink.md",
        desc: "Франко-бельгийский стиль BD, легкая тушь, акварельные переходы",
        color: "cyan",
        tags: ["комикс", "акварель", "иллюстрация", "литература", "история"],
        promptCore:
          "European BD comic book graphic novel illustration: delicate ink line art, soft watercolor wash gradients, elegant crosshatching, expressive character silhouettes, textured artist watercolor paper, poetic mood. Graphic novel watercolor illustration.",
      },
      {
        id: "noir_true_crime_poster",
        art: "noir",
        name: "Нуар true-crime",
        file: "visual_style/noir_true_crime_poster.md",
        desc: "Graphic novel, halftone grain, тяжёлые чёрные тени",
        color: "gray",
        tags: ["true-crime", "детектив", "ночь", "город", "документалка"],
        promptCore:
          "Noir Graphic Novel True-Crime Thriller Poster: gritty noir graphic novel style, high-contrast crime thriller poster art, deep pitch-black shadows, stark chiaroscuro lighting, rough print texture with halftone dots and film scratches, dirty cream highlights, vivid crimson blood-red accents.",
      },
      {
        id: "clay_plasticine_2d",
        art: "clay",
        name: "Пластилин",
        file: "visual_style/clay_plasticine_2d.md",
        desc: "Claymation-миниатюра, отпечатки пальцев, matte texture",
        color: "orange",
        tags: ["дети", "сказка", "уют", "еда", "обучение"],
        promptCore:
          "Claymation Plasticine Miniature Illustration: handcrafted polymer clay model, authentic tactile clay texture with subtle fingerprints and matte finish, soft rounded clay sculpts, warm earthy stop-motion lighting, charming handmade tactile diorama aesthetic.",
      },
      {
        id: "textile_cut_paper_knitted",
        art: "knit",
        name: "Вязаный / войлок",
        file: "visual_style/textile_cut_paper_knitted.md",
        desc: "Textile, cut-paper, тёплая осенняя палитра, вышивка",
        color: "cyan",
        tags: ["дети", "сказка", "зима", "уют", "животные"],
        promptCore:
          "Handcrafted Textile and Cut-Paper Illustration: layered felt and textured craft paper collage, embroidered stitches, tactile woolen cloth textures, warm autumnal palette, cozy whimsical children's book aesthetic, soft dimensional papercraft shadows.",
      },
      {
        id: "gritty_doc_noir_historical",
        art: "noir",
        name: "Док-нуар историч.",
        file: "visual_style/gritty_doc_noir_historical.md",
        desc: "Акварель и тушь, состаренная бумага, холодная луна",
        color: "blue",
        tags: ["история", "мистика", "документалка", "война", "тайны"],
        promptCore:
          "Gritty Documentary Noir Historical Mystery Illustration: dark historical archival concept art, raw watercolor wash and expressive ink splatters on aged textured parchment, dramatic chiaroscuro shadows, cold moonlight with faded sepia and muted navy tones, mysterious atmospheric focal point.",
      },
    ],
  },
  {
    id: "infographic",
    art: "tutor",
    name: "Инфографика",
    styles: [
      {
        id: "infographic_tutor",
        art: "tutor",
        cover: "/gen-styles/infographic_tutor.jpg",
        name: "Tutor",
        file: "visual_style/infographic_tutor.md",
        desc: "Обложка урока: крупный заголовок, 3D-тьютор у доски, кремовая палитра",
        color: "yellow",
        tags: ["обучение", "обложка", "3D-тьютор", "заголовок", "урок"],
        promptCore:
          "Tutor Title Card: stylized educational vertical poster. Ultra-detailed 3D animated movie aesthetic, cute fluffy animal tutor with a pointer at a dark chalkboard, warm cozy classroom setting, Pixar-like 3D render, soft studio lighting. Bold rounded display typography, clean uppercase headline, cream-beige backdrop with dark graphite blackboard and warm amber orange accents.",
      },
      {
        id: "infographic_isometric_cutaway",
        art: "diagram",
        name: "3D Разрез в изометрии",
        file: "visual_style/infographic_isometric_cutaway.md",
        desc: "Архитектурный или технический разрез в изометрии, детализированные уровни",
        color: "purple",
        tags: ["3D", "изометрия", "схема", "архитектура", "технологии"],
        promptCore:
          "Detailed isometric 3D cutaway diorama illustration: cross-section revealing interior layers and rooms, miniature architectural model aesthetic, soft directional lighting, crisp clean lines, vibrant thematic color accents, educational exploded view. Stylized 3D isometric diorama.",
      },
      {
        id: "infographic_modern_dashboard",
        art: "diagram",
        name: "UI Дашборд / Glass",
        file: "visual_style/infographic_modern_dashboard.md",
        desc: "Премиальный интерфейс, матовое стекло, графики и карточки данных",
        color: "cyan",
        tags: ["UI", "бизнес", "технологии", "веб", "данные"],
        promptCore:
          "Modern high-tech UI dashboard presentation: floating dark glassmorphism cards, glowing telemetry graphs, sleek typography, clean data visualizations, minimalist isometric angle, soft ambient cyan and violet lighting.",
      },
      {
        id: "infographic_flat_vector",
        art: "infographic",
        name: "Flat vector",
        file: "visual_style/infographic_flat_vector.md",
        desc: "Плоские формы, иконки, стрелки, 2–4 акцентных цвета",
        color: "blue",
        tags: ["бизнес", "обучение", "технологии", "финансы", "шаги"],
        promptCore:
          "Flat Vector Infographic Illustration: minimalist flat 2D vector graphic art, bold clean icons, modern flow diagrams, cohesive limited color palette (2-4 accent colors), sharp vector lines, ample clean negative space, corporate editorial infographic aesthetic.",
      },
      {
        id: "infographic_isometric_data",
        art: "infographic",
        name: "Изометрия data",
        file: "visual_style/infographic_isometric_data.md",
        desc: "Изометрические диаграммы, парящие блоки данных, сетка",
        color: "cyan",
        tags: ["данные", "технологии", "финансы", "статистика", "стартапы"],
        promptCore:
          "Isometric Data 3D Illustration: clean isometric projection, 3D geometric charts and floating data cubes, sleek tech color palette (cyan, navy blue, white with warm orange accent), soft ambient shadows, precise technical diagram aesthetic.",
      },
      {
        id: "infographic_chalkboard_sketch",
        art: "infographic",
        name: "Меловая доска",
        file: "visual_style/infographic_chalkboard_sketch.md",
        desc: "Рукотворные маркерные схемы, стрелки, стик-фигуры",
        color: "green",
        tags: ["обучение", "лайфхаки", "план", "идеи", "наука"],
        promptCore:
          "Chalkboard Sketch Infographic: hand-drawn chalk illustrations on dark slate blackboard, authentic chalk dust and smudge textures, crisp white and pastel chalk lines, hand-lettered labels and diagram arrows, cozy classroom lecture visual.",
      },
      {
        id: "infographic_blueprint",
        art: "infographic",
        name: "Blueprint",
        file: "visual_style/infographic_blueprint.md",
        desc: "Белые линии на синьке, размерные линии, сечения",
        color: "blue",
        tags: ["техника", "механизмы", "архитектура", "наука", "изобретения"],
        promptCore:
          "Architectural Blueprint Technical Drawing: crisp white drafting schematics on classic cyan-blue blueprint paper, fine grid lines, architectural cross-sections, precision technical annotations, vintage engineering draft aesthetic.",
      },
    ],
  },
  {
    id: "photo",
    art: "photo",
    name: "Фото/кино",
    styles: [
      {
        id: "photo_cinematic_film_still",
        art: "photo",
        name: "Кинокадр",
        file: "visual_style/photo_cinematic_film_still.md",
        desc: "Анаморфот, малая ГРИП, киношный грейдинг",
        color: "orange",
        tags: ["драма", "кино", "история", "портрет", "ночь"],
        promptCore:
          "Cinematic 35mm Film Still: captured on Panavision anamorphic lens, shallow depth of field, rich cinematic color grade, subtle film grain, natural rim lighting, soft halation around highlights, atmospheric movie screenshot quality.",
      },
      {
        id: "photo_editorial_portrait",
        art: "fashion",
        name: "Fashion портрет",
        file: "visual_style/photo_editorial_portrait.md",
        desc: "Студийный портрет для журнала, контролируемый свет, фактура кожи",
        color: "yellow",
        tags: ["портрет", "мода", "люди", "глянец", "студия"],
        promptCore:
          "High-fashion editorial studio portrait: dramatic split and rim lighting, authentic skin texture with fine pores, cinematic depth of field, minimalist studio background, elegant pose, captured on medium format Hasselblad camera, 85mm prime lens. Professional studio portrait.",
      },
      {
        id: "photo_wildlife_natgeo",
        art: "nature",
        name: "Дикая природа / NatGeo",
        file: "visual_style/photo_wildlife_natgeo.md",
        desc: "Крупный план животных в среде, телеобъектив, мягкий золотой свет",
        color: "green",
        tags: ["природа", "животные", "natgeo", "пейзаж", "золотой час"],
        promptCore:
          "National Geographic style wildlife photography: tack-sharp focus on animal eyes, natural golden hour sunlight, soft creamy background bokeh, authentic natural habitat, 400mm telephoto lens compression, breathtaking candid wildlife moment.",
      },
      {
        id: "photo_documentary",
        art: "photo",
        name: "Документальное",
        file: "visual_style/photo_documentary.md",
        desc: "Репортаж, естественный свет, зерно 35mm",
        color: "gray",
        tags: ["реальность", "люди", "город", "репортаж", "соцтемы"],
        promptCore:
          "Documentary Photojournalism: authentic candid 35mm street photography, natural ambient lighting, genuine unposed composition, Kodak Tri-X film grain, muted realistic documentary color tones, compelling visual storytelling.",
      },
      {
        id: "photo_macro_product",
        art: "photo",
        name: "Макро-предметка",
        file: "visual_style/photo_macro_product.md",
        desc: "Предмет крупно, студийный свет, премиальный глянец",
        color: "yellow",
        tags: ["предметы", "еда", "техника", "детали", "реклама"],
        promptCore:
          "Commercial Macro Studio Product Photography: extreme high-resolution close-up, razor-sharp micro details, shallow depth of field, soft seamless studio backdrop, controlled softbox diffusion lighting, commercial luxury advertising quality.",
      },
      {
        id: "photo_night_street",
        art: "photo",
        name: "Ночная улица",
        file: "visual_style/photo_night_street.md",
        desc: "Зерно высокого ISO, неон, мокрый асфальт, смаз",
        color: "pink",
        tags: ["город", "ночь", "неон", "молодёжь", "музыка"],
        promptCore:
          "Cinematic Night Street Photography: moody rain-slicked asphalt reflecting vibrant neon city lights, natural high-ISO film grain, dramatic urban shadows, atmospheric night street scene, shallow depth of field.",
      },
    ],
  },
  {
    id: "retro",
    art: "retro",
    name: "Ретро/архив",
    styles: [
      {
        id: "retro_synthwave_80s",
        art: "retro",
        name: "Синтвейв 80-х",
        file: "visual_style/retro_synthwave_80s.md",
        desc: "Неоновая сетка, хром, фиолетово-бирюзовый закат, VHS",
        color: "pink",
        tags: ["80е", "синтвейв", "неон", "кибер", "музыка"],
        promptCore:
          "1980s retro synthwave outrun aesthetic: glowing neon grid landscape extending into a magenta and cyan horizon, low-poly wireframe mountain peaks, chrome reflections, vibrant sunset gradient, nostalgic VHS tape scanline glow.",
      },
      {
        id: "retro_propaganda_poster",
        art: "retro",
        name: "Ретро-постер / Пин-ап",
        file: "visual_style/retro_propaganda_poster.md",
        desc: "Графика середины XX века, шелкография, текстура крафта",
        color: "orange",
        tags: ["винтаж", "постер", "середина века", "арт", "история"],
        promptCore:
          "Mid-century vintage illustrated travel / propaganda poster: bold flat shapes, screen printing halftone texture, limited warm nostalgic palette, aged kraft paper grain, elegant retro typography, stylized heroic figures.",
      },
      {
        id: "retro_archive_8mm",
        art: "retro",
        name: "Хроника 8мм",
        file: "visual_style/retro_archive_8mm.md",
        desc: "Зерно, царапины, выцветшие цвета, мерцание кадра",
        color: "yellow",
        tags: ["история", "хроника", "война", "семья", "XX век"],
        promptCore:
          "Archival 8mm Vintage Film Still: authentic 1960s color film footage, gentle grain and film dust scratches, warm Kodachrome faded tones, soft vintage vignette, nostalgic historical documentary footage look.",
      },
      {
        id: "retro_polaroid",
        art: "retro",
        name: "Полароид",
        file: "visual_style/retro_polaroid.md",
        desc: "Вспышка в лоб, вымытые цвета, снимок 80–90х",
        color: "orange",
        tags: ["90е", "семья", "ностальгия", "вечеринка", "личное"],
        promptCore:
          "Vintage Polaroid SX-70 Instant Photo: direct retro on-camera flash, soft muted pastel colors, authentic instant film chemical border feel, subtle light leaks, nostalgic 1980s candid memory snapshot.",
      },
      {
        id: "retro_newspaper_print",
        art: "retro",
        name: "Газетная печать",
        file: "visual_style/retro_newspaper_print.md",
        desc: "Растр, пожелтевшая бумага, старые чернила",
        color: "gray",
        tags: ["история", "скандал", "пресса", "XX век", "криминал"],
        promptCore:
          "Vintage Halftone Newspaper Print: high-contrast monochrome printing on aged yellowed newsprint paper, authentic halftone dot screen pattern, ink bleed artifacts, retro 1950s investigative press illustration.",
      },
      {
        id: "retro_investigation_board",
        art: "retro",
        name: "Доска расследования",
        file: "visual_style/retro_investigation_board.md",
        desc: "Пробковая доска, фото, красные нити, заметки",
        color: "red",
        tags: ["true-crime", "детектив", "тайны", "расследование", "улики"],
        promptCore:
          "Detective Crime Investigation Wall: cork board pinned with Polaroid evidence photos, red string connecting clues, aged newspaper clippings, handwritten case notes, dramatic warm desk lamp illumination.",
      },
    ],
  },
  {
    id: "art_concept",
    art: "concept",
    name: "Концепт-арт / 3D",
    styles: [
      {
        id: "concept_dark_fantasy",
        art: "concept",
        name: "Тёмное фэнтези",
        file: "visual_style/concept_dark_fantasy.md",
        desc: "Эпический масштаб, мрачная готика, туман, эстетика Elden Ring",
        color: "orange",
        tags: ["фэнтези", "готика", "мистика", "игры", "пейзаж"],
        promptCore:
          "Epic dark fantasy concept art: vast colossal gothic ruins, dense atmospheric fog, moody chiaroscuro lighting, faint glowing embers and magic runes, grim painterly digital matte painting, FromSoftware aesthetic. No cheerful bright colors, no cartoonish styles, no clean modern elements.",
      },
      {
        id: "concept_isometric_diorama",
        art: "clay",
        name: "Миниатюрная 3D-диорама",
        file: "visual_style/concept_isometric_diorama.md",
        desc: "Изометрический мини-мир, эффект tilt-shift, осязаемые игрушечные материалы",
        color: "cyan",
        tags: ["3D", "изометрия", "диорама", "игрушки", "уют"],
        promptCore:
          "Whimsical miniature 3D isometric diorama: tiny handcrafted scene floating in void, tilt-shift macro lens depth of field, soft warm directional studio key light, tactile matte materials, charming tiny details. No flat 2D vector, no chaotic crowds, no realistic human faces.",
      },
      {
        id: "concept_prismatic_glass",
        art: "concept",
        name: "Призматическое стекло",
        file: "visual_style/concept_prismatic_glass.md",
        desc: "Полупрозрачные стеклянные формы, дисперсия света, радужные блики",
        color: "purple",
        tags: ["абстракция", "стекло", "премиум", "3D", "дизайн"],
        promptCore:
          "Surreal translucent glass sculpture: iridescent refractive glass shapes, chromatic dispersion and caustics, soft pastel rainbow reflections, minimalist studio composition, ethereal clean lighting. No dark muddy tones, no flat cartoons, no noisy textures.",
      },
      {
        id: "concept_whimsical_watercolor",
        art: "knit",
        name: "Акварельная сказка",
        file: "visual_style/concept_whimsical_watercolor.md",
        desc: "Мягкая акварель, карандашные контуры, атмосфера детской книжной иллюстрации",
        color: "green",
        tags: ["сказка", "акварель", "уют", "дети", "природа"],
        promptCore:
          "Whimsical storybook children's illustration: soft transparent watercolor washes, delicate graphite pencil outlines, gentle pastel color harmony, cozy dreamy atmosphere, textured watercolor paper background. Storybook watercolor illustration art.",
      },
    ],
  },
];

export const GEN_ASSISTANT_ALL_STYLES: GenStyleDef[] =
  GEN_ASSISTANT_CATEGORIES.flatMap((c) => c.styles);

/** Цвета плиток в палитре окна генерации (dark + cyan accent). */
export const GEN_STYLE_COLORS: Record<GenStyleDef["color"], string> = {
  red: "var(--gen-style-red)",
  purple: "var(--gen-style-purple)",
  gray: "var(--gen-style-gray)",
  orange: "var(--gen-style-orange)",
  cyan: "var(--gen-style-cyan)",
  blue: "var(--gen-style-blue)",
  green: "var(--gen-style-green)",
  pink: "var(--gen-style-pink)",
  yellow: "var(--gen-style-yellow)",
};


/** Длиннее — это агент-инструкция со слотами, а не ядро стиля. */
export const STYLE_CORE_MAX_CHARS = 700;

const INSTRUCTION_MARKERS = ["\n#", "\n|", "```", "negative", "шаблон prompt", "чек-лист"];

/**
 * Ядро стиля вставляется в промпт дословно, агент-инструкция — нет:
 * её исполняет LLM по кнопке «Сгенерировать» (зеркало looks_like_agent_echo).
 */
export function isInstructionAgent(agentText: string): boolean {
  const text = (agentText ?? "").trim();
  if (!text) return false;
  if (text.length > STYLE_CORE_MAX_CHARS) return true;
  const low = "\n" + text.toLowerCase();
  return INSTRUCTION_MARKERS.some((m) => low.includes(m));
}

const VISUAL_RE =
  /график|контур|иконк|схем|палитр|фон|свет|компон|шрифт|линейн|вектор|hex|#|palette|outline|flat|vector|background|lighting|typography|contrast|gradient|shadow|frame|колонк|рамк|насыщен|освещен|диаграмм|стрелк|круг|слайд|презентац|плоск|геометр|заголов|типограф|line|fill|icon|grid/i;
const TOPIC_RE =
  /фитнес|плиометр|мышц|биомехан|спортивн|нервн\w*\s+систем|трениров|fitness|plyometr|workout|muscle|sport\b/i;

const STRONG_VISUAL_RE =
  /hex|#|палитр|фон\b|outline|контур|линейн|освещен|palette|background|lighting/i;

export function isTopicClause(clause: string): boolean {
  const c = (clause || "").trim();
  if (c.length < 6) return false;
  if (TOPIC_RE.test(c) && !STRONG_VISUAL_RE.test(c)) return true;
  if (!VISUAL_RE.test(c) && (c.match(/,/g) || []).length >= 2) return true;
  return false;
}

export function scrubStyleTopic(text: string): string {
  const kept = (text || "")
    .split(/[.;]\s+/)
    .map((c) => c.trim().replace(/^[.;]+|[.;]+$/g, ""))
    .filter((c) => c && !isTopicClause(c));
  return kept.join(". ").trim();
}

/** Ядро стиля из длинного агента (блок после «ядра:»), без инструкции и без темы референсов. */
export function extractAgentCore(agentText: string): string {
  const text = (agentText ?? "").trim();
  if (!text) return "";
  let raw = "";
  const marked = text.match(
    /(?:скопированного\s+ядра|ядра)[^\n]{0,80}:\s*\n+([\s\S]+?)(?:\n+После\s+ядра|\n+В\s+финальной|$)/i,
  );
  if (marked?.[1]) {
    const core = marked[1].replace(/\s+/g, " ").trim();
    if (core.length >= 40) raw = core.slice(0, 4000);
  }
  if (!raw && !isInstructionAgent(text) && !/^агент отвечает/i.test(text)) raw = text;
  if (!raw) {
    for (const para of text.split(/\n\s*\n/)) {
      const p = para.trim();
      if (p.length < 80) continue;
      if (/^(#|---|[\|])/.test(p) || /^агент отвечает/i.test(p)) continue;
      raw = p.replace(/\s+/g, " ").slice(0, 4000);
      break;
    }
  }
  return scrubStyleTopic(raw) || raw;
}

export function extractAgentNegatives(agentText: string): string {
  const m = (agentText ?? "").match(
    /(?:негативе\s+обязательно\s*:|NEGATIVE\s*\n)\s*([^\n]+)/i,
  );
  return (m?.[1] || "").trim();
}

/** Локально промпт не собираем — только ответ LLM. */
export function assembleGenPrompt(_opts: {
  request: string;
  agentText: string;
  aspect: string;
  refLabels?: string[];
}): string {
  return "";
}

export function assistantRefHandle(index: number): string {
  return `@image${index + 1}`;
}

/** Сырой запрос без ответа LLM — в генератор картинки слать нельзя. */
export function isUnfilledAssistantPrompt(prompt: string, request = ""): boolean {
  const p = prompt.trim();
  const req = request.trim();
  if (!p) return true;
  if (req) {
    const head = req.slice(0, 80).toLowerCase();
    if (head && p.toLowerCase().startsWith(head) && p.length <= req.length + 200) {
      return true;
    }
  }
  return false;
}

/** Текст варианта k из N: тот же промпт + указание варьировать ракурс. */
export function genPromptVariant(base: string, idx: number, total: number): string {
  if (total <= 1) return base;
  return `${base}\n\n-- Вариант ${idx + 1} из ${total}: то же содержание и стиль, другой ракурс и мелкие детали кадра.`;
}
