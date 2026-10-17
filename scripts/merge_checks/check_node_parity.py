"""Housepc-vs-INT node settings parity check.

For every node-settings file, re-applies the housepc delta (merge-base -> housepc)
onto the current tree with git merge-file. If that would still change the file,
a housepc change is missing -> FAIL. Intentional main-side files are allowlisted.
Usage: python scripts/merge_checks/check_node_parity.py [--housepc-ref REF] [--main-ref REF] [--live DIR]
"""
import argparse, difflib, fnmatch, io, os, subprocess, sys, tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCOPE = [
    "web/src/components/canvas/*panel*.tsx", "web/src/components/canvas/pipeline-node.tsx",
    "web/src/components/canvas/node-palette.tsx", "web/src/components/canvas/library-nodes-tab.tsx",
    "web/src/components/studio/*.tsx", "web/src/lib/node-*.ts", "web/src/lib/canvas-node-merge.ts",
    "web/src/lib/workflow-node-serialize.ts", "app/services/style_agent/*.py", "prompts/style_agent/*.md",
    "app/services/advance_runner.py", "app/services/agent_harness.py", "app/services/chatgpt_xlsx.py",
    "app/services/run_sync.py", "app/services/xlsx_step_runners.py", "app/web/routers/style_agent.py",
]
# Intentional main-side choices (user decision): model picker/prices/paths, Excel-GPT presets,
# result views, Anthropic/gpt_api, model lists, API tracker, questionnaire.
MAINSIDE = {"node-model-picker.tsx", "node-model-catalog.ts", "node-result-views.tsx", "node-result-resolver.ts",
            "node-result-panel.tsx", "excel-gpt-settings-panel.tsx", "generation-models-panel.tsx",
            "outsee-gen-panel.tsx", "node-model-logos.tsx"}
# Files where both sides edited the same lines; reviewed manually, housepc intent already present.
REVIEWED = {"node-catalog.ts", "node-prompts.ts", "frame-prompts-panel.tsx", "node-step-params-panel.tsx",
            "node-studio.tsx", "pipeline-node.tsx", "advance_runner.py", "agent_harness.py",
            "chatgpt_xlsx.py", "run_sync.py", "xlsx_step_runners.py",
            "node-prompt-block.tsx"}  # INT only wraps texts in brandText (provider hiding)

def git(*a):
    r = subprocess.run(["git", "-C", ROOT, *a], capture_output=True)
    return r.stdout.decode("utf-8", "ignore") if r.returncode == 0 else None

def norm(s): return None if s is None else s.replace("\r\n", "\n")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--housepc-ref", default="backup/housepc-20261017")
    ap.add_argument("--main-ref", default="c53a238b")
    ap.add_argument("--live", default=os.environ.get("HOUSEPC_DIR") or (r"C:\Users\Admin\Desktop\video-pipeline" if os.path.isdir(r"C:\Users\Admin\Desktop\video-pipeline") else None), help="optional live housepc checkout (read-only)")
    a = ap.parse_args()
    mb = (git("merge-base", a.main_ref, a.housepc_ref) or "").strip()
    if not mb:
        print("SKIP: refs not available"); return 0
    files = set(f for f in (git("ls-tree", "-r", "--name-only", a.housepc_ref) or "").split("\n")
                if any(fnmatch.fnmatch(f, g) for g in SCOPE))
    if a.live:
        for g in SCOPE:
            d = os.path.join(a.live, os.path.dirname(g))
            if os.path.isdir(d):
                for f in os.listdir(d):
                    rel = os.path.dirname(g) + "/" + f
                    if fnmatch.fnmatch(rel, g): files.add(rel)
    fails, checked = [], 0
    for rel in sorted(files):
        name = os.path.basename(rel)
        if name in MAINSIDE or name in REVIEWED: continue
        house = None
        if a.live and os.path.exists(os.path.join(a.live, rel)):
            house = norm(io.open(os.path.join(a.live, rel), encoding="utf-8", errors="ignore").read())
        if house is None: house = norm(git("show", f"{a.housepc_ref}:{rel}"))
        base = norm(git("show", f"{mb}:{rel}")) or ""
        p = os.path.join(ROOT, rel)
        cur = norm(io.open(p, encoding="utf-8", errors="ignore").read()) if os.path.exists(p) else None
        checked += 1
        if cur is None:
            fails.append((rel, "missing in INT")); continue
        if house == base or cur == house: continue
        added = [l[2:].strip() for l in difflib.ndiff(base.split("\n"), house.split("\n"))
                 if l.startswith("+ ") and l[2:].strip()]
        curset = set(l.strip() for l in cur.split("\n"))
        if added and all(l in curset for l in added): continue  # every housepc-added line present
        with tempfile.TemporaryDirectory() as td:
            fo, fb, ft = (os.path.join(td, x) for x in "obt")
            for f, c in ((fo, cur), (fb, base), (ft, house)):
                io.open(f, "w", encoding="utf-8", newline="\n").write(c)
            r = subprocess.run(["git", "merge-file", "-p", "-q", fo, fb, ft], capture_output=True)
        out = r.stdout.decode("utf-8", "ignore")
        if r.returncode: fails.append((rel, f"{r.returncode} conflict(s) not reviewed"))
        elif out != cur: fails.append((rel, "housepc change not applied"))
    for rel, why in fails: print(f"FAIL {rel}: {why}")
    print(f"node parity: checked={checked} fails={len(fails)}")
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())