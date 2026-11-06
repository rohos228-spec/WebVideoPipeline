/**
 * Преобразует URL изображения в URL WebP-миниатюры (320px) для карточек на канвасе и списков превью.
 *
 * Сервер отдаёт кэшированную WebP-копию 320px с заголовком Cache-Control: immutable,
 * экономя трафик и ускоряя отрисовку канваса в сотни раз (9.5 КБ вместо 5 МБ).
 *
 * Фулл-вью модалок, увеличение на весь экран и ссылки "Скачать"/"Оригинал"
 * этот хелпер НЕ используют, чтобы отдавать полноразмерный оригинальный файл.
 */
export function toThumbnailUrl(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  if (url.includes("thumb=1")) return url;

  // Видео, аудио и документы не миниатюризируем через этот путь
  if (/\.(mp4|webm|mov|mp3|wav|ogg|m4a|xlsx|csv)(\?|$)/i.test(url)) {
    return url;
  }

  // /api/artifacts/.../file
  if (url.includes("/api/artifacts/") && url.includes("/file")) {
    return url.includes("?") ? `${url}&thumb=1` : `${url}?thumb=1`;
  }

  // /api/artifacts/project-cover/...
  if (url.includes("/api/artifacts/project-cover/")) {
    return url.includes("?") ? `${url}&thumb=1` : `${url}?thumb=1`;
  }

  // /api/files?
  if (url.includes("/api/files?")) {
    return `${url}&thumb=1`;
  }

  return url;
}
