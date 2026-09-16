"""GIT-001: SnapshotIdentity determinism, error states and boundary handling."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import commit_all, run_git

from morphojudge.contracts.errors import ErrorCode, MorphoJudgeError
from morphojudge.git.snapshot import (
    compute_snapshot_id,
    repository_id_for,
    resolve_commit,
    resolve_snapshot,
)

RULES = "batch01-test"


def _resolve(repo: Path, roots: list[Path], base: str = "HEAD~1", target: str = "HEAD", rules: str = RULES):
    return resolve_snapshot(
        repo, base_ref=base, target_ref=target, rules_version=rules, allowed_roots=roots
    )


def _error_code(excinfo) -> str:
    return excinfo.value.code.value


# --- determinism -----------------------------------------------------------


def test_same_input_yields_same_snapshot_id(temp_repo, tmp_path):
    first = _resolve(temp_repo, [tmp_path])
    second = _resolve(temp_repo, [tmp_path])
    assert first.identity.snapshot_id == second.identity.snapshot_id


def test_snapshot_id_follows_frozen_formula(temp_repo, tmp_path):
    report = _resolve(temp_repo, [tmp_path])
    identity = report.identity
    expected_repository_id = repository_id_for(identity.canonical_path)
    expected_snapshot_id = compute_snapshot_id(
        expected_repository_id,
        identity.base_commit,
        identity.target_commit,
        identity.rules_version,
    )
    assert identity.repository_id == expected_repository_id
    assert identity.snapshot_id == expected_snapshot_id
    assert len(identity.snapshot_id) == 64


def test_different_commits_yield_different_snapshot_id(temp_repo, tmp_path):
    (temp_repo / "second.txt").write_text("v2\n", encoding="utf-8")
    commit_all(temp_repo, "second")
    earlier = _resolve(temp_repo, [tmp_path], base="HEAD~2", target="HEAD~1")
    later = _resolve(temp_repo, [tmp_path], base="HEAD~1", target="HEAD")
    assert earlier.identity.snapshot_id != later.identity.snapshot_id


def test_rules_version_participates_in_snapshot_id(temp_repo, tmp_path):
    first = _resolve(temp_repo, [tmp_path])
    second = _resolve(temp_repo, [tmp_path], rules="batch01-other")
    assert first.identity.snapshot_id != second.identity.snapshot_id


# --- error states ----------------------------------------------------------


def test_missing_repository_errors_not_found(tmp_path):
    with pytest.raises(MorphoJudgeError) as excinfo:
        _resolve(tmp_path / "nope", [tmp_path])
    assert _error_code(excinfo) == ErrorCode.REPOSITORY_NOT_FOUND.value


def test_non_git_directory_errors_not_a_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "file.txt").write_text("x\n", encoding="utf-8")
    with pytest.raises(MorphoJudgeError) as excinfo:
        _resolve(plain, [tmp_path])
    assert _error_code(excinfo) == ErrorCode.NOT_A_GIT_REPOSITORY.value


def test_invalid_ref_errors_ref_not_found(temp_repo, tmp_path):
    with pytest.raises(MorphoJudgeError) as excinfo:
        _resolve(temp_repo, [tmp_path], base="refs/heads/does-not-exist")
    assert _error_code(excinfo) == ErrorCode.REF_NOT_FOUND.value


def test_detached_head_still_resolves(temp_repo, tmp_path):
    run_git(temp_repo, "checkout", "--detach", "HEAD")
    report = _resolve(temp_repo, [tmp_path])
    assert report.identity.base_commit != report.identity.target_commit


def test_dirty_workspace_flagged_but_identity_stable(temp_repo, tmp_path):
    clean = _resolve(temp_repo, [tmp_path])
    (temp_repo / "README.md").write_text("# dirty\n", encoding="utf-8")
    dirty = _resolve(temp_repo, [tmp_path])
    assert dirty.workspace_dirty is True
    assert dirty.working_tree, "dirty entries must be recorded"
    assert dirty.identity.snapshot_id == clean.identity.snapshot_id
    assert dirty.identity.base_commit == clean.identity.base_commit


def test_untracked_files_reported_informationally(temp_repo, tmp_path):
    (temp_repo / "untracked.ts").write_text("export const x = 1;\n", encoding="utf-8")
    report = _resolve(temp_repo, [tmp_path])
    paths = {entry.path for entry in report.working_tree}
    assert "untracked.ts" in paths
    assert report.workspace_dirty is True


# --- boundaries ------------------------------------------------------------


def test_repository_outside_roots_rejected(temp_repo, tmp_path):
    other_root = tmp_path / "roots"
    other_root.mkdir()
    with pytest.raises(MorphoJudgeError) as excinfo:
        _resolve(temp_repo, [other_root])
    assert _error_code(excinfo) == ErrorCode.PATH_OUT_OF_ROOTS.value


def test_symlinked_repository_root_rejected(temp_repo, tmp_path):
    link = tmp_path / "repo-link"
    os.symlink(temp_repo, link)
    with pytest.raises(MorphoJudgeError) as excinfo:
        _resolve(link, [tmp_path])
    assert _error_code(excinfo) == ErrorCode.SYMLINK_ESCAPE.value


def test_symlink_escape_to_outside_rejected(temp_repo, tmp_path):
    outside = tmp_path.parent / f"outside-{temp_repo.name}"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "door"
    os.symlink(outside, link)
    with pytest.raises(MorphoJudgeError) as excinfo:
        _resolve(link / "anything", [tmp_path])
    assert _error_code(excinfo) == ErrorCode.SYMLINK_ESCAPE.value


def test_intermediate_symlink_inside_roots_canonicalized(tmp_path):
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    repo = real_parent / "repo"
    repo.mkdir()
    run_git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("# x\n", encoding="utf-8")
    commit_all(repo, "init")
    run_git(repo, "commit", "--allow-empty", "-m", "second", date="2026-02-02T08:00:00+00:00")

    link = tmp_path / "jump"
    os.symlink(real_parent, link)
    report = _resolve(link / "repo", [tmp_path])
    assert report.identity.canonical_path == str(real_parent / "repo")


def test_linked_worktree_rejected(temp_repo, tmp_path):
    worktree = tmp_path / "worktree"
    run_git(temp_repo, "worktree", "add", str(worktree), "-b", "wt")
    with pytest.raises(MorphoJudgeError) as excinfo:
        _resolve(worktree, [tmp_path])
    assert _error_code(excinfo) == ErrorCode.LINKED_WORKTREE_NOT_SUPPORTED.value
    run_git(temp_repo, "worktree", "remove", str(worktree), "--force")


def test_submodule_present_recorded_as_note(tmp_path):
    sub = tmp_path / "sub-repo"
    sub.mkdir()
    run_git(sub, "init", "-b", "main")
    (sub / "inner.txt").write_text("inner\n", encoding="utf-8")
    commit_all(sub, "sub init")

    parent = tmp_path / "parent"
    parent.mkdir()
    run_git(parent, "init", "-b", "main")
    (parent / "README.md").write_text("# parent\n", encoding="utf-8")
    commit_all(parent, "parent init")
    sub_sha = run_git(sub, "rev-parse", "HEAD").strip()
    run_git(parent, "update-index", "--add", "--cacheinfo", f"160000,{sub_sha},vendored")
    # gitlink 指向的目录不在磁盘上，必须直接提交已就绪的索引；
    # `git add -A` 会把不存在的路径从索引移除。
    run_git(parent, "commit", "-m", "add gitlink", date="2026-02-03T08:00:00+00:00")

    report = _resolve(parent, [tmp_path])
    assert "submodule_present" in report.notes
    assert report.identity.base_commit != report.identity.target_commit


def test_lfs_filter_recorded_as_note(temp_repo, tmp_path):
    (temp_repo / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n", encoding="utf-8")
    commit_all(temp_repo, "lfs attrs", date="2026-02-04T08:00:00+00:00")
    report = _resolve(temp_repo, [tmp_path])
    assert "lfs_filter_present" in report.notes


def test_base_equals_target_noted(temp_repo, tmp_path):
    report = _resolve(temp_repo, [tmp_path], base="HEAD", target="HEAD")
    assert "base_equals_target" in report.notes
    assert report.identity.base_commit == report.identity.target_commit


def test_relative_repository_path_rejected(temp_repo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(MorphoJudgeError) as excinfo:
        _resolve(Path("repo"), [tmp_path])
    assert _error_code(excinfo) == ErrorCode.INVALID_INPUT.value


def test_ref_with_option_prefix_rejected_before_git(temp_repo, tmp_path):
    with pytest.raises(MorphoJudgeError) as excinfo:
        resolve_commit(temp_repo, "--upload-pack=evil")
    assert _error_code(excinfo) == ErrorCode.INVALID_INPUT.value
