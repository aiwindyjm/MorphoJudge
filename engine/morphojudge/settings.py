"""Daemon settings, read from environment only (no config files, no secrets)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

DEFAULT_REPOSITORY_ROOTS = "/fixtures"


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
