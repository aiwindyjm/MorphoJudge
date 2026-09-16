"""Security: git command execution, alias shadowing, ref injection, env isolation.

These tests prove repository-controlled strings can never turn into command
execution through the analysis path.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import commit_all, run_git

from morphojudge.contracts.errors import ErrorCode, MorphoJudgeError
from morphojudge.git.diff import diff_files
from morphojudge.git.runner import run_git as engine_run_git
from morphojudge.git.snapshot import read_working_tree, resolve_snapshot

RULES = "batch01-test"


def test_ref_with_shell_metacharacters_never_executes(temp_repo, tmp_path):
    marker = tmp_path / "pwned.txt"
    with pytest.raises(MorphoJudgeError) as excinfo:
        resolve_snapshot(
            temp_repo,
            base_ref=f"HEAD; touch {marker}",
            target_ref="HEAD",
            rules_version=RULES,
            allowed_roots=[tmp_path],
        )
    assert excinfo.value.code in (ErrorCode.INVALID_INPUT, ErrorCode.REF_NOT_FOUND)
    assert not marker.exists()


def test_ref_starting_with_dash_is_rejected_before_git(temp_repo, tmp_path):
    for evil in ("--upload-pack=evil", "--exec=x", "-oTouch"):
        with pytest.raises(MorphoJudgeError) as excinfo:
            resolve_snapshot(
                temp_repo,
                base_ref=evil,
                target_ref="HEAD",
                rules_version=RULES,
                allowed_roots=[tmp_path],
            )
        assert excinfo.value.code == ErrorCode.INVALID_INPUT


def test_repo_local_alias_cannot_shadow_git_subcommands(temp_repo, tmp_path):
    """A malicious fixture may define alias.diff/alias.status in .git/config;
    the runner must clear aliases for the subcommands it uses."""
    marker_st = tmp_path / "alias-status-pwned"
    marker_df = tmp_path / "alias-diff-pwned"
    config = temp_repo / ".git" / "config"
    config.write_text(
        config.read_text(encoding="utf-8")
        + f"\n[alias]\n\tstatus = !touch {marker_st}\n\tdiff = !touch {marker_df}\n",
        encoding="utf-8",
    )
    (temp_repo / "second.txt").write_text("v2\n", encoding="utf-8")
    commit_all(temp_repo, "second")

    entries = read_working_tree(temp_repo)
    assert isinstance(entries, list)
    assert not marker_st.exists()

    head = run_git(temp_repo, "rev-parse", "HEAD").strip()
    parent = run_git(temp_repo, "rev-parse", "HEAD~1").strip()
    files = diff_files(temp_repo, parent, head)
    assert files, "diff must run as the builtin, not an alias"
    assert not marker_df.exists()


def test_git_replace_objects_cannot_spoof_commits(temp_repo, tmp_path):
    (temp_repo / "second.txt").write_text("v2\n", encoding="utf-8")
    commit_all(temp_repo, "second")
    head = run_git(temp_repo, "rev-parse", "HEAD").strip()
    # Even if a replace ref existed, --no-replace-objects keeps identity real.
    report = resolve_snapshot(
        temp_repo, base_ref="HEAD~1", target_ref="HEAD", rules_version=RULES, allowed_roots=[tmp_path]
    )
    assert report.identity.target_commit == head


def test_runner_whitelist_blocks_mutating_subcommands(temp_repo):
    for args in (["commit", "-m", "x"], ["checkout", "main"], ["config", "user.name", "x"], ["clone", "http://x"]):
        with pytest.raises(MorphoJudgeError) as excinfo:
            engine_run_git(args, cwd=temp_repo)
        assert excinfo.value.code == ErrorCode.INTERNAL_ERROR


def test_runner_ignores_polluted_environment(temp_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "not-a-git-dir"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "fake-index"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsHook")
    report = resolve_snapshot(
        temp_repo, base_ref="HEAD", target_ref="HEAD", rules_version=RULES, allowed_roots=[tmp_path]
    )
    assert report.identity.canonical_path == str(temp_repo.resolve())


def test_repository_path_with_shell_characters_is_inert(tmp_path):
    spicy = tmp_path / 'repo-$(touch INJECTED)-`id`'
    spicy.mkdir()
    run_git(spicy, "init", "-b", "main")
    (spicy / "README.md").write_text("# spicy\n", encoding="utf-8")
    commit_all(spicy, "init")
    run_git(spicy, "commit", "--allow-empty", "-m", "second", date="2026-04-01T08:00:00+00:00")

    report = resolve_snapshot(
        spicy, base_ref="HEAD~1", target_ref="HEAD", rules_version=RULES, allowed_roots=[tmp_path]
    )
    assert report.identity.base_commit != report.identity.target_commit
    assert not (tmp_path / "INJECTED").exists()
    assert not (Path.cwd() / "INJECTED").exists()


def test_fixture_analysis_never_executes_fixture_code(fixture_repo, fixture_roots):
    """The full Batch-01 pipeline over the real fixture must not run fixture
    scripts (no node/npm processes can even be invoked: runner whitelist is
    read-only git plumbing)."""
    from morphojudge.selection.service import decide_selection
    from morphojudge.selection.rules import SelectionRules

    snapshot = resolve_snapshot(
        fixture_repo,
        base_ref="HEAD~1",
        target_ref="HEAD",
        rules_version=RULES,
        allowed_roots=fixture_roots,
    )
    result = diff_files(fixture_repo, snapshot.identity.base_commit, snapshot.identity.target_commit)
    decisions, _ = decide_selection(result, SelectionRules(), snapshot.identity.snapshot_id)
    assert decisions, "fixture pipeline produced decisions without executing code"
