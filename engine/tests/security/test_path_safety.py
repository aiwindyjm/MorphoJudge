"""Security: path traversal, symlink escapes and root containment."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from morphojudge.contracts.errors import ErrorCode, MorphoJudgeError
from morphojudge.git.snapshot import resolve_snapshot

RULES = "batch01-test"


def _code(excinfo) -> str:
    return excinfo.value.code.value


def test_dotdot_into_private_is_outside_roots():
    with pytest.raises(MorphoJudgeError) as excinfo:
        resolve_snapshot(
            Path("/fixtures/../PRIVATE"),
            base_ref="HEAD~1",
            target_ref="HEAD",
            rules_version=RULES,
            allowed_roots=[Path("/fixtures")],
        )
    assert _code(excinfo) == ErrorCode.PATH_OUT_OF_ROOTS.value


def test_long_traversal_chain_is_outside_roots():
    with pytest.raises(MorphoJudgeError) as excinfo:
        resolve_snapshot(
            Path("/fixtures/ts-web/../../../../../../../etc"),
            base_ref="HEAD",
            target_ref="HEAD",
            rules_version=RULES,
            allowed_roots=[Path("/fixtures")],
        )
    assert _code(excinfo) in {
        ErrorCode.PATH_OUT_OF_ROOTS.value,
        ErrorCode.REPOSITORY_NOT_FOUND.value,
    }


def test_nul_in_repository_path_rejected(tmp_path):
    with pytest.raises(MorphoJudgeError) as excinfo:
        resolve_snapshot(
            tmp_path / "re\x00po",
            base_ref="HEAD",
            target_ref="HEAD",
            rules_version=RULES,
            allowed_roots=[tmp_path],
        )
    assert _code(excinfo) == ErrorCode.INVALID_INPUT.value


def test_file_instead_of_directory_rejected(tmp_path):
    a_file = tmp_path / "plain.txt"
    a_file.write_text("not a repo\n", encoding="utf-8")
    with pytest.raises(MorphoJudgeError) as excinfo:
        resolve_snapshot(
            a_file,
            base_ref="HEAD",
            target_ref="HEAD",
            rules_version=RULES,
            allowed_roots=[tmp_path],
        )
    assert _code(excinfo) == ErrorCode.REPOSITORY_NOT_FOUND.value


def test_symlink_chain_escaping_root_rejected(temp_repo, tmp_path):
    secret_repo = tmp_path.parent / f"secret-{temp_repo.name}"
    secret_repo.mkdir()
    os.symlink(secret_repo, tmp_path / "innocent-link")
    with pytest.raises(MorphoJudgeError) as excinfo:
        resolve_snapshot(
            tmp_path / "innocent-link",
            base_ref="HEAD",
            target_ref="HEAD",
            rules_version=RULES,
            allowed_roots=[tmp_path],
        )
    assert _code(excinfo) == ErrorCode.SYMLINK_ESCAPE.value


def test_root_boundary_enforced_for_existing_outside_repo(fixture_repo):
    """The real fixture repo is reachable ONLY through its registered roots."""
    with pytest.raises(MorphoJudgeError) as excinfo:
        resolve_snapshot(
            fixture_repo,
            base_ref="HEAD~1",
            target_ref="HEAD",
            rules_version=RULES,
            allowed_roots=[Path("/tmp")],
        )
    assert _code(excinfo) == ErrorCode.PATH_OUT_OF_ROOTS.value


def test_container_mount_whitelist():
    if not Path("/.dockerenv").exists():
        pytest.skip("container boundary checks require docker execution")
    assert Path("/fixtures/ts-web").is_dir()
    assert not Path("/PRIVATE").exists()
    assert not Path("/var/run/docker.sock").exists()
    assert not os.access("/fixtures", os.W_OK)
    engine_writable = os.access("/engine", os.W_OK)
    assert engine_writable is False, "engine must be mounted read-only"
