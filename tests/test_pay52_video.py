import pytest

from app.bots.pay52_video import (
    Pay52VideoError,
    build_pay52_video_payload,
    pay52_video_key_for_model,
)
from app.settings import Settings


def test_pay52_video_key_selection(monkeypatch) -> None:
    monkeypatch.setenv("SEEDANCE_MINI_KEY", "key-mini")
    monkeypatch.setenv("SEEDANCE_FAST_KEY", "key-fast")
    monkeypatch.setenv("SEEDANCE_OVERSEAS_KEY", "key-overseas")
    s = Settings()

    assert pay52_video_key_for_model("seedance-2.0-mini-ref", cfg=s) == "key-mini"
    assert pay52_video_key_for_model("seedance-2-0-mini", cfg=s) == "key-mini"
    assert pay52_video_key_for_model("Seedance 2.0 Fast 海外", cfg=s) == "key-fast"
    assert pay52_video_key_for_model("seedance-2-0-fast-overseas", cfg=s) == "key-fast"
    assert pay52_video_key_for_model("Seedance 2.0 海外", cfg=s) == "key-overseas"
    assert pay52_video_key_for_model("seedance-2-0-overseas", cfg=s) == "key-overseas"
    assert pay52_video_key_for_model("seedance-2.5", cfg=s) == "key-overseas"


def test_build_payload_seedance_mini_ref() -> None:
    spec = {"model": "seedance-2.0-mini-ref"}
    values = {
        "prompt": "Cinematic sunset",
        "seconds": 8,
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "first_frame_url": "https://img.example/start.png",
        "last_frame_url": "https://img.example/end.png",
        "reference_image_urls": ["https://img.example/ref1.png", "https://img.example/ref2.png"],
        "reference_video_urls": ["https://img.example/vid1.mp4"],
        "reference_audio_urls": ["https://img.example/aud1.mp3"],
    }

    payload = build_pay52_video_payload(spec, values)
    assert payload["model"] == "seedance-2.0-mini-ref"
    assert payload["prompt"] == "Cinematic sunset"
    assert payload["seconds"] == "8"
    assert payload["aspect_ratio"] == "16:9"
    assert payload["resolution"] == "720p"

    content = payload["metadata"]["content"]
    assert {"role": "first_frame", "url": "https://img.example/start.png"} in content
    assert {"role": "last_frame", "url": "https://img.example/end.png"} in content
    assert {"role": "reference_image", "url": "https://img.example/ref1.png"} in content
    assert {"role": "reference_video", "url": "https://img.example/vid1.mp4"} in content
    assert {"role": "reference_audio", "url": "https://img.example/aud1.mp3"} in content


def test_build_payload_seedance_mini_rejects_audio_alone() -> None:
    spec = {"model": "seedance-2.0-mini-ref"}
    values = {
        "prompt": "Talking head",
        "reference_audio_urls": ["https://img.example/aud1.mp3"],
    }
    with pytest.raises(Pay52VideoError, match="аудио не может передаваться без изображения"):
        build_pay52_video_payload(spec, values)


def test_build_payload_seedance_fast_overseas() -> None:
    spec = {"model": "Seedance 2.0 Fast 海外"}
    values = {
        "prompt": "Portrait of a smiling woman walking in Tokyo",
        "duration": 6,
        "ratio": "9:16",
        "resolution": "720p",
        "first_frame": "https://img.example/face1.png",
        "reference_images": ["https://img.example/face2.png"],
        "reference_videos": ["https://img.example/vid.mp4"],
        "reference_audios": ["https://img.example/aud.mp3"],
    }

    payload = build_pay52_video_payload(spec, values)
    assert payload["model"] == "Seedance 2.0 Fast 海外"
    assert payload["prompt"] == "Portrait of a smiling woman walking in Tokyo"
    assert payload["duration"] == 6
    assert payload["ratio"] == "9:16"
    assert payload["resolution"] == "720p"
    assert payload["referenceImages"] == [
        "https://img.example/face1.png",
        "https://img.example/face2.png",
    ]
    assert payload["referenceVideos"] == ["https://img.example/vid.mp4"]
    assert payload["referenceAudios"] == ["https://img.example/aud.mp3"]


def test_build_payload_seedance_overseas_1080p() -> None:
    spec = {"model": "Seedance 2.0 海外"}
    values = {
        "prompt": "Ultra HD nature scenery",
        "duration": 10,
        "ratio": "16:9",
        "resolution": "1080p",
    }
    payload = build_pay52_video_payload(spec, values)
    assert payload["model"] == "Seedance 2.0 海外"
    assert payload["duration"] == 10
    assert payload["resolution"] == "1080p"
