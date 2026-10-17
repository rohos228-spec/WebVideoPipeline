# -*- coding: utf-8 -*-
import json
import re
from pathlib import Path

root = Path(r"c:\Users\Admin\Desktop\video-pipeline")
run = root / r"data\videos\tkach-finalochka\node_trace\n_excel_gpt_fw_shots\20261013-142145-104919-1"
log = root / "data" / "studio-live.log"
out = root / "data" / "_batch.txt"

lines_out = []
raw = log.read_bytes()
text = re.sub(r"\x1b\[[0-9;]*m", "", raw[-4_000_000:].decode("utf-8", "replace"))
keep = []
for ln in text.splitlines():
    if "n_excel_gpt_fw_shots" not in ln and "db_frames_batch" not in ln:
        continue
    if "2026-10-13 14:" not in ln[:24]:
        continue
    # drop noisy prep lines
    if any(s in ln for s in (
        "pending=", "wave ", "пустой apply", "L1 fail", "L2 ", "L4 ",
        "got_ops", "coverage", "пропущена", "apply-ops JSON",
        "JSON retry", "всё ещё без", "ERROR", "Traceback",
        "finished", "NodeRun", "slot",
    )):
        keep.append(ln[:420])

lines_out.append(f"log_bytes={log.stat().st_size} hits={len(keep)}")
lines_out.extend(keep)

lines_out.append("\n=== CALLS ===")
for d in sorted(p for p in run.iterdir() if p.is_dir()):
    inp = d / "input.json"
    ops = d / "ops_final.json"
    n_frames = "?"
    ids = []
    if inp.exists():
        try:
            data = json.loads(inp.read_text(encoding="utf-8"))
        except Exception as e:
            data = {"_err": str(e)}
        if isinstance(data, list):
            frames = data
        else:
            frames = data.get("frames") or data.get("cells") or data.get("rows") or []
        if isinstance(frames, list):
            n_frames = len(frames)
            for fr in frames[:20]:
                if isinstance(fr, dict):
                    ids.append(str(fr.get("number") or fr.get("кадр") or fr.get("id") or "")[:12])
        else:
            n_frames = f"keys={list(data)[:8]}"
    n_ops = "?"
    if ops.exists():
        try:
            od = json.loads(ops.read_text(encoding="utf-8"))
        except Exception as e:
            od = {"_err": str(e)}
        if isinstance(od, dict):
            arr = od.get("ops") or od.get("operations") or []
            n_ops = len(arr) if isinstance(arr, list) else f"keys={list(od)[:6]}"
        elif isinstance(od, list):
            n_ops = len(od)
    lines_out.append(f"{d.name} frames={n_frames} ops_final={n_ops} nums={ids}")

out.write_text("\n".join(lines_out), encoding="utf-8")
print("wrote", len(keep))
