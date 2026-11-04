"""Every `from app.X import a, b` in app/ (top level and inside functions, relative imports
resolved) must name an existing module and names defined/imported in it (or submodules).

Checked statically via AST, so heavy modules are not imported. Imports inside
``try/except ImportError`` are fallbacks and are not failures. Regression guard for ported
code that keeps importing a name from the module it lived in before (live stand crash:
``lints.new_secret_hits`` imported ``_CREDENTIAL_TEXT`` from ``observation`` instead of ``redact``).
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def mod_path(mod: str) -> Path | None:
    p = ROOT.joinpath(*mod.split("."))
    if (p / "__init__.py").is_file():
        return p / "__init__.py"
    if p.with_suffix(".py").is_file():
        return p.with_suffix(".py")
    if p.is_dir():  # namespace package
        return p
    return None


def _collect_targets(t, out: set[str]) -> None:
    if isinstance(t, ast.Name):
        out.add(t.id)
    elif isinstance(t, (ast.Tuple, ast.List)):
        for e in t.elts:
            _collect_targets(e, out)
    elif isinstance(t, ast.Starred):
        _collect_targets(t.value, out)


def _walk_module_level(
    body, mod: str, names: set[str], stars: list[str], flags: dict, is_pkg: bool = False
) -> None:
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            if node.name == "__getattr__":
                flags["getattr"] = True
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                _collect_targets(t, names)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            _collect_targets(node.target, names)
        elif isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            src = resolve(node, mod, is_pkg)
            for a in node.names:
                if a.name == "*":
                    if src:
                        stars.append(src)
                else:
                    names.add(a.asname or a.name)
        elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
            for fld in ("body", "orelse", "finalbody"):
                _walk_module_level(getattr(node, fld, []) or [], mod, names, stars, flags, is_pkg)
            for h in getattr(node, "handlers", []) or []:
                _walk_module_level(h.body, mod, names, stars, flags, is_pkg)
            if isinstance(node, ast.For):
                _collect_targets(node.target, names)
            if isinstance(node, ast.With):
                for it in node.items:
                    if it.optional_vars is not None:
                        _collect_targets(it.optional_vars, names)
        elif hasattr(ast, "TryStar") and isinstance(node, ast.TryStar):
            _walk_module_level(node.body, mod, names, stars, flags, is_pkg)
        elif isinstance(node, ast.Expr):
            pass
    # walrus at module level is rare; ignore


def resolve(node: ast.ImportFrom, cur_mod: str, is_pkg: bool = False) -> str | None:
    if node.level == 0:
        return node.module
    parts = cur_mod.split(".")
    pkg = parts if is_pkg else parts[:-1]
    up = node.level - 1
    if up > len(pkg):
        return None
    base = pkg[: len(pkg) - up] if up else pkg
    return ".".join(base + ([node.module] if node.module else []))


@cache
def module_names(mod: str) -> tuple[frozenset, bool] | None:
    p = mod_path(mod)
    if p is None:
        return None
    if p.is_dir():
        return frozenset(), False
    tree = ast.parse(p.read_text(encoding="utf-8-sig"), str(p))
    names: set[str] = set()
    stars: list[str] = []
    flags = {"getattr": False}
    _walk_module_level(tree.body, mod, names, stars, flags, p.name == "__init__.py")
    for s in stars:
        if s.startswith("app"):
            sub = module_names(s)
            if sub:
                names |= {n for n in sub[0] if not n.startswith("_")}
                flags["getattr"] = flags["getattr"] or sub[1]
        else:
            flags["getattr"] = True  # unknown external star import -> be lenient
    return frozenset(names), flags["getattr"]


def file_mod(path: Path) -> tuple[str, bool]:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    is_pkg = parts[-1] == "__init__"
    if is_pkg:
        parts = parts[:-1]
    return ".".join(parts), is_pkg


_GUARD_EXC = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}


def guarded_imports(tree) -> set[int]:
    """ids of ImportFrom nodes inside a try whose handler catches ImportError (fallback import)."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        names = set()
        for h in node.handlers:
            if h.type is None:
                names.add("Exception")
            for t in (h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]) if h.type else []:
                if isinstance(t, ast.Name):
                    names.add(t.id)
                elif isinstance(t, ast.Attribute):
                    names.add(t.attr)
        if names & _GUARD_EXC:
            for stmt in node.body:
                for sub in ast.walk(stmt):
                    if isinstance(sub, ast.ImportFrom):
                        out.add(id(sub))
    return out


def sweep(root: Path | None = None) -> tuple[int, list[str], list[str]]:
    """(checked, guarded_hits, broken) for every from-import of app.* under <root>/app."""
    global ROOT
    if root is not None:
        ROOT = Path(root).resolve()
        module_names.cache_clear()
    broken: list[str] = []
    guarded_hits: list[str] = []
    checked = 0
    for f in sorted((ROOT / "app").rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        mod, is_pkg = file_mod(f)
        try:
            tree = ast.parse(f.read_text(encoding="utf-8-sig"), str(f))
        except SyntaxError as e:
            broken.append(f"{f.relative_to(ROOT)}: SYNTAX {e}")
            continue
        guarded = guarded_imports(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            sink = guarded_hits if id(node) in guarded else broken
            target = resolve(node, mod, is_pkg)
            if not target or not (target == "app" or target.startswith("app.")):
                continue
            checked += 1
            loc = f"{f.relative_to(ROOT).as_posix()}:{node.lineno}"
            info = module_names(target)
            if info is None:
                sink.append(f"{loc}: module {target} not found")
                continue
            names, lenient = info
            for a in node.names:
                if a.name == "*":
                    continue
                if a.name in names:
                    continue
                if mod_path(f"{target}.{a.name}") is not None:
                    continue  # submodule
                if lenient:
                    continue
                sink.append(f"{loc}: {target} has no name {a.name!r}")
            # plain `import app.x.y` is handled below
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith("app.") and mod_path(a.name) is None:
                        broken.append(
                            f"{f.relative_to(ROOT).as_posix()}:{node.lineno}: module {a.name} not found"
                        )
    return checked, guarded_hits, broken


def test_every_app_from_import_resolves() -> None:
    checked, _guarded, broken = sweep(Path(__file__).resolve().parents[1])
    assert checked > 1000
    assert broken == []


def test_sweep_sees_imports_inside_functions(tmp_path) -> None:
    pkg = tmp_path / "app"
    (pkg / "svc").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "svc" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "svc" / "redact.py").write_text("SECRET = 1\n", encoding="utf-8")
    (pkg / "svc" / "user.py").write_text(
        "def f():\n"
        "    from app.svc.redact import SECRET, MOVED\n"
        "    try:\n"
        "        from app.svc.gone import X\n"
        "    except ImportError:\n"
        "        X = 0\n",
        encoding="utf-8",
    )
    try:
        _checked, guarded, broken = sweep(tmp_path)
    finally:
        sweep(Path(__file__).resolve().parents[1])
    assert broken == ["app/svc/user.py:2: app.svc.redact has no name 'MOVED'"]
    assert guarded == ["app/svc/user.py:4: module app.svc.gone not found"]
