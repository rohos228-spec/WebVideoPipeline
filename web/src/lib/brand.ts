/**
 * Brand scrub for user-visible text (ТЗ п.22: never show any provider anywhere).
 * Mirrors app/brand.py. Use brandText() on any string that comes from catalogs/API
 * and is rendered to the user (labels, hints, descriptions, toasts, titles).
 * Do NOT use it on identifiers sent back to the API.
 */
const RULES: Array<[RegExp, string]> = [
  [/outsee\.io/gi, "VIA"],
  [/\b(?:outsee)\b|оутси|аутси/gi, "VIA Render"],
  [/\bkie(?:\.ai)?\b|\bкие\b/gi, "VIA Render"],
  [/\b52\s?pay\b|\bpay52\b/gi, "VIA"],
  [/\bvibe\s?code(?:\.moe)?\b/gi, "VIA"],
  [/\bwavespeed(?:\.ai)?\b/gi, "VIA Render"],
  [/\beleven\s?labs\b|\b11\s?labs\b/gi, "Озвучка VIA"],
  [/\b(?:anthropic|openai)\b/gi, "VIA"],
];
const ANY =
  /outsee|оутси|аутси|\bkie\b|\bкие\b|52\s?pay|pay52|vibe\s?code|wavespeed|eleven\s?labs|11\s?labs|anthropic|openai/i;
const ENV_RX = /\b(?:OUTSEE|KIE|PAY52(?:_[A-Z]+)*|VIBECODE|WAVESPEED|ELEVENLABS)_(API_KEY|TOKEN|KEY)\b/g;

export function hasProvider(s: unknown): boolean {
  return typeof s === "string" && (ANY.test(s) || new RegExp(ENV_RX.source).test(s));
}

export function brandText<T>(s: T): T {
  if (typeof s !== "string" || !s || !hasProvider(s)) return s;
  let out: string = s.replace(ENV_RX, "$1");
  for (const [rx, rep] of RULES) out = out.replace(rx, rep);
  out = out.replace(/VIA(?:\s*[·\-/]\s*VIA)+/g, "VIA").replace(/VIA Render Render/g, "VIA Render");
  return out as unknown as T;
}