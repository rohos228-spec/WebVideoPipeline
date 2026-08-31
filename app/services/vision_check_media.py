"""Упаковка картинок для vision-проверки: ленты вместо россыпи полноразмерных PNG.

**Зачем.** Проверочная нода (`checkMode`) прикладывала кадры как есть. Кадр
2K весит ~3 МБ, и запрос с ними не проходил вовсе: живой прогон 2026-08-31
получил `HTTP 413` (`anthropic.RequestTooLargeError`) и на 33 кадрах, и на
восьми — то есть контур vision-проверки кадров на ролике штатной длины не
запускался ни разу. Три отказа подряд уводили проект в получасовую паузу.

**Почему лента, а не «слать по одной».** Тот же приём в проекте уже есть и
работает: `animation_prompt_gpt` собирает кадры горизонтальной лентой, а
`video_sheet` — сеткой 3×2 из стиллов. Лента сразу решает три вещи: вес
(высота 768 px и потолок в байтах), число вложений (один файл вместо
десятков) и путаницу «какая панель какой кадр» — на каждую панель
`compose_horizontal_strip` выжигает подпись с именем файла.

**Что НЕ делает.** Не трогает исходные PNG на диске и не участвует в сборке
блока «База (source of truth)»: та строится пофреймово по именам файлов, и
подменять пути до неё нельзя (проверено — иначе база схлопывается в «нет PNG
во входе»). Пакуем строго после снапшота.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

#: Сколько кадров в одной ленте. Шесть при высоте 768 px дают картинку,
#: на которой ещё виден и общий план, и лица; больше — панели мельчают,
#: и vision начинает путать соседние.
PANELS_PER_STRIP = 6

#: Порог, ниже которого паковать незачем: одна-две картинки проходят как есть
#: и в полном разрешении их разглядеть проще.
PACK_FROM = 3

#: Потолок веса одной ленты. `compose_horizontal_strip` при превышении сам
#: уходит в JPEG с понижением — 3.5 МБ выбраны им же под лимит vision ≤4 МБ.
STRIP_MAX_BYTES = 3_500_000

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_SUFFIXES


def pack_images_for_vision(paths: list[Path], out_dir: Path) -> list[Path]:
    """Картинки → ленты по ``PANELS_PER_STRIP``; не-картинки проходят как есть.

    Возвращает исходный список без изменений, если паковать нечего или если
    склейка не удалась: проверка на уменьшенных картинках лучше, чем отказ,
    но отказ лучше, чем потерянные файлы.
    """
    images = [p for p in paths if _is_image(p) and p.is_file()]
    others = [p for p in paths if p not in images]
    if len(images) < PACK_FROM:
        return list(paths)

    from app.services.image_strip import compose_horizontal_strip

    out_dir.mkdir(parents=True, exist_ok=True)
    strips: list[Path] = []
    for i in range(0, len(images), PANELS_PER_STRIP):
        chunk = images[i : i + PANELS_PER_STRIP]
        target = out_dir / f"vision_strip_{i // PANELS_PER_STRIP + 1:02d}.png"
        try:
            strips.append(
                compose_horizontal_strip(
                    chunk,
                    target,
                    max_bytes=STRIP_MAX_BYTES,
                    panel_labels=[p.stem for p in chunk],
                )
            )
        except Exception:  # noqa: BLE001 — склейка не должна ронять проверку
            logger.exception("pack_images_for_vision: лента {} не собралась", target.name)
            return list(paths)
    return [*others, *strips]
