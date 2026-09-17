"""Public clone bootstrap: offline restore, identity and non-overwrite policy."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from conftest import run_git


@pytest.fixture
def setup_module_under_test(tmp_path, fixture_repo, manifest, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "fixture_setup", "/usr/local/lib/morphojudge-setup-fixture.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle = tmp_path / "test.bundle"
    run_git(fixture_repo, "bundle", "create", str(bundle), "HEAD")
    data = json.loads(json.dumps(manifest))
    data["distribution"]["sha256"] = hashlib.sha256(bundle.read_bytes()).hexdigest()
    metadata = tmp_path / "manifest.json"
    metadata.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(module, "SEED", bundle)
    monkeypatch.setattr(module, "MANIFEST", metadata)
    monkeypatch.setattr(module, "STORE", tmp_path / "store")
    return module


def test_setup_restores_exact_commits_and_is_idempotent(setup_module_under_test):
    setup = setup_module_under_test
    setup.main()
    target = setup.STORE / "ts-web"
    before = (target / ".git" / "HEAD").stat().st_mtime_ns
    setup.main()
    assert (target / ".git" / "HEAD").stat().st_mtime_ns == before
    assert (setup.STORE / "manifests").is_dir()
    assert setup.git("remote", cwd=target) == ""


def test_setup_rejects_checksum_before_writing(setup_module_under_test):
    setup = setup_module_under_test
    setup.SEED.write_bytes(setup.SEED.read_bytes() + b"corrupt")
    with pytest.raises(RuntimeError, match="checksum"):
        setup.main()
    assert not setup.STORE.exists()


def test_setup_refuses_modified_fixture_without_overwriting(setup_module_under_test):
    setup = setup_module_under_test
    setup.main()
    note = setup.STORE / "ts-web" / "user-change.txt"
    note.write_text("keep this data", encoding="utf-8")
    with pytest.raises(RuntimeError, match="local changes"):
        setup.main()
    assert note.read_text() == "keep this data"


def test_setup_refuses_incomplete_restore(setup_module_under_test):
    setup = setup_module_under_test
    (setup.STORE / ".ts-web-incoming").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="incomplete"):
        setup.main()
    assert (setup.STORE / ".ts-web-incoming").exists()


def test_setup_refuses_fixture_symlink(setup_module_under_test, tmp_path):
    setup = setup_module_under_test
    setup.STORE.mkdir()
    (setup.STORE / "ts-web").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symlink"):
        setup.main()
