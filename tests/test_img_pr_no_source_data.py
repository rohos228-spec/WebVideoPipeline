"""img_pr: ответ «Нет исходных данных для заполнения» — не промт и не повод дробить батч.

Живые данные _chat_test2 (2026-10-22 00:20–01:03 UTC):
- #14 кадры 5–8: image_prompt = «Нет исходных данных для заполнения. STYLE: …»
  (2482 симв. за счёт стиля) — шаг поставил image_prompts_ready;
- 381 завершённый вызов img_pr на #10/#12/#14: модель на кадры без
  place/фона/действия отвечала «Нет исходных данных для заполнения» (34 симв.),
  ответ отбрасывался как короткий, батч дробился 8→4→2→1 и перезапрашивался,
  затем шаг повторялся (attempt 2, soft retry ×9, авто-резюм).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.generation_options import image_prompt_missing, is_no_source_data_prompt
from app.services import img_pr_batches as ipb

STYLE = (
    "STYLE: Archival Noir Watercolor Grunge Dossier Poster Illustration. archival true-crime noir "
    "dossier poster + dark watercolor wash + grunge prison mystery illustration + distressed printmaking. "
) * 6
NO_DATA_WITH_STYLE = "Нет исходных данных для заполнения. " + STYLE
REAL = (
    "Фон: подземная камера муравьиного гнезда, тесные земляные стенки с влажными слоями почвы, "
    "извилистый ход уходит вглубь. Действие: рабочий муравей несёт личинку. " + STYLE
)


def test_no_data_prompt_with_style_counts_as_missing() -> None:
    assert is_no_source_data_prompt(NO_DATA_WITH_STYLE) is True
    assert image_prompt_missing(NO_DATA_WITH_STYLE) is True
    assert image_prompt_missing("[ID: 5]\n" + NO_DATA_WITH_STYLE) is True


def test_real_prompt_is_not_missing() -> None:
    assert is_no_source_data_prompt(REAL) is False
    assert image_prompt_missing(REAL) is False
    # одна оговорка внутри нормального промта — не отказ
    mixed = REAL.replace(
        "Действие:", "Свет: нет исходных данных для заполнения, мягкий рассеянный. Действие:"
    )
    assert image_prompt_missing(mixed) is False


def test_writeable_ops_drop_no_data_even_when_long() -> None:
    uid = "4dc49573dc344b139508a686"
    ops = [{"frame_uuid": uid, "fields": {"промт_картинки": NO_DATA_WITH_STYLE}}]
    assert ipb.writeable_img_pr_ops(ops) == []
    assert ipb.no_source_data_uuids(ops) == {uid}


@pytest.mark.asyncio
async def test_runner_does_not_split_or_reask_no_source_reply(tmp_path, monkeypatch) -> None:
    from app.services import chatgpt_xlsx as cx
    from app.services import xlsx_gpt_flow as xgf
    from app.services import xlsx_step_runners as xsr

    frames = [
        SimpleNamespace(number=n, uuid=f"{n:024x}", voiceover_text=f"vo {n}", image_prompt=None, attrs={})
        for n in range(9, 14)
    ]
    project = SimpleNamespace(id=14, slug="t", data_dir=tmp_path, meta={})
    prompt_file = tmp_path / "prompt_img_pr.txt"
    prompt_file.write_text("master", encoding="utf-8")
    calls: list[list[str]] = []

    class FakeGpt:
        async def new_conversation(self):
            return None

        async def ask_with_files(self, msg, attach, **_kw):
            uuids = [line.split(" = ")[1] for line in msg.splitlines() if " = " in line]
            calls.append(uuids)
            return json.dumps(
                {
                    "ops": [
                        {"frame_uuid": u, "fields": {"промт_картинки": "Нет исходных данных для заполнения"}}
                        for u in uuids
                    ]
                },
                ensure_ascii=False,
            )

    async def _ctx(_project, **_kw):
        return list(frames), [], "", list(frames)

    async def _under_lock(_pid, _name, fn):
        return await fn()

    def _db_frames(_p, tmp_dir, batch, *_a, batch_tag="", **_kw):
        out = Path(tmp_dir) / f"db_{batch_tag}.json"
        out.write_text("{}", encoding="utf-8")
        return out

    monkeypatch.setattr(xsr, "_ensure_project_xlsx", lambda p: tmp_path / "project.xlsx")
    monkeypatch.setattr(cx, "tmp_gpt_dir", lambda p: tmp_path)
    monkeypatch.setattr(cx, "write_img_pr_prompt_file", lambda p, d, ts=None: prompt_file)
    monkeypatch.setattr(cx, "ensure_current_voiceover", lambda p: None)
    monkeypatch.setattr(cx, "chat_message", lambda *a, **k: "img_pr\n")
    monkeypatch.setattr("app.services.img_pr_style.resolve_project_img_style", lambda *a, **k: None)
    monkeypatch.setattr(xsr, "_load_img_pr_context", _ctx)
    monkeypatch.setattr(xsr, "_write_img_pr_db_frames_for", _db_frames)
    monkeypatch.setattr(xsr, "img_pr_live_streams", lambda p: 8)
    monkeypatch.setattr("app.services.gpt_client.ApiGptClient", FakeGpt)
    monkeypatch.setattr(xgf, "run_under_xlsx_lock", _under_lock)

    with pytest.raises(ipb.ImgPrNoSourceData) as err:
        await xsr.run_img_pr_xlsx(project, n_frames=5, project_id=14)
    assert len(calls) == 1, f"ожидался 1 вызов модели, было {len(calls)}"
    assert err.value.frame_numbers == [9, 10, 11, 12, 13]
    assert "нет данных сцены" in str(err.value)
