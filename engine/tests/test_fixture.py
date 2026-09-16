"""FIX-001: the real ts-web fixture is present, reproducible and inert."""

from __future__ import annotations

import json

from conftest import run_git

from morphojudge.git.runner import check_git
from morphojudge.git.snapshot import resolve_snapshot


def test_manifest_commits_match_repository(fixture_repo, manifest):
    target = check_git(["rev-parse", "HEAD"], cwd=fixture_repo).strip()
    base = check_git(["rev-parse", "HEAD~1"], cwd=fixture_repo).strip()
    assert base == manifest["commits"]["base"]
    assert target == manifest["commits"]["target"]
    assert base != target


def test_commit_identity_is_deterministic(fixture_repo, manifest):
    for commit in ("HEAD", "HEAD~1"):
        author = check_git(
            ["log", "-1", "--format=%an <%ae> %aI", commit], cwd=fixture_repo
        ).strip()
        assert "fixture@morphojudge.invalid" in author
    assert manifest["author"]["email"] == "fixture@morphojudge.invalid"


def test_working_tree_is_clean(fixture_repo, fixture_roots):
    snapshot = resolve_snapshot(
        fixture_repo,
        base_ref="HEAD~1",
        target_ref="HEAD",
        rules_version="batch01-test",
        allowed_roots=fixture_roots,
    )
    assert snapshot.workspace_dirty is False
    assert snapshot.working_tree == []


def test_required_base_entities_exist(fixture_repo, manifest):
    base = manifest["commits"]["base"]
    tree = check_git(["ls-tree", "-r", "--name-only", base], cwd=fixture_repo).splitlines()
    required_at_base = [
        "src/pages/login/page.tsx",
        "src/pages/orders/page.tsx",
        "src/pages/orders/[id]/page.tsx",
        "src/pages/admin/page.tsx",
        "src/lib/schema.ts",
        "src/lib/dynamic.ts",
        "src/lib/shell.ts",
        "src/lib/files.ts",
        "src/lib/permissions.ts",
        "src/lib/old-notify.ts",
        "src/db/database.ts",
        "src/db/queries.ts",
    ]
    for path in required_at_base:
        assert path in tree, path


def test_fixture_repo_has_no_active_hooks(fixture_repo):
    hooks_dir = fixture_repo / ".git" / "hooks"
    assert hooks_dir.is_dir()
    files = [item.name for item in hooks_dir.iterdir()]
    assert files, "expected default sample hooks"
    assert all(name.endswith(".sample") for name in files), files


def test_fixture_scripts_are_text_only(fixture_repo, manifest):
    """Deliberately-invalid TS and shell script must exist as data blobs."""
    target = manifest["commits"]["target"]
    tree = check_git(["ls-tree", "-r", "--name-only", target], cwd=fixture_repo).splitlines()
    assert "src/broken/invalid.ts" in tree
    assert "scripts/deploy.sh" in tree
    invalid = check_git(["cat-file", "-p", f"{target}:src/broken/invalid.ts"], cwd=fixture_repo)
    assert "export function brokenSample" in invalid


def test_manifest_expected_diff_shape_matches_git(fixture_repo, manifest):
    out = run_git(
        fixture_repo,
        "diff",
        "--find-renames=50%",
        "--find-copies",
        "--name-status",
        manifest["commits"]["base"],
        manifest["commits"]["target"],
    )
    lines = [line.split("\t") for line in out.strip().splitlines()]
    added = {parts[1] for parts in lines if parts[0] == "A"}
    modified = {parts[1] for parts in lines if parts[0] == "M"}
    deleted = {parts[1] for parts in lines if parts[0] == "D"}
    renamed = {(parts[1], parts[2]) for parts in lines if parts[0].startswith("R")}
    assert added == set(manifest["expected_diff"]["added"])
    assert modified == set(manifest["expected_diff"]["modified"])
    assert deleted == set(manifest["expected_diff"]["deleted"])
    assert renamed == {
        (item["old_path"], item["new_path"]) for item in manifest["expected_diff"]["renamed"]
    }


def test_manifest_is_valid_json_with_required_sections(manifest):
    for key in (
        "commits",
        "expected_diff",
        "required_entities",
        "human_feature_mapping",
        "selection_expectations",
    ):
        assert key in manifest
    json.dumps(manifest)
