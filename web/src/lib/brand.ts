/**
 * Brand scrub for user-visible text (ТЗ п.22: never show any provider anywhere).
 * Mirrors app/brand.py. Use brandText() on any string that comes from catalogs/API
 * and is rendered to the user (labels, hints, descriptions, toasts, titles).
 * Do NOT use it on identifiers sent back to the API.
 *
 * The provider words themselves are stored shifted (+1 char code) and decoded at runtime,
 * so the shipped bundle never contains them in plain text. Neutral wire codes coming from
 * the API (zkq, zoq, … — see app/web/wire_neutral.py) are scrubbed to "VIA" as well.
 */
const d = (s: string): string =>
  Array.from(s, (ch) => String.fromCharCode(ch.charCodeAt(0) - 1)).join("");

// provider words, shifted by +1 (see header)
const W = {
  o: d("pvutff"),
  k: d("ljf"),
  p52: d("63qbz"),
  pay52: d("qbz63"),
  vibe: d("wjcf"),
  code: d("dpef"),
  wave: d("xbwftqffe"),
  eleven: d("fmfwfo"),
  labs: d("mbct"),
  an: d("bouispqjd"),
  oa: d("pqfobj"),
};

let cache: { rules: Array<[RegExp, string]>; any: RegExp; env: RegExp } | null = null;

function rules() {
  if (cache) return cache;
  const r: Array<[RegExp, string]> = [
    [new RegExp(`${W.o}\\.io`, "gi"), "VIA"],
    [new RegExp(`\\b(?:${W.o})\\b|оутси|аутси`, "gi"), "VIA Render"],
    [new RegExp(`\\b${W.k}(?:\\.ai)?\\b|\\bкие\\b`, "gi"), "VIA Render"],
    [new RegExp(`\\b52\\s?${W.p52.slice(2)}\\b|\\b${W.pay52}\\b`, "gi"), "VIA"],
    [new RegExp(`\\b${W.vibe}\\s?${W.code}(?:\\.moe)?\\b`, "gi"), "VIA"],
    [new RegExp(`\\b${W.wave}(?:\\.ai)?\\b`, "gi"), "VIA Render"],
    [new RegExp(`\\b${W.eleven}\\s?${W.labs}\\b|\\b11\\s?${W.labs}\\b`, "gi"), "Озвучка VIA"],
    [new RegExp(`\\b(?:${W.an}|${W.oa})\\b`, "gi"), "VIA"],
    [/\b(?:zkq|zoq|zvq|zwq|zpq|zaq|zgq)\b/gi, "VIA"],
    [/\bzeq\b/gi, "Озвучка VIA"],
  ];
  const any = new RegExp(
    `${W.o}|оутси|аутси|\\b${W.k}\\b|\\bкие\\b|52\\s?${W.p52.slice(2)}|${W.pay52}|${W.vibe}\\s?${W.code}|${W.wave}|${W.eleven}\\s?${W.labs}|11\\s?${W.labs}|${W.an}|${W.oa}|\\bz[kovewpag]q\\b`,
    "i",
  );
  const env = new RegExp(
    `\\b(?:${[W.o, W.k, W.pay52 + "(?:_[A-Z]+)*", W.vibe + W.code, W.wave, W.eleven + W.labs]
      .map((x) => x.toUpperCase().replace("(?:_[A-Z]+)*".toUpperCase(), "(?:_[A-Z]+)*"))
      .join("|")}|Z[KOVEWPAG]Q)_(API_KEY|TOKEN|KEY)\\b`,
    "g",
  );
  cache = { rules: r, any, env };
  return cache;
}

export function hasProvider(s: unknown): boolean {
  if (typeof s !== "string") return false;
  const { any, env } = rules();
  return any.test(s) || new RegExp(env.source).test(s);
}

export function brandText<T>(s: T): T {
  if (typeof s !== "string" || !s || !hasProvider(s)) return s;
  const { rules: r, env } = rules();
  let out: string = s.replace(env, "$1");
  for (const [rx, rep] of r) out = out.replace(rx, rep);
  out = out.replace(/VIA(?:\s*[·\-/]\s*VIA)+/g, "VIA").replace(/VIA Render Render/g, "VIA Render");
  return out as unknown as T;
}
