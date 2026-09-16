"""Shared pytest fixtures: real fixture repo (bind-mounted read-only) and
real temporary git repositories built with the container's git.

Tests are meant to run inside the daemon container:
    docker compose exec daemon pytest
Nothing here executes fixture code; git is used as data plumbing only.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

FIXTURES_ROOT = Path(os.environ.get("MORPHOJUDGE_TEST_FIXTURES", "/fixtures"))
FIXTURE_REPO = FIXTURES_ROOT / "ts-web"
MANIFEST_PATH = FIXTURES_ROOT / "manifests" / "ts-web-manifest.json"

_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "MorphoJudge Test",
    "GIT_AUTHOR_EMAIL": "test@morphojudge.invalid",
    "GIT_COMMITTER_NAME": "MorphoJudge Test",
    "GIT_COMMITTER_EMAIL": "test@morphojudge.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
}


def run_git(cwd: Path, *args: str, date: str | None = None) -> str:
    env = {**os.environ, **_GIT_IDENTITY}
    if date is not None:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    completed = subprocess.run(  # noqa: S603 - fixed binary, argument array
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "commit.gpgsign=false",
            "-c",
            f"safe.directory={cwd}",
            *args,
        ],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"git {args} failed: {completed.stderr.strip()[:300]}"
        )
    return completed.stdout


def commit_all(repo: Path, message: str, date: str = "2026-02-01T08:00:00+00:00") -> None:
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-m", message, date=date)


@pytest.fixture(scope="session")
def fixture_repo() -> Path:
    if not FIXTURE_REPO.exists():
        pytest.fail(
            f"fixture repo missing at {FIXTURE_REPO}; run tests via "
            "`docker compose exec daemon pytest` so /fixtures is mounted"
        )
    return FIXTURE_REPO


@pytest.fixture(scope="session")
def manifest() -> dict:
    if not MANIFEST_PATH.exists():
        pytest.fail(f"fixture manifest missing at {MANIFEST_PATH}")
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """A real git repository with two commits, inside the test's own root."""

    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("# temp repo\n", encoding="utf-8")
    commit_all(repo, "init")
    (repo / "second.txt").write_text("second\n", encoding="utf-8")
    commit_all(repo, "second", date="2026-02-02T08:00:00+00:00")
    return repo


@pytest.fixture(scope="session")
def fixture_roots() -> list[Path]:
    return [Path("/fixtures")]


EMPTY_MANIFEST: dict = {"human_feature_mapping": []}


def build_scenario_repo(
    tmp_path: Path,
    base_files: dict[str, str],
    target_files: dict[str, str | None],
) -> Path:
    """Create a real git repo with a base commit and a target commit.

    target_files maps path -> new content (str) or None to delete.
    Paths missing on either side simply don't exist there. Line endings are
    written exactly as given (CRLF stays CRLF thanks to * -text attributes).
    """

    repo = tmp_path / "scenario"
    repo.mkdir(parents=True)
    run_git(repo, "init", "-b", "main")
    (repo / ".gitattributes").write_text("* -text\n", encoding="utf-8", newline="")

    def write_all(files: dict[str, str]) -> None:
        for rel_path, content in files.items():
            target = repo / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode("utf-8"))

    write_all(base_files)
    commit_all(repo, "base", date="2026-05-01T08:00:00+00:00")

    for rel_path, content in target_files.items():
        target = repo / rel_path
        if content is None:
            if target.exists():
                run_git(repo, "rm", "-q", rel_path)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode("utf-8"))
    commit_all(repo, "target", date="2026-05-02T08:00:00+00:00")
    return repo


def analyze_repo(repo: Path, roots: list[Path], manifest: dict | None = None, **kwargs):
    """analyze_snapshot over HEAD~1..HEAD of a scenario repo."""
    import sys

    sys.path.insert(0, "/engine")
    from morphojudge.analyzer.service import analyze_snapshot
    from morphojudge.git.snapshot import resolve_snapshot

    snapshot = resolve_snapshot(
        repo, base_ref="HEAD~1", target_ref="HEAD",
        rules_version="batch03-test", allowed_roots=roots,
    )
    return analyze_snapshot(repo, snapshot, manifest or EMPTY_MANIFEST, **kwargs)
