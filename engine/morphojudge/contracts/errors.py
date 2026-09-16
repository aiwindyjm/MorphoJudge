"""Unified error semantics (Freeze 1 §F1.3, docs/contract-freezes.md)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Dict

from pydantic import Field

from .domain import SCHEMA_VERSION, ContractModel


class ErrorCode(StrEnum):
    REPOSITORY_NOT_FOUND = "REPOSITORY_NOT_FOUND"
    NOT_A_GIT_REPOSITORY = "NOT_A_GIT_REPOSITORY"
    LINKED_WORKTREE_NOT_SUPPORTED = "LINKED_WORKTREE_NOT_SUPPORTED"
    REF_NOT_FOUND = "REF_NOT_FOUND"
    PATH_OUT_OF_ROOTS = "PATH_OUT_OF_ROOTS"
    PATH_TRAVERSAL_DETECTED = "PATH_TRAVERSAL_DETECTED"
    SYMLINK_ESCAPE = "SYMLINK_ESCAPE"
    GIT_COMMAND_FAILED = "GIT_COMMAND_FAILED"
    INVALID_INPUT = "INVALID_INPUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorObject(ContractModel):
    code: ErrorCode
    message: str = Field(min_length=1)
    retryable: bool
    details: Dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(ContractModel):
    schema_version: str = SCHEMA_VERSION
    error: ErrorObject


class MorphoJudgeError(Exception):
    """Service-level error carrying a stable ErrorCode.

    message must stay user-safe: no secrets, no arbitrary host paths beyond
    the requested repository path itself.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: Dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details: Dict[str, Any] = details or {}

    def to_response(self) -> ErrorResponse:
        return ErrorResponse(
            error=ErrorObject(
                code=self.code,
                message=self.message,
                retryable=self.retryable,
                details=self.details,
            )
        )


_HTTP_STATUS_BY_CODE = {
    ErrorCode.REPOSITORY_NOT_FOUND: 404,
    ErrorCode.NOT_A_GIT_REPOSITORY: 400,
    ErrorCode.LINKED_WORKTREE_NOT_SUPPORTED: 400,
    ErrorCode.REF_NOT_FOUND: 400,
    ErrorCode.PATH_OUT_OF_ROOTS: 400,
    ErrorCode.PATH_TRAVERSAL_DETECTED: 400,
    ErrorCode.SYMLINK_ESCAPE: 400,
    ErrorCode.GIT_COMMAND_FAILED: 500,
    ErrorCode.INVALID_INPUT: 400,
    ErrorCode.INTERNAL_ERROR: 500,
}


def http_status_for(code: ErrorCode) -> int:
    return _HTTP_STATUS_BY_CODE.get(code, 500)


__all__ = [
    "ErrorCode",
    "ErrorObject",
    "ErrorResponse",
    "MorphoJudgeError",
    "http_status_for",
]
