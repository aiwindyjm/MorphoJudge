"""EVD-001: Evidence Resolver.

Resolves a (commit, side, path, line range, rule) request into either an
exact EvidenceAnchor or an explicit unresolved result with a reason — never
a "near enough" position. Snippets are the raw blob text of the requested
line range (no escaping, no HTML treatment; source is data, display escape
belongs to later web batches).

The frozen EvidenceAnchor has no resolver fields, so the full state lives in
the internal EvidenceResolution record; anchors are only created on success.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..contracts.domain import EvidenceAnchor, EvidenceSide, validate_repo_relative_path
from ..contracts.errors import ErrorCode, MorphoJudgeError
from ..git.blob import read_blob_bytes

MAX_SNIPPET_LINES = 50
MAX_SNIPPET_BYTES = 16_384


class EvidenceStatus(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class EvidenceRequest:
    commit: str
    side: EvidenceSide
    path: str
    start_line: int
    end_line: int
    rule_id: str | None
    source_kind: str = "rule"

    @property
    def key(self) -> str:
        return f"{self.commit}|{self.side.value}|{self.path}|{self.start_line}-{self.end_line}|{self.rule_id or ''}"


@dataclass(frozen=True)
class EvidenceResolution:
    request: EvidenceRequest
    status: EvidenceStatus
    reason: str | None = None
    anchor: EvidenceAnchor | None = None

    @property
    def anchor_id(self) -> str | None:
        return self.anchor.id if self.anchor is not None else None


def _anchor_id(request: EvidenceRequest) -> str:
    digest = hashlib.sha256(request.key.encode("utf-8")).hexdigest()[:16]
    return f"evidence:{digest}"


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:8000]


class EvidenceResolver:
    """Caching resolver bound to one snapshot; reads only git blobs."""

    def __init__(self, repo: Path, snapshot_id: str) -> None:
        self.repo = repo
        self.snapshot_id = snapshot_id
        self._cache: dict[str, EvidenceResolution] = {}
        self._lines_cache: dict[tuple[str, str], list[str]] = {}

    def _blob_lines(self, commit: str, path: str) -> list[str]:
        cached = self._lines_cache.get((commit, path))
        if cached is not None:
            return cached
        data = read_blob_bytes(self.repo, commit, path)
        text = data.decode("utf-8", errors="replace")
        lines = text.split("\n")
        # 保留行尾 \r（原始文本语义）；split("\n") 已按行拆分
        self._lines_cache[(commit, path)] = lines
        return lines

    def resolve(self, request: EvidenceRequest) -> EvidenceResolution:
        if request.key in self._cache:
            return self._cache[request.key]

        resolution = self._resolve_uncached(request)
        self._cache[request.key] = resolution
        return resolution

    def _resolve_uncached(self, request: EvidenceRequest) -> EvidenceResolution:
        def unresolved(reason: str) -> EvidenceResolution:
            return EvidenceResolution(request=request, status=EvidenceStatus.UNRESOLVED, reason=reason)

        try:
            validate_repo_relative_path(request.path)
        except ValueError:
            return unresolved("unsafe_path")

        if request.start_line < 1 or request.end_line < request.start_line:
            return unresolved("invalid_line_range")

        try:
            data = read_blob_bytes(self.repo, request.commit, request.path)
        except MorphoJudgeError as exc:
            if exc.code == ErrorCode.GIT_COMMAND_FAILED:
                return unresolved("path_missing_at_commit")
            # 非法 commit/路径等内部校验失败同样如实记录，不冒充定位成功
            return unresolved(f"blob_read_failed:{exc.code.value}")

        if _looks_binary(data):
            return unresolved("binary_file")

        lines = self._blob_lines(request.commit, request.path)
        line_count = len(lines)
        # 文本以换行结尾时最后一个元素为空串，不算实际行
        effective = line_count - 1 if lines and lines[-1] == "" else line_count
        if request.start_line > effective:
            return unresolved("line_out_of_range")
        if request.end_line > effective:
            return unresolved("end_line_out_of_range")
        if request.end_line - request.start_line + 1 > MAX_SNIPPET_LINES:
            return unresolved("snippet_range_exceeds_budget")
        snippet_lines = lines[request.start_line - 1 : request.end_line]
        snippet = "\n".join(snippet_lines)
        if len(snippet.encode("utf-8")) > MAX_SNIPPET_BYTES:
            return unresolved("snippet_bytes_exceed_budget")

        anchor = EvidenceAnchor(
            id=_anchor_id(request),
            snapshot_id=self.snapshot_id,
            path=request.path,
            side=request.side,
            start_line=request.start_line,
            end_line=request.end_line,
            snippet=snippet,
            source_kind=request.source_kind,
            rule_id=request.rule_id,
            commit=request.commit,
        )
        return EvidenceResolution(request=request, status=EvidenceStatus.RESOLVED, anchor=anchor)
