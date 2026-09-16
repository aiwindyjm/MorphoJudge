"""GIT-002: diff file statuses, stats, binary policy and line mapping."""

from __future__ import annotations

import os

import pytest

from conftest import commit_all, run_git

from morphojudge.contracts.errors import MorphoJudgeError
from morphojudge.git.diff import build_diff_result, diff_files
from morphojudge.git.snapshot import resolve_snapshot

RULES = "batch01-test"


def _fixture_snapshot(fixture_repo, fixture_roots):
    return resolve_snapshot(
        fixture_repo,
        base_ref="HEAD~1",
        target_ref="HEAD",
        rules_version=RULES,
        allowed_roots=fixture_roots,
    )


def _by_path(files):
    return {entry.path: entry for entry in files}


# --- real fixture ----------------------------------------------------------


def test_fixture_diff_statuses_match_manifest(fixture_repo, fixture_roots, manifest):
    result = build_diff_result(fixture_repo, _fixture_snapshot(fixture_repo, fixture_roots))
    by_path = _by_path(result.files)

    for path in manifest["expected_diff"]["added"]:
        assert by_path[path].status == "added", path
    for path in manifest["expected_diff"]["modified"]:
        assert by_path[path].status == "modified", path
    for path in manifest["expected_diff"]["deleted"]:
        assert by_path[path].status == "deleted", path
    for rename in manifest["expected_diff"]["renamed"]:
        entry = by_path[rename["new_path"]]
        assert entry.status == "renamed"
        assert entry.old_path == rename["old_path"]


def test_binary_file_policy(fixture_repo, fixture_roots):
    result = build_diff_result(fixture_repo, _fixture_snapshot(fixture_repo, fixture_roots))
    logo = _by_path(result.files)["assets/logo.png"]
    assert logo.binary is True
    assert logo.additions is None
    assert logo.deletions is None
    assert logo.line_map is None
    assert logo.line_map_unavailable_reason == "binary_file"


def test_renamed_file_has_line_map(fixture_repo, fixture_roots):
    result = build_diff_result(fixture_repo, _fixture_snapshot(fixture_repo, fixture_roots))
    renamed = _by_path(result.files)["src/lib/notify.ts"]
    assert renamed.status == "renamed"
    assert renamed.line_map is not None and renamed.line_map, "edited rename must map lines"
    assert renamed.additions is not None and renamed.additions > 0


def test_modified_file_stats_match_numstat(fixture_repo, fixture_roots):
    snapshot = _fixture_snapshot(fixture_repo, fixture_roots)
    result = build_diff_result(fixture_repo, snapshot)
    queries = _by_path(result.files)["src/db/queries.ts"]
    numstat = run_git(
        fixture_repo, "diff", "--numstat", snapshot.identity.base_commit, snapshot.identity.target_commit
    )
    expected = None
    for line in numstat.strip().splitlines():
        additions, deletions, path = line.split("\t")
        if path == "src/db/queries.ts":
            expected = (int(additions), int(deletions))
    assert expected is not None
    assert (queries.additions, queries.deletions) == expected


def test_deleted_file_keeps_old_bytes(fixture_repo, fixture_roots):
    result = build_diff_result(fixture_repo, _fixture_snapshot(fixture_repo, fixture_roots))
    deleted = _by_path(result.files)["src/lib/legacy-format.ts"]
    assert deleted.status == "deleted"
    assert deleted.new_bytes is None
    assert deleted.old_bytes is not None and deleted.old_bytes > 0
    assert deleted.deletions > 0


def test_added_files_have_zero_old_side(fixture_repo, fixture_roots):
    result = build_diff_result(fixture_repo, _fixture_snapshot(fixture_repo, fixture_roots))
    for path in ("src/broken/invalid.ts", "src/generated/big-table.ts", ".gitignore"):
        entry = _by_path(result.files)[path]
        assert entry.status == "added"
        assert entry.old_bytes is None
        assert entry.new_bytes is not None
        assert entry.line_map is not None
        assert all(hunk.old_count == 0 for hunk in entry.line_map), path


def test_hunks_stay_within_file_bounds(fixture_repo, fixture_roots):
    snapshot = _fixture_snapshot(fixture_repo, fixture_roots)
    result = build_diff_result(fixture_repo, snapshot)
    queries = _by_path(result.files)["src/db/queries.ts"]
    base_text = run_git(
        fixture_repo, "cat-file", "-p", f"{snapshot.identity.base_commit}:src/db/queries.ts"
    )
    target_text = run_git(
        fixture_repo, "cat-file", "-p", f"{snapshot.identity.target_commit}:src/db/queries.ts"
    )
    base_lines = base_text.count("\n")
    target_lines = target_text.count("\n")
    for hunk in queries.line_map or []:
        assert hunk.old_start + hunk.old_count <= base_lines + 1
        assert hunk.new_start + hunk.new_count <= target_lines + 1


def test_identical_commits_produce_empty_diff(temp_repo, tmp_path):
    empty = diff_files(temp_repo, _head(temp_repo), _head(temp_repo))
    assert empty == []


def _head(repo, rev: str = "HEAD") -> str:
    return run_git(repo, "rev-parse", rev).strip()


def _parent(repo) -> str:
    return _head(repo, "HEAD~1")


# --- scenario repos --------------------------------------------------------


def test_copied_file_detected(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-b", "main")
    original = repo / "original.ts"
    original.write_text("export const A = 1;\nexport const B = 2;\n", encoding="utf-8")
    commit_all(repo, "one")
    (repo / "copy.ts").write_text(original.read_text(encoding="utf-8"), encoding="utf-8")
    commit_all(repo, "two", date="2026-03-01T08:00:00+00:00")

    files = diff_files(repo, _parent(repo), _head(repo))
    copied = [entry for entry in files if entry.status == "copied"]
    assert copied, f"expected copy detection, got {[f.status for f in files]}"
    assert copied[0].path == "copy.ts"
    assert copied[0].old_path == "original.ts"


def test_type_change_to_symlink(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-b", "main")
    target_file = repo / "target.txt"
    target_file.write_text("target\n", encoding="utf-8")
    link = repo / "link.ts"
    link.write_text("export const L = 1;\n", encoding="utf-8")
    commit_all(repo, "one")
    link.unlink()
    os.symlink("target.txt", link)
    commit_all(repo, "two", date="2026-03-02T08:00:00+00:00")

    files = diff_files(repo, _parent(repo), _head(repo))
    entry = _by_path(files)["link.ts"]
    assert entry.status == "type_changed"
    assert entry.is_symlink is True
    assert entry.line_map is None
    assert entry.line_map_unavailable_reason == "symlink_target"


def test_submodule_pointer_change(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    run_git(sub, "init", "-b", "main")
    (sub / "inner.txt").write_text("inner\n", encoding="utf-8")
    commit_all(sub, "sub init")
    sub_sha_1 = run_git(sub, "rev-parse", "HEAD").strip()
    (sub / "inner.txt").write_text("inner2\n", encoding="utf-8")
    commit_all(sub, "sub init 2", date="2026-03-03T08:00:00+00:00")
    sub_sha_2 = run_git(sub, "rev-parse", "HEAD").strip()

    parent = tmp_path / "parent"
    parent.mkdir()
    run_git(parent, "init", "-b", "main")
    (parent / "README.md").write_text("# p\n", encoding="utf-8")
    run_git(parent, "add", "-A")
    run_git(parent, "commit", "-m", "p0")
    # gitlink 指向的目录不存在于磁盘，add -A 会清除索引中的 gitlink；
    # 因此每次 update-index 后直接提交已就绪的索引。
    run_git(parent, "update-index", "--add", "--cacheinfo", f"160000,{sub_sha_1},vendored")
    run_git(parent, "commit", "-m", "p1", date="2026-03-04T08:00:00+00:00")
    run_git(parent, "update-index", "--cacheinfo", f"160000,{sub_sha_2},vendored")
    run_git(parent, "commit", "-m", "p2", date="2026-03-05T08:00:00+00:00")

    files = diff_files(parent, _parent(parent), _head(parent))
    entry = _by_path(files)["vendored"]
    assert entry.is_submodule is True
    assert entry.line_map is None
    assert entry.line_map_unavailable_reason == "submodule_pointer"


def test_untracked_files_reported_but_not_in_files(temp_repo, tmp_path):
    (temp_repo / "fresh.ts").write_text("export const F = 1;\n", encoding="utf-8")
    (temp_repo / "second.txt").write_text("v2\n", encoding="utf-8")
    commit_all(temp_repo, "second")
    (temp_repo / "untracked.ts").write_text("export const U = 1;\n", encoding="utf-8")

    snapshot = resolve_snapshot(
        temp_repo, base_ref="HEAD~1", target_ref="HEAD", rules_version=RULES, allowed_roots=[tmp_path]
    )
    result = build_diff_result(temp_repo, snapshot)
    assert "untracked.ts" in result.untracked
    paths = {entry.path for entry in result.files}
    assert "untracked.ts" not in paths


def test_diff_rejects_non_sha_input(temp_repo):
    with pytest.raises(MorphoJudgeError):
        diff_files(temp_repo, "not-a-sha", "also-not-a-sha")
