"""Commit-to-commit diff (GIT-002).

Produces DiffFileEntry per file with status (added/modified/deleted/renamed/
copied/type_changed/unmerged), additions/deletions (null for binary), byte
sizes per side, symlink/submodule flags and an old↔new line hunk map (or an
explicit reason when unmappable, e.g. binary files).

Both SHAs are validated 40-hex before being passed to git, so they can never
be interpreted as options; all invocations go through the hardened runner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..contracts.domain import (
    DiffFileEntry,
    DiffFileStatus,
    DiffResult,
    LineMapHunk,
    SnapshotReport,
)
from ..contracts.errors import ErrorCode, MorphoJudgeError
from .runner import check_git, run_git

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_STATUS_TOKEN_RE = re.compile(r"^([ADMRCTUXB])(\d*)$")

_DETECT = ["--find-renames=50%", "--find-copies", "--find-copies-harder"]


@dataclass
class _FileDiff:
    hunks: list[tuple[int, int, int, int]] = field(default_factory=list)
    binary: bool = False


def _validate_commit_sha(value: str) -> str:
    if not _COMMIT_RE.fullmatch(value):
        raise MorphoJudgeError(
            ErrorCode.INTERNAL_ERROR,
            "diff expects validated 40-hex commit shas",
            details={"value_prefix": value[:8]},
        )
    return value


def _parse_name_status(out: str) -> list[tuple[str, str, str | None]]:
    """Parse `git diff --name-status -z` records: (status, path, old_path?).

    With -z the output field separator is NUL, so each record is
    ``STATUS NUL path NUL`` — or ``R100 NUL old NUL new NUL`` for renames —
    with status tokens always sitting at record boundaries.
    """

    tokens = out.split("\x00")
    records: list[tuple[str, str, str | None]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "":
            index += 1
            continue
        match = _STATUS_TOKEN_RE.match(token)
        if match is None:
            raise MorphoJudgeError(
                ErrorCode.INTERNAL_ERROR,
                "unexpected name-status token at record boundary",
                details={"token_prefix": token[:40]},
            )
        letter = match.group(1)
        try:
            if letter in ("R", "C"):
                records.append((letter, tokens[index + 2], tokens[index + 1]))
                index += 3
            else:
                records.append((letter, tokens[index + 1], None))
                index += 2
        except IndexError as exc:
            raise MorphoJudgeError(
                ErrorCode.INTERNAL_ERROR, "truncated name-status record"
            ) from exc
    return records


def _map_status(letter: str) -> DiffFileStatus:
    mapping = {
        "A": DiffFileStatus.ADDED,
        "M": DiffFileStatus.MODIFIED,
        "D": DiffFileStatus.DELETED,
        "R": DiffFileStatus.RENAMED,
        "C": DiffFileStatus.COPIED,
        "T": DiffFileStatus.TYPE_CHANGED,
        "U": DiffFileStatus.UNMERGED,
    }
    status = mapping.get(letter)
    if status is None:
        raise MorphoJudgeError(
            ErrorCode.INTERNAL_ERROR, f"unsupported diff status letter {letter!r}"
        )
    return status


def _strip_ab(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _parse_unified(out: str) -> dict[tuple[str | None, str | None], _FileDiff]:
    """Parse a zero-context unified diff keyed by (old_path, new_path).

    Binary markers ("Binary files a/x and b/y differ") can appear without any
    ---/+++ header, so they are parsed into a key on their own.
    """

    result: dict[tuple[str | None, str | None], _FileDiff] = {}
    old_path: str | None = None
    new_path: str | None = None
    current: _FileDiff | None = None

    def _register(key: tuple[str | None, str | None]) -> _FileDiff:
        return result.setdefault(key, _FileDiff())

    for line in out.splitlines():
        if line.startswith("Binary files ") and line.rstrip().endswith("differ"):
            payload = line.rstrip()[: -len(" differ")]
            left, _, right = payload[len("Binary files ") :].rpartition(" and ")
            old = None if left.strip() == "/dev/null" else _strip_ab(left.strip())
            new = None if right.strip() == "/dev/null" else _strip_ab(right.strip())
            _register((old, new)).binary = True
            current = None
            continue
        if line.startswith("--- "):
            value = line[4:].strip()
            old_path = None if value == "/dev/null" else _strip_ab(value)
            continue
        if line.startswith("+++ "):
            value = line[4:].strip()
            new_path = None if value == "/dev/null" else _strip_ab(value)
            current = _register((old_path, new_path))
            continue
        if line.startswith("GIT binary patch"):
            if current is not None:
                current.binary = True
            continue
        if current is None:
            continue
        hunk = _HUNK_RE.match(line)
        if hunk:
            o_start = int(hunk.group(1))
            o_count = 1 if hunk.group(2) is None else int(hunk.group(2))
            n_start = int(hunk.group(3))
            n_count = 1 if hunk.group(4) is None else int(hunk.group(4))
            current.hunks.append((o_start, o_count, n_start, n_count))
    return result


def _tree_meta(repo: Path, commit: str) -> dict[str, tuple[str, int | None]]:
    """path -> (mode, blob_size|None) for every entry (blobs, symlinks, gitlinks).

    `ls-tree -l` pads the size column with spaces, so split on whitespace runs.
    """

    out = check_git(["ls-tree", "-r", "-l", "-z", commit], cwd=repo)
    meta: dict[str, tuple[str, int | None]] = {}
    for token in out.split("\x00"):
        if not token:
            continue
        fields, _, path = token.partition("\t")
        parts = fields.split()
        if len(parts) != 4 or not path:
            continue
        mode, kind, _sha, size_text = parts
        size = int(size_text) if size_text.isdigit() else None
        meta[path] = (mode, size if kind == "blob" else None)
    return meta


def diff_files(repo: Path, base_commit: str, target_commit: str) -> list[DiffFileEntry]:
    base_commit = _validate_commit_sha(base_commit)
    target_commit = _validate_commit_sha(target_commit)

    name_status = run_git(
        ["diff", "--name-status", "-z", *_DETECT, base_commit, target_commit], cwd=repo
    )
    if name_status.returncode != 0:
        raise MorphoJudgeError(
            ErrorCode.GIT_COMMAND_FAILED,
            "git diff --name-status failed",
            details={"stderr": name_status.stderr.strip()[:200]},
        )
    unified = run_git(["diff", "-U0", *_DETECT, base_commit, target_commit], cwd=repo)
    if unified.returncode != 0:
        raise MorphoJudgeError(
            ErrorCode.GIT_COMMAND_FAILED,
            "git diff -U0 failed",
            details={"stderr": unified.stderr.strip()[:200]},
        )

    hunks_by_key = _parse_unified(unified.stdout)
    base_meta = _tree_meta(repo, base_commit)
    target_meta = _tree_meta(repo, target_commit)

    entries: list[DiffFileEntry] = []
    for letter, primary_path, old_path in _parse_name_status(name_status.stdout):
        status = _map_status(letter)

        # (base-side path, target-side path) for hunk lookup and size reading.
        if status in (DiffFileStatus.RENAMED, DiffFileStatus.COPIED):
            old_side, new_side = old_path, primary_path
        elif status is DiffFileStatus.DELETED:
            old_side, new_side = primary_path, None
        elif status is DiffFileStatus.ADDED:
            old_side, new_side = None, primary_path
        else:  # modified / type_changed / unmerged
            old_side, new_side = primary_path, primary_path

        record = hunks_by_key.get((old_side, new_side))

        old_mode, old_bytes = base_meta.get(old_side, ("", None)) if old_side else ("", None)
        new_mode, new_bytes = target_meta.get(new_side, ("", None)) if new_side else ("", None)

        is_symlink = "120000" in (old_mode, new_mode)
        is_submodule = "160000" in (old_mode, new_mode)
        binary = bool(record and record.binary)

        if is_symlink:
            reason, line_map, additions, deletions = "symlink_target", None, None, None
        elif is_submodule:
            reason, line_map, additions, deletions = "submodule_pointer", None, None, None
        elif status is DiffFileStatus.UNMERGED:
            reason, line_map, additions, deletions = "unmerged_entry", None, None, None
        elif binary:
            reason, line_map, additions, deletions = "binary_file", None, None, None
        else:
            hunks = record.hunks if record else []
            line_map = [
                LineMapHunk(
                    old_start=o_start,
                    old_count=o_count,
                    new_start=n_start,
                    new_count=n_count,
                )
                for o_start, o_count, n_start, n_count in hunks
            ]
            reason = None
            additions = sum(hunk[3] for hunk in hunks)
            deletions = sum(hunk[1] for hunk in hunks)

        entries.append(
            DiffFileEntry(
                path=primary_path,
                old_path=old_side if old_side and old_side != primary_path else None,
                status=status,
                additions=additions,
                deletions=deletions,
                binary=binary,
                is_symlink=is_symlink,
                is_submodule=is_submodule,
                old_bytes=old_bytes,
                new_bytes=new_bytes,
                line_map=line_map,
                line_map_unavailable_reason=reason,
            )
        )
    return entries


def build_diff_result(repo: Path, snapshot: SnapshotReport) -> DiffResult:
    """Combine diff entries with the snapshot's working-tree report so
    untracked files surface informationally (frozen untracked policy)."""

    identity = snapshot.identity
    files = diff_files(repo, identity.base_commit, identity.target_commit)
    untracked = sorted(
        {entry.path for entry in snapshot.working_tree if "?" in (entry.x, entry.y)}
    )
    return DiffResult(
        snapshot_id=identity.snapshot_id,
        base_commit=identity.base_commit,
        target_commit=identity.target_commit,
        files=files,
        untracked=untracked,
    )
