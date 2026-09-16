"""Snapshot Identity resolution (GIT-001).

Resolves (repository path, base ref, target ref, rules version) into an
immutable SnapshotIdentity with explicit, non-silent handling of:
missing repository, non-git directory, invalid refs, detached HEAD,
dirty workspace, symlinked roots, linked worktrees/submodules, LFS filters
and path escapes. Deterministic: identical inputs yield identical ids.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Sequence

from ..contracts.domain import SnapshotIdentity, SnapshotReport, WorkingTreeEntry
from ..contracts.errors import ErrorCode, MorphoJudgeError
from .runner import check_git, run_git

_COMMIT_HEX_LEN = 40
_MAX_REF_LEN = 200
_MAX_WORKING_TREE_ENTRIES = 50
_SNAPSHOT_DOMAIN = "morphojudge-snapshot-v1"

_WORKING_TREE_CAP_NOTE = "working_tree_truncated_at_50_entries"


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def repository_id_for(canonical_path: str) -> str:
    return _sha256_hex(canonical_path)


def compute_snapshot_id(
    repository_id: str, base_commit: str, target_commit: str, rules_version: str
) -> str:
    payload = "\x00".join(
        [_SNAPSHOT_DOMAIN, repository_id, base_commit, target_commit, rules_version]
    )
    return _sha256_hex(payload)


def validate_ref(ref: str) -> str:
    """Reject refs that could be mistaken for git options or carry control bytes.

    No shell is involved anywhere; this is defense in depth on top of
    argument-array execution and rev-parse's own --end-of-options.
    """

    if not ref or len(ref) > _MAX_REF_LEN:
        raise MorphoJudgeError(
            ErrorCode.INVALID_INPUT, f"ref length out of range: {ref!r}"
        )
    if any(ch in ref for ch in "\x00\n\r"):
        raise MorphoJudgeError(
            ErrorCode.INVALID_INPUT, "ref must not contain control characters"
        )
    if ref.startswith("-") or ref.startswith(":"):
        raise MorphoJudgeError(
            ErrorCode.INVALID_INPUT,
            "ref must not start with '-' or ':' (possible option injection)",
            details={"ref_prefix": ref[:8]},
        )
    return ref


def _realpath_within_root(repository: Path, roots: Sequence[Path]) -> Path:
    """Canonicalize the repository path with symlink/escape checks.

    Rules (frozen behavior, tested):
    - The repository root itself must not be a symlink -> SYMLINK_ESCAPE.
    - Intermediate symlinks are canonicalized; the result must stay inside an
      allowed root -> otherwise SYMLINK_ESCAPE.
    - Lexical traversal (..) is accepted only if the final real path is inside
      an allowed root -> otherwise PATH_OUT_OF_ROOTS.
    """

    raw = os.fspath(repository)
    if "\x00" in raw:
        raise MorphoJudgeError(ErrorCode.INVALID_INPUT, "path contains NUL")

    try:
        lexical = os.path.abspath(raw)
        real = Path(os.path.realpath(raw))
    except (OSError, ValueError) as exc:
        raise MorphoJudgeError(
            ErrorCode.INVALID_INPUT, f"unusable repository path: {exc}"
        ) from exc

    resolved_roots = [Path(os.path.realpath(os.fspath(root))) for root in roots]

    def _inside(path: Path) -> bool:
        return any(
            path == root or root in path.parents for root in resolved_roots
        )

    if os.path.islink(raw.rstrip("/")):
        target = os.path.realpath(raw)
        raise MorphoJudgeError(
            ErrorCode.SYMLINK_ESCAPE,
            "repository root itself is a symlink; pass the real directory",
            details={"symlink_target": target},
        )

    if real != Path(lexical) and not _inside(real):
        raise MorphoJudgeError(
            ErrorCode.SYMLINK_ESCAPE,
            "symlinked path components escape the allowed repository roots",
            details={"canonical_path": str(real)},
        )

    if not _inside(real):
        raise MorphoJudgeError(
            ErrorCode.PATH_OUT_OF_ROOTS,
            "repository path is outside the allowed roots",
            details={
                "canonical_path": str(real),
                "allowed_roots": [str(r) for r in resolved_roots],
            },
        )
    return real


def resolve_commit(repo: Path, ref: str) -> str:
    validate_ref(ref)
    completed = run_git(
        ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        cwd=repo,
    )
    if completed.returncode != 0:
        raise MorphoJudgeError(
            ErrorCode.REF_NOT_FOUND,
            f"ref does not resolve to a commit: {ref}",
            details={"stderr": completed.stderr.strip()[:200]},
        )
    sha = completed.stdout.strip()
    if len(sha) != _COMMIT_HEX_LEN or any(
        ch not in "0123456789abcdef" for ch in sha
    ):
        raise MorphoJudgeError(
            ErrorCode.INTERNAL_ERROR,
            "rev-parse returned a non-hex object id",
            details={"value_prefix": sha[:8]},
        )
    return sha


def read_working_tree(repo: Path) -> list[WorkingTreeEntry]:
    """Parse `git status --porcelain -z`. Read-only on the repo (no locks)."""

    completed = run_git(["status", "--porcelain", "-z", "--untracked-files=all"], cwd=repo)
    if completed.returncode != 0:
        raise MorphoJudgeError(
            ErrorCode.GIT_COMMAND_FAILED,
            "git status failed",
            details={"stderr": completed.stderr.strip()[:200]},
        )
    entries: list[WorkingTreeEntry] = []
    tokens = [t for t in completed.stdout.split("\x00") if t]
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if len(token) < 4:
            index += 1
            continue
        xy, path = token[:2], token[3:]
        old_path: str | None = None
        if xy[0] in "RC" and index + 1 < len(tokens):
            index += 1
            old_path = tokens[index]
        # Informational only: bypass strict path validation so exotic
        # working-tree names never abort snapshot resolution.
        entries.append(
            WorkingTreeEntry.model_construct(
                x=xy[0], y=xy[1], path=path, old_path=old_path
            )
        )
        index += 1
    return entries


def _tree_entries(repo: Path, commit: str) -> list[tuple[str, str, str, str]]:
    """(mode, kind, sha, path) rows of `git ls-tree -r -z <commit>`."""

    out = check_git(["ls-tree", "-r", "-z", commit], cwd=repo)
    rows: list[tuple[str, str, str, str]] = []
    for token in out.split("\x00"):
        if not token:
            continue
        meta, _, path = token.partition("\t")
        parts = meta.split(" ")
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2], path))
    return rows


def _blob_text(repo: Path, sha: str) -> str | None:
    completed = run_git(["cat-file", "-p", sha], cwd=repo)
    if completed.returncode != 0:
        return None
    return completed.stdout


def resolve_snapshot(
    repository: Path | str,
    *,
    base_ref: str,
    target_ref: str,
    rules_version: str,
    allowed_roots: Sequence[Path],
) -> SnapshotReport:
    if not rules_version or len(rules_version) > 100:
        raise MorphoJudgeError(ErrorCode.INVALID_INPUT, "rules_version is required")

    raw_repo = Path(repository)
    if not raw_repo.is_absolute():
        raise MorphoJudgeError(
            ErrorCode.INVALID_INPUT,
            "repository path must be absolute (container path)",
        )

    repo = _realpath_within_root(raw_repo, allowed_roots)

    if not repo.exists():
        raise MorphoJudgeError(
            ErrorCode.REPOSITORY_NOT_FOUND,
            "repository path does not exist",
            details={"canonical_path": str(repo)},
        )
    if not repo.is_dir():
        raise MorphoJudgeError(
            ErrorCode.REPOSITORY_NOT_FOUND,
            "repository path is not a directory",
            details={"canonical_path": str(repo)},
        )

    git_dir = repo / ".git"
    if git_dir.is_file():
        raise MorphoJudgeError(
            ErrorCode.LINKED_WORKTREE_NOT_SUPPORTED,
            ".git is a file (linked worktree or submodule); not supported in v0.1",
            details={"canonical_path": str(repo)},
        )
    if not git_dir.exists():
        raise MorphoJudgeError(
            ErrorCode.NOT_A_GIT_REPOSITORY,
            "directory is not a git repository",
            details={"canonical_path": str(repo)},
        )

    base_commit = resolve_commit(repo, base_ref)
    target_commit = resolve_commit(repo, target_ref)

    notes: list[str] = []

    working_tree = read_working_tree(repo)
    if len(working_tree) > _MAX_WORKING_TREE_ENTRIES:
        working_tree = working_tree[:_MAX_WORKING_TREE_ENTRIES]
        notes.append(_WORKING_TREE_CAP_NOTE)
    workspace_dirty = bool(working_tree)

    for commit in (base_commit, target_commit):
        rows = _tree_entries(repo, commit)
        if any(mode == "160000" for mode, _, _, _ in rows):
            notes.append("submodule_present")
            break

    for commit in (base_commit, target_commit):
        attrs = [row for row in _tree_entries(repo, commit) if row[3] == ".gitattributes"]
        for _, kind, sha, _ in attrs:
            if kind != "blob":
                continue
            text = _blob_text(repo, sha) or ""
            if "filter=lfs" in text:
                notes.append("lfs_filter_present")
                break

    if base_commit == target_commit:
        notes.append("base_equals_target")

    canonical = os.fspath(repo)
    repository_id = repository_id_for(canonical)
    snapshot_id = compute_snapshot_id(
        repository_id, base_commit, target_commit, rules_version
    )
    identity = SnapshotIdentity(
        repository_id=repository_id,
        canonical_path=canonical,
        base_commit=base_commit,
        target_commit=target_commit,
        snapshot_id=snapshot_id,
        rules_version=rules_version,
    )
    return SnapshotReport(
        identity=identity,
        workspace_dirty=workspace_dirty,
        working_tree=working_tree,
        notes=notes,
    )
