"""Batch-02 pipeline: snapshot → full-tree selection → parse → IR → SoftwareMap.

Reads every analyzed file from git blobs of the frozen target commit
(never the working tree), parses selected TypeScript/JavaScript, extracts
entities/relations deterministically, and assembles a SoftwareMap bound to
the snapshot id. Manifest is data only; feature semantics never come from
method names.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from .contracts.domain import (
    CoverageSummary,
    DiffFileEntry,
    DiffFileStatus,
    FileParseReport,
    MapEdge,
    MapNode,
    MapRelation,
    Resolution,
    SelectionStatus,
    SoftwareMap,
)
from .git.blob import list_tree_blobs, read_blob_bytes
from .git.snapshot import SnapshotReport
from .parser import extract as ex
from .parser import relations as rel
from .parser import python_extract as py_ex
from .parser import python_relations as py_rel
from .parser.typescript import ParsedSource, ParseStatus, language_for_path, parse_source
from .selection.rules import SelectionRules
from .selection.service import decide_selection

MAX_NODES = 5000
MAX_EDGES = 20000
SUPPORTED_PARSE_LANGUAGES = {"typescript", "tsx", "javascript", "python"}


@dataclass
class BuildResult:
    software_map: SoftwareMap
    coverage: CoverageSummary
    decisions: list = field(default_factory=list)
    parse_reports: list[FileParseReport] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # 规则层（BEH/一致性）需要对方法做跨提交对齐，暴露符号表与路由映射
    symbols_by_path: dict = field(default_factory=dict)
    routes_by_path: dict = field(default_factory=dict)
    # 一致性规则输入：symbol node_id -> JSDoc 声明
    jsdoc_by_node_id: dict = field(default_factory=dict)


def _edge_id(source: str, relation: str, target: str, path: str, line: int) -> str:
    digest = hashlib.sha256(
        f"{source}|{relation}|{target}|{path}|{line}".encode("utf-8")
    ).hexdigest()[:16]
    return f"edge:{digest}"


def select_snapshot_files(
    repo: Path,
    commit: str,
    rules: SelectionRules,
    snapshot_id: str,
):
    """Full-tree selection reusing the SAME decision function as diff-based
    selection (preview/actual parity is structural, not duplicated)."""

    tree = list_tree_blobs(repo, commit)
    entries = [
        DiffFileEntry(
            path=path,
            status=DiffFileStatus.MODIFIED,  # 快照全量视角：文件两侧同身份
            is_symlink=(mode == "120000"),
            is_submodule=(mode == "160000"),
            old_bytes=size,
            new_bytes=size,
        )
        for path, (mode, size) in sorted(tree.items())
    ]
    return decide_selection(entries, rules, snapshot_id)


def build_contract_usage_edges(
    snapshot_id: str,
    symbols_by_path: dict[str, list],
    contracts_by_path: dict[str, list],
    import_index: dict,
) -> list[MapEdge]:
    """accepts/returns edges, one per AST type reference, each pointing at
    that reference's own line (union/intersection/nested-generic members and
    repeated names get distinct, exact positions)."""

    edges: list[MapEdge] = []
    seen_ids: set[str] = set()
    for path in sorted(symbols_by_path):
        local_contract_names = {c.name for c in contracts_by_path.get(path, [])}
        imported_contracts: dict[str, str] = {}  # type name -> contract node id
        for ref in import_index.get(path, []):
            if ref.resolved_path and ref.imported_name:
                for contract in contracts_by_path.get(ref.resolved_path, []):
                    if contract.name == ref.imported_name:
                        imported_contracts[contract.name] = contract.node_id
        for symbol in symbols_by_path[path]:
            references: list[tuple[str, str, int]] = []
            for param in symbol.params:
                for type_ref in param.type_refs:
                    references.append((type_ref.name, "accepts", type_ref.line))
            for type_ref in symbol.return_type_refs:
                references.append((type_ref.name, "returns", type_ref.line))
            for name, relation, line in references:
                target_id = None
                if name in local_contract_names:
                    target_id = f"contract:{path}:{name}"
                elif name in imported_contracts:
                    target_id = imported_contracts[name]
                if target_id is None:
                    continue
                edge_id = _edge_id(symbol.node_id, relation, target_id, path, line)
                if edge_id in seen_ids:
                    continue  # 同一位置的确定性重复产物去重，不输出重复 ID
                seen_ids.add(edge_id)
                edges.append(
                    MapEdge(
                        id=edge_id,
                        snapshot_id=snapshot_id,
                        relation=MapRelation(relation),
                        source_id=symbol.node_id,
                        target_id=target_id,
                        resolution=Resolution.RESOLVED,
                        note=f"type_annotation:{name}",
                        file_path=path,
                        line=line,
                    )
                )
    return edges


def build_software_map(
    repo: Path,
    snapshot: SnapshotReport,
    manifest: dict,
    rules: SelectionRules | None = None,
    *,
    commit: str | None = None,
) -> BuildResult:
    """Build the SoftwareMap for one snapshot side.

    commit defaults to the target commit; pass snapshot.identity.base_commit
    to build the base-side map for cross-commit alignment.
    """

    rules = rules or SelectionRules()
    snapshot_id = snapshot.identity.snapshot_id
    target_commit = commit if commit is not None else snapshot.identity.target_commit
    notes: list[str] = []

    decisions, coverage = select_snapshot_files(repo, target_commit, rules, snapshot_id)
    selected_paths = [
        decision.path for decision in decisions if decision.status == SelectionStatus.SELECTED
    ]

    parsed_by_path: dict[str, ParsedSource] = {}
    parse_reports: list[FileParseReport] = []
    for path in selected_paths:
        if language_for_path(path) not in SUPPORTED_PARSE_LANGUAGES:
            continue
        blob = read_blob_bytes(repo, target_commit, path)
        parsed = parse_source(path, blob)
        parsed_by_path[path] = parsed
        parse_reports.append(
            FileParseReport(
                path=path,
                status=parsed.status,
                issue_count=len(parsed.issues),
                note=parsed.note,
            )
        )
        if parsed.status != ParseStatus.PARSED:
            notes.append(f"parse:{path}:{parsed.status.value}")

    symbols_by_path: dict = {}
    contracts_by_path: dict = {}
    events_by_path: dict = {}
    jsdoc_by_node_id: dict = {}
    for path, parsed in parsed_by_path.items():
        if parsed.root is None:
            continue
        if parsed.language == "python":
            symbols_by_path[path] = py_ex.extract_symbols(parsed)
        else:
            symbols_by_path[path] = ex.extract_symbols(parsed)
        jsdoc_by_node_id.update(ex.extract_jsdoc_symbols(parsed))
        contracts_by_path[path] = ex.extract_contracts(parsed)
        events_by_path[path] = ex.extract_page_events(parsed)

    nodes: list[MapNode] = []
    edges: list[MapEdge] = []

    # --- pages ---
    route_by_path = {route.path: route for route in ex.extract_routes(parsed_by_path)}
    for path, route in sorted(route_by_path.items()):
        nodes.append(
            MapNode(
                id=route.node_id,
                snapshot_id=snapshot_id,
                kind="page",
                label=route.pattern,
                file_path=path,
                resolution=Resolution.RESOLVED,
                note="route:file_convention",
            )
        )

    # --- methods ---
    for path in sorted(symbols_by_path):
        for symbol in symbols_by_path[path]:
            nodes.append(
                MapNode(
                    id=symbol.node_id,
                    snapshot_id=snapshot_id,
                    kind="method",
                    label=symbol.name,
                    language="typescript" if path.endswith((".ts", ".tsx")) else "python" if path.endswith(".py") else "javascript",
                    file_path=path,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    resolution=Resolution.RESOLVED,
                )
            )

    # --- contracts ---
    for path in sorted(contracts_by_path):
        for contract in contracts_by_path[path]:
            nodes.append(
                MapNode(
                    id=contract.node_id,
                    snapshot_id=snapshot_id,
                    kind="contract",
                    label=contract.name,
                    language="typescript",
                    file_path=path,
                    start_line=contract.start_line,
                    end_line=contract.end_line,
                    resolution=Resolution.RESOLVED,
                )
            )

    # --- features (manifest only) ---
    feature_mappings = ex.load_feature_mapping(manifest)
    symbols_by_name: dict[str, list[ex.Symbol]] = {}
    for path in sorted(symbols_by_path):
        for symbol in symbols_by_path[path]:
            symbols_by_name.setdefault(symbol.name, []).append(symbol)
    for mapping in feature_mappings:
        nodes.append(
            MapNode(
                id=mapping.node_id,
                snapshot_id=snapshot_id,
                kind="feature",
                label=mapping.feature,
                file_path=mapping.page_path,
                resolution=Resolution.RESOLVED,
                note="feature_mapping:human",
            )
        )
        route = route_by_path.get(mapping.page_path)
        if route is not None:
            edges.append(
                MapEdge(
                    id=_edge_id(route.node_id, "triggers", mapping.node_id, mapping.page_path, 0),
                    snapshot_id=snapshot_id,
                    relation=MapRelation.TRIGGERS,
                    source_id=route.node_id,
                    target_id=mapping.node_id,
                    resolution=Resolution.RESOLVED,
                    note="manifest:page_feature",
                    file_path=mapping.page_path,
                    line=None,  # manifest 级事实，无单一适用行
                )
            )
        for event in mapping.events:
            handlers = symbols_by_name.get(event, [])
            handler = next((h for h in handlers if h.path == mapping.page_path), None)
            if handler is None and len(handlers) == 1:
                handler = handlers[0]
            if handler is None:
                notes.append(f"feature_event_unresolved:{mapping.feature_id}:{event}")
                continue
            edges.append(
                MapEdge(
                    id=_edge_id(mapping.node_id, "implements", handler.node_id, mapping.page_path, 0),
                    snapshot_id=snapshot_id,
                    relation=MapRelation.IMPLEMENTS,
                    source_id=mapping.node_id,
                    target_id=handler.node_id,
                    resolution=Resolution.RESOLVED,
                    note="manifest:event_handler",
                    file_path=handler.path,
                    line=handler.start_line,
                )
            )

    # --- behavior/data edges ---
    import_index = rel.build_import_index(parsed_by_path)
    raw_edges = rel.extract_relation_edges(parsed_by_path, symbols_by_path, import_index)
    # Python 行为边与 TS/JS 边合并（同一 RawEdge 接口，BEH-* 规则自动生效）
    raw_edges.extend(py_rel.extract_relation_edges(parsed_by_path, symbols_by_path, import_index))

    synthesized_ids: dict[str, str] = {}

    def target_node_id(label: str) -> str:
        node_id = label if label.startswith(("svc:", "data:")) else f"data:{label}"
        if label not in synthesized_ids:
            synthesized_ids[label] = node_id
            kind = "external_service" if label.startswith("svc:") else "data"
            if label.startswith("unknown:"):
                resolution = Resolution.UNRESOLVED
            elif ":unknown" in label:
                resolution = Resolution.CANDIDATE
            else:
                resolution = Resolution.RESOLVED
            nodes.append(
                MapNode(
                    id=node_id,
                    snapshot_id=snapshot_id,
                    kind=kind,
                    label=label,
                    resolution=resolution,
                    note="synthesized:behavior_target",
                )
            )
        return node_id

    seen_edge_ids: set[str] = set()
    for raw in raw_edges:
        if raw.target_id is not None:
            target_id = raw.target_id
        elif raw.target_label:
            target_id = target_node_id(raw.target_label)
        else:
            target_id = target_node_id(f"unknown:{raw.note or 'unresolved'}")
        edge_id = _edge_id(raw.source_id, raw.relation, target_id, raw.path, raw.line)
        if edge_id in seen_edge_ids:
            continue
        seen_edge_ids.add(edge_id)
        edges.append(
            MapEdge(
                id=edge_id,
                snapshot_id=snapshot_id,
                relation=MapRelation(raw.relation),
                source_id=raw.source_id,
                target_id=target_id,
                resolution=Resolution(raw.resolution),
                note=raw.note,
                file_path=raw.path,
                line=raw.line,
            )
        )

    # --- contract usage edges (accepts/returns，精确到类型注解行) ---
    edges.extend(
        build_contract_usage_edges(snapshot_id, symbols_by_path, contracts_by_path, import_index)
    )

    # --- budgets ---
    truncated = False
    if len(nodes) > MAX_NODES:
        nodes = nodes[:MAX_NODES]
        notes.append(f"nodes_truncated_at_{MAX_NODES}")
        truncated = True
    node_id_set = {node.id for node in nodes}
    if len(edges) > MAX_EDGES:
        edges = edges[:MAX_EDGES]
        notes.append(f"edges_truncated_at_{MAX_EDGES}")
        truncated = True
    edges = [edge for edge in edges if edge.source_id in node_id_set and edge.target_id in node_id_set]

    software_map = SoftwareMap(
        snapshot_id=snapshot_id,
        nodes=nodes,
        edges=edges,
        truncated=truncated,
        limits=notes,
        file_reports=parse_reports,
    )
    return BuildResult(
        software_map=software_map,
        coverage=coverage,
        decisions=decisions,
        parse_reports=parse_reports,
        notes=notes,
        symbols_by_path=symbols_by_path,
        routes_by_path={route.path: route for route in ex.extract_routes(parsed_by_path)},
        jsdoc_by_node_id=jsdoc_by_node_id,
    )


# ---------------------------------------------------------------------------
# BuildResult checkpoint 序列化（ANL-003 / Batch-04-R1）
# parse checkpoint 需要完整重建 BuildResult，恢复时跳过解析；这里只做
# 数据搬移，不引入新的推导逻辑。
# ---------------------------------------------------------------------------


def dump_build_state(result: BuildResult) -> dict:
    """BuildResult → JSON 可序列化 dict（纯 asdict/model_dump）。"""

    from dataclasses import asdict

    return {
        "software_map": result.software_map.model_dump(mode="json"),
        "coverage": result.coverage.model_dump(mode="json"),
        "decisions": [decision.model_dump(mode="json") for decision in result.decisions],
        "parse_reports": [report.model_dump(mode="json") for report in result.parse_reports],
        "notes": list(result.notes),
        "symbols_by_path": {
            path: [asdict(symbol) for symbol in symbols]
            for path, symbols in result.symbols_by_path.items()
        },
        "routes_by_path": {
            path: asdict(route) for path, route in result.routes_by_path.items()
        },
        "jsdoc_by_node_id": {
            node_id: asdict(info) for node_id, info in result.jsdoc_by_node_id.items()
        },
    }


def _symbol_from_state(data: dict) -> ex.Symbol:
    return ex.Symbol(
        name=data["name"],
        path=data["path"],
        kind=data["kind"],
        start_line=data["start_line"],
        end_line=data["end_line"],
        exported=data["exported"],
        params=[
            ex.Param(
                name=param["name"],
                type_text=param["type_text"],
                line=param.get("line"),
                type_refs=tuple(
                    ex.TypeReference(
                        name=ref["name"],
                        start_byte=ref["start_byte"],
                        end_byte=ref["end_byte"],
                        line=ref["line"],
                    )
                    for ref in param.get("type_refs") or ()
                ),
            )
            for param in data.get("params") or ()
        ],
        return_type=data.get("return_type"),
        return_type_line=data.get("return_type_line"),
        return_type_refs=tuple(
            ex.TypeReference(
                name=ref["name"],
                start_byte=ref["start_byte"],
                end_byte=ref["end_byte"],
                line=ref["line"],
            )
            for ref in data.get("return_type_refs") or ()
        ),
        enclosing_class=data.get("enclosing_class"),
        body_fingerprint=data.get("body_fingerprint", ""),
    )


def _jsdoc_from_state(data: dict) -> ex.JSDocInfo:
    return ex.JSDocInfo(
        start_line=data["start_line"],
        end_line=data["end_line"],
        params=tuple(
            ex.JSDocParam(
                name=param["name"],
                type_text=param.get("type_text"),
                line=param["line"],
            )
            for param in data.get("params") or ()
        ),
        returns_type=data.get("returns_type"),
        returns_line=data.get("returns_line"),
        pure_declared=data.get("pure_declared", False),
    )


def load_build_state(data: dict) -> BuildResult:
    """dump_build_state 的逆运算；输入来自本服务持久化的 checkpoint。"""

    from .contracts.domain import (
        CoverageSummary as _CoverageSummary,
        SelectionDecision as _SelectionDecision,
        SoftwareMap as _SoftwareMap,
    )

    software_map = _SoftwareMap.model_validate(data["software_map"])
    coverage = _CoverageSummary.model_validate(data["coverage"])
    decisions = [_SelectionDecision.model_validate(item) for item in data.get("decisions") or []]
    parse_reports = [
        FileParseReport.model_validate(item) for item in data.get("parse_reports") or []
    ]
    symbols_by_path = {
        path: [_symbol_from_state(symbol) for symbol in symbols]
        for path, symbols in (data.get("symbols_by_path") or {}).items()
    }
    routes_by_path = {
        path: ex.RouteDecl(pattern=route["pattern"], path=route["path"])
        for path, route in (data.get("routes_by_path") or {}).items()
    }
    jsdoc_by_node_id = {
        node_id: _jsdoc_from_state(info)
        for node_id, info in (data.get("jsdoc_by_node_id") or {}).items()
    }
    return BuildResult(
        software_map=software_map,
        coverage=coverage,
        decisions=decisions,
        parse_reports=parse_reports,
        notes=list(data.get("notes") or []),
        symbols_by_path=symbols_by_path,
        routes_by_path=routes_by_path,
        jsdoc_by_node_id=jsdoc_by_node_id,
    )
