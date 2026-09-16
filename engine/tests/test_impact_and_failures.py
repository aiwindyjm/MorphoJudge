"""B03-07 / B03-08：影响路径（多上游/循环/候选传播/预算/孤立）与局部失败
（单文件解析失败/全排除/超大文件/锁文件单独失败/规则失败注入）。
"""

from __future__ import annotations

from conftest import analyze_repo, build_scenario_repo

PERM = "export function guard(role: string): boolean {\n  return true;\n}\n"


def _manifest_with_pages(pages):
    return {
        "human_feature_mapping": [
            {
                "page": page,
                "feature_id": f"F-{index}",
                "feature": f"feature-{index}",
                "confirmed_by": "human",
                "events": [event],
            }
            for index, (page, event) in enumerate(pages)
        ],
        "required_entities": {"permission_modules": ["src/lib/perm.ts"]},
    }


SHARED_LIB = (
    'import { guard } from "@/lib/perm";\n\n'
    "export function submit(order: string) {\n"
    '  guard("admin");\n'
    "  return order;\n"
    "}\n"
)


def _page(name: str) -> str:
    return (
        f'import {{ submit }} from "@/lib/orders";\n\n'
        f"export default function {name}() {{\n"
        f"  return {{ onSubmit: submit }};\n"
        f"}}\n"
    )


def test_multiple_upstream_pages_in_impact_path(tmp_path):
    manifest = _manifest_with_pages(
        [("src/pages/a/page.tsx", "submit"), ("src/pages/b/page.tsx", "submit")]
    )
    repo = build_scenario_repo(
        tmp_path / "shared",
        {
            "src/lib/perm.ts": PERM,
            "src/lib/orders.ts": SHARED_LIB,
            "src/pages/a/page.tsx": _page("PageA"),
            "src/pages/b/page.tsx": _page("PageB"),
        },
        {"src/marker.txt": "m\n"},
    )
    result = analyze_repo(repo, [tmp_path / "shared"], manifest)
    perm_findings = [f for f in result.findings if f.rule_id == "BEH-PERM-CHECK"]
    assert perm_findings
    path = next(
        p for p in result.impact_paths if p.origin_id.startswith("method:src/lib/orders.ts:submit")
    )
    assert "page:/a" in path.node_ids and "page:/b" in path.node_ids, "共享方法回溯两个页面"
    assert path.resolution == "resolved"
    assert not path.truncated


def test_call_cycle_terminates_in_impact(tmp_path):
    cycle = (
        "export function a(n: number): number {\n"
        "  if (n <= 0) {\n"
        "    return 0;\n"
        "  }\n"
        "  return b(n - 1);\n"
        "}\n"
        "export async function b(n: number): Promise<number> {\n"
        '  await fetch("https://cycle.example.invalid/x");\n'
        "  return a(n - 1);\n"
        "}\n"
        "export function entry() {\n"
        "  return a(1);\n"
        "}\n"
    )
    repo = build_scenario_repo(
        tmp_path / "cycle", {"src/cycle.ts": cycle}, {"src/marker.txt": "m\n"}
    )
    result = analyze_repo(repo, [tmp_path / "cycle"])
    paths = [
        p for p in result.impact_paths
        if p.origin_id.startswith("method:src/cycle.ts:b")
    ]
    assert paths, "环内行为需要产生路径"
    path = paths[0]
    assert isinstance(path.node_ids, frozenset), "遍历必须终止（visited 集合）"
    assert "method:src/cycle.ts:entry:11" in path.node_ids, "上游经过 entry"
    assert path.resolution == "resolved"
    assert not path.truncated, "环经 visited 终止，不产生截断"


def test_candidate_edges_propagate_to_path(tmp_path):
    """候选传播：路径经过 candidate 边时整体保持 candidate（分析器级验证）。

    v0.1 语义下，指向已知方法的调用边要么 resolved（直接/命名导入），
    要么目标未知（computed/member）；候选边不指向具体方法，因此上游
    路径中的候选传播在 _build_impact_path 层用显式构造的图验证。
    """

    import sys

    sys.path.insert(0, "/engine")
    from morphojudge.analyzer.service import _build_impact_path
    from morphojudge.contracts.domain import (
        MapEdge,
        MapNode,
        MapRelation,
        Resolution,
        SoftwareMap,
    )
    from morphojudge.pipeline import BuildResult
    from morphojudge.selection.rules import SelectionRules
    from morphojudge.contracts.domain import CoverageSummary

    snap = "e" * 64

    def node(node_id, kind="method"):
        return MapNode(id=node_id, snapshot_id=snap, kind=kind, label=node_id, resolution=Resolution.RESOLVED)

    def edge(eid, src, dst, resolution):
        return MapEdge(
            id=eid, snapshot_id=snap, relation=MapRelation.CALLS,
            source_id=src, target_id=dst, resolution=Resolution(resolution),
            file_path="src/x.ts", line=1,
        )

    software_map = SoftwareMap(
        snapshot_id=snap,
        nodes=[node("m:origin"), node("m:caller"), node("page:/x", "page")],
        edges=[
            edge("e1", "m:caller", "m:origin", "candidate"),
            edge("e2", "page:/x", "m:caller", "resolved"),
        ],
    )
    result = BuildResult(software_map=software_map, coverage=CoverageSummary(
        snapshot_id=snap, total=0, selected=0, excluded=0, limited=0, failed=0, partial=False))
    path = _build_impact_path(result, "m:origin", [], "fallback")
    assert path.resolution == "candidate", "候选边必须把路径整体降为 candidate"
    assert path.node_ids == {"m:origin", "m:caller", "page:/x"}


def test_budget_truncation_reports_reason(tmp_path):
    chain_files = {
        "src/lib/perm.ts": PERM,
        "src/lib/f0.ts": 'import { guard } from "@/lib/perm";\n\nexport function step0() {\n  guard("x");\n}\n',
    }
    for i in range(1, 8):
        chain_files[f"src/lib/f{i}.ts"] = (
            f'import {{ step{i - 1} }} from "@/lib/f{i - 1}";\n\nexport function step{i}() {{\n  return step{i - 1}();\n}}\n'
        )
    repo = build_scenario_repo(tmp_path / "chain", chain_files, {"src/marker.txt": "m\n"})
    result = analyze_repo(
        repo, [tmp_path / "chain"], _manifest_with_pages([]), impact_max_depth=3
    )
    truncated = [p for p in result.impact_paths if p.truncated]
    assert truncated, "深度预算必须产生截断"
    assert all(p.stop_reason == "max_depth" for p in truncated)
    assert all(p.limit_note and p.limit_note.startswith("truncated:") for p in truncated)
    stage = next(s for s in result.stage_coverage if s.stage == "impact")
    assert stage.status == "completed_with_limits" and stage.limited > 0


def test_isolated_method_path_is_honest(tmp_path):
    lone = 'export async function orphan(url: string) {\n  await fetch(url);\n}\n'
    repo = build_scenario_repo(tmp_path / "lone", {"src/lone.ts": lone}, {"src/marker.txt": "m\n"})
    result = analyze_repo(repo, [tmp_path / "lone"])
    path = next(
        p for p in result.impact_paths if p.origin_id.startswith("method:src/lone.ts:orphan")
    )
    assert path.resolution == "unresolved"
    assert path.limit_note == "no_path_in_graph:not_a_safety_claim", \
        "未查到路径不承诺全局无影响"


# --- B03-08：局部失败 --------------------------------------------------------


def test_broken_file_does_not_swallow_others(tmp_path):
    good = 'export async function ok() {\n  await fetch("https://ok.example.invalid/x");\n}\n'
    broken = "export function broken(: string {\n  const x = ;\n}\n"
    repo = build_scenario_repo(
        tmp_path / "mixed",
        {"src/good.ts": good, "src/bad/broken.ts": broken},
        {"src/marker.txt": "m\n"},
    )
    result = analyze_repo(repo, [tmp_path / "mixed"])
    network = [f for f in result.findings if f.rule_id == "BEH-NETWORK"]
    assert network, "好文件的结果必须保留"
    parse_stage = next(s for s in result.stage_coverage if s.stage == "parse")
    assert parse_stage.status == "completed_with_limits"
    assert parse_stage.limited >= 1
    assert any("src/bad/broken.ts" in note for note in result.limits)


def test_all_excluded_reports_no_analyzable_files(tmp_path):
    repo = build_scenario_repo(
        tmp_path / "allexcl",
        {"README.md": "# nothing\n"},
        {"docs/more.txt": "text\n"},
    )
    result = analyze_repo(repo, [tmp_path / "allexcl"])
    stage = next(s for s in result.stage_coverage if s.stage == "selection")
    assert stage.completed == 0
    assert "no_analyzable_source_files:not_a_safety_claim" in stage.notes
    assert result.findings == []


def test_oversize_file_limited_others_analyzed(tmp_path):
    small = 'export async function tiny() {\n  await fetch("https://tiny.example.invalid/x");\n}\n'
    huge = "export const pad = [\n" + "".join(f'  "row-{i}",\n' for i in range(100000)) + "];\n"
    repo = build_scenario_repo(
        tmp_path / "oversize",
        {"src/tiny.ts": small, "src/huge.ts": huge},
        {"src/marker.txt": "m\n"},
    )
    result = analyze_repo(repo, [tmp_path / "oversize"])
    stage = next(s for s in result.stage_coverage if s.stage == "selection")
    assert stage.limited >= 1
    network = [f for f in result.findings if f.rule_id == "BEH-NETWORK"]
    assert network, "未超限文件继续分析"


def test_broken_lock_does_not_block_source_analysis(tmp_path):
    source = 'export async function ping() {\n  await fetch("https://ping.example.invalid/x");\n}\n'
    repo = build_scenario_repo(
        tmp_path / "badlock",
        {"src/ping.ts": source, "package.json": "{\"name\":\"x\",\"dependencies\":{\"a\":\"1.0.0\"}}\n"},
        {"pnpm-lock.yaml": "lockfileVersion: '9.0'\nimporters:\n  .:\n    dependencies:\n\t broken: [unclosed\n"},
    )
    result = analyze_repo(repo, [tmp_path / "badlock"])
    dependency_stage = next(s for s in result.stage_coverage if s.stage == "dependency")
    assert dependency_stage.status == "completed_with_limits"
    assert any(f.rule_id == "BEH-NETWORK" for f in result.findings), "源码分析不受锁文件失败影响"
    assert result.analyzer_errors == []


def test_rule_failure_isolated_and_recorded(tmp_path, monkeypatch):
    import sys

    sys.path.insert(0, "/engine")
    from morphojudge.analyzer import service as analyzer_service

    def injected_failure(*args, **kwargs):
        raise RuntimeError("injected-rule-failure")

    monkeypatch.setattr(
        analyzer_service.behavior_rules, "collect_behavior_signals", injected_failure
    )
    source = 'export async function ping() {\n  await fetch("https://x.example.invalid/x");\n}\n'
    repo = build_scenario_repo(tmp_path / "rulefail", {"src/ping.ts": source}, {"src/marker.txt": "m\n"})
    result = analyze_repo(repo, [tmp_path / "rulefail"])
    assert "behavior_rules_failed:RuntimeError" in result.analyzer_errors
    stage = next(s for s in result.stage_coverage if s.stage == "behavior_rules")
    assert stage.status == "failed"
    assert not any(f.rule_id and f.rule_id.startswith("BEH-") for f in result.findings)
    consistency_stage = next(s for s in result.stage_coverage if s.stage == "consistency")
    assert consistency_stage.status == "completed", "单规则失败不吞掉其他阶段"
