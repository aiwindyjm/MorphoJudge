"""API-001: analysis creation, status and cancellation.

The API accepts only registered repository ids (Freeze 4) — never raw host
paths, never commands. Idempotent creation per docs/contract-freezes.md.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Header, Request, Response

from ...contracts.domain import AnalysisStatus, StageName, StageRecord, StageStatus
from ...contracts.errors import ErrorCode, MorphoJudgeError
from ...db.repository import AnalysisRepository, is_terminal_status
from ..schemas import (
    AnalysisCounts,
    AnalysisResponse,
    CreateAnalysisRequest,
    RepositoriesPage,
    RepositoryInfo,
    analysis_id_for,
    derive_idempotency_key,
    request_hash,
)

router = APIRouter(prefix="/v1", tags=["analyses"])


def build_analysis_response(
    repository: AnalysisRepository, row: sqlite3.Row
) -> AnalysisResponse:
    analysis_id = str(row["analysis_id"])
    stages = [
        StageRecord(
            stage=StageName(stage_row["stage"]),
            status=StageStatus(stage_row["status"]),
            detail=stage_row["detail"],
        )
        for stage_row in repository.stage_records(analysis_id)
    ]
    counts = AnalysisCounts()
    if str(row["status"]) in ("completed", "completed_with_limits"):
        counts = AnalysisCounts(
            findings=repository.findings_count(analysis_id),
            evidence=repository.evidence_count(analysis_id),
        )
    return AnalysisResponse(
        analysis_id=analysis_id,
        status=AnalysisStatus(row["status"]),
        repository_id=str(row["repository_id"]),
        base_ref=str(row["base_ref"]),
        target_ref=str(row["target_ref"]),
        rules_version=str(row["rules_version"]),
        snapshot_id=row["snapshot_id"],
        resumable=bool(row["resumable"]),
        cancel_requested=bool(row["cancel_requested"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        failure_reason=row["failure_reason"],
        stages=stages,
        counts=counts,
    )


@router.get("/repositories", response_model=RepositoriesPage)
def list_repositories(request: Request) -> RepositoriesPage:
    repository: AnalysisRepository = request.app.state.repository
    return RepositoriesPage(
        items=[
            RepositoryInfo(
                repository_id=str(row["repository_id"]),
                name=str(row["name"]),
                registered_at=str(row["registered_at"]),
            )
            for row in repository.list_repositories()
        ]
    )


@router.post("/analyses", response_model=AnalysisResponse, status_code=201)
def create_analysis(
    payload: CreateAnalysisRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AnalysisResponse:
    state = request.app.state
    repository: AnalysisRepository = state.repository

    if repository.get_repository(payload.repository_id) is None:
        raise MorphoJudgeError(
            ErrorCode.REPOSITORY_NOT_REGISTERED,
            "repository_id is not registered on this daemon",
            details={"repository_id": payload.repository_id},
        )

    try:
        key = derive_idempotency_key(idempotency_key, payload)
    except ValueError as error:
        raise MorphoJudgeError(
            ErrorCode.INVALID_INPUT, str(error), details={"header": "Idempotency-Key"}
        ) from error
    payload_hash = request_hash(payload)
    options_json = (
        json.dumps(payload.options.model_dump(mode="json"), sort_keys=True)
        if payload.options is not None
        else None
    )

    def _existing_replay(existing: sqlite3.Row) -> AnalysisResponse:
        if str(existing["request_hash"]) != payload_hash:
            raise MorphoJudgeError(
                ErrorCode.ANALYSIS_CONFLICT,
                "idempotency key was already used for a different request",
                details={"analysis_id": str(existing["analysis_id"])},
            )
        response.status_code = 200
        response.headers["Idempotency-Replayed"] = "true"
        return build_analysis_response(repository, existing)

    existing = repository.get_analysis_by_key(key)
    if existing is not None:
        return _existing_replay(existing)

    analysis_id = analysis_id_for(key)
    try:
        repository.create_analysis(
            analysis_id=analysis_id,
            idempotency_key=key,
            request_hash=payload_hash,
            repository_id=payload.repository_id,
            base_ref=payload.base_ref,
            target_ref=payload.target_ref,
            rules_version=payload.rules_version,
            options_json=options_json,
        )
    except sqlite3.IntegrityError:
        # 并发同键创建：按既有行走重放/冲突逻辑。
        existing = repository.get_analysis_by_key(key)
        if existing is not None:
            return _existing_replay(existing)
        raise

    state.runner.submit(analysis_id)
    row = repository.get_analysis(analysis_id)
    assert row is not None  # 刚创建
    return build_analysis_response(repository, row)


@router.get("/analyses/{analysis_id}", response_model=AnalysisResponse)
def get_analysis(analysis_id: str, request: Request) -> AnalysisResponse:
    repository: AnalysisRepository = request.app.state.repository
    row = repository.get_analysis(analysis_id)
    if row is None:
        raise MorphoJudgeError(
            ErrorCode.ANALYSIS_NOT_FOUND, f"analysis not found: {analysis_id}"
        )
    return build_analysis_response(repository, row)


@router.post("/analyses/{analysis_id}/cancel", response_model=AnalysisResponse, status_code=202)
def cancel_analysis(analysis_id: str, request: Request) -> AnalysisResponse:
    state = request.app.state
    repository: AnalysisRepository = state.repository
    row = repository.get_analysis(analysis_id)
    if row is None:
        raise MorphoJudgeError(
            ErrorCode.ANALYSIS_NOT_FOUND, f"analysis not found: {analysis_id}"
        )
    if is_terminal_status(str(row["status"])):
        raise MorphoJudgeError(
            ErrorCode.ANALYSIS_CONFLICT,
            "analysis already reached a terminal state and cannot be cancelled",
            details={"status": str(row["status"])},
        )
    if state.runner.cancel_pending(analysis_id):
        # 排队中且尚未执行：直接进入终态（apply_cancel 内部 CAS，
        # 若此刻已被并发转入终态则不动任何行，按冲突返回）。
        repository.apply_cancel(analysis_id)
    elif not repository.request_cancel(analysis_id):
        # report 先提交终态：取消未获接受，不改 cancel_requested/updated_at/阶段。
        raise MorphoJudgeError(
            ErrorCode.ANALYSIS_CONFLICT,
            "analysis already reached a terminal state and cannot be cancelled",
            details={"status": str(repository.analysis_status(analysis_id))},
        )
    refreshed = repository.get_analysis(analysis_id)
    assert refreshed is not None
    if str(refreshed["status"]) != "cancelled" and is_terminal_status(str(refreshed["status"])):
        # cancel_pending 分支的并发竞态：任务在 CAS 前已被转成其他终态。
        raise MorphoJudgeError(
            ErrorCode.ANALYSIS_CONFLICT,
            "analysis already reached a terminal state and cannot be cancelled",
            details={"status": str(refreshed["status"])},
        )
    return build_analysis_response(repository, refreshed)
