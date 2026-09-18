"""Daemon settings, read from environment only (no config files, no secrets)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_REPOSITORY_ROOTS = "/fixtures"
DEFAULT_DATA_DIR = "/data"
DEFAULT_MANIFEST_DIR = "/fixtures/manifests"


@lru_cache(maxsize=1)
def repository_roots() -> tuple[Path, ...]:
    """Allowed repository root directories (realpath-resolved at use site).

    Compose sets MORPHOJUDGE_REPOSITORY_ROOTS=/fixtures; paths outside these
    roots are rejected with PATH_OUT_OF_ROOTS.
    """

    raw = os.environ.get("MORPHOJUDGE_REPOSITORY_ROOTS", DEFAULT_REPOSITORY_ROOTS)
    roots = [Path(part).resolve() for part in raw.split(":") if part.strip()]
    if not roots:
        raise RuntimeError("MORPHOJUDGE_REPOSITORY_ROOTS resolved to an empty set")
    return tuple(roots)


@dataclass(frozen=True)
class DaemonSettings:
    """Everything the daemon needs at startup (Batch-04).

    db_path must resolve inside data_dir (validated by db.database).
    """

    db_path: Path
    data_dir: Path
    repository_roots: tuple[Path, ...]
    manifest_dir: Path | None
    worker_concurrency: int = 1


def load_daemon_settings() -> DaemonSettings:
    from .db.database import validate_db_path

    data_dir = Path(os.environ.get("MORPHOJUDGE_DATA_DIR", DEFAULT_DATA_DIR))
    raw_db = os.environ.get("MORPHOJUDGE_DB_PATH") or str(
        data_dir / "morphojudge.sqlite"
    )
    try:
        db_path = validate_db_path(Path(raw_db), data_dir)
    except ValueError as error:
        raise RuntimeError(str(error)) from error

    raw_manifest = os.environ.get("MORPHOJUDGE_MANIFEST_DIR", DEFAULT_MANIFEST_DIR)
    manifest_dir = Path(raw_manifest) if raw_manifest else None

    try:
        concurrency = max(1, int(os.environ.get("MORPHOJUDGE_WORKER_CONCURRENCY", "1")))
    except ValueError:
        concurrency = 1

    return DaemonSettings(
        db_path=db_path,
        data_dir=data_dir,
        repository_roots=repository_roots(),
        manifest_dir=manifest_dir,
        worker_concurrency=concurrency,
    )
