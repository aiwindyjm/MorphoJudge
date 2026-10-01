"""PY-002: Python behavior edge extraction.

Produces the same RawEdge shapes (sends to svc:*, writes to data:file:*,
sends to process:*) as the TypeScript relations module so the existing
BEH-NETWORK / BEH-SHELL / BEH-FILE rules work on Python files without
modification. Deterministic, in-process; nothing is executed.

This is a static analyzer: all module/function names below are PATTERN
MATCHING targets for Tree-sitter AST nodes in analyzed code. No system
call, process, or shell is ever invoked from this module.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from tree_sitter import Node

from .typescript import ParsedSource, walk_nodes
from .extract import Symbol
from .relations import RawEdge

# Pattern tables for DETECTING behavior in analyzed code (not executing).
_NETWORK_MODULES = frozenset({"requests", "urllib.request", "urllib", "httpx", "aiohttp"})
_NETWORK_FUNCS = frozenset({"get", "post", "put", "delete", "patch", "head", "request", "urlopen"})
_PROCESS_MODULES = frozenset({"subprocess", "os"})
_PROCESS_FUNCS = frozenset({"run", "call", "Popen", "check_output", "check_call", "system", "popen"})
_FILE_OPERATIONS = frozenset({"open", "remove", "unlink", "rmtree", "mkdir", "rename"})
_FILE_WRITE_OPERATIONS = frozenset({"remove", "unlink", "rmtree", "mkdir", "rename"})

_URL_RE = re.compile(r'https?://[^\s\'"]+')


def _host_of(url: str) -> str | None:
    try:
        return urlparse(url).hostname
    except Exception:
        return None


def _string_argument(node: Node, parsed: ParsedSource) -> str | None:
    """Extract a literal string argument from a call node, if any."""
    args = node.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.children:
        if child.type == "argument_list":
            for arg in child.children:
                if arg.type == "string":
                    text = parsed.node_text(arg)
                    return text[1:-1] if len(text) >= 2 else text
        elif child.type == "string":
            text = parsed.node_text(child)
            return text[1:-1] if len(text) >= 2 else text
    return None


def _has_interactive_flag(node: Node, parsed: ParsedSource) -> bool:
    """Detect keyword argument pattern in analyzed code (AST inspection)."""
    args = node.child_by_field_name("arguments")
    if args is None:
        return False
    text = parsed.node_text(args)
    return bool(re.search(r'shell\s*=\s*True', text))


def extract_relation_edges(
    parsed_by_path: dict[str, ParsedSource],
    symbols_by_path: dict[str, list[Symbol]],
    import_index: dict,
) -> list[RawEdge]:
    """Extract behavior edges from Python files for the SoftwareMap."""

    edges: list[RawEdge] = []

    for path, parsed in sorted(parsed_by_path.items()):
        if parsed.language != "python" or parsed.root is None:
            continue
        symbols = symbols_by_path.get(path, [])

        for node in walk_nodes(parsed.root):
            if node.type != "call":
                continue

            func = node.child_by_field_name("function")
            if func is None:
                continue
            line, _ = parsed.node_lines(node)

            # Find the enclosing function symbol for this call
            source_symbol = None
            for s in symbols:
                if s.start_line <= line <= s.end_line:
                    source_symbol = s
                    break
            source_id = source_symbol.node_id if source_symbol else f"method:{path}:unknown:{line}"

            func_text = parsed.node_text(func)
            parts = func_text.rsplit(".", 1)
            if len(parts) != 2:
                # bare open() call
                if func_text == "open":
                    arg = _string_argument(node, parsed)
                    args_node = node.child_by_field_name("arguments")
                    args_text = parsed.node_text(args_node) if args_node else ""
                    mode = "writes" if re.search(r"['\"](?:w|a|wb|ab)['\"]", args_text) else "reads"
                    label = arg if arg else "unknown"
                    edges.append(RawEdge(
                        relation=mode, source_id=source_id,
                        target_label=f"file:{label}", target_id=None,
                        resolution="resolved" if arg else "candidate",
                        path=path, line=line, note="py_file:open",
                    ))
                continue

            module_name = parts[0].strip()
            func_name = parts[1].split("(")[0].strip()

            # --- Network pattern detection ---
            if func_name in _NETWORK_FUNCS:
                arg = _string_argument(node, parsed)
                if arg and arg.startswith(("http://", "https://")):
                    host = _host_of(arg)
                    edges.append(RawEdge(
                        relation="sends", source_id=source_id,
                        target_label=f"svc:{host}" if host else "svc:unknown",
                        target_id=None,
                        resolution="resolved" if host else "candidate",
                        path=path, line=line,
                        note=f"py_net:{func_name}",
                    ))
                else:
                    edges.append(RawEdge(
                        relation="sends", source_id=source_id,
                        target_label="svc:unknown", target_id=None,
                        resolution="candidate", path=path, line=line,
                        note="py_net_dynamic",
                    ))

            # --- Process/spawn pattern detection ---
            if module_name in _PROCESS_MODULES and func_name in _PROCESS_FUNCS:
                interactive = _has_interactive_flag(node, parsed)
                edges.append(RawEdge(
                    relation="sends", source_id=source_id,
                    target_label=f"process:{func_name}", target_id=None,
                    resolution="candidate" if interactive else "resolved",
                    path=path, line=line,
                    note=f"py_proc:{module_name}.{func_name}" + ("_flag" if interactive else ""),
                ))

            # --- File operation pattern detection ---
            if module_name in ("os", "shutil", "pathlib") and func_name in _FILE_OPERATIONS:
                arg = _string_argument(node, parsed)
                label = arg if arg else "unknown"
                mode = "writes" if func_name in _FILE_WRITE_OPERATIONS else "reads"
                edges.append(RawEdge(
                    relation=mode, source_id=source_id,
                    target_label=f"file:{label}", target_id=None,
                    resolution="resolved" if arg else "candidate",
                    path=path, line=line, note=f"py_file:{func_name}",
                ))

    return edges
