"""Дробление VO-ячейки на шоты для группы script_frames_qc."""

from app.services.scene_design.camera_expand import split_text_into_parts
from app.services.vo_shot_expand import shots_needed_for_vo, vo_duration_sec


def test_apply_shot_meta_keeps_scene_chain() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import _apply_shot_meta, looks_like_scene_chain

    chain = (
        "1. следственный отдел — отдел целиком\n"
        "(Сергей Ткач парадокс)\n"
        "2. комната Ткача — листает инструкции\n"
        "(которые он прекрасно знал изнутри.)"
    )
    assert looks_like_scene_chain(chain)
    fr = SimpleNamespace(attrs={"main_action": chain})
    _apply_shot_meta(
        fr,
        {"место": "следственный отдел", "действие": "отдел целиком", "план": "ОБЩИЙ"},
    )
    assert fr.attrs["main_action"] == chain
    assert fr.attrs["shot01_action"] == "отдел целиком"
    assert fr.attrs["place"] == "следственный отдел"


def test_apply_shot_meta_sets_action_when_empty() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import _apply_shot_meta

    fr = SimpleNamespace(attrs={})
    _apply_shot_meta(fr, {"действие": "у доски сверяют папки"})
    assert fr.attrs["main_action"] == "у доски сверяют папки"
    assert fr.attrs["shot01_action"] == "у доски сверяют папки"


def test_shots_needed_splits_sentences() -> None:
    text = "Первая фраза тут. Вторая фраза здесь. Третья уже конец."
    assert shots_needed_for_vo(text) == 3


def test_shots_needed_two_when_one_long_clause() -> None:
    text = "История Дарьи Салтыковой началась не с громкого судебного процесса"
    assert shots_needed_for_vo(text) == 2


def test_shots_needed_one_when_short() -> None:
    assert shots_needed_for_vo("Коротко.") == 1


def test_vo_parts_sum_equals_cell() -> None:
    text = "Дарья Салтыкова История началась не с процесса, а с двух жалоб крестьян."
    n = shots_needed_for_vo(text)
    parts = split_text_into_parts(text, n)
    assert " ".join(parts).split() == text.split()


def test_vo_duration_at_least_two_sec_per_shot() -> None:
    assert vo_duration_sec("аб", shots=2) >= 4.0


def test_resolve_shot_plan_uses_kadrы() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import (
        planned_shots_from_attrs,
        resolve_shot_plan,
    )

    vo = "Ткач родился в Киселёвске, прошёл службу в армии, после чего начал карьеру в милиции."
    planned = [
        {"порядок": 1, "закадр": "Ткач родился в Киселёвске,", "план": "ОБЩИЙ"},
        {"порядок": 2, "закадр": "прошёл службу в армии,", "план": "СРЕДНИЙ"},
        {
            "порядок": 3,
            "закадр": "после чего начал карьеру в милиции.",
            "план": "СРЕДНИЙ",
        },
    ]
    fr = SimpleNamespace(attrs={"кадры": planned})
    assert planned_shots_from_attrs(fr) == planned
    need, parts = resolve_shot_plan(vo, planned)
    assert need == 3
    assert parts == [p["закадр"] for p in planned]
    assert "".join(parts).replace(" ", "") == vo.replace(" ", "")


def test_resolve_shot_plan_falls_back_without_kadrы() -> None:
    from app.services.vo_shot_expand import resolve_shot_plan

    vo = "Первая фраза тут. Вторая фраза здесь."
    need, parts = resolve_shot_plan(vo, [])
    # Без кадры[] не выдумываем нарезку: 1 слот со всем текстом.
    assert need == 1
    assert parts == [vo]


def test_repair_glues_fragments_back_to_parent() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import repair_split_vo_on_parents

    pu = "aa" * 12
    parent = SimpleNamespace(
        uuid=pu,
        voiceover_text="Первая фраза.",
        attrs={
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": pu,
                "shot_index": 1,
            }
        },
    )
    child = SimpleNamespace(
        uuid="bb" * 12,
        voiceover_text="Вторая фраза.",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": pu,
                "shot_index": 2,
            }
        },
    )
    n = repair_split_vo_on_parents([parent, child])
    assert n == 1
    assert parent.voiceover_text == "Первая фраза. Вторая фраза."
    assert child.voiceover_text == ""


def test_cut_duplicate_leaves_split_vo_intact() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import apply_vo_shot_cuts

    pu = "aa" * 12
    cell = "Первая фраза. Вторая фраза."
    parent = SimpleNamespace(
        uuid=pu,
        voiceover_text=cell,
        attrs={
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": pu,
                "shot_index": 1,
            }
        },
    )
    child = SimpleNamespace(
        uuid="bb" * 12,
        voiceover_text="",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": pu,
                "shot_index": 2,
            }
        },
    )
    n = apply_vo_shot_cuts([parent, child])
    assert n == 2
    assert parent.voiceover_text == cell
    assert child.voiceover_text == ""
    p_shot = parent.attrs["camera_subdivide"]["vo_shot"]
    c_shot = child.attrs["camera_subdivide"]["vo_shot"]
    assert p_shot
    assert c_shot
    assert p_shot != cell
    assert " ".join((p_shot + " " + c_shot).split()) == " ".join(cell.split())


def test_apply_shot_voiceover_writes_cell_parts() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import apply_shot_voiceover_to_cells

    pu = "aa" * 12
    full = "Дарья Салтыкова. История началась не с процесса, а с двух жалоб."
    parent = SimpleNamespace(
        uuid=pu,
        voiceover_text=full,
        attrs={
            "кадры": [
                {"id": "1-K1", "закадр": "Дарья Салтыкова. "},
                {
                    "id": "1-K2",
                    "закадр": "История началась не с процесса, а с двух жалоб.",
                },
            ],
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": pu,
                "shot_index": 1,
            },
        },
    )
    child = SimpleNamespace(
        uuid="bb" * 12,
        voiceover_text="",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": pu,
                "shot_index": 2,
            }
        },
    )
    n = apply_shot_voiceover_to_cells([parent, child])
    assert n == 3  # кадры.закадр без хвостового пробела + 2 voiceover_text
    assert parent.voiceover_text == "Дарья Салтыкова."
    assert child.voiceover_text.startswith("История началась")
    assert parent.attrs["vo_cell_full"] == full


def test_distribute_coverage_prompts_fills_child_shot2() -> None:
    from types import SimpleNamespace

    from app.services.plan_shot2 import SHOT2_PROMPT_ATTR, SHOT2_STATUS_ATTR
    from app.services.vo_shot_expand import distribute_coverage_prompts

    pu = "aa" * 12
    parent = SimpleNamespace(
        uuid=pu,
        image_prompt="master prompt",
        animation_prompt="",
        attrs={
            "image_prompt_shot2": "child closer",
            "промты_детей": [{"промт_картинки": "child closer", "промт_видео": "child move"}],
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": pu,
                "shot_index": 1,
            },
        },
    )
    child = SimpleNamespace(
        uuid="bb" * 12,
        image_prompt="SHOULD CLEAR",
        animation_prompt="",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": pu,
                "shot_index": 2,
            }
        },
    )
    n = distribute_coverage_prompts([parent, child])
    assert n == 1
    assert child.image_prompt == ""
    assert child.attrs[SHOT2_PROMPT_ATTR] == "child closer"
    assert child.attrs[SHOT2_STATUS_ATTR] == "image_prompt_ready"
    assert child.animation_prompt == "child move"
    assert parent.attrs[SHOT2_STATUS_ATTR] == "skipped"


def _kadry_frame(number: int, shot_id: str, **extra):
    from types import SimpleNamespace

    return SimpleNamespace(
        number=number,
        uuid=f"u{number:02d}" * 8,
        image_prompt=extra.pop("image_prompt", ""),
        attrs={
            "кадры": [{"id": shot_id, "порядок": 1}],
            "place": extra.get("place", "канцелярия"),
            "shot01_bg": extra.get("shot01_bg", "стол и шкаф"),
            "camera_subdivide": {"role": "vo_parent", "shot_id": shot_id},
        },
    )


def test_inherit_camera_copies_move_set_to_child() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import inherit_camera_on_children

    pu = "p" * 24
    parent = SimpleNamespace(
        uuid=pu,
        attrs={
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": pu,
                "крупность": "ОБЩИЙ",
                "движение": "панорама",
                "набор": "господский дом",
            }
        },
    )
    child = SimpleNamespace(
        uuid="c" * 24,
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": pu,
                "крупность": "ДЕТАЛЬ",
            }
        },
    )
    assert inherit_camera_on_children([parent, child]) == 1
    cs = child.attrs["camera_subdivide"]
    assert cs["движение"] == "панорама"
    assert cs["набор"] == "господский дом"


def test_inherit_camera_orphan_uses_place_as_set() -> None:
    """Родитель удалён — набор из место, движение не оставляем пустым."""
    from types import SimpleNamespace

    from app.services.vo_shot_expand import inherit_camera_on_children

    child = SimpleNamespace(
        uuid="c" * 24,
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": "d" * 24,
                "крупность": "ДЕТАЛЬ",
                "место": "господский дом",
            }
        },
    )
    assert inherit_camera_on_children([child]) == 1
    cs = child.attrs["camera_subdivide"]
    assert cs["набор"] == "господский дом"
    assert str(cs.get("движение") or "").strip()


def test_kadry_from_bits_covers_full_vo() -> None:
    from app.services.vo_shot_expand import kadry_from_bits

    vo = (
        "Сергей Ткач Самый страшный парадокс этой истории заключается в том, "
        "что Сергея Ткача искали по тем же правилам. "
        "Для следствия он оставался неизвестным преступником. "
        "Ткач родился в Киселёвске."
    )
    bits = [
        {"порядок": 1, "глагол": "раскрывает", "якорь": "искали по тем же правилам"},
        {
            "порядок": 2,
            "глагол": "противопоставляет",
            "якорь": "оставался неизвестным преступником",
        },
        {"порядок": 3, "глагол": "рождается", "якорь": "Ткач родился в Киселёвске"},
    ]
    planned = kadry_from_bits(vo, bits)
    assert len(planned) == 3
    joined = " ".join(item["закадр"] for item in planned)
    assert joined.split() == vo.split()
    assert planned[0]["id"] == "B01-K1"
    assert planned[1]["id"] == "B02-K1"
    assert planned[1]["parent_id"] is None


def test_bit_kadry_do_not_collapse_as_coverage() -> None:
    from app.services.vo_shot_expand import (
        flattened_coverage_groups,
        kadry_from_bits,
    )

    vo = "Первая фраза целиком. Вторая тоже на месте. Третья закрывает."
    bits = [
        {"порядок": 1, "якорь": "Первая фраза"},
        {"порядок": 2, "якорь": "Вторая тоже"},
        {"порядок": 3, "якорь": "Третья закрывает"},
    ]
    planned = kadry_from_bits(vo, bits)
    frames = [_kadry_frame(i + 1, planned[i]["id"]) for i in range(3)]
    for fr, shot in zip(frames, planned, strict=True):
        fr.voiceover_text = shot["закадр"]
        fr.attrs["кадры"][0]["закадр"] = shot["закадр"]
    assert flattened_coverage_groups(frames) == {}


def test_kadry_partition_rejects_dangling() -> None:
    from app.services.vo_shot_expand import kadry_vo_partition

    full = "Его записывали как «душу», продавали вместе с землёй."
    assert (
        kadry_vo_partition(
            full,
            [
                {"закадр": "Его записывали как «душу», продавали вместе с"},
                {"закадр": "землёй."},
            ],
        )
        is None
    )


def test_kadry_partition_requires_full_cover() -> None:
    from app.services.vo_shot_expand import kadry_vo_partition

    full = "Первая фраза целиком. Вторая тоже на месте."
    ok = kadry_vo_partition(
        full,
        [
            {"закадр": "Первая фраза целиком."},
            {"закадр": "Вторая тоже на месте."},
        ],
    )
    assert ok == ["Первая фраза целиком.", "Вторая тоже на месте."]
    assert kadry_vo_partition(full, [{"закадр": "Первая фраза целиком."}]) is None


def test_apply_shot_voiceover_uses_clauses_not_word_split() -> None:
    from types import SimpleNamespace

    from app.services.scene_design.camera_expand import vo_chunk_is_dangling
    from app.services.vo_shot_expand import apply_shot_voiceover_to_cells

    pu = "cc" * 12
    full = "Его записывали как «душу», продавали вместе с землёй, переселяли."
    kadry = [
        {"id": "1-K1", "закадр": "Его записывали как «душу», продавали вместе с"},
        {"id": "1-K2", "закадр": "землёй, переселяли."},
    ]
    parent = SimpleNamespace(
        uuid=pu,
        voiceover_text=full,
        attrs={
            "кадры": kadry,
            "vo_cell_full": full,
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": pu,
                "shot_index": 1,
            },
        },
    )
    child = SimpleNamespace(
        uuid="dd" * 12,
        voiceover_text="",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": pu,
                "shot_index": 2,
            }
        },
    )
    apply_shot_voiceover_to_cells([parent, child])
    assert not vo_chunk_is_dangling(parent.voiceover_text)
    assert not vo_chunk_is_dangling(child.voiceover_text)
    assert " ".join((parent.voiceover_text + " " + child.voiceover_text).split()) == " ".join(full.split())


def test_coverage_child_detects_flattened_k2() -> None:
    from app.services.vo_shot_expand import (
        coverage_parent_shot_id,
        find_coverage_parent_frame,
        is_coverage_child,
        merge_parent_scene_refs,
        with_parent_scene_lock,
    )

    k1 = _kadry_frame(1, "1-K1")
    k2 = _kadry_frame(2, "1-K2")
    k3 = _kadry_frame(8, "4-K3")
    k1b = _kadry_frame(6, "4-K1")
    assert is_coverage_child(k2)
    assert is_coverage_child(k3)
    assert not is_coverage_child(k1)
    assert coverage_parent_shot_id(k2) == "1-K1"
    assert coverage_parent_shot_id(k3) == "4-K1"
    assert find_coverage_parent_frame([k1, k2], k2) is k1
    assert find_coverage_parent_frame([k1b, k3], k3) is k1b
    refs = merge_parent_scene_refs("parent.png", ["c01.png", "c02.png"], max_refs=2)
    assert refs == ["parent.png", "c01.png"]
    locked = with_parent_scene_lock("сцена", has_parent_ref=True, has_char_ref=True)
    assert locked.startswith("Image 1 is the previous coverage still")
    assert "Image 2 is the character sheet" in locked
    assert "same body count" in locked or "do not invent extra" in locked
    assert "painting already in Image 1" in locked
    assert locked.endswith("сцена")


def test_merge_flattened_coverage_rebuilds_kadry() -> None:
    from app.services.vo_shot_expand import (
        flattened_coverage_groups,
        merge_flattened_coverage_members,
    )

    k1 = _kadry_frame(1, "5-K1")
    k1.voiceover_text = "двор целиком, героиня у крыльца."
    k1.attrs["кадры"][0]["место"] = "двор усадьбы"
    k1.attrs["кадры"][0]["действие"] = "героиня стоит у крыльца"
    k2 = _kadry_frame(2, "5-K2")
    k2.voiceover_text = "жест рук, две бумаги жалоб."
    k2.attrs["кадры"][0]["место"] = "музейный зал"
    k2.attrs["кадры"][0]["действие"] = "посетитель у портрета"
    k2.attrs["camera_subdivide"]["parent_uuid"] = k2.uuid
    k1.attrs["camera_subdivide"]["parent_uuid"] = k1.uuid
    groups = flattened_coverage_groups([k1, k2])
    assert list(groups) == ["5"]
    parent, extra = merge_flattened_coverage_members(groups["5"])
    assert parent is k1
    assert extra == [k2]
    kadry = parent.attrs["кадры"]
    assert len(kadry) == 2
    assert kadry[0]["id"] == "5-K1"
    assert kadry[0]["parent_id"] is None
    assert kadry[1]["id"] == "5-K2"
    assert kadry[1]["parent_id"] == "5-K1"
    assert "двор целиком" in parent.voiceover_text
    assert "жест рук" in parent.voiceover_text
    assert kadry[1]["закадр"] == "жест рук, две бумаги жалоб."


def test_distribute_coverage_prompts_all_children_from_list() -> None:
    """K2 и K3 равноправны: каждый берёт свой элемент промты_детей, не _2."""
    from types import SimpleNamespace

    from app.services.plan_shot2 import SHOT2_PROMPT_ATTR
    from app.services.vo_shot_expand import distribute_coverage_prompts

    pu = "aa" * 12
    parent = SimpleNamespace(
        uuid=pu,
        image_prompt="master",
        animation_prompt="",
        attrs={
            "промты_детей": [
                {"промт_картинки": "k2 img", "промт_видео": "k2 vid"},
                {"промт_картинки": "k3 img", "промт_видео": "k3 vid"},
            ],
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": pu,
                "shot_index": 1,
            },
        },
    )
    child2 = SimpleNamespace(
        uuid="bb" * 12,
        image_prompt="",
        animation_prompt="",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": pu,
                "shot_index": 2,
            }
        },
    )
    child3 = SimpleNamespace(
        uuid="cc" * 12,
        image_prompt="",
        animation_prompt="",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": pu,
                "shot_index": 3,
            }
        },
    )
    n = distribute_coverage_prompts([parent, child2, child3])
    assert n == 2
    assert child2.attrs[SHOT2_PROMPT_ATTR] == "k2 img"
    assert child2.animation_prompt == "k2 vid"
    assert child3.attrs[SHOT2_PROMPT_ATTR] == "k3 img"
    assert child3.animation_prompt == "k3 vid"


def test_shot_child_locks_to_vo_parent_not_cross_scene_id() -> None:
    """K2/K3 ячейки 5-* не берут PNG чужого 2-K1 из coverage_parent_id."""
    from types import SimpleNamespace

    from app.services.vo_shot_expand import find_coverage_parent_frame

    k1 = SimpleNamespace(
        number=13,
        uuid="parent-5k1",
        attrs={
            "кадры": [{"id": "5-K1"}],
            "camera_subdivide": {
                "role": "vo_parent",
                "shot_id": "5-K1",
                "parent_uuid": "parent-5k1",
            },
        },
    )
    wrong = SimpleNamespace(
        number=4,
        uuid="other-2k1",
        attrs={
            "кадры": [{"id": "2-K1"}],
            "camera_subdivide": {"role": "vo_parent", "shot_id": "2-K1"},
        },
    )
    k4 = SimpleNamespace(
        number=16,
        uuid="child-5k4",
        attrs={
            "кадры": [{"id": "5-K4", "parent_id": "2-K1"}],
            "camera_subdivide": {
                "role": "shot",
                "shot_id": "5-K4",
                "parent_uuid": "parent-5k1",
                "coverage_parent_id": "2-K1",
                "scene_split": 1,
            },
        },
    )
    assert find_coverage_parent_frame([wrong, k1, k4], k4) is k1


def test_vo_parent_with_x1_parent_id_is_not_shot_child() -> None:
    """K1 новой ячейки с coverage_parent_id — не дочка: PNG-lock не вешаем."""
    from types import SimpleNamespace

    from app.services.vo_shot_expand import is_coverage_child, is_shot_child

    k1 = SimpleNamespace(
        number=13,
        uuid="u13",
        attrs={
            "кадры": [{"id": "5-K1", "parent_id": "2-K1"}],
            "camera_subdivide": {
                "role": "vo_parent",
                "shot_id": "5-K1",
                "coverage_parent_id": "2-K1",
                "scene_split": 1,
            },
        },
    )
    assert is_coverage_child(k1) is True
    assert is_shot_child(k1) is False
