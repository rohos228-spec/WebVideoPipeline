"""Скрипт парсинга и сборки каталога 200 русских голосов ElevenLabs.

Сканирует MP3 файлы из Downloads/Telegram Desktop (ru_voices_female и ru_voices_male),
копирует их в data/voices/samples/{voice_id}.mp3,
создаёт JSON-каталог:
- app/services/ru_voices_catalog.json
- web/src/lib/ru-voices.json
"""

import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FEMALE_DIR = Path(r"C:\Users\EternalFlow\Downloads\Telegram Desktop\ru_voices_female\ru_voices_female\mp3\female")
MALE_DIR = Path(r"C:\Users\EternalFlow\Downloads\Telegram Desktop\ru_voices_male\ru_voices_male\mp3\male")

DATA_SAMPLES_DIR = ROOT / "data" / "voices" / "samples"
DATA_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

PATTERN = re.compile(r"^(\d+)_(.*)_([a-zA-Z0-9]{20})\.mp3$")


def parse_filename(filename: str, gender: str) -> dict:
    m = PATTERN.match(filename)
    if not m:
        raise ValueError(f"Filename does not match pattern: {filename}")
    num_str, middle, voice_id = m.groups()
    sep_match = re.search(r"_*-_*|__+", middle)
    if sep_match:
        name_raw = middle[: sep_match.start()]
        desc_raw = middle[sep_match.end() :]
    else:
        name_raw = middle
        desc_raw = ""
    name = re.sub(r"_+", " ", name_raw).strip()
    desc = re.sub(r"_+", " ", desc_raw).strip()
    return {
        "id": voice_id,
        "index": int(num_str),
        "name": name,
        "description": desc,
        "gender": gender,
        "filename": filename,
        "sample_url": f"/api/voices/{voice_id}/sample",
    }


def main():
    print(f"Scanning female voices: {FEMALE_DIR}")
    female_files = sorted(FEMALE_DIR.glob("*.mp3"))
    print(f"Found {len(female_files)} female files")

    print(f"Scanning male voices: {MALE_DIR}")
    male_files = sorted(MALE_DIR.glob("*.mp3"))
    print(f"Found {len(male_files)} male files")

    voices = []

    # Process females
    for f in female_files:
        info = parse_filename(f.name, "female")
        dst = DATA_SAMPLES_DIR / f"{info['id']}.mp3"
        if not dst.exists() or dst.stat().st_size != f.stat().st_size:
            shutil.copy2(f, dst)
        voices.append(info)

    # Process males
    for f in male_files:
        info = parse_filename(f.name, "male")
        dst = DATA_SAMPLES_DIR / f"{info['id']}.mp3"
        if not dst.exists() or dst.stat().st_size != f.stat().st_size:
            shutil.copy2(f, dst)
        voices.append(info)

    print(f"Total voices processed: {len(voices)}")
    unique_ids = {v["id"] for v in voices}
    print(f"Unique voice IDs: {len(unique_ids)}")
    assert len(voices) == len(unique_ids) == 200, "Must be exactly 200 unique voices"

    # Save to data/voices/catalog.json
    data_catalog_path = ROOT / "data" / "voices" / "catalog.json"
    with open(data_catalog_path, "w", encoding="utf-8") as fp:
        json.dump(voices, fp, ensure_ascii=False, indent=2)
    print(f"Saved: {data_catalog_path}")

    # Save to app/services/ru_voices_catalog.json
    backend_catalog_path = ROOT / "app" / "services" / "ru_voices_catalog.json"
    with open(backend_catalog_path, "w", encoding="utf-8") as fp:
        json.dump(voices, fp, ensure_ascii=False, indent=2)
    print(f"Saved: {backend_catalog_path}")

    # Save to web/src/lib/ru-voices.json
    frontend_catalog_dir = ROOT / "web" / "src" / "lib"
    frontend_catalog_dir.mkdir(parents=True, exist_ok=True)
    frontend_catalog_path = frontend_catalog_dir / "ru-voices.json"
    with open(frontend_catalog_path, "w", encoding="utf-8") as fp:
        json.dump(voices, fp, ensure_ascii=False, indent=2)
    print(f"Saved: {frontend_catalog_path}")

    print("Successfully built catalog and copied samples!")


if __name__ == "__main__":
    main()
