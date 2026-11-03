"""prompt_is_hollow: «нет/отсутствует» про предмет сцены — содержание, про входные данные — заглушка.

Детектор теперь решает и запись img_pr, и платный гейт картинок, поэтому ложное
«пусто» у здоровой сцены блокирует работу, а ложное «есть сцена» у заглушки — платит.
"""

from __future__ import annotations

import pytest

from app.services.action_layer.img_pr_guard import prompt_is_hollow

STYLE = " STYLE: Archival Noir Watercolor, dark wash, halftone dots."

HEALTHY = [
    "Пустой ночной перрон, людей нет." + STYLE,
    "Фон: заброшенный цех, окон нет. Действие: людей нет. Свет: электричества нет." + STYLE,
    "Свет: источник не указан, мягкий рассеянный." + STYLE,
    "Фон: пустыня, тень отсутствует, жара." + STYLE,
    "Фон: нет исходных данных о фоне, но лиса бежит по снегу." + STYLE,
    "Фон: нет исходных данных о фоне но лиса бежит по снегу." + STYLE,
]
HOLLOW = [
    "Фон: нет исходных данных, но сохранить нейтральный фон. "
    "Действие: нет исходных данных, однако оставить кадр пустым." + STYLE,
    "Фон: нет исходных данных, но общий стиль сохраняется. "
    "Действие: нет исходных данных, зато стиль единый." + STYLE,
    "Фон: нет исходных данных но сохранить нейтральный фон." + STYLE,
    "Фон: данных о фоне нет. Действие: информация о действии отсутствует." + STYLE,
    "Фон: описание локации не указано. Свет: не задан." + STYLE,
    "Нет исходных данных для заполнения." + STYLE,
]


@pytest.mark.parametrize("text", HEALTHY)
def test_absent_scene_object_is_content(text: str) -> None:
    assert prompt_is_hollow(text) is False


@pytest.mark.parametrize("text", HOLLOW)
def test_missing_source_data_is_hollow(text: str) -> None:
    assert prompt_is_hollow(text) is True


# --- таблица ревью: формы, которые раньше проходили в оплату, и сцены, которые должны остаться ---

LOCK = (
    " STYLE: Archival Noir Watercolor Grunge Dossier Poster Illustration, dark watercolor wash, "
    "distressed printmaking, halftone dots, charcoal shadow masses."
) * 4

# (текст, пустой ли промт). Каждая строка проверяется и без стиля, и со стилевым замком.
TABLE = [
    # пустые
    ("Недостаточно исходных данных для заполнения.", True),
    ("No source data available to fill this frame.", True),
    ("Insufficient input data for this frame.", True),
    ("Фон: —. Действие: —. Свет: —.", True),
    ("Фон: н/д. Действие: н/д. Персонажи: н/д.", True),
    ("Фон: неизвестно. Действие: неизвестно.", True),
    ("Фон: . Действие: .", True),
    ("Фон: . Действие: . Свет: .", True),
    ("", True),  # только стилевой замок
    ("Нет исходных данных для заполнения, но кадр должен быть атмосферным.", True),
    ("Нет исходных данных, но STYLE LOCK обязательный, акварель.", True),
    ("Background: not specified. Action: not specified.", True),
    ("Фон: не определено.", True),
    ("Исходные данные недоступны.", True),
    ("Фон: пусто.", True),
    ("Фон: ничего.", True),
    ("Фон: см. выше.", True),
    ("Фон: [фон]. Действие: [действие].", True),
    ("[фон], [описание действия], [свет]", True),
    ("Background: {background}. Action: <action>.", True),
    ("Lorem ipsum dolor sit amet, consectetur adipiscing elit.", True),
    ("Фон: lorem ipsum dolor sit amet. Действие: lorem ipsum.", True),
    # сцена
    ("Пустой ночной перрон, людей нет.", False),
    ("Фон: заброшенный цех, окон нет. Действие: людей нет. Свет: электричества нет.", False),
    ("Свет: источник не указан, мягкий рассеянный.", False),
    ("Фон: пустыня, тень отсутствует, жара.", False),
    ("нет исходных данных о фоне, но лиса бежит по снегу", False),
    ("Фон: —. Действие: лиса бежит по снегу.", False),
    ("Неизвестный путник идёт по ночной дороге.", False),
    ("No source data for the background, but a fox runs over the snow.", False),
    ("Background: empty platform, no people.", False),
    ("Фон: [заброшенный цех с ржавыми трубами]. Действие: кот спит.", False),
    ("Фон: вывеска <OPEN> над дверью кафе, дождь.", False),
    ("Фон: —. Тени длинные, снег искрится.", False),
    ("Фон: ничего лишнего, белая стена и стул.", False),
    ("Фон: пустой зал, эхо.", False),
]


def _case_id(row: tuple[str, bool]) -> str:
    return ("hollow:" if row[1] else "scene:") + (row[0][:40] or "<style only>")


@pytest.mark.parametrize("row", TABLE, ids=_case_id)
@pytest.mark.parametrize("with_style", [False, True], ids=["plain", "style"])
def test_review_table(row: tuple[str, bool], with_style: bool) -> None:
    from types import SimpleNamespace

    from app.generation_options import image_prompt_missing, is_no_source_data_prompt
    from app.orchestrator.steps.generate_images import _hollow_prompt_frames
    from app.services import img_pr_batches as ipb

    text, hollow = row
    if not text and not with_style:
        pytest.skip("пустая строка без стиля — не заглушка, а просто нет промта")
    prompt = (text + LOCK).strip() if with_style else text
    assert prompt_is_hollow(prompt) is hollow
    assert image_prompt_missing(prompt) is hollow
    assert is_no_source_data_prompt(prompt) is hollow
    # Платный гейт картинок: кадр с пустым промтом не идёт в генерацию.
    frame = SimpleNamespace(number=1, image_prompt=prompt, attrs={}, meta={}, status=None)
    assert _hollow_prompt_frames([frame]) == ([1] if hollow else [])
    if with_style:
        # Запись img_pr: пустой промт не пишется, сцена (длинная, со стилем) пишется.
        ops = [{"frame_uuid": "a" * 24, "fields": {"промт_картинки": prompt}}]
        assert bool(ipb.writeable_img_pr_ops(ops)) is (not hollow)
