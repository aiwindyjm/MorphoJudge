"""Hardened read-only Git runner.

Security properties (tested in engine/tests/security/):
- Argument arrays only; shell is never used, so repository-controlled strings
  can never become shell syntax.
- Global/system/user git config is ignored (GIT_CONFIG_GLOBAL/SYSTEM=/dev/null)
  which kills credential helpers, aliases and hooks configuration from outside.
- The repo-local alias of the subcommand we invoke is force-cleared
  (``-c alias.<sub>=``) because aliases can shadow builtins.
- ``safe.directory`` is set per invocation (bind mounts break ownership checks);
  ``--no-replace-objects`` defeats refs/replace spoofing.
- fsmonitor / untracked cache are disabled so `status` never spawns helpers.
- User-controlled refs are validated by callers BEFORE reaching git; the
  runner additionally refuses NUL/newline-bearing arguments as defense in depth.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Mapping, Sequence

from ..contracts.errors import ErrorCode, MorphoJudgeError

# Subcommands whose repo-local aliases we force-clear (see module docstring).
_USED_SUBCOMMANDS = frozenset(
    {"rev-parse", "status", "diff", "ls-tree", "cat-file", "log", "show"}
)


def _runner_env() -> Mapping[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _check_args(args: Sequence[str]) -> None:
    if not args:
        raise MorphoJudgeError(ErrorCode.INTERNAL_ERROR, "empty git argument list")
    for arg in args:
        if not isinstance(arg, str):
            raise MorphoJudgeError(
                ErrorCode.INTERNAL_ERROR, "git arguments must be strings"
            )
        if "\x00" in arg or "\n" in arg or "\r" in arg:
            raise MorphoJudgeError(
                ErrorCode.INVALID_INPUT,
                "git arguments must not contain control characters",
            )


def run_git_bytes(
    args: Sequence[str],
    *,
    cwd: Path,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[bytes]:
    """run_git core with raw byte output (for `cat-file` blob reads).

    Same hardening as run_git: argument array, whitelisted subcommand,
    alias clearing, isolated environment, no shell.
    """

    _check_args(args)
    subcommand = args[0]
    if subcommand not in _USED_SUBCOMMANDS:
        raise MorphoJudgeError(
            ErrorCode.INTERNAL_ERROR,
            f"git subcommand {subcommand!r} is not whitelisted for the runner",
        )

    hardening = [
        "--no-replace-objects",
        "-C",
        str(cwd),
        "-c",
        f"alias.{subcommand}=",
        "-c",
        f"safe.directory={cwd}",
        "-c",
        "core.fileMode=false",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
    ]
    argv = ["git", *hardening, *args]
    try:
        return subprocess.run(  # noqa: S603 - fixed binary, argument array
            argv,
            cwd=str(cwd),
            env=dict(_runner_env()),
            capture_output=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MorphoJudgeError(
            ErrorCode.GIT_COMMAND_FAILED,
            f"git {subcommand} timed out after {timeout}s",
            retryable=True,
        ) from exc
    except OSError as exc:
        raise MorphoJudgeError(
            ErrorCode.GIT_COMMAND_FAILED,
            f"failed to execute git: {exc.strerror}",
            retryable=True,
        ) from exc


def run_git(
    args: Sequence[str],
    *,
    cwd: Path,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    """Run git with an argument array. Never raises on nonzero exit; callers
    interpret returncode/stderr (or use check_git)."""

    completed = run_git_bytes(args, cwd=cwd, timeout=timeout)
    stdout = completed.stdout.decode("utf-8", errors="surrogateescape")
    stderr = completed.stderr.decode("utf-8", errors="surrogateescape")
    return subprocess.CompletedProcess(completed.args, completed.returncode, stdout, stderr)


def check_git(
    args: Sequence[str],
    *,
    cwd: Path,
    timeout: float = 30.0,
) -> str:
    """run_git that raises GIT_COMMAND_FAILED on nonzero exit."""

    completed = run_git(args, cwd=cwd, timeout=timeout)
    if completed.returncode != 0:
        raise MorphoJudgeError(
            ErrorCode.GIT_COMMAND_FAILED,
            f"git {args[0]} exited {completed.returncode}: "
            + completed.stderr.strip()[:300],
            retryable=False,
        )
    return completed.stdout
