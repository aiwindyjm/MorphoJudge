"""Restore only the published synthetic fixture. Never execute its source.

Run with: docker compose --profile fixtures run --rm --build fixture-setup
The setup container has no network and writes only its dedicated volume.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess

SEED = Path("/seed/ts-web.bundle")
MANIFEST = Path("/manifests/ts-web-manifest.json")
STORE = Path("/fixture-store")


def git(*args: str, cwd: Path) -> str:
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "LANG": "C",
    }
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
         "-c", "core.autocrlf=false", "-c", f"safe.directory={cwd}", *args],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=60, check=True,
    )
    return result.stdout.strip()


def verify(repo: Path, manifest: dict) -> None:
    if repo.is_symlink() or not (repo / ".git").is_dir() or (repo / ".git").is_symlink():
        raise RuntimeError("fixture must be a standalone repository, not a symlink")
    for ref, key in (("HEAD", "target"), ("HEAD~1", "base")):
        if git("rev-parse", ref, cwd=repo) != manifest["commits"][key]:
            raise RuntimeError(f"fixture {ref} differs from the published manifest")
    if git("status", "--porcelain", "--untracked-files=all", cwd=repo):
        raise RuntimeError("fixture has local changes; refusing to overwrite it")
    git("fsck", "--no-reflogs", cwd=repo)


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    digest = hashlib.sha256(SEED.read_bytes()).hexdigest()
    if digest != manifest["distribution"]["sha256"]:
        raise RuntimeError("fixture bundle checksum mismatch")
    STORE.mkdir(exist_ok=True)
    # Serialize independent setup invocations. A failed partial clone is retained,
    # never deleted or silently replaced.
    with (STORE / ".setup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Reserve the nested bind-mount point before the parent is mounted RO.
        (STORE / "manifests").mkdir(exist_ok=True)
        target = STORE / "ts-web"
        if target.exists() or target.is_symlink():
            verify(target, manifest)
            print("Fixture already verified; no files overwritten.")
            return
        incoming = STORE / ".ts-web-incoming"
        if incoming.exists() or incoming.is_symlink():
            raise RuntimeError("incomplete fixture setup exists; inspect the dedicated volume")
        git("clone", "--no-checkout", "--no-hardlinks", str(SEED), str(incoming), cwd=STORE)
        git("checkout", "--detach", manifest["commits"]["target"], cwd=incoming)
        # Bundle origin is a local setup-only path, not an external service.
        git("remote", "remove", "origin", cwd=incoming)
        verify(incoming, manifest)
        os.rename(incoming, target)
        print("Fixture restored and verified. Source, hooks and package scripts were not executed.")


if __name__ == "__main__":
    main()
