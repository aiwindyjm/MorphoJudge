"""Batch-03 集成切片：真实 fixture + 临时仓库的 Git→Finding→Evidence→
影响路径→覆盖全链路。映射 B03-01（确定性/空 diff/空行）与 B03-10（ID
稳定、无悬空引用、同侧一致）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import analyze_repo, build_scenario_repo

from morphojudge.analyzer.service import analyze_snapshot
from morphojudge.git.snapshot import resolve_snapshot

RULES = "batch03-test"


@pytest.fixture(scope="module")
def fixture_result(fixture_repo, fixture_roots, manifest):
    snapshot = resolve_snapshot(
        fixture_repo, base_ref="HEAD~1", target_ref="HEAD",
        rules_version=RULES, allowed_roots=fixture_roots,
    )
    return analyze_snapshot(fixture_repo, snapshot, manifest)


# --- B03-01：同输入同结果 ---------------------------------------------------


def test_same_input_repeats_to_identical_result(fixture_repo, fixture_roots, manifest):
    snapshot = resolve_snapshot(
        fixture_repo, base_ref="HEAD~1", target_ref="HEAD",
        rules_version=RULES, allowed_roots=fixture_roots,
    )
    first = analyze_snapshot(fixture_repo, snapshot, manifest)
    second = analyze_snapshot(fixture_repo, snapshot, manifest)

    def dump(result):
        return {
            "findings": [f.model_dump() for f in result.findings],
            "evidence": [a.model_dump() for a in result.evidence],
            "impact": [
                {k: (sorted(v) if isinstance(v, frozenset) else v) for k, v in vars(p).items()}
                for p in result.impact_paths
            ],
            "stages": [vars(s) for s in result.stage_coverage],
            "limits": result.limits,
            "errors": result.analyzer_errors,
            "base_map": result.base_map.model_dump(),
            "target_map": result.target_map.model_dump(),
        }

    assert dump(first) == dump(second), "同输入必须产出完全一致的结果与 ID"


def test_finding_ids_are_stable_and_unique(fixture_result):
    ids = [finding.id for finding in fixture_result.findings]
    assert len(ids) == len(set(ids)), "去重后不得出现重复 finding ID"
    assert all(finding.id.startswith("finding:") for finding in fixture_result.findings)


# --- B03-10：无悬空引用、同侧一致 -------------------------------------------


def test_no_dangling_references(fixture_result):
    evidence_ids = {anchor.id for anchor in fixture_result.evidence}
    target_node_ids = {node.id for node in fixture_result.target_map.nodes}
    target_edge_ids = {edge.id for edge in fixture_result.target_map.edges}
    sid = fixture_result.snapshot.identity.snapshot_id

    for finding in fixture_result.findings:
        assert finding.snapshot_id == sid
        if finding.evidence_ids:
            assert finding.unresolved_reason is None
            for evidence_id in finding.evidence_ids:
                assert evidence_id in evidence_ids, f"{finding.id} 悬空证据引用"
        else:
            assert finding.unresolved_reason, f"{finding.id} 既无证据也无原因"

    for anchor in fixture_result.evidence:
        assert anchor.snapshot_id == sid
        assert len(anchor.commit) == 40

    for path in fixture_result.impact_paths:
        for node_id in path.node_ids:
            assert node_id in target_node_ids or node_id.startswith("finding:"), path.origin_id
        for edge_id in path.edge_ids:
            assert edge_id in target_edge_ids


def test_evidence_anchors_point_at_correct_side_and_commit(fixture_result):
    base_commit = fixture_result.snapshot.identity.base_commit
    target_commit = fixture_result.snapshot.identity.target_commit
    for anchor in fixture_result.evidence:
        if anchor.side.value == "old":
            assert anchor.commit == base_commit
        else:
            assert anchor.commit == target_commit


def test_duplicate_positions_deduplicate_not_dropping_rules(fixture_result):
    """同位置不同规则必须共存；同规则同位置只保留一条。"""
    keys = [
        (finding.rule_id, finding.unresolved_reason or "", tuple(finding.evidence_ids))
        for finding in fixture_result.findings
    ]
    assert len(keys) == len(set(keys))
    rules = {finding.rule_id for finding in fixture_result.findings}
    assert {"BEH-NETWORK", "BEH-SHELL", "BEH-FILE", "BEH-PERM-CHECK"} <= rules


# --- 真实 fixture 行为分类 ---------------------------------------------------


def _svc_targets(software_map):
    return {
        edge.target_id
        for edge in software_map.edges
        if edge.relation.value == "sends" and edge.target_id.startswith("svc:")
    }


def test_fixture_network_classification(fixture_result):
    base_svc = _svc_targets(fixture_result.base_map)
    target_svc = _svc_targets(fixture_result.target_map)
    assert "svc:telemetry.example.invalid" in target_svc - base_svc, "target 新增外部遥测行为"
    assert "svc:api.internal.example.invalid" in target_svc & base_svc, "内部 API 行为基线已有"

    findings = [f for f in fixture_result.findings if f.rule_id == "BEH-NETWORK"]
    assert findings, "网络行为必须有对应 Finding"
    telemetry = [
        a for a in fixture_result.evidence
        if a.path == "src/lib/api-client.ts" and "telemetry" in a.snippet.lower()
    ]
    assert telemetry, "新行为证据必须锚定在 api-client.ts 的 telemetry 调用行"


def test_fixture_new_network_anchor_points_at_target_commit(fixture_repo, fixture_result):
    finding = next(
        f for f in fixture_result.findings
        if f.rule_id == "BEH-NETWORK" and f.evidence_ids
    )
    anchor = next(a for a in fixture_result.evidence if a.id in finding.evidence_ids)
    blob = _blob_lines(fixture_repo, fixture_result, anchor)
    assert "fetch" in blob[anchor.start_line - 1], "证据行必须是实际 fetch 调用行"


def _blob_lines(repo, result, anchor):
    from morphojudge.git.blob import read_blob_bytes

    data = read_blob_bytes(repo, anchor.commit, anchor.path)
    return data.decode("utf-8", errors="replace").split("\n")


def test_fixture_guard_checks_are_existing(fixture_result):
    perm = [f for f in fixture_result.findings if f.rule_id == "BEH-PERM-CHECK"]
    assert perm, "requireRole 调用点必须被识别（经 manifest 权限模块）"
    assert all(f.kind.value == "fact" for f in perm), "权限检查存在性是事实线索而非漏洞"


def test_fixture_no_lockfile_is_not_checked_not_safe(fixture_result):
    assert "no_lockfile:not_checked" in fixture_result.limits
    dependency_stage = next(s for s in fixture_result.stage_coverage if s.stage == "dependency")
    assert dependency_stage.status == "completed"


# --- B03-01：空 diff / 空行插入 / rename / 删除（临时仓库） -------------------


NET_SOURCE = """export async function ping(url: string) {
  await fetch("https://api.example.invalid/health");
  return true;
}
"""


def test_empty_diff_keeps_existing_and_is_not_safe(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    from conftest import commit_all, run_git

    run_git(repo, "init", "-b", "main")
    (repo / ".gitattributes").write_text("* -text\n", encoding="utf-8", newline="")
    (repo / "src/net.ts").parent.mkdir(parents=True, exist_ok=True)
    (repo / "src/net.ts").write_text(NET_SOURCE, encoding="utf-8", newline="")
    commit_all(repo, "base", date="2026-05-01T08:00:00+00:00")
    run_git(repo, "commit", "--allow-empty", "-m", "target", date="2026-05-02T08:00:00+00:00")

    result = analyze_repo(repo, [tmp_path])
    network = [f for f in result.findings if f.rule_id == "BEH-NETWORK"]
    assert network, "空 diff 不等于安全：基线行为仍如实列出"
    resolution_notes = [p.limit_note for p in result.impact_paths if p.origin_id.startswith("method:")]
    assert result.diff.files == []
    assert all(f.unresolved_reason is None for f in network)


def test_blank_line_insertion_does_not_reclassify(tmp_path):
    clean = build_scenario_repo(
        tmp_path / "clean", {"src/net.ts": NET_SOURCE}, {"src/other.txt": "x\n"}
    )
    blanked_source = NET_SOURCE.replace(
        'export async function ping(url: string) {',
        'export async function ping(url: string) {\n\n',
    )
    blanked = build_scenario_repo(
        tmp_path / "blanked", {"src/net.ts": NET_SOURCE}, {"src/net.ts": blanked_source}
    )

    def classification(result):
        return sorted(
            (f.rule_id, f.evidence_ids and next(
                a for a in result.evidence if a.id in f.evidence_ids
            ).path)
            for f in result.findings if f.rule_id.startswith("BEH-")
        )

    clean_result = analyze_repo(clean, [tmp_path / "clean"])
    blanked_result = analyze_repo(blanked, [tmp_path / "blanked"])
    assert classification(clean_result) == classification(blanked_result), \
        "仅插入空行不得改变行为的 existing/new 分类"
    old_side = [a for a in blanked_result.evidence if a.side.value == "old"]
    assert not old_side, "空行插入不得产生任何 deleted(old) 侧证据"


def test_renamed_file_behavior_stays_existing(tmp_path):
    renamed_repo = build_scenario_repo(
        tmp_path / "renamed",
        {"src/old-net.ts": NET_SOURCE},
        {"src/old-net.ts": None, "src/new-net.ts": NET_SOURCE},
    )
    result = analyze_repo(renamed_repo, [tmp_path / "renamed"])
    network = [f for f in result.findings if f.rule_id == "BEH-NETWORK"]
    assert network, "rename 后行为仍在"
    anchors = [a for a in result.evidence if a.id in network[0].evidence_ids]
    assert anchors[0].path == "src/new-net.ts", "existing 证据指向 target 侧新路径"
    assert anchors[0].side.value == "new"


def test_deleted_behavior_gets_old_side_evidence(tmp_path):
    gone_repo = build_scenario_repo(
        tmp_path / "gone", {"src/net.ts": NET_SOURCE}, {"src/net.ts": None}
    )
    result = analyze_repo(gone_repo, [tmp_path / "gone"])
    network = [f for f in result.findings if f.rule_id == "BEH-NETWORK"]
    assert network, "删除的行为必须留有 deleted 记录"
    anchor = next(a for a in result.evidence if a.id in network[0].evidence_ids)
    assert anchor.side.value == "old"
    assert anchor.commit == result.snapshot.identity.base_commit
    assert anchor.path == "src/net.ts"
    assert "fetch" in anchor.snippet


def test_method_body_edit_moves_behavior_to_new(tmp_path):
    edited = NET_SOURCE.replace(
        'await fetch("https://api.example.invalid/health");',
        'await fetch("https://other.example.invalid/ping");',
    )
    repo = build_scenario_repo(
        tmp_path / "edited", {"src/net.ts": NET_SOURCE}, {"src/net.ts": edited}
    )
    result = analyze_repo(repo, [tmp_path / "edited"])
    targets = {a.path for a in result.evidence}
    new_edges = [
        e for e in result.target_map.edges
        if e.relation.value == "sends" and e.target_id == "svc:other.example.invalid"
    ]
    assert new_edges, "新 URL 行为进入 target 图"
    old_edges = [
        e for e in result.base_map.edges
        if e.relation.value == "sends" and e.target_id == "svc:api.example.invalid"
    ]
    assert old_edges, "旧 URL 行为保留在 base 图"
