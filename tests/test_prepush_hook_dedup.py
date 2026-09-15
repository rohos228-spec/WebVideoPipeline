"""Пропуск повторного gauntlet'а в pre-push — только для уже проверенного.

Выкладка идёт в два шага: пуш ветки, затем перемотка `main` на неё. Коммиты
одни и те же, и полный прогон шёл дважды — лишние двадцать минут на каждую
выкладку. Хук научился пропускать второй прогон.

Ошибка тут тихая и дорогая: достаточно чуть расширить условие — и гейт
перестанет работать вовсе, а красноту станет видно только в CI (или на
проде). Поэтому проверяется поведение, а не текст: поднимается настоящий
репозиторий с origin и хук запускается ровно так, как его зовёт git —
строками `<local_ref> <local_sha> <remote_ref> <remote_sha>` на stdin.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".githooks" / "pre-push"


def _clean_env(**extra: str) -> dict[str, str]:
    """Окружение без наследства от git.

    Тест может идти ИЗ git-хука (ярус commit), а git выставляет хуку
    `GIT_DIR`, `GIT_INDEX_FILE` и прочее. Унаследовав их, вложенный `git`
    работал бы с ОСНОВНЫМ репозиторием вместо временного — тест падал, а мог
    бы и напортить. Заодно отвязываемся от пользовательского конфига.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "t"
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "t@example.com"
    env.update(extra)
    return env


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=_clean_env(),
    ).stdout.strip()


@pytest.fixture
def repo_with_origin(tmp_path: Path) -> tuple[Path, Path]:
    """Клон с настоящим origin и одним коммитом, уже уехавшим на origin."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True, env=_clean_env())

    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    (work / "a.txt").write_text("1", encoding="utf-8")
    _git(work, "add", "a.txt")
    _git(work, "commit", "-qm", "первый")
    _git(work, "remote", "add", "origin", str(origin))
    # Пуш без хуков: origin здесь — декорация для проверки, а не гейт.
    _git(work, "push", "-q", "origin", "main")
    _git(work, "fetch", "-q", "origin")
    return work, tmp_path


def _run_hook(work: Path, home: Path, stdin: str) -> tuple[int, str, bool]:
    """Запустить хук как это делает git. Возвращает (код, stderr, звал ли движок)."""
    hooks_dir = home / ".agents" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    marker = home / "engine-called"
    engine = hooks_dir / "verify.sh"
    engine.write_text(
        f"#!/usr/bin/env bash\ntouch {marker}\nexit 0\n",
        encoding="utf-8",
    )
    engine.chmod(0o755)

    env = _clean_env(HOME=str(home))
    proc = subprocess.run(
        ["bash", str(HOOK), "origin", str(work / ".." / "origin.git")],
        cwd=work,
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, proc.stderr, marker.exists()


def test_hook_exists_and_is_executable() -> None:
    assert HOOK.is_file()
    assert os.access(HOOK, os.X_OK), "хук должен быть исполняемым, иначе git его молча не позовёт"


def test_commit_already_on_origin_skips_the_run(repo_with_origin) -> None:
    """Перемотка main на уже проверенную ветку не гоняет суиту второй раз."""
    work, home = repo_with_origin
    sha = _git(work, "rev-parse", "HEAD")
    code, err, engine_called = _run_hook(work, home, f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n")
    assert code == 0
    assert not engine_called, "коммит уже на origin и уже прошёл гейт — прогон лишний"
    assert "прогон пропущен" in err


def test_new_commit_still_runs_the_gate(repo_with_origin) -> None:
    """Любой коммит, которого на origin нет, проверяется полностью."""
    work, home = repo_with_origin
    (work / "b.txt").write_text("2", encoding="utf-8")
    _git(work, "add", "b.txt")
    _git(work, "commit", "-qm", "второй")
    sha = _git(work, "rev-parse", "HEAD")
    code, _err, engine_called = _run_hook(work, home, f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n")
    assert code == 0
    assert engine_called, "новый коммит обязан пройти gauntlet"


def test_mixed_push_runs_the_gate(repo_with_origin) -> None:
    """Одна ссылка проверена, вторая нет — гоняем: решает худший случай."""
    work, home = repo_with_origin
    old = _git(work, "rev-parse", "HEAD")
    (work / "c.txt").write_text("3", encoding="utf-8")
    _git(work, "add", "c.txt")
    _git(work, "commit", "-qm", "третий")
    new = _git(work, "rev-parse", "HEAD")
    stdin = (
        f"refs/heads/main {old} refs/heads/main {'0' * 40}\n"
        f"refs/heads/feat {new} refs/heads/feat {'0' * 40}\n"
    )
    _code, _err, engine_called = _run_hook(work, home, stdin)
    assert engine_called


def test_deletion_alone_does_not_skip_silently(repo_with_origin) -> None:
    """Удаление ветки не считается «проверенным коммитом»: гонять нечего, но и
    молчаливого пропуска с признаком «всё уже на origin» быть не должно."""
    work, home = repo_with_origin
    stdin = f"(delete) {'0' * 40} refs/heads/old {_git(work, 'rev-parse', 'HEAD')}\n"
    code, _err, engine_called = _run_hook(work, home, stdin)
    assert code == 0
    assert engine_called, "в списке нет ни одного проверенного коммита — решает движок"
