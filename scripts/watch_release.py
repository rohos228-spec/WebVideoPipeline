#!/usr/bin/env python3
"""Следить за GitHub Release по sha коммита, без gh CLI.

    python3 scripts/watch_release.py <sha>

Токен — из git credential helper (тот же, что у push). Печатает id прогона,
смену статусов джобов и RUN_DONE <conclusion>. Запускать из корня репо;
из сессии агента — отвязанным процессом или через Monitor: фоновые задачи
харнесса гибнут посреди долгих прогонов.
"""

import json
import subprocess
import sys
import time
import urllib.request

sha = sys.argv[1]
tok = subprocess.run(
    ["git", "credential", "fill"], input="protocol=https\nhost=github.com\n", capture_output=True, text=True
).stdout
tok = [ln.split("=", 1)[1] for ln in tok.splitlines() if ln.startswith("password=")][0]
H = {"Authorization": "Bearer " + tok, "User-Agent": "vp-watch"}


def get(u):
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=30) as r:
            return json.load(r)
    except Exception as e:
        return {"error": str(e)}


run = None
prev = ""
for _attempt in range(60):
    if run is None:
        d = get(
            f"https://api.github.com/repos/multikco/video-pipeline/actions/runs?head_sha={sha}&per_page=5"
        )
        runs = [r for r in d.get("workflow_runs", []) if r["name"] == "Release"]
        if runs:
            run = runs[0]["id"]
            print("run", run, runs[0]["html_url"], flush=True)
        else:
            print("no Release run yet:", d.get("error", d.get("message", "")), flush=True)
            time.sleep(30)
            continue
    j = get(f"https://api.github.com/repos/multikco/video-pipeline/actions/runs/{run}/jobs?per_page=30")
    cur = " | ".join(f"{x['name']}: {x['status']}/{x['conclusion']}" for x in j.get("jobs", [])) or (
        "api-error " + str(j.get("error", j.get("message")))
    )
    if cur != prev:
        print(cur, flush=True)
        prev = cur
    r = get(f"https://api.github.com/repos/multikco/video-pipeline/actions/runs/{run}")
    if r.get("status") == "completed":
        print("RUN_DONE", r.get("conclusion"), flush=True)
        break
    time.sleep(60)
print("WATCH_END", flush=True)
