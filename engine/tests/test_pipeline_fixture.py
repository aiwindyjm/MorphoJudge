"""Batch-02 pipeline over the REAL fixture: entities locatable, statuses
correct (alias/dynamic/parse-error/cycle), no fixture code executed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from morphojudge.git.snapshot import resolve_snapshot
from morphojudge.pipeline import build_software_map
from morphojudge.relations.graph import build_graph

RULES = "batch02-test"


@pytest.fixture(scope="module")
def pipeline_result(fixture_repo, fixture_roots, manifest):
    snapshot = resolve_snapshot(
        fixture_repo,
        base_ref="HEAD~1",
        target_ref="HEAD",
        rules_version=RULES,
        allowed_roots=fixture_roots,
    )
    return build_software_map(fixture_repo, snapshot, manifest)


@pytest.fixture(scope="module")
def nodes_by_id(pipeline_result):
    return {node.id: node for node in pipeline_result.software_map.nodes}


@pytest.fixture(scope="module")
def edges(pipeline_result):
    return pipeline_result.software_map.edges


def _labels(nodes_by_id, kind):
    return sorted(node.label for node in nodes_by_id.values() if node.kind == kind)


def test_all_fixture_routes_are_pages(pipeline_result, nodes_by_id):
    patterns = _labels(nodes_by_id, "page")
    assert patterns == sorted(
        ["/login", "/orders", "/orders/[id]", "/admin", "/admin/audit-log"]
    )
    for node in nodes_by_id.values():
        if node.kind == "page":
            assert node.resolution == "resolved"
            assert node.note == "route:file_convention"


def test_features_come_only_from_manifest(pipeline_result, nodes_by_id, manifest):
    feature_labels = {node.label for node in nodes_by_id.values() if node.kind == "feature"}
    expected = {entry["feature"] for entry in manifest["human_feature_mapping"]}
    assert feature_labels == expected, "feature semantics must never come from method names"
    features = [node for node in nodes_by_id.values() if node.kind == "feature"]
    assert all(node.note == "feature_mapping:human" for node in features)


def test_manifest_events_implement_methods(pipeline_result, nodes_by_id, edges, manifest):
    implements = [e for e in edges if e.relation == "implements"]
    assert implements, "manifest events must produce implements edges"
    handlers = {e.target_id for e in implements}
    for node_id in handlers:
        assert node_id in nodes_by_id
        assert nodes_by_id[node_id].kind == "method"
    triggers = [e for e in edges if e.relation == "triggers"]
    assert len(triggers) == len(manifest["human_feature_mapping"])


def test_key_methods_located_with_lines(pipeline_result, nodes_by_id):
    methods = {node.label: node for node in nodes_by_id.values() if node.kind == "method"}
    for name in ("createOrder", "deleteOrder", "listAuditLog", "submitLogin", "handleExportOrders"):
        assert name in methods, name
        assert methods[name].start_line >= 1
        assert methods[name].end_line >= methods[name].start_line
        assert methods[name].file_path.startswith("src/")
    assert methods["createOrder"].file_path == "src/db/queries.ts"


def test_contracts_include_fixture_api_types(pipeline_result, nodes_by_id):
    contract_labels = _labels(nodes_by_id, "contract")
    for expected in ("LoginResponse", "OrderRecord", "AuditRow", "LoginFormData"):
        assert expected in contract_labels, expected
    login_response = nodes_by_id.get("contract:src/lib/schema.ts:LoginResponse")
    assert login_response is not None and login_response.start_line >= 1


def test_database_read_write_edges(pipeline_result, edges):
    reads = [e for e in edges if e.relation == "reads" and e.target_id == "data:table:orders"]
    writes = [e for e in edges if e.relation == "writes" and e.target_id == "data:table:orders"]
    assert reads, "SELECT orders must produce reads edge"
    assert writes, "INSERT/DELETE orders must produce writes edges"
    assert all(e.resolution == "resolved" for e in reads + writes)
    audit = [e for e in edges if e.target_id == "data:table:audit_log"]
    assert audit and all(e.resolution == "resolved" for e in audit)


def test_external_services_and_send_edges(pipeline_result, nodes_by_id, edges):
    svc_labels = _labels(nodes_by_id, "external_service")
    assert "svc:telemetry.example.invalid" in svc_labels
    assert "svc:api.internal.example.invalid" in svc_labels
    telemetry = [
        e for e in edges
        if e.relation == "sends" and e.target_id == "svc:telemetry.example.invalid"
    ]
    assert telemetry and all(e.resolution == "resolved" for e in telemetry)
    internal = [
        e for e in edges
        if e.relation == "sends" and e.target_id == "svc:api.internal.example.invalid"
    ]
    assert internal and all(e.resolution == "candidate" for e in internal), "模板 URL 保持 candidate"


def test_shell_process_node_is_candidate(pipeline_result, nodes_by_id, edges):
    assert "data:process:shellHandlers" in nodes_by_id
    process_edges = [
        e for e in edges if e.target_id == "data:process:shellHandlers"
    ]
    assert process_edges
    assert all(e.resolution == "candidate" for e in process_edges)


def test_file_behavior_edges(pipeline_result, edges):
    file_edges = [
        e for e in edges
        if e.relation in ("reads", "writes") and e.target_id and e.target_id.startswith("data:file:")
    ]
    assert file_edges, "fs 操作必须产生文件行为边"
    assert all(e.resolution in ("resolved", "candidate") for e in file_edges)


def test_dynamic_import_is_unresolved_edge(pipeline_result, edges):
    dynamic = [e for e in edges if e.note == "dynamic_import"]
    assert dynamic
    assert all(e.resolution == "unresolved" and e.relation == "calls" for e in dynamic)
    assert any(e.source_id.startswith("method:src/lib/dynamic.ts:") for e in dynamic)


def test_computed_dispatch_is_candidate(pipeline_result, edges):
    computed = [
        e for e in edges
        if e.note in ("computed_callee", "registry_alias_call", "registry_dispatch", "member_call")
    ]
    assert computed
    assert all(e.resolution in ("candidate", "unresolved") for e in computed)


def test_cross_file_alias_call_resolves(pipeline_result, edges):
    cross = [
        e for e in edges
        if e.note == "cross_file" and e.resolution == "resolved" and e.relation == "calls"
    ]
    assert cross, "别名/跨文件调用必须解析到目标方法"
    orders_to_queries = [
        e for e in cross
        if e.source_id.startswith("method:src/pages/orders/page.tsx:")
        and e.target_id and e.target_id.startswith("method:src/db/queries.ts:")
    ]
    assert orders_to_queries, "orders 页面经 @/ 别名调用 queries.ts 必须解析"


def test_accepts_returns_contract_edges(pipeline_result, edges):
    accepts = [e for e in edges if e.relation == "accepts"]
    returns = [e for e in edges if e.relation == "returns"]
    assert accepts, "参数类型必须产生 accepts 边"
    assert returns, "返回类型必须产生 returns 边"
    login_accepts = [
        e for e in accepts
        if e.source_id.startswith("method:src/pages/login/page.tsx:submitLogin")
        and e.target_id == "contract:src/pages/login/page.tsx:LoginFormData"
    ]
    assert login_accepts


def test_broken_file_is_reported_not_hidden(pipeline_result):
    report = next(
        (r for r in pipeline_result.parse_reports if r.path == "src/broken/invalid.ts"),
        None,
    )
    assert report is not None
    assert report.status.value == "parsed_with_errors"
    assert report.issue_count >= 1
    assert any("src/broken/invalid.ts" in note for note in pipeline_result.notes)


def test_coverage_counts_match_decisions(pipeline_result):
    result = pipeline_result
    assert result.coverage.total == len(result.decisions)
    assert result.coverage.selected + result.coverage.excluded + result.coverage.limited + result.coverage.failed == result.coverage.total
    parsed_paths = {r.path for r in result.parse_reports}
    selected_ts = {
        d.path for d in result.decisions
        if d.status.value == "selected" and d.path.endswith((".ts", ".tsx", ".js", ".jsx"))
    }
    assert parsed_paths == selected_ts


def test_impact_query_reaches_page_from_table(pipeline_result):
    graph = build_graph(pipeline_result.software_map)
    impact = graph.upstream("data:table:orders")
    labels = {
        node.label
        for node in pipeline_result.software_map.nodes
        if node.id in impact.node_ids
    }
    assert any(label.startswith("/orders") for label in labels), "orders 页面必须进入 orders 表的反向影响集"
    assert not impact.truncated


def test_map_is_valid_contract_and_deterministic(pipeline_result, fixture_repo, fixture_roots, manifest):
    dumped = pipeline_result.software_map.model_dump(mode="json")
    json.dumps(dumped)
    # 重复构建（同输入）产出完全相同的 map
    snapshot = resolve_snapshot(
        fixture_repo, base_ref="HEAD~1", target_ref="HEAD",
        rules_version=RULES, allowed_roots=fixture_roots,
    )
    second = build_software_map(fixture_repo, snapshot, manifest)
    assert second.software_map.model_dump(mode="json") == dumped


def test_map_respects_snapshot_binding(pipeline_result):
    snapshot_id = pipeline_result.software_map.snapshot_id
    assert len(snapshot_id) == 64
    assert all(ch in "0123456789abcdef" for ch in snapshot_id)


# ---------------------------------------------------------------------------
# 审计修复：关系边来源不可丢失（文件、行号、快照、解析状态全链可回溯）
# ---------------------------------------------------------------------------


def test_every_edge_carries_file_provenance(pipeline_result):
    for edge in pipeline_result.software_map.edges:
        assert edge.file_path, f"{edge.id} 缺少 file_path"
        if edge.note != "manifest:page_feature":
            assert edge.line is not None and edge.line >= 1, f"{edge.id} 缺少行号"


def test_edge_provenance_traces_to_parse_status(pipeline_result):
    software_map = pipeline_result.software_map
    reports = {report.path: report for report in software_map.file_reports}
    assert len(reports) >= 15, "每个被解析的文件都必须有解析报告"
    for edge in software_map.edges:
        assert edge.file_path in reports, f"{edge.id} 来源文件 {edge.file_path} 无解析报告"
        assert edge.snapshot_id == software_map.snapshot_id, edge.id
    broken = reports["src/broken/invalid.ts"]
    assert broken.status.value == "parsed_with_errors"
    assert broken.issue_count >= 1


def test_call_edge_line_falls_within_source_method_span(pipeline_result, nodes_by_id):
    checked = 0
    for edge in pipeline_result.software_map.edges:
        if edge.relation == "calls" and edge.line is not None:
            source = nodes_by_id[edge.source_id]
            assert source.start_line <= edge.line <= source.end_line, (
                f"{edge.id} 行号 {edge.line} 越出源方法 [{source.start_line},{source.end_line}]"
            )
            checked += 1
    assert checked > 0, "必须有带行号的 calls 边被检查"


def test_manifest_edges_have_explicit_provenance(pipeline_result, nodes_by_id):
    triggers = [e for e in pipeline_result.software_map.edges if e.note == "manifest:page_feature"]
    implements = [e for e in pipeline_result.software_map.edges if e.note == "manifest:event_handler"]
    assert triggers and implements
    for edge in triggers:
        assert edge.line is None, "manifest 页面级事实允许且仅允许 line=null"
        assert edge.file_path and edge.file_path.startswith("src/pages/")
    for edge in implements:
        assert edge.line is not None and edge.line >= 1
        handler = nodes_by_id[edge.target_id]
        assert edge.file_path == handler.file_path
        assert edge.line == handler.start_line


def test_specific_edge_locates_exact_source_line(pipeline_result, nodes_by_id):
    """抽样：orders 页 handleCreateOrder → queries.createOrder 的调用边，
    其 file_path/line 必须落在 orders 页面文件中 handleCreateOrder 函数体内。"""
    edge = next(
        e for e in pipeline_result.software_map.edges
        if e.relation == "calls"
        and e.note == "cross_file"
        and e.source_id.startswith("method:src/pages/orders/page.tsx:handleCreateOrder")
        and e.target_id and e.target_id.startswith("method:src/db/queries.ts:createOrder")
    )
    assert edge.file_path == "src/pages/orders/page.tsx"
    source = nodes_by_id[edge.source_id]
    assert source.start_line <= edge.line <= source.end_line
