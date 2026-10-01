"""PY-001: Python symbol extraction via Tree-sitter.

Produces the same Symbol / ImportRef shapes as the TypeScript extractor so
the pipeline, behavior rules, evidence resolver and SoftwareMap consume
Python files without any language-specific branching. Deterministic,
in-process, text-only; nothing is executed or imported.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from tree_sitter import Node

from .typescript import ParsedSource, walk_nodes
from .extract import Symbol, Param
from .relations import ImportRef

def _normalized_body(text: str) -> str:
    lines = [line.rstrip() for line in text.split("\n") if line.strip()]
    return "\n".join(lines)


def _function_fingerprint(parsed: ParsedSource, node: Node) -> str:
    body = node.child_by_field_name("body")
    if body is None:
        return ""
    return hashlib.sha256(_normalized_body(parsed.node_text(body)).encode()).hexdigest()


def extract_symbols(parsed: ParsedSource) -> list[Symbol]:
    """Extract function symbols from Python AST."""
    if parsed.root is None:
        return []
    path = parsed.path
    symbols: list[Symbol] = []
    for node in walk_nodes(parsed.root):
        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = parsed.node_text(name_node)
            start_line, end_line = parsed.node_lines(node)
            params_node = node.child_by_field_name("parameters")
            params: list[Param] = []
            if params_node is not None:
                for child in params_node.children:
                    if child.type == "identifier":
                        params.append(Param(name=parsed.node_text(child), type_text=None))
            symbols.append(Symbol(
                name=name,
                path=path,
                kind="function",
                start_line=start_line,
                end_line=end_line,
                exported=not name.startswith("_"),
                params=params,
                body_fingerprint=_function_fingerprint(parsed, node),
            ))
    return symbols


def extract_imports(parsed: ParsedSource) -> list[ImportRef]:
    """Extract import references from Python AST."""
    if parsed.root is None:
        return []
    imports: list[ImportRef] = []
    path = parsed.path
    for node in walk_nodes(parsed.root):
        if node.type == "import_statement":
            # import X or import X as Y
            text = parsed.node_text(node)
            for clause in node.children:
                if clause.type == "dotted_name":
                    imports.append(ImportRef(
                        local_name=parsed.node_text(clause),
                        imported_name=None,
                        source_specifier=parsed.node_text(clause),
                        resolved_path=None,
                        external_kind="package",
                    ))
                elif clause.type == "aliased_import":
                    name = clause.child_by_field_name("name")
                    alias = clause.child_by_field_name("alias")
                    if name and alias:
                        imports.append(ImportRef(
                            local_name=parsed.node_text(alias),
                            imported_name=parsed.node_text(name),
                            source_specifier=parsed.node_text(name),
                            resolved_path=None,
                            external_kind="package",
                        ))
        elif node.type == "import_from_statement":
            # from X import Y, Z
            module = node.child_by_field_name("module_name")
            if module is None:
                continue
            source = parsed.node_text(module)
            for child in node.children:
                if child.type == "dotted_name" and child != module:
                    imports.append(ImportRef(
                        local_name=parsed.node_text(child),
                        imported_name=parsed.node_text(child),
                        source_specifier=source,
                        resolved_path=None,
                        external_kind="package",
                    ))
                elif child.type == "aliased_import":
                    name = child.child_by_field_name("name")
                    alias = child.child_by_field_name("alias")
                    if name and alias:
                        imports.append(ImportRef(
                            local_name=parsed.node_text(alias),
                            imported_name=parsed.node_text(name),
                            source_specifier=source,
                            resolved_path=None,
                            external_kind="package",
                        ))
    return imports


def build_import_index(parsed_by_path: dict[str, ParsedSource]) -> dict[str, list[ImportRef]]:
    """Build import index from all parsed Python files."""
    index: dict[str, list[ImportRef]] = {}
    for path, parsed in parsed_by_path.items():
        if parsed.language == "python" and parsed.root is not None:
            index[path] = extract_imports(parsed)
    return index
