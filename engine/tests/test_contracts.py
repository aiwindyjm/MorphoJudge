"""DATA-001: contract validity, round trips, JSON Schema and TS drift guards."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from morphojudge.contracts import export
from morphojudge.contracts.domain import (
    SCHEMA_VERSION,
    CoverageSummary,
    DiffFileEntry,
    DiffFileStatus,
    EvidenceAnchor,
    Explanation,
    ExplanationClaim,
    FileParseReport,
    Finding,
    FindingCategory,
    ImmutableAnalysisInput,
    LineMapHunk,
    MapEdge,
    MapNode,
    MapNodeKind,
    MapRelation,
    Resolution,
    Review,
    ReviewState,
    SelectionDecision,
    SelectionStatus,
    SnapshotIdentity,
    SnapshotReport,
    SoftwareMap,
    StageRecord,
    StageStatus,
    WorkingTreeEntry,
    validate_repo_relative_path,
    AnalysisSession,
    AnalysisStatus,
    DiffResult,
)

SNAP_ID = "a" * 64
REPO_ID = "b" * 64
COMMIT = "c" * 40


def _identity() -> SnapshotIdentity:
    return SnapshotIdentity(
        repository_id=REPO_ID,
        canonical_path="/fixtures/ts-web",
        base_commit=COMMIT,
        target_commit="d" * 40,
        snapshot_id=SNAP_ID,
        rules_version="batch01-test",
    )


def _samples() -> dict[str, object]:
    return {
        "SnapshotIdentity": _identity(),
        "WorkingTreeEntry": WorkingTreeEntry(path="src/a.ts", x="M", y=" "),
        "SnapshotReport": SnapshotReport(
            identity=_identity(), workspace_dirty=False, notes=[]
        ),
        "LineMapHunk": LineMapHunk(old_start=1, old_count=2, new_start=1, new_count=3),
        "DiffFileEntry": DiffFileEntry(
            path="src/a.ts", status=DiffFileStatus.MODIFIED, additions=3, deletions=1
        ),
        "DiffResult": DiffResult(
            snapshot_id=SNAP_ID, base_commit=COMMIT, target_commit="d" * 40
        ),
        "SelectionDecision": SelectionDecision(
            snapshot_id=SNAP_ID,
            path="src/a.ts",
            status=SelectionStatus.SELECTED,
            reason="selected for deterministic analysis",
            rule_id=None,
            language="typescript",
            bytes=120,
        ),
        "CoverageSummary": CoverageSummary(
            snapshot_id=SNAP_ID, total=1, selected=1, excluded=0, limited=0, failed=0, partial=False
        ),
        "MapNode": MapNode(
            id="node-1",
            snapshot_id=SNAP_ID,
            kind=MapNodeKind.METHOD,
            label="createOrder",
            language="typescript",
            file_path="src/db/queries.ts",
            start_line=10,
            end_line=20,
            resolution=Resolution.RESOLVED,
        ),
        "MapEdge": MapEdge(
            id="edge-1",
            snapshot_id=SNAP_ID,
            relation=MapRelation.CALLS,
            source_id="node-1",
            target_id="node-2",
            resolution=Resolution.CANDIDATE,
            file_path="src/a.ts",
            line=12,
        ),
        "FileParseReport": FileParseReport(
            path="src/a.ts", status="parsed", issue_count=0
        ),
        "SoftwareMap": SoftwareMap(
            snapshot_id=SNAP_ID,
            file_reports=[FileParseReport(path="src/a.ts", status="parsed_with_errors", issue_count=2)],
        ),
        "EvidenceAnchor": EvidenceAnchor(
            id="ev-1",
            snapshot_id=SNAP_ID,
            path="src/a.ts",
            side="new",
            start_line=3,
            end_line=4,
            snippet="fetch(url)",
            source_kind="rule",
        ),
        "Finding": Finding(
            id="f-1",
            snapshot_id=SNAP_ID,
            category=FindingCategory.BEHAVIOR_NETWORK,
            kind="rule_hint",
            impact="medium",
            reliability="rule_based",
            evidence_ids=["ev-1"],
        ),
        "ExplanationClaim": ExplanationClaim(
            text="restates fetch call", evidence_ids=["ev-1"], kind="restatement"
        ),
        "Explanation": Explanation(
            id="x-1",
            provider="fake",
            model="fake-model",
            claims=[
                ExplanationClaim(
                    text="inference", evidence_ids=["ev-1"], kind="inference"
                )
            ],
            uncertainty="cannot prove execution",
        ),
        "Review": Review(
            finding_id="f-1",
            state=ReviewState.NEEDS_INVESTIGATION,
            note="",
            updated_at="2026-09-16T08:00:00Z",
        ),
        "StageRecord": StageRecord(stage="git", status=StageStatus.COMPLETED),
        "ImmutableAnalysisInput": ImmutableAnalysisInput(
            repository_id=REPO_ID,
            base_ref="HEAD~1",
            target_ref="HEAD",
            rules_version="batch01",
            snapshot_id=SNAP_ID,
        ),
        "AnalysisSession": AnalysisSession(
            id="session-1",
            status=AnalysisStatus.QUEUED,
            immutable_input=ImmutableAnalysisInput(
                repository_id=REPO_ID,
                base_ref="HEAD~1",
                target_ref="HEAD",
                rules_version="batch01",
                snapshot_id=SNAP_ID,
            ),
        ),
    }


def test_every_model_round_trips():
    for name, sample in _samples().items():
        dumped = sample.model_dump(mode="json")  # type: ignore[attr-defined]
        assert json.dumps(dumped, sort_keys=True), name
        restored = type(sample).model_validate(dumped)  # type: ignore[attr-defined]
        assert restored == sample, name


def test_unknown_fields_are_rejected():
    payload = _identity().model_dump()
    payload["surprise"] = 1
    with pytest.raises(ValidationError):
        SnapshotIdentity.model_validate(payload)


def test_unknown_enum_value_is_rejected():
    payload = _samples()["SelectionDecision"].model_dump()  # type: ignore[attr-defined]
    payload["status"] = "skipped"
    with pytest.raises(ValidationError):
        SelectionDecision.model_validate(payload)


def test_commit_must_be_lowercase_hex40():
    payload = _identity().model_dump()
    payload["base_commit"] = "XYZ"
    with pytest.raises(ValidationError):
        SnapshotIdentity.model_validate(payload)


def test_repo_relative_path_rules():
    for bad in ("/abs/path", "a/../b", "a//b", "", "a\\b", "..", "a/./b"):
        with pytest.raises(ValueError):
            validate_repo_relative_path(bad)
    assert validate_repo_relative_path("src/lib/a.ts") == "src/lib/a.ts"


def test_evidence_anchor_line_order():
    with pytest.raises(ValidationError):
        EvidenceAnchor(
            id="e",
            snapshot_id=SNAP_ID,
            path="a.ts",
            side="old",
            start_line=5,
            end_line=2,
            snippet="x",
            source_kind="rule",
        )


def test_selection_decision_requires_rule_id_when_not_selected():
    with pytest.raises(ValidationError):
        SelectionDecision(
            snapshot_id=SNAP_ID,
            path="a.ts",
            status=SelectionStatus.EXCLUDED,
            reason="why",
            rule_id=None,
            language="typescript",
            bytes=10,
        )


def test_finding_requires_evidence_or_unresolved_reason():
    with pytest.raises(ValidationError):
        Finding(
            id="f",
            snapshot_id=SNAP_ID,
            category=FindingCategory.STRUCTURE,
            kind="fact",
            impact="low",
            reliability="deterministic",
        )


def test_coverage_summary_counts_must_sum():
    with pytest.raises(ValidationError):
        CoverageSummary(
            snapshot_id=SNAP_ID, total=5, selected=1, excluded=1, limited=1, failed=1, partial=True
        )


def test_model_explanation_never_lives_in_relation_types():
    """Deterministic relation models must not carry model free-text fields."""
    relation_fields = set(DiffFileEntry.model_fields) | set(MapNode.model_fields) | set(MapEdge.model_fields)
    forbidden = {"explanation", "model_text", "llm_note", "ai_summary"}
    assert not (relation_fields & forbidden)


def test_map_edge_provenance_is_validated():
    base = dict(
        id="edge-x",
        snapshot_id=SNAP_ID,
        relation="calls",
        source_id="a",
        target_id="b",
        resolution="resolved",
    )
    with pytest.raises(ValidationError):
        MapEdge.model_validate({**base, "file_path": "../escape.ts", "line": 3})
    with pytest.raises(ValidationError):
        MapEdge.model_validate({**base, "file_path": "src/a.ts", "line": 0})
    # manifest 级事实允许 line=null，但路径必须合法
    edge = MapEdge.model_validate({**base, "file_path": "src/pages/x/page.tsx", "line": None})
    assert edge.line is None


# ---------------------------------------------------------------------------
# Schema + TS drift guards
# ---------------------------------------------------------------------------

SCHEMA_FILE = Path("/packages/contracts/schema.json")
TS_FILE = Path("/ts-lib/contracts.ts")


def test_committed_schema_matches_generated():
    if not SCHEMA_FILE.exists():
        pytest.fail(
            "packages/contracts/schema.json missing; regenerate via "
            "`docker compose run --rm --no-deps daemon python -m morphojudge.contracts.export "
            "> packages/contracts/schema.json`"
        )
    assert SCHEMA_FILE.read_text(encoding="utf-8") == export.render_schema()


def _iter_enum_values(node: object):
    if isinstance(node, dict):
        if isinstance(node.get("enum"), list):
            yield from (value for value in node["enum"] if isinstance(value, str))
        for value in node.values():
            yield from _iter_enum_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_enum_values(item)


def test_typescript_mirror_matches_schema_enums_and_fields():
    if not TS_FILE.exists():
        pytest.fail("app/lib/contracts.ts not mounted at /ts-lib/contracts.ts")
    ts_source = TS_FILE.read_text(encoding="utf-8")
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8")) if SCHEMA_FILE.exists() else export.generate_schema()

    for value in sorted(set(_iter_enum_values(schema))):
        assert f"'{value}'" in ts_source, f"enum value {value!r} missing in contracts.ts"

    for name in schema["models"]:
        assert name in ts_source, f"model {name} missing in contracts.ts"

    for name, definition in schema["definitions"].items():
        properties = definition.get("properties")
        if not properties:
            continue
        match = re.search(rf"interface {name} \{{(.*?)\n\}}", ts_source, re.DOTALL)
        assert match is not None, f"interface {name} missing in contracts.ts"
        block = match.group(1)
        for field in properties:
            assert re.search(rf"^\s+readonly {field}\??:", block, re.MULTILINE), (
                f"field {name}.{field} missing in contracts.ts"
            )


def test_schema_version_exported():
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert schema["schema_version"] == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# B02-R2-02 / B02-R2-03：Schema 引用完整性与实际消费、版本与旧输入兼容
# ---------------------------------------------------------------------------


def _iter_refs(node: object):
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            yield ref
        for value in node.values():
            yield from _iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_refs(item)


def test_all_local_schema_refs_resolve():
    """生成器缺陷守卫：根 Schema 中每个本地 $ref 都必须命中根 definitions。"""
    import jsonschema

    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    # 先做结构级检查，再让 jsonschema 对根 Schema 自身做完整编译
    definitions = schema.get("definitions", {})
    missing = sorted(
        {
            ref.removeprefix("#/definitions/")
            for ref in _iter_refs(schema)
            if ref.startswith("#/definitions/")
        }
        - set(definitions)
    )
    assert not missing, f"unresolvable local refs: {missing}"
    # Draft 2020-12 编译会再次验证全部引用可达
    jsonschema.Draft202012Validator.check_schema(schema)
    for enum in (
        "ParseStatus",
        "AnalysisStatus",
        "DiffFileStatus",
        "SelectionStatus",
        "MapRelation",
        "Resolution",
    ):
        assert enum in definitions, enum


def test_schema_actually_consumes_valid_and_invalid_payloads():
    """用根 Schema 实际校验 FileParseReport：合法通过、非法状态被拒绝。

    边界说明：Schema 层只编码枚举与数值约束；路径穿越等更严规则
    由 Pydantic 校验器承担（自定义校验器不进入生成的 JSON Schema）。
    """
    import jsonschema

    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    probe = {
        "$ref": "#/definitions/FileParseReport",
        "definitions": schema["definitions"],
    }
    validator = jsonschema.Draft202012Validator(probe)

    valid = {"path": "src/a.ts", "status": "parsed_with_errors", "issue_count": 2}
    validator.validate(valid)

    invalid_status = {"path": "src/a.ts", "status": "bogus", "issue_count": 0}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid_status)

    negative_issues = {"path": "src/a.ts", "status": "parsed", "issue_count": -1}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(negative_issues)

    missing_field = {"path": "src/a.ts", "status": "parsed"}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(missing_field)


def test_generated_schema_has_no_unhoisted_defs():
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    for name, definition in schema["definitions"].items():
        assert "$defs" not in definition, f"{name} still carries nested $defs"


def test_v1_0_0_map_edge_input_still_validates():
    """兼容边界：缺少 file_path/line 的旧输入继续通过（Schema 不拒绝旧数据），
    新产物的完整性由 pipeline 测试强制。"""
    legacy = {
        "id": "edge-old",
        "snapshot_id": SNAP_ID,
        "relation": "calls",
        "source_id": "a",
        "target_id": "b",
        "resolution": "resolved",
    }
    edge = MapEdge.model_validate(legacy)
    assert edge.file_path is None and edge.line is None
    # F1.4：当前契约版本（1.3.0 = Batch-04-R1 API DTO 兼容追加）。
    assert SCHEMA_VERSION == "1.3.0"
