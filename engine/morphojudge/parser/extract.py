"""PARSE-002: symbol / route / event / contract extraction.

All facts come from syntax trees; the manifest is loaded as DATA only and is
the sole source of feature semantics (never method names). No module code is
executed; comments and strings are inert text.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterable

from tree_sitter import Node

from .typescript import ParsedSource, walk_nodes

_ROUTE_RE = re.compile(r"(?:^|/)pages/(.+)\.(?:tsx|ts|jsx|js)$", re.IGNORECASE)


def normalized_body_fingerprint(body_text: str) -> str:
    """Hash of a function body with blank lines and trailing whitespace
    removed, so inserting empty lines does not change the identity of a
    method across base/target alignment (B03-01)."""

    normalized = "\n".join(
        line.strip() for line in body_text.splitlines() if line.strip()
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def route_pattern_for(path: str) -> str | None:
    """Both real Next.js conventions are supported as pure file facts:
    - pages/<segments>/page.tsx  (route/<segments>)
    - pages/<segments>.tsx       (pages-router flat file, route/<segments>)
    Underscore-prefixed files (_app/_document/...) are not routes.
    """

    match = _ROUTE_RE.search(path)
    if match is None:
        return None
    segments = match.group(1)
    if segments.split("/")[-1].startswith("_"):
        return None
    if segments.endswith("/page"):
        segments = segments[: -len("/page")]
    elif segments == "page":
        segments = ""
    if not segments:
        return "/"
    return "/" + segments


@dataclass(frozen=True)
class TypeReference:
    """One type-name reference inside an annotation, with its own AST span.

    Extracted from type syntax nodes (type_identifier 等) — never from
    regex over full text — so each reference keeps its exact position
    even when the annotation spans multiple lines (union/intersection/
    nested generics/repeated names).
    """

    name: str
    start_byte: int
    end_byte: int
    line: int


def extract_type_references(annotation: Node, parsed: ParsedSource) -> list[TypeReference]:
    """Collect every type-name reference with its own position.

    Only type syntax nodes count: type_identifier (plain names) and
    nested_type_identifier (qualified names, recorded whole without
    descending, so A.B yields one reference, not three). Comments, string
    literals and property keys are other node kinds and are ignored.
    """

    references: list[TypeReference] = []

    def visit(node: Node) -> None:
        if node.type in ("type_identifier", "nested_type_identifier"):
            references.append(
                TypeReference(
                    name=parsed.node_text(node),
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                    line=parsed.node_lines(node)[0],
                )
            )
            return  # 不再下钻：限定名的成员不单独成引用
        for child in node.children:
            visit(child)

    for child in annotation.children:
        visit(child)
    return references


@dataclass(frozen=True)
class Param:
    name: str
    type_text: str | None
    # 类型引用所在行（1-based，指向注解节点，而非方法首行）
    line: int | None = None
    # 逐引用精确位置（联合/交叉/嵌套泛型/重复引用各自独立）
    type_refs: tuple[TypeReference, ...] = ()


@dataclass
class Symbol:
    name: str
    path: str
    kind: str  # function | const | method
    start_line: int
    end_line: int
    exported: bool
    params: list[Param] = field(default_factory=list)
    return_type: str | None = None
    # 返回类型引用所在行（1-based，指向注解节点）
    return_type_line: int | None = None
    # 逐引用精确位置（联合/交叉/嵌套泛型/重复引用各自独立）
    return_type_refs: tuple[TypeReference, ...] = ()
    enclosing_class: str | None = None
    # 规范化函数体指纹（空行/行尾空白不敏感），用于 base/target 方法对齐
    body_fingerprint: str = ""

    @property
    def node_id(self) -> str:
        return f"method:{self.path}:{self.name}:{self.start_line}"

    @property
    def alignment_key(self) -> str:
        """Stable cross-commit identity: path + name + body fingerprint.
        Deliberately excludes line numbers so whitespace-only changes keep
        the same key; body edits change it (the method 'evolved')."""

        return f"{self.path}|{self.name}|{self.body_fingerprint[:16] if self.body_fingerprint else 'nobody'}"


@dataclass(frozen=True)
class ContractDecl:
    name: str
    path: str
    start_line: int
    end_line: int
    kind: str  # interface | type_alias

    @property
    def node_id(self) -> str:
        return f"contract:{self.path}:{self.name}"


@dataclass(frozen=True)
class RouteDecl:
    pattern: str
    path: str

    @property
    def node_id(self) -> str:
        return f"page:{self.pattern}"


@dataclass(frozen=True)
class EventHandler:
    event: str
    handler: str
    page_path: str
    line: int


@dataclass(frozen=True)
class FeatureMapping:
    page_path: str
    feature_id: str
    feature: str
    events: tuple[str, ...]

    @property
    def node_id(self) -> str:
        return f"feature:{self.feature_id}"


def _strip_colon(text: str) -> str:
    return text.lstrip(":").strip()


def _annotation_type_line(annotation: Node, parsed: ParsedSource) -> int:
    """类型引用行 = 注解内部类型表达式节点的起始行。

    多行注解时注解节点从冒号开始（如 ":\\n    Output"），而引用点在
    类型名所在行；取内部类型子节点避免把冒号行当引用行。
    """
    type_children = [child for child in annotation.children if child.type != ":"]
    if len(type_children) == 1:
        return parsed.node_lines(type_children[0])[0]
    return parsed.node_lines(annotation)[0]


def _params_from(node: Node, parsed: ParsedSource) -> list[Param]:
    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        return []
    params: list[Param] = []
    for child in params_node.children:
        if child.type not in ("required_parameter", "optional_parameter", "rest_pattern"):
            continue
        pattern = child.child_by_field_name("pattern")
        name = "<pattern>"
        if pattern is not None and pattern.type == "identifier":
            name = parsed.node_text(pattern)
        elif pattern is not None and pattern.type == "rest_pattern":
            inner = next((c for c in pattern.children if c.type == "identifier"), None)
            if inner is not None:
                name = parsed.node_text(inner)
        # 此 grammar 中 type_annotation 是具名子节点而非字段
        annotation = next(
            (c for c in child.children if c.type == "type_annotation"), None
        )
        type_text: str | None = None
        annotation_line: int | None = None
        type_refs: tuple[TypeReference, ...] = ()
        if annotation is not None:
            type_text = _strip_colon(parsed.node_text(annotation)) or None
            annotation_line = _annotation_type_line(annotation, parsed)
            type_refs = tuple(extract_type_references(annotation, parsed))
        fallback_line = parsed.node_lines(child)[0]
        params.append(
            Param(
                name=name,
                type_text=type_text,
                line=annotation_line or fallback_line,
                type_refs=type_refs,
            )
        )
    return params


def _return_type_of(node: Node, parsed: ParsedSource) -> tuple[str | None, int | None, tuple]:
    annotation = node.child_by_field_name("return_type")
    if annotation is None:
        return None, None, ()
    text = _strip_colon(parsed.node_text(annotation)) or None
    line = _annotation_type_line(annotation, parsed)
    refs = tuple(extract_type_references(annotation, parsed))
    return text, line, refs


def _function_symbol(
    node: Node, parsed: ParsedSource, exported: bool, enclosing: str | None, kind: str
) -> Symbol:
    name_node = node.child_by_field_name("name")
    name = parsed.node_text(name_node) if name_node is not None else "<anonymous>"
    start_line, end_line = parsed.node_lines(node)
    return_type, return_type_line, return_type_refs = _return_type_of(node, parsed)
    body = node.child_by_field_name("body")
    body_fp = normalized_body_fingerprint(parsed.node_text(body)) if body is not None else ""
    return Symbol(
        name=name,
        path=parsed.path,
        kind=kind,
        start_line=start_line,
        end_line=end_line,
        exported=exported,
        params=_params_from(node, parsed),
        return_type=return_type,
        return_type_line=return_type_line,
        return_type_refs=return_type_refs,
        enclosing_class=enclosing,
        body_fingerprint=body_fp,
    )


def _const_function_symbol(
    declarator: Node, value: Node, parsed: ParsedSource, exported: bool
) -> Symbol | None:
    name_node = declarator.child_by_field_name("name")
    if name_node is None or name_node.type != "identifier":
        return None
    symbol = _function_symbol(value, parsed, exported, None, "const")
    # 位置以声明器为准（变量名所在行）
    start_line, end_line = parsed.node_lines(declarator)
    symbol.start_line, symbol.end_line = start_line, end_line
    symbol.name = parsed.node_text(name_node)
    return symbol


def extract_symbols(parsed: ParsedSource) -> list[Symbol]:
    if parsed.root is None:
        return []
    symbols: list[Symbol] = []

    def exported(node: Node) -> bool:
        return node.type == "export_statement"

    parents: dict[int, Node] = {}
    for node in walk_nodes(parsed.root):
        for child in node.children:
            parents[id(child)] = node

    for node in walk_nodes(parsed.root):
        parent = parents.get(id(node))
        is_exported = parent is not None and exported(parent)
        if node.type == "function_declaration":
            symbols.append(_function_symbol(node, parsed, is_exported, None, "function"))
        elif node.type == "method_definition":
            class_parent = parents.get(id(parents.get(id(node))))
            enclosing = None
            if class_parent is not None and class_parent.type == "class_declaration":
                name_node = class_parent.child_by_field_name("name")
                if name_node is not None:
                    enclosing = parsed.node_text(name_node)
            symbols.append(_function_symbol(node, parsed, is_exported, enclosing, "method"))
        elif node.type == "variable_declarator":
            value = node.child_by_field_name("value")
            if value is not None and value.type in ("arrow_function", "function_expression"):
                symbol = _const_function_symbol(node, value, parsed, is_exported)
                if symbol is not None:
                    symbols.append(symbol)
    return symbols


def extract_contracts(parsed: ParsedSource) -> list[ContractDecl]:
    if parsed.root is None:
        return []
    contracts: list[ContractDecl] = []
    for node in walk_nodes(parsed.root):
        if node.type == "interface_declaration":
            kind = "interface"
        elif node.type == "type_alias_declaration":
            kind = "type_alias"
        else:
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        start_line, end_line = parsed.node_lines(node)
        contracts.append(
            ContractDecl(
                name=parsed.node_text(name_node),
                path=parsed.path,
                start_line=start_line,
                end_line=end_line,
                kind=kind,
            )
        )
    return contracts


def extract_routes(paths: Iterable[str]) -> list[RouteDecl]:
    routes: list[RouteDecl] = []
    for path in sorted(paths):
        pattern = route_pattern_for(path)
        if pattern is not None:
            routes.append(RouteDecl(pattern=pattern, path=path))
    return routes


def _default_export_function(parsed: ParsedSource) -> Node | None:
    if parsed.root is None:
        return None
    for node in parsed.root.children:
        if node.type != "export_statement":
            continue
        has_default = any(child.type == "default" for child in node.children)
        if not has_default:
            continue
        declaration = node.child_by_field_name("declaration")
        if declaration is not None and declaration.type == "function_declaration":
            return declaration
    return None


def extract_page_events(parsed: ParsedSource) -> list[EventHandler]:
    """Event handlers from the page module's default-export return object:

    export default function P() { return { onX: handler, ... } }
    Only identifier-valued pairs count as handler references.
    """

    entry = _default_export_function(parsed)
    if entry is None:
        return []
    body = entry.child_by_field_name("body")
    if body is None:
        return []

    handlers: list[EventHandler] = []
    for node in walk_nodes(body):
        if node.type != "return_statement":
            continue
        expression = next((child for child in node.children if child.type == "object"), None)
        if expression is None:
            continue
        for pair in expression.children:
            if pair.type != "pair":
                continue
            key = pair.child_by_field_name("key")
            value = pair.child_by_field_name("value")
            if key is None or value is None or value.type != "identifier":
                continue
            line, _ = parsed.node_lines(pair)
            handlers.append(
                EventHandler(
                    event=parsed.node_text(key),
                    handler=parsed.node_text(value),
                    page_path=parsed.path,
                    line=line,
                )
            )
    return handlers


def load_feature_mapping(manifest: dict) -> list[FeatureMapping]:
    """Feature semantics come ONLY from the human-confirmed manifest."""

    mappings: list[FeatureMapping] = []
    for entry in manifest.get("human_feature_mapping", []):
        if entry.get("confirmed_by") != "human":
            raise ValueError(
                f"feature mapping requires confirmed_by=human: {entry.get('feature_id')}"
            )
        mappings.append(
            FeatureMapping(
                page_path=entry["page"],
                feature_id=entry["feature_id"],
                feature=entry["feature"],
                events=tuple(entry.get("events", [])),
            )
        )
    return mappings


# ---------------------------------------------------------------------------
# JSDoc 提取（一致性规则输入；逐 @param/@returns 行号）
# ---------------------------------------------------------------------------

# 纯函数声明只认这些明确措辞；自然语言含糊描述保持未知
PURE_DECLARATION_MARKERS = ("no side effects", "no side-effects", "pure function", "无副作用", "纯函数")

_PARAM_RE = re.compile(r"@param\s+(?:\{([^}]*)\}\s*)?([A-Za-z_$][\w$]*)")
_RETURN_RE = re.compile(r"@returns?\s+(?:\{([^}]*)\})?")


@dataclass(frozen=True)
class JSDocParam:
    name: str
    type_text: str | None
    line: int


@dataclass(frozen=True)
class JSDocInfo:
    start_line: int
    end_line: int
    params: tuple[JSDocParam, ...]
    returns_type: str | None
    returns_line: int | None
    pure_declared: bool

    @property
    def has_declarations(self) -> bool:
        return bool(self.params) or self.returns_type is not None or self.pure_declared


def _parse_jsdoc(comment_text: str, start_line: int, end_line: int) -> JSDocInfo:
    params: list[JSDocParam] = []
    returns_type: str | None = None
    returns_line: int | None = None
    pure = False

    for offset, raw_line in enumerate(comment_text.splitlines()):
        line_number = start_line + offset
        param_match = _PARAM_RE.search(raw_line)
        if param_match:
            params.append(
                JSDocParam(
                    name=param_match.group(2),
                    type_text=param_match.group(1) or None,
                    line=line_number,
                )
            )
            continue
        return_match = _RETURN_RE.search(raw_line)
        if return_match and return_match.group(1):
            returns_type = return_match.group(1).strip()
            returns_line = line_number
            continue
        lowered = raw_line.lower()
        if any(marker in lowered for marker in PURE_DECLARATION_MARKERS):
            pure = True

    return JSDocInfo(
        start_line=start_line,
        end_line=end_line,
        params=tuple(params),
        returns_type=returns_type,
        returns_line=returns_line,
        pure_declared=pure,
    )


def extract_jsdoc_symbols(parsed: ParsedSource) -> dict[str, JSDocInfo]:
    """JSDoc（/** … */）按紧邻前置注释归属到函数类节点。

    key = symbol.node_id；注释内的指令永远只是文本。
    """

    if parsed.root is None:
        return {}

    parents: dict[int, Node] = {}
    for node in walk_nodes(parsed.root):
        for child in node.children:
            parents[id(child)] = node

    def anchor_node(node: Node) -> Node:
        parent = parents.get(id(node))
        if parent is not None and parent.type == "export_statement":
            return parent
        return node

    result: dict[str, JSDocInfo] = {}
    for node in walk_nodes(parsed.root):
        if node.type == "function_declaration":
            pass
        elif node.type == "variable_declarator":
            value = node.child_by_field_name("value")
            if value is None or value.type not in ("arrow_function", "function_expression"):
                continue
        elif node.type == "method_definition":
            pass
        else:
            continue

        anchor = anchor_node(node)
        parent = parents.get(id(anchor))
        if parent is None:
            continue
        siblings = parent.children
        try:
            index = next(i for i, sibling in enumerate(siblings) if sibling.id == anchor.id)
        except StopIteration:
            continue
        comment = None
        for previous in reversed(siblings[:index]):
            if previous.type == "comment":
                comment = previous
                break
            if previous.type not in ("comment",):
                break
        if comment is None:
            continue
        text = parsed.node_text(comment)
        if not text.startswith("/**"):
            continue
        start_line, end_line = parsed.node_lines(comment)
        name_node = node.child_by_field_name("name")
        if name_node is None and node.type == "variable_declarator":
            name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        symbol_start, _ = parsed.node_lines(node)
        node_id = f"method:{parsed.path}:{parsed.node_text(name_node)}:{symbol_start}"
        result[node_id] = _parse_jsdoc(text, start_line, end_line)
    return result
