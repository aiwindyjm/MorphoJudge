"""Selection service (SEL-001).

One pure decision function used by BOTH preview and real analysis — the
preview can never drift from what actually gets analyzed. Every changed file
receives exactly one decision; exclusions and limitations always carry
reason + rule_id (+ language/bytes when knowable). Nothing here reads file
contents or executes anything.
"""

from __future__ import annotations

from typing import Sequence

from ..contracts.domain import (
    CoverageSummary,
    DiffFileEntry,
    SelectionDecision,
    SelectionStatus,
)
from ..contracts.domain import validate_repo_relative_path
from . import rules as rule_ids
from .rules import SelectionRules


def _decision_bytes(entry: DiffFileEntry) -> int | None:
    if entry.new_bytes is not None:
        return entry.new_bytes
    return entry.old_bytes


def decide_selection(
    files: Sequence[DiffFileEntry],
    rules: SelectionRules,
    snapshot_id: str,
) -> tuple[list[SelectionDecision], CoverageSummary]:
    """Pure function: same (files, rules, snapshot_id) → same output.

    Check order (frozen, deterministic): path bounds → PRIVATE → credentials
    → unmerged → binary → symlink → submodule → unknown size → unsupported
    language → file size cap → total budget cap → selected.
    Files are processed in ascending path order so budget cutoffs are stable.
    """

    decisions: list[SelectionDecision] = []
    selected_bytes_total = 0

    def add(
        entry: DiffFileEntry,
        status: SelectionStatus,
        reason: str,
        rule_id: str | None,
        language: str | None,
        byte_count: int | None,
    ) -> None:
        decisions.append(
            SelectionDecision(
                snapshot_id=snapshot_id,
                path=entry.path,
                status=status,
                reason=reason,
                rule_id=rule_id,
                language=language,
                bytes=byte_count,
            )
        )

    for entry in sorted(files, key=lambda item: item.path):
        language = rules.language_for(entry.path)
        byte_count = _decision_bytes(entry)

        try:
            validate_repo_relative_path(entry.path)
        except ValueError:
            add(
                entry,
                SelectionStatus.EXCLUDED,
                "path is not a safe repo-relative path",
                rule_ids.RULE_PATH_TRAVERSAL,
                language,
                byte_count,
            )
            continue

        if rules.is_private_path(entry.path):
            add(
                entry,
                SelectionStatus.EXCLUDED,
                "path is under a private directory",
                rule_ids.RULE_PRIVATE_PATH,
                language,
                byte_count,
            )
            continue

        if rules.is_credential_path(entry.path):
            add(
                entry,
                SelectionStatus.EXCLUDED,
                "credential-like file name is excluded from analysis",
                rule_ids.RULE_CREDENTIAL_FILE,
                language,
                byte_count,
            )
            continue

        if entry.status.value == "unmerged":
            add(
                entry,
                SelectionStatus.FAILED,
                "unmerged index entry cannot be analyzed",
                rule_ids.RULE_UNMERGED,
                language,
                byte_count,
            )
            continue

        if entry.binary:
            add(
                entry,
                SelectionStatus.EXCLUDED,
                "binary file has no text analysis",
                rule_ids.RULE_BINARY,
                language,
                byte_count,
            )
            continue

        if entry.is_symlink:
            add(
                entry,
                SelectionStatus.EXCLUDED,
                "symlink entry is excluded from content analysis",
                rule_ids.RULE_SYMLINK,
                language,
                byte_count,
            )
            continue

        if entry.is_submodule:
            add(
                entry,
                SelectionStatus.EXCLUDED,
                "submodule pointer is not analyzed (no network fetch)",
                rule_ids.RULE_SUBMODULE,
                language,
                byte_count,
            )
            continue

        if byte_count is None:
            add(
                entry,
                SelectionStatus.FAILED,
                "file size could not be determined",
                rule_ids.RULE_SIZE_UNKNOWN,
                language,
                None,
            )
            continue

        if language is None:
            add(
                entry,
                SelectionStatus.EXCLUDED,
                "unknown file extension has no language mapping",
                rule_ids.RULE_UNSUPPORTED_LANGUAGE,
                None,
                byte_count,
            )
            continue

        if language not in rules.supported_languages:
            add(
                entry,
                SelectionStatus.EXCLUDED,
                f"unsupported language: {language}",
                rule_ids.RULE_UNSUPPORTED_LANGUAGE,
                language,
                byte_count,
            )
            continue

        if byte_count > rules.max_file_bytes:
            add(
                entry,
                SelectionStatus.LIMITED,
                f"file exceeds per-file budget of {rules.max_file_bytes} bytes",
                rule_ids.RULE_OVERSIZE,
                language,
                byte_count,
            )
            continue

        if selected_bytes_total + byte_count > rules.max_total_selected_bytes:
            add(
                entry,
                SelectionStatus.LIMITED,
                "total selected-bytes budget exhausted before this file",
                rule_ids.RULE_BUDGET_EXHAUSTED,
                language,
                byte_count,
            )
            continue

        selected_bytes_total += byte_count
        add(
            entry,
            SelectionStatus.SELECTED,
            "selected for deterministic analysis",
            None,
            language,
            byte_count,
        )

    counts = {status: 0 for status in SelectionStatus}
    for decision in decisions:
        counts[decision.status] += 1

    summary = CoverageSummary(
        snapshot_id=snapshot_id,
        total=len(decisions),
        selected=counts[SelectionStatus.SELECTED],
        excluded=counts[SelectionStatus.EXCLUDED],
        limited=counts[SelectionStatus.LIMITED],
        failed=counts[SelectionStatus.FAILED],
        completed=0,
        partial=counts[SelectionStatus.LIMITED] > 0 or counts[SelectionStatus.FAILED] > 0,
        updated_at=None,
    )
    return decisions, summary


def preview_selection(
    files: Sequence[DiffFileEntry],
    rules: SelectionRules,
    snapshot_id: str,
) -> tuple[list[SelectionDecision], CoverageSummary]:
    """UI preview entrypoint — MUST stay the same function as real analysis."""
    return decide_selection(files, rules, snapshot_id)


def plan_analysis_selection(
    files: Sequence[DiffFileEntry],
    rules: SelectionRules,
    snapshot_id: str,
) -> tuple[list[SelectionDecision], CoverageSummary]:
    """Worker entrypoint — MUST stay the same function as preview."""
    return decide_selection(files, rules, snapshot_id)


# ---------------------------------------------------------------------------
# 依赖分析选择用途（DEP-001）
#
# 独立于源码选择：只接受仓库根的依赖文件白名单（package.json、
# pnpm-lock.yaml），不复用源码语言表（JSON/YAML 本就不在 TS/JS 支持表内），
# 但完整复用路径边界、PRIVATE、凭据、symlink/submodule、大小与预算检查。
# 该用途不改变源码选择结果（调用 decide_selection 的路径保持原样）。
# ---------------------------------------------------------------------------

DEPENDENCY_FILE_WHITELIST = ("package.json", "pnpm-lock.yaml")
MAX_DEPENDENCY_FILE_BYTES = 2_000_000
MAX_DEPENDENCY_TOTAL_BYTES = 8_000_000


def select_dependency_files(
    tree_meta: dict[str, tuple[str, int | None]],
    rules: SelectionRules,
    snapshot_id: str,
) -> tuple[list[SelectionDecision], CoverageSummary]:
    """Whitelist-based selection for dependency manifests at the repo root.

    tree_meta: path -> (mode, size) from git ls-tree of one commit.
    Files outside the whitelist are NOT analyzed by this purpose and are
    returned as excluded with SEL-DEP-NOT-WHITELISTED so the coverage stays
    explicit; whitelist entries reuse the same safety checks as source
    selection (private/credential/traversal/symlink/submodule/size/budget).
    """

    decisions: list[SelectionDecision] = []
    total = 0

    def add(path: str, status: SelectionStatus, reason: str, rule_id: str | None, size: int | None):
        decisions.append(
            SelectionDecision(
                snapshot_id=snapshot_id,
                path=path,
                status=status,
                reason=reason,
                rule_id=rule_id,
                language="json" if path.endswith(".json") else "yaml",
                bytes=size,
            )
        )

    for path in sorted(tree_meta):
        if path not in DEPENDENCY_FILE_WHITELIST:
            continue  # 非白名单文件不进入依赖用途（也不逐个记 excluded，避免全仓噪声）
        mode, size = tree_meta[path]

        try:
            validate_repo_relative_path(path)
        except ValueError:
            add(path, SelectionStatus.EXCLUDED, "path is not a safe repo-relative path", rule_ids.RULE_PATH_TRAVERSAL, size)
            continue
        if rules.is_private_path(path):
            add(path, SelectionStatus.EXCLUDED, "path is under a private directory", rule_ids.RULE_PRIVATE_PATH, size)
            continue
        if rules.is_credential_path(path):
            add(path, SelectionStatus.EXCLUDED, "credential-like file name is excluded from analysis", rule_ids.RULE_CREDENTIAL_FILE, size)
            continue
        if mode == "160000":
            add(path, SelectionStatus.EXCLUDED, "submodule pointer is not analyzed", rule_ids.RULE_SUBMODULE, size)
            continue
        if mode == "120000":
            add(path, SelectionStatus.EXCLUDED, "symlink entry is excluded from content analysis", rule_ids.RULE_SYMLINK, size)
            continue
        if size is None:
            add(path, SelectionStatus.FAILED, "file size could not be determined", rule_ids.RULE_SIZE_UNKNOWN, None)
            continue
        if size > MAX_DEPENDENCY_FILE_BYTES:
            add(path, SelectionStatus.LIMITED, f"dependency file exceeds {MAX_DEPENDENCY_FILE_BYTES} bytes", rule_ids.RULE_OVERSIZE, size)
            continue
        if total + size > MAX_DEPENDENCY_TOTAL_BYTES:
            add(path, SelectionStatus.LIMITED, "dependency-file total budget exhausted", rule_ids.RULE_DEP_BUDGET_EXHAUSTED, size)
            continue
        total += size
        add(path, SelectionStatus.SELECTED, "selected for dependency analysis", None, size)

    counts = {status: 0 for status in SelectionStatus}
    for decision in decisions:
        counts[decision.status] += 1
    summary = CoverageSummary(
        snapshot_id=snapshot_id,
        total=len(decisions),
        selected=counts[SelectionStatus.SELECTED],
        excluded=counts[SelectionStatus.EXCLUDED],
        limited=counts[SelectionStatus.LIMITED],
        failed=counts[SelectionStatus.FAILED],
        partial=counts[SelectionStatus.LIMITED] > 0 or counts[SelectionStatus.FAILED] > 0,
        updated_at=None,
    )
    return decisions, summary
