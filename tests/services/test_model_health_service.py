from app.services import model_health as mh


def test_record_and_aggregate(tmp_path, monkeypatch):
    monkeypatch.setenv("VIA_MODEL_HEALTH_DB", str(tmp_path / "mh.db"))
    mh.record_run(model="kie/veo-3", ok=True, call_type="video", duration_sec=4)
    mh.record_run(model="veo-3", ok=True, call_type="video", duration_sec=6)
    mh.record_run(model="veo-3", ok=False, call_type="video", error="Outsee 500")
    data = mh.get_health(24)
    rec = next(m for m in data["models"] if m["model"] == "veo-3")
    assert rec["success"] == 2 and rec["errors"] == 1
    assert rec["kind"] == "video"
    assert rec["avg_latency_s"] == 5.0
    assert 0 <= rec["success_rate"] <= 1
    assert rec["series"] and rec["updated_at"]
    assert "outsee" not in str(data).lower() and "kie" not in str(data).lower()


def test_parse_window():
    assert mh.parse_window("24h") == 24
    assert mh.parse_window("7d") == 168
    assert mh.parse_window("bad") == 24