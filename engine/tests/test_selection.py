"""SEL-001: SelectionDecision + CoverageSummary over the real fixture."""

from __future__ import annotations

import pytest

from morphojudge.contracts.domain import DiffFileEntry, DiffFileStatus, SelectionStatus
from morphojudge.git.diff import build_diff_result
from morphojudge.git.snapshot import resolve_snapshot
from morphojudge.selection.rules import RULE_BUDGET_EXHAUSTED, RULE_OVERSIZE, SelectionRules
from morphojudge.selection.service import (
    decide_selection,
    plan_analysis_selection,
    preview_selection,
)

RULES = "batch01-test"


@pytest.fixture(scope="module")
def fixture_diff(fixture_repo, fixture_roots):
    snapshot = resolve_snapshot(
        fixture_repo,
        base_ref="HEAD~1",
        target_ref="HEAD",
        rules_version=RULES,
        allowed_roots=fixture_roots,
    )
    return snapshot, build_diff_result(fixture_repo, snapshot)


def _decisions(fixture_diff, rules=None):
    snapshot, diff = fixture_diff
    return decide_selection(diff.files, rules or SelectionRules(), snapshot.identity.snapshot_id)


def test_every_changed_file_gets_exactly_one_decision(fixture_diff):
    snapshot, diff = fixture_diff
    decisions, summary = _decisions(fixture_diff)
    assert len(decisions) == len(diff.files)
    assert summary.total == len(diff.files)
    assert summary.selected + summary.excluded + summary.limited + summary.failed == summary.total


def test_manifest_selection_expectations(fixture_diff, manifest):
    decisions, _ = _decisions(fixture_diff)
    by_path = {decision.path: decision for decision in decisions}

    for path, rule_id in manifest["selection_expectations"]["excluded"].items():
        decision = by_path[path]
        assert decision.status == SelectionStatus.EXCLUDED, path
        assert decision.rule_id == rule_id, path
        assert decision.language is not None or rule_id in {
            "SEL-UNSUPPORTED-LANGUAGE"
        }, path
        assert decision.bytes is not None, path

    for path in manifest["selection_expectations"]["selected"]:
        decision = by_path[path]
        assert decision.status == SelectionStatus.SELECTED, path
        assert decision.rule_id is None


def test_excluded_reasons_are_specific(fixture_diff):
    decisions, _ = _decisions(fixture_diff)
    by_path = {decision.path: decision for decision in decisions}
    deploy = by_path["scripts/deploy.sh"]
    assert deploy.language == "shell"
    assert "shell" in deploy.reason
    private_note = by_path["PRIVATE/internal-notes.md"]
    assert private_note.rule_id == "SEL-PRIVATE-PATH"


def test_broken_syntax_is_selected_not_failed(fixture_diff):
    """Parse errors belong to the parser stage; selection stays selected."""
    decisions, _ = _decisions(fixture_diff)
    by_path = {decision.path: decision for decision in decisions}
    assert by_path["src/broken/invalid.ts"].status == SelectionStatus.SELECTED


def test_default_budget_keeps_partial_false(fixture_diff):
    _, summary = _decisions(fixture_diff)
    assert summary.limited == 0
    assert summary.failed == 0
    assert summary.partial is False
    assert summary.completed == 0


def test_coverage_counts_match_detail(fixture_diff):
    decisions, summary = _decisions(fixture_diff)
    for status in SelectionStatus:
        expected = sum(1 for decision in decisions if decision.status == status)
        actual = {
            SelectionStatus.SELECTED: summary.selected,
            SelectionStatus.EXCLUDED: summary.excluded,
            SelectionStatus.LIMITED: summary.limited,
            SelectionStatus.FAILED: summary.failed,
        }[status]
        assert actual == expected


def test_oversize_file_becomes_limited(fixture_diff):
    rules = SelectionRules(max_file_bytes=4096)
    decisions, summary = _decisions(fixture_diff, rules)
    by_path = {decision.path: decision for decision in decisions}
    big = by_path["src/generated/big-table.ts"]
    assert big.status == SelectionStatus.LIMITED
    assert big.rule_id == RULE_OVERSIZE
    assert summary.limited >= 1
    assert summary.partial is True


def test_total_budget_cutoff_is_deterministic(fixture_diff):
    snapshot, diff = fixture_diff
    rules = SelectionRules(max_total_selected_bytes=4000)
    first = decide_selection(diff.files, rules, snapshot.identity.snapshot_id)
    second = decide_selection(diff.files, rules, snapshot.identity.snapshot_id)
    assert [d.model_dump() for d in first[0]] == [d.model_dump() for d in second[0]]

    limited = [d for d in first[0] if d.rule_id == RULE_BUDGET_EXHAUSTED]
    assert limited, "tiny budget must produce budget_exhausted limitations"
    selected_bytes = sum(
        d.bytes for d in first[0] if d.status == SelectionStatus.SELECTED
    )
    assert selected_bytes <= 4000
    assert first[1].partial is True


def test_preview_and_analysis_share_one_function(fixture_diff):
    snapshot, diff = fixture_diff
    rules = SelectionRules()
    preview = preview_selection(diff.files, rules, snapshot.identity.snapshot_id)
    actual = plan_analysis_selection(diff.files, rules, snapshot.identity.snapshot_id)
    base = decide_selection(diff.files, rules, snapshot.identity.snapshot_id)
    assert [d.model_dump() for d in preview[0]] == [d.model_dump() for d in actual[0]]
    assert preview[1].model_dump() == actual[1].model_dump() == base[1].model_dump()


def test_path_traversal_entry_excluded(fixture_diff):
    snapshot, _ = fixture_diff
    evil = DiffFileEntry.model_construct(
        path="../escape.ts",
        old_path=None,
        status=DiffFileStatus.ADDED,
        binary=False,
        is_symlink=False,
        is_submodule=False,
        old_bytes=None,
        new_bytes=10,
    )
    decisions, _ = decide_selection([evil], SelectionRules(), snapshot.identity.snapshot_id)
    assert decisions[0].status == SelectionStatus.EXCLUDED
    assert decisions[0].rule_id == "SEL-PATH-TRAVERSAL"


def test_unknown_size_fails_openly(fixture_diff):
    snapshot, _ = fixture_diff
    unknown = DiffFileEntry.model_construct(
        path="src/mystery.ts",
        old_path=None,
        status=DiffFileStatus.MODIFIED,
        binary=False,
        is_symlink=False,
        is_submodule=False,
        old_bytes=None,
        new_bytes=None,
    )
    decisions, summary = decide_selection(
        [unknown], SelectionRules(), snapshot.identity.snapshot_id
    )
    assert decisions[0].status == SelectionStatus.FAILED
    assert decisions[0].rule_id == "SEL-SIZE-UNKNOWN"
    assert summary.failed == 1
    assert summary.partial is True


def test_unmerged_entry_fails_openly(fixture_diff):
    snapshot, _ = fixture_diff
    unmerged = DiffFileEntry.model_construct(
        path="src/conflict.ts",
        old_path=None,
        status=DiffFileStatus.UNMERGED,
        binary=False,
        is_symlink=False,
        is_submodule=False,
        old_bytes=10,
        new_bytes=12,
    )
    decisions, _ = decide_selection(
        [unmerged], SelectionRules(), snapshot.identity.snapshot_id
    )
    assert decisions[0].status == SelectionStatus.FAILED
    assert decisions[0].rule_id == "SEL-UNMERGED"


def test_symlink_and_submodule_entries_excluded(fixture_diff):
    snapshot, _ = fixture_diff
    symlink_entry = DiffFileEntry.model_construct(
        path="link.ts", old_path=None, status=DiffFileStatus.TYPE_CHANGED,
        binary=False, is_symlink=True, is_submodule=False, old_bytes=5, new_bytes=9,
    )
    gitlink = DiffFileEntry.model_construct(
        path="vendored", old_path=None, status=DiffFileStatus.MODIFIED,
        binary=False, is_symlink=False, is_submodule=True, old_bytes=41, new_bytes=41,
    )
    decisions, _ = decide_selection(
        [symlink_entry, gitlink], SelectionRules(), snapshot.identity.snapshot_id
    )
    by_path = {d.path: d for d in decisions}
    assert by_path["link.ts"].rule_id == "SEL-SYMLINK"
    assert by_path["vendored"].rule_id == "SEL-SUBMODULE"


def test_unknown_extension_excluded_with_null_language(fixture_diff):
    snapshot, _ = fixture_diff
    odd = DiffFileEntry.model_construct(
        path="data.zxy", old_path=None, status=DiffFileStatus.ADDED,
        binary=False, is_symlink=False, is_submodule=False, old_bytes=None, new_bytes=7,
    )
    decisions, _ = decide_selection([odd], SelectionRules(), snapshot.identity.snapshot_id)
    assert decisions[0].status == SelectionStatus.EXCLUDED
    assert decisions[0].rule_id == "SEL-UNSUPPORTED-LANGUAGE"
    assert decisions[0].language is None


def test_no_status_is_rendered_as_safe(fixture_diff):
    """Guard: limited/failed/excluded must never masquerade as selected."""
    decisions, summary = _decisions(fixture_diff)
    for decision in decisions:
        if decision.status != SelectionStatus.SELECTED:
            assert decision.rule_id, decision.path
            assert decision.reason
    if summary.excluded or summary.limited or summary.failed:
        assert summary.selected < summary.total
