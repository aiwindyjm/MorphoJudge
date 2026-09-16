"""PARSE-003: call and data-operation extraction.

Deterministic rules over syntax trees:
- Local imports (relative + "@/"-style alias) resolve to repo files; named
  bindings (incl. `as` aliases) map call identifiers to target symbols.
- Direct calls resolve; computed member/subscript calls stay `candidate`;
  dynamic imports and unknown identifiers stay `unresolved`.
- SQL strings produce reads/writes table edges; fetch/fs/child_process
  produce sends/reads/writes edges with the analyzability recorded honestly.

No target code is imported or executed; specifiers are pure text.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from tree_sitter import Node

from .extract import Symbol
from .typescript import ParsedSource, walk_nodes

DEFAULT_ALIAS_MAP = {"@": "src"}

_LOCAL_EXTENSIONS = [".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx", "/index.js"]

FS_READ_OPS = {"readFile", "readFileSync", "readdir", "stat", "access", "read"}
FS_WRITE_OPS = {"writeFile", "writeFileSync", "appendFile", "mkdir", "unlink", "rm", "rename", "chmod"}

_BUILTIN_FS_SOURCES = {"node:fs/promises", "node:fs", "fs", "fs/promises"}
_BUILTIN_CHILD_PROCESS = {"node:child_process", "child_process"}

_SQL_PATTERNS = [
    (re.compile(r"\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE), "SELECT"),
    (re.compile(r"\bINSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE), "INSERT"),
    (re.compile(r"\bUPDATE\s+([A-Za-z_][A-Za-z0-9_]*)\s+SET", re.IGNORECASE), "UPDATE"),
    (re.compile(r"\bDELETE\s+FROM\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE), "DELETE"),
]

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class ImportRef:
    local_name: str
    imported_name: str | None  # None for namespace/default
    source_specifier: str
    resolved_path: str | None  # repo path when local
    external_kind: str | None  # builtin | package | None


@dataclass
class RawEdge:
    relation: str  # calls | reads | writes | sends
    source_id: str
    target_label: str | None  # data:/svc: labels for synthesized targets
    target_id: str | None  # set when target is a known method node
    resolution: str  # resolved | candidate | unresolved
    path: str
    line: int
    note: str | None = None


def resolve_specifier(specifier: str, importing_path: str, alias_map: dict[str, str]) -> str | None:
    """Resolve an import specifier to a repo-relative path (no fs access)."""

    for prefix, target in sorted(alias_map.items(), key=lambda kv: -len(kv[0])):
        if specifier == prefix or specifier.startswith(prefix + "/"):
            rest = specifier[len(prefix) :].lstrip("/")
            candidate = posixpath.normpath(posixpath.join(target, rest))
            return candidate if not candidate.startswith("..") else None
    if specifier.startswith("./") or specifier.startswith("../"):
        base_dir = posixpath.dirname(importing_path)
        candidate = posixpath.normpath(posixpath.join(base_dir, specifier))
        return candidate if not candidate.startswith("..") else None
    return None


def _external_kind(specifier: str) -> str | None:
    if specifier.startswith("node:") or specifier in {
        "fs", "fs/promises", "child_process", "path", "os", "util", "crypto",
    }:
        return "builtin"
    return "package"


def _with_extension(candidate: str, known_paths) -> str | None:
    if candidate in known_paths:
        return candidate
    for ext in _LOCAL_EXTENSIONS:
        if candidate + ext in known_paths:
            return candidate + ext
    return None


def build_import_index(
    parsed_by_path: dict[str, ParsedSource],
    alias_map: dict[str, str] | None = None,
) -> dict[str, list[ImportRef]]:
    alias_map = alias_map or DEFAULT_ALIAS_MAP
    known_paths = set(parsed_by_path)
    index: dict[str, list[ImportRef]] = {}

    for path, parsed in parsed_by_path.items():
        refs: list[ImportRef] = []
        if parsed.root is None:
            index[path] = refs
            continue
        for node in walk_nodes(parsed.root):
            if node.type != "import_statement":
                continue
            source_node = next(
                (child for child in node.children if child.type == "string"), None
            )
            if source_node is None:
                continue
            specifier = parsed.node_text(source_node).strip("\"'")
            resolved = resolve_specifier(specifier, path, alias_map)
            if resolved is not None:
                resolved = _with_extension(resolved, known_paths)
            kind = None if resolved else _external_kind(specifier)

            for clause in node.children:
                if clause.type != "import_clause":
                    continue
                for part in clause.children:
                    if part.type == "named_imports":
                        for spec in part.children:
                            if spec.type != "import_specifier":
                                continue
                            original = spec.child_by_field_name("name")
                            alias = spec.child_by_field_name("alias")
                            refs.append(
                                ImportRef(
                                    local_name=parsed.node_text(alias if alias else original),
                                    imported_name=parsed.node_text(original),
                                    source_specifier=specifier,
                                    resolved_path=resolved,
                                    external_kind=kind,
                                )
                            )
                    elif part.type == "identifier":
                        refs.append(
                            ImportRef(
                                local_name=parsed.node_text(part),
                                imported_name="default",
                                source_specifier=specifier,
                                resolved_path=resolved,
                                external_kind=kind,
                            )
                        )
                    elif part.type == "namespace_import":
                        name = part.child_by_field_name("name")
                        if name is None:
                            name = next(
                                (c for c in part.children if c.type == "identifier"), None
                            )
                        if name is not None:
                            refs.append(
                                ImportRef(
                                    local_name=parsed.node_text(name),
                                    imported_name="*",
                                    source_specifier=specifier,
                                    resolved_path=resolved,
                                    external_kind=kind,
                                )
                            )
        index[path] = refs
    return index


def _module_const_strings(parsed: ParsedSource) -> dict[str, str]:
    """const NAME = "literal" at any level (simple dataflow for URL/dir bases)."""
    values: dict[str, str] = {}
    if parsed.root is None:
        return values
    for node in walk_nodes(parsed.root):
        if node.type != "variable_declarator":
            continue
        name_node = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if name_node is None or value is None or value.type != "string":
            continue
        text = parsed.node_text(value).strip("\"'")
        values[parsed.node_text(name_node)] = text
    return values


def _module_registries(parsed: ParsedSource, builtin_imports: set[str]) -> dict[str, list[str]]:
    """const R = { k: <builtin-imported-name>, ... } — child_process handlers."""
    registries: dict[str, list[str]] = {}
    if parsed.root is None:
        return registries
    for node in walk_nodes(parsed.root):
        if node.type != "variable_declarator":
            continue
        name_node = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if name_node is None or value is None or value.type != "object":
            continue
        entries: list[str] = []
        for pair in value.children:
            if pair.type != "pair":
                continue
            val = pair.child_by_field_name("value")
            if val is not None and val.type == "identifier" and parsed.node_text(val) in builtin_imports:
                entries.append(parsed.node_text(pair.child_by_field_name("key")))
        if entries:
            registries[parsed.node_text(name_node)] = entries
    return registries


def _sql_tables(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for regex, verb in _SQL_PATTERNS:
        match = regex.search(text)
        if match:
            found.append((verb, match.group(1)))
    return found


def _string_like_text(node: Node, parsed: ParsedSource) -> str | None:
    """Static text of a string/template node; None when fully dynamic."""
    if node.type == "string":
        return parsed.node_text(node).strip("\"'")
    if node.type == "template_string":
        return parsed.node_text(node)
    return None


def extract_relation_edges(
    parsed_by_path: dict[str, ParsedSource],
    symbols_by_path: dict[str, list[Symbol]],
    import_index: dict[str, list[ImportRef]],
) -> list[RawEdge]:
    edges: list[RawEdge] = []

    for path, parsed in parsed_by_path.items():
        if parsed.root is None:
            continue
        imports = import_index.get(path, [])
        imports_by_local = {ref.local_name: ref for ref in imports}
        const_strings = _module_const_strings(parsed)
        child_builtins = {
            ref.local_name
            for ref in imports
            if ref.source_specifier in _BUILTIN_CHILD_PROCESS
        }
        registries = _module_registries(parsed, child_builtins)

        # Cross-file registry bindings: imported name -> source registry var.
        imported_registries: dict[str, tuple[str, list[str]]] = {}
        for ref in imports:
            if ref.resolved_path and ref.imported_name:
                other = parsed_by_path.get(ref.resolved_path)
                if other is None:
                    continue
                other_builtins = {
                    r.local_name
                    for r in import_index.get(ref.resolved_path, [])
                    if r.source_specifier in _BUILTIN_CHILD_PROCESS
                }
                for var, entries in _module_registries(other, other_builtins).items():
                    if var == ref.imported_name:
                        imported_registries[ref.local_name] = (ref.resolved_path, entries)

        local_symbols = {symbol.name: symbol for symbol in symbols_by_path.get(path, [])}

        def symbol_node_id(source_path: str, name: str) -> str | None:
            for candidate in symbols_by_path.get(source_path, []):
                if candidate.name == name:
                    return candidate.node_id
            return None

        def add(relation, source, label, target_id, resolution, line, note=None):
            edges.append(
                RawEdge(
                    relation=relation,
                    source_id=source,
                    target_label=label,
                    target_id=target_id,
                    resolution=resolution,
                    path=path,
                    line=line,
                    note=note,
                )
            )

        for symbol in symbols_by_path.get(path, []):
            # 找到该符号的语法节点，遍历其子树内的调用点
            symbol_nodes = [
                node
                for node in walk_nodes(parsed.root)
                if node.type in ("function_declaration", "method_definition", "variable_declarator")
                and _node_name(node, parsed) == symbol.name
                and parsed.node_lines(node)[0] == symbol.start_line
            ]
            if not symbol_nodes:
                continue
            subtree = symbol_nodes[0]

            for node in walk_nodes(subtree):
                if node.type == "call_expression":
                    line, _ = parsed.node_lines(node)
                    function = node.child_by_field_name("function")
                    arguments = node.child_by_field_name("arguments")

                    # --- dynamic import: import(x) 的 callee 节点类型是 import ---
                    if function is not None and function.type in ("identifier", "import") and parsed.node_text(function) == "import":
                        add("calls", symbol.node_id, None, None, "unresolved", line, "dynamic_import")
                        continue

                    # --- SQL in arguments（字符串/模板/常量引用） ---
                    if arguments is not None:
                        for arg in arguments.children:
                            text = _string_like_text(arg, parsed)
                            if text is None and arg.type == "identifier":
                                text = const_strings.get(parsed.node_text(arg))
                            if text:
                                for verb, table in _sql_tables(text):
                                    relation = "reads" if verb == "SELECT" else "writes"
                                    add(relation, symbol.node_id, f"table:{table}", None, "resolved", line, f"sql_{verb.lower()}")

                    if function is None:
                        continue
                    callee_text = parsed.node_text(function)

                    # --- fetch(...)（未被本地同名函数或导入绑定遮蔽时） ---
                    if (
                        function.type == "identifier"
                        and callee_text == "fetch"
                        and callee_text not in local_symbols
                        and callee_text not in imports_by_local
                    ):
                        _handle_fetch(add, symbol, arguments, parsed, const_strings, line)
                        continue

                    # --- fs builtins（具名导入直接调用） ---
                    ref = imports_by_local.get(callee_text) if function.type == "identifier" else None
                    if ref is not None and ref.source_specifier in _BUILTIN_FS_SOURCES and ref.imported_name:
                        op = ref.imported_name
                        relation = "reads" if op in FS_READ_OPS else "writes"
                        label, resolution = _file_target(arguments, parsed, const_strings)
                        add(relation, symbol.node_id, label, None, resolution, line, f"fs_{op}")
                        continue

                    # --- computed calls: obj[expr]() 或 变量别名调用 ---
                    if function.type == "subscript_expression":
                        obj = function.child_by_field_name("object")
                        if obj is not None and obj.type == "identifier":
                            obj_name = parsed.node_text(obj)
                            if obj_name in registries or obj_name in imported_registries:
                                add("sends", symbol.node_id, f"process:{obj_name}", None, "candidate", line, "registry_dispatch")
                                continue
                        add("calls", symbol.node_id, None, None, "candidate", line, "computed_callee")
                        continue

                    if function.type == "identifier":
                        # 本地符号
                        if callee_text in local_symbols and local_symbols[callee_text].node_id != symbol.node_id:
                            add("calls", symbol.node_id, None, local_symbols[callee_text].node_id, "resolved", line)
                            continue
                        # 注册表别名 const h = R[name]; h(...)
                        alias_target = _registry_alias_target(callee_text, parsed, set(registries) | set(imported_registries))
                        if alias_target:
                            add("sends", symbol.node_id, f"process:{alias_target}", None, "candidate", line, "registry_alias_call")
                            continue
                        # 导入绑定
                        if ref is not None:
                            if ref.resolved_path:
                                target = symbol_node_id(ref.resolved_path, ref.imported_name or "")
                                if target and target != symbol.node_id:
                                    add("calls", symbol.node_id, None, target, "resolved", line, "cross_file")
                                else:
                                    add("calls", symbol.node_id, None, None, "unresolved", line, "imported_symbol_not_found")
                            else:
                                add("calls", symbol.node_id, None, None, "unresolved", line, f"external_{ref.external_kind}")
                            continue
                        # 未知标识符
                        add("calls", symbol.node_id, None, None, "unresolved", line, "unknown_identifier")
                        continue

                    if function.type == "member_expression":
                        # 命名空间/成员调用：无类型信息，一律 candidate（不猜测）
                        add("calls", symbol.node_id, None, None, "candidate", line, "member_call")
                        continue

    return edges


def _node_name(node: Node, parsed: ParsedSource) -> str | None:
    name = node.child_by_field_name("name")
    if name is not None:
        return parsed.node_text(name)
    return None


def _registry_alias_target(name: str, parsed: ParsedSource, registry_names: set[str]) -> str | None:
    """const h = R[name] —— h 是注册表项别名时返回注册表变量名。"""
    if parsed.root is None:
        return None
    for node in walk_nodes(parsed.root):
        if node.type != "variable_declarator":
            continue
        name_node = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if name_node is None or value is None or value.type != "subscript_expression":
            continue
        if parsed.node_text(name_node) != name:
            continue
        obj = value.child_by_field_name("object")
        if obj is not None and obj.type == "identifier" and parsed.node_text(obj) in registry_names:
            return parsed.node_text(obj)
    return None


def _handle_fetch(add, symbol, arguments, parsed, const_strings, line) -> None:
    arg0 = None
    if arguments is not None:
        arg0 = next((child for child in arguments.children if child.type in ("string", "template_string", "identifier")), None)
    if arg0 is None:
        add("sends", symbol.node_id, "svc:unknown", None, "unresolved", line, "fetch_no_static_arg")
        return
    if arg0.type == "string":
        url = parsed.node_text(arg0).strip("\"'")
        host = _host_of(url)
        if host:
            add("sends", symbol.node_id, f"svc:{host}", None, "resolved", line, "fetch_literal")
        else:
            add("sends", symbol.node_id, "svc:unknown", None, "candidate", line, "fetch_non_http")
        return
    if arg0.type == "template_string":
        template = parsed.node_text(arg0)
        host, fully_static = _static_host_from_template(template, const_strings)
        if host:
            resolution = "resolved" if fully_static else "candidate"
            note = "fetch_literal" if fully_static else "fetch_template"
            add("sends", symbol.node_id, f"svc:{host}", None, resolution, line, note)
        else:
            resolution = "resolved" if "${" not in template else "candidate"
            add("sends", symbol.node_id, "svc:unknown", None, resolution, line, "fetch_dynamic")
        return
    if arg0.type == "identifier":
        value = const_strings.get(parsed.node_text(arg0))
        if value and _host_of(value):
            add("sends", symbol.node_id, f"svc:{_host_of(value)}", None, "resolved", line, "fetch_const")
        else:
            add("sends", symbol.node_id, "svc:unknown", None, "candidate", line, "fetch_dynamic")
        return


def _file_target(arguments, parsed: ParsedSource, const_strings: tuple | dict) -> tuple[str, str]:
    arg0 = None
    if arguments is not None:
        arg0 = next((child for child in arguments.children if child.type in ("string", "template_string", "identifier")), None)
    if arg0 is not None and arg0.type == "string":
        literal = parsed.node_text(arg0)
        return f"file:{literal.strip(chr(34)).strip(chr(39))}", "resolved"
    if arg0 is not None and arg0.type == "identifier":
        value = const_strings.get(parsed.node_text(arg0))
        if value:
            return f"file:{value}", "candidate"
        return "file:unknown", "candidate"
    return "file:unknown", "candidate"


_TEMPLATE_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _static_host_from_template(template: str, const_strings: dict[str, str]) -> tuple[str | None, bool]:
    """Accumulate static text of a template until the first unknown variable.

    Returns (host, fully_static). Known const variables are substituted;
    hitting an unknown variable keeps the accumulated prefix and marks the
    result non-static (candidate).
    """

    accumulated = ""
    template = template.strip("`")
    pos = 0
    fully_static = True
    for match in _TEMPLATE_VAR_RE.finditer(template):
        accumulated += template[pos:match.start()]
        var = match.group(1)
        value = const_strings.get(var)
        if value is None:
            fully_static = False
            break
        accumulated += value
        pos = match.end()
    else:
        accumulated += template[pos:]
    return _host_of(accumulated), fully_static


def _host_of(url: str) -> str | None:
    try:
        parsed_url = urlparse(url)
    except ValueError:
        return None
    if parsed_url.scheme in ("http", "https") and parsed_url.hostname:
        host = parsed_url.hostname
        if parsed_url.port:
            host = f"{host}:{parsed_url.port}"
        return host
    return None


def referenced_type_names(type_text: str) -> list[str]:
    return _IDENT_RE.findall(type_text or "")
