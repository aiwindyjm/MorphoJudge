"""Snapshot-bound blob reads: file content always comes from git objects,
never from the working tree, so analysis stays bound to the frozen commits.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..contracts.domain import validate_repo_relative_path
from ..contracts.errors import ErrorCode, MorphoJudgeError
from .runner import run_git_bytes

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_MAX_BLOB_BYTES = 64 * 1024 * 1024


def read_blob_bytes(repo: Path, commit: str, path: str) -> bytes:
    """Read one blob's exact bytes at a commit via hardened cat-file.

    Raises GIT_COMMAND_FAILED for missing paths (git exits nonzero); the
    snapshot_id binding in callers guarantees we only read frozen content.
    """

    if not _COMMIT_RE.fullmatch(commit):
        raise MorphoJudgeError(
            ErrorCode.INTERNAL_ERROR,
            "blob reads require validated 40-hex commit shas",
        )
    validate_repo_relative_path(path)

    completed = run_git_bytes(["cat-file", "-p", f"{commit}:{path}"], cwd=repo)
    if completed.returncode != 0:
        raise MorphoJudgeError(
            ErrorCode.GIT_COMMAND_FAILED,
            f"blob read failed for {path}",
            details={"stderr": completed.stderr.decode("utf-8", "replace")[:200]},
        )
    blob = completed.stdout
    if len(blob) > _MAX_BLOB_BYTES:
        raise MorphoJudgeError(
            ErrorCode.GIT_COMMAND_FAILED,
            f"blob exceeds read budget: {path}",
        )
    return blob


def list_tree_blobs(repo: Path, commit: str) -> dict[str, tuple[str, int | None]]:
    """path -> (mode, size|None) for every tree entry at a commit."""

    from .diff import _tree_meta  # local import avoids a cycle

    return _tree_meta(repo, commit)
