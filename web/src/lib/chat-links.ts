/** Ссылки в Markdown ответов чата оркестратора: какие адреса можно открыть и как их найти в тексте. */

// Отчёт проекта (/api/projects/5/reports/…) или общий отчёт по нескольким проектам (/api/projects/reports/…).
const REPORT_HREF = /^\/api\/projects\/(?:\d+\/)?reports\/[A-Za-z0-9][A-Za-z0-9_.-]*\.(xlsx|md|json|csv|html)$/;

// «[Скачать отчёт]( /api/…)»: модель иногда ставит пробел после скобки — ссылка всё равно ссылка.
export const MD_LINK = /\[([^\]\n]+)\]\(\s*([^()\s]+)\s*\)/;

export function safeHref(raw: string): string | null {
  const url = raw.trim();
  if (REPORT_HREF.test(url) && !url.includes("..")) return url;
  if (!/^https?:\/\/[^\s]+$/i.test(url)) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
  } catch {
    return null;
  }
}

/** Первая Markdown-ссылка в строке: подпись и безопасный адрес (null — показать подпись текстом). */
export function findMarkdownLink(
  text: string,
): { index: number; length: number; label: string; href: string | null } | null {
  const match = MD_LINK.exec(text);
  if (!match) return null;
  return { index: match.index, length: match[0].length, label: match[1], href: safeHref(match[2]) };
}
