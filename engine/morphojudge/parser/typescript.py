"""PARSE-001: TypeScript/JavaScript parsing via Tree-sitter.

Deterministic, in-process, text-only: sources arrive as bytes (from git
blobs or inline test strings), are decoded as UTF-8 and parsed with the
matching grammar. Nothing here executes, imports or evaluates module code;
comments and string contents are pure data and can never become
instructions. Every symbol/error carries byte spans plus 1-based lines.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

import tree_sitter
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Node

from ..contracts.domain import ParseStatus  # single frozen definition

MAX_PARSE_BYTES = 2_000_000
MAX_ISSUES = 50

_LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".py": "python",
}


@dataclass(frozen=True)
class ParseIssue:
    message: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int


@dataclass
class ParsedSource:
    path: str
    language: str
    status: ParseStatus
    text: str | None = None
    root: Node | None = None
    issues: list[ParseIssue] = field(default_factory=list)
    note: str | None = None
    line_starts: list[int] = field(default_factory=list)
    data: bytes | None = None

    def line_for_byte(self, byte_offset: int) -> int:
        return bisect_right(self.line_starts, byte_offset)

    def node_lines(self, node: Node) -> tuple[int, int]:
        return self.line_for_byte(node.start_byte), self.line_for_byte(node.end_byte)

    def node_text(self, node: Node) -> str:
        """Exact source text of a node (byte span decoded; safe for Unicode)."""
        if self.data is None:
            raise ValueError(f"parsed source {self.path!r} has no data")
        return self.data[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def language_for_path(path: str) -> str | None:
    lowered = path.lower()
    for extension, language in _LANGUAGE_BY_EXTENSION.items():
        if lowered.endswith(extension):
            return language
    return None


_PARSERS: dict[str, tree_sitter.Parser] = {}


def _parser_for(language: str) -> tree_sitter.Parser:
    parser = _PARSERS.get(language)
    if parser is None:
        if language == "typescript":
            grammar = tree_sitter_typescript.language_typescript()
        elif language == "tsx":
            grammar = tree_sitter_typescript.language_tsx()
        elif language == "javascript":
            grammar = tree_sitter_javascript.language()
        elif language == "python":
            grammar = tree_sitter_python.language()
        else:
            raise ValueError(f"unsupported language: {language}")
        parser = tree_sitter.Parser(tree_sitter.Language(grammar))
        _PARSERS[language] = parser
    return parser


def _line_starts(data: bytes) -> list[int]:
    starts = [0]
    for index, byte in enumerate(data):
        if byte == 0x0A:
            starts.append(index + 1)
    return starts


def _collect_issues(root: Node, line_starts: list[int]) -> tuple[list[ParseIssue], bool]:
    issues: list[ParseIssue] = []
    truncated = False
    stack = [root]
    while stack:
        node = stack.pop()
        is_error = node.type == "ERROR"
        if is_error or node.is_missing:
            if len(issues) >= MAX_ISSUES:
                truncated = True
                break
            start_line = bisect_right(line_starts, node.start_byte)
            end_line = bisect_right(line_starts, node.end_byte)
            message = "syntax_error" if is_error else f"missing_{node.type}"
            issues.append(
                ParseIssue(
                    message=message,
                    start_line=start_line,
                    end_line=end_line,
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                )
            )
        stack.extend(node.children)
    return sorted(issues, key=lambda issue: issue.start_byte), truncated


def parse_source(path: str, data: bytes) -> ParsedSource:
    """Parse one source file. Never raises for content problems; problematic
    inputs come back with status limited/error and a note."""

    language = language_for_path(path)
    if language is None:
        return ParsedSource(
            path=path,
            language="unknown",
            status=ParseStatus.ERROR,
            note="no_ts_grammar_for_extension",
        )

    if len(data) > MAX_PARSE_BYTES:
        return ParsedSource(
            path=path,
            language=language,
            status=ParseStatus.LIMITED,
            note=f"oversize_{len(data)}_bytes",
        )

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        issue_start = 0
        return ParsedSource(
            path=path,
            language=language,
            status=ParseStatus.ERROR,
            issues=[
                ParseIssue(
                    message="utf8_decode_failed",
                    start_line=1,
                    end_line=1,
                    start_byte=issue_start,
                    end_byte=issue_start,
                )
            ],
            note=f"utf8_error_at_byte_{exc.start}",
        )

    tree = _parser_for(language).parse(data)
    root = tree.root_node
    line_starts = _line_starts(data)
    issues, truncated = _collect_issues(root, line_starts)

    status = ParseStatus.PARSED_WITH_ERRORS if issues else ParseStatus.PARSED
    note = "issues_truncated_at_50" if truncated else None
    return ParsedSource(
        path=path,
        language=language,
        status=status,
        text=text,
        root=root,
        issues=issues,
        note=note,
        line_starts=line_starts,
        data=data,
    )


def walk_nodes(root: Node):
    """Deterministic pre-order traversal of all nodes."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))
