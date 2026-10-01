"""Batch-06 / LLM-001..003: POST /v1/analyses/{id}/explain + 查询端点。

编排：按 ID 取本分析证据（参数化，范围外/幻觉拒绝）→ 受限上下文
（截断降级、输入哈希、无工具调用）→ Provider（Fake/Ollama 本地环回/
远程按次授权）→ 校验（引用存在性、命令文本拒绝）→ 落库 completed|failed。

事实边界：解释失败/不可用不阻塞报告（503/failed 只影响本端点）；
模型输出永不改图、永不改分析状态机；provider 失败不自动切换。
"""

from __future__ import annotations

import json
import os
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from ...contracts.errors import ErrorCode, MorphoJudgeError
from ...db.repository import AnalysisRepository, utc_now
from ...llm.fake import FakeProvider
from ...llm.ollama import OllamaProvider
from ...llm.remote import RemoteConsent, RemoteProvider
from ...llm.service import adjacency_for_subject, run_explanation
from ..schemas import (
    ExplainClaimItem,
    ExplainConsentInput,
    ExplainProvidersResponse,
    ExplainRequest,
    ExplanationPayload,
    RemoteAuthorizationItem,
    RemoteAuthorizationsPage,
)

router = APIRouter(prefix="/v1/analyses", tags=["explain"])


def _row_to_payload(row: Any) -> ExplanationPayload:
    return ExplanationPayload(
        explanation_id=str(row["explanation_id"]),
        analysis_id=str(row["analysis_id"]),
        subject_type=str(row["subject_type"]),
        subject_id=str(row["subject_id"]),
        status=str(row["status"]),
        provider=str(row["provider"]),
        model=str(row["model"]),
        claims=json.loads(str(row["claims_json"])),
        uncertainty=str(row["uncertainty"]),
        errors=json.loads(str(row["errors_json"])),
        context_hash=str(row["context_hash"]),
        duration_ms=row["duration_ms"],
        authorization_id=row["authorization_id"],
        created_at=str(row["created_at"]),
    )


def _get_subject(
    repository: AnalysisRepository, analysis_id: str, subject_type: str, subject_id: str
) -> tuple[str, list[str], list[dict[str, Any]]]:
    """返回 (label, evidence_ids, adjacency)；主体不存在 → 404。"""

    if subject_type == "finding":
        row = repository.get_finding(analysis_id, subject_id)
        if row is None:
            raise MorphoJudgeError(
                ErrorCode.EXPLAIN_SUBJECT_NOT_FOUND,
                f"finding not found in this analysis: {subject_id}",
            )
        evidence_ids = json.loads(str(row["evidence_ids_json"]))
        return str(row["finding_id"]), list(evidence_ids), []
    map_dict = repository.get_map(analysis_id, "target")
    if map_dict is None:
        raise MorphoJudgeError(
            ErrorCode.EXPLAIN_SUBJECT_NOT_FOUND,
            "software map is not persisted for this analysis",
            details={"subject_id": subject_id},
        )
    node = next((n for n in map_dict.get("nodes", []) if n.get("id") == subject_id), None)
    if node is None:
        raise MorphoJudgeError(
            ErrorCode.EXPLAIN_SUBJECT_NOT_FOUND,
            f"node not found in this analysis map: {subject_id}",
        )
    return str(node.get("label") or subject_id), list(node.get("evidence_ids") or []), adjacency_for_subject(map_dict, subject_id)


def _resolve_evidence_rows(
    repository: AnalysisRepository, analysis_id: str, evidence_ids: list[str]
) -> list:
    rows = repository.evidence_for_finding(analysis_id, evidence_ids)
    by_id = {str(row["evidence_id"]): row for row in rows}
    missing = [eid for eid in evidence_ids if eid not in by_id]
    if missing:
        raise MorphoJudgeError(
            ErrorCode.EXPLAIN_EVIDENCE_NOT_FOUND,
            "evidence id not found in this analysis (cross-analysis or hallucinated references are rejected)",
            details={"first_missing": missing[0][:40]},
        )
    return [by_id[eid] for eid in evidence_ids]


def _build_provider(request: ExplainRequest, repository: AnalysisRepository, analysis_id: str):
    if request.provider == "fake":
        return FakeProvider(), None
    if request.provider == "ollama":
        url = os.environ.get("MORPHOJUDGE_OLLAMA_URL", "")
        model = os.environ.get("MORPHOJUDGE_OLLAMA_MODEL", "qwen2.5-coder")
        if not url:
            raise MorphoJudgeError(
                ErrorCode.EXPLAIN_PROVIDER_UNAVAILABLE,
                "local Ollama is not configured (MORPHOJUDGE_OLLAMA_URL is unset)",
                retryable=False,
            )
        provider = OllamaProvider(url, model)
        ok, reason = provider.available()
        if not ok:
            raise MorphoJudgeError(
                ErrorCode.EXPLAIN_PROVIDER_UNAVAILABLE, reason, retryable=True
            )
        return provider, None
    # remote
    consent_input = request.remote_consent
    if consent_input is None or not consent_input.acknowledged:
        raise MorphoJudgeError(
            ErrorCode.EXPLAIN_CONSENT_REQUIRED,
            "remote provider requires an explicit one-time consent with acknowledged=true",
            retryable=False,
        )
    provider = RemoteProvider()
    ok, reason = provider.available(RemoteConsent(consent_input.endpoint, True))
    if not ok:
        raise MorphoJudgeError(ErrorCode.EXPLAIN_CONSENT_REQUIRED, reason, retryable=False)
    return provider, RemoteConsent(consent_input.endpoint, True)


@router.post("/{analysis_id}/explain", response_model=ExplanationPayload, status_code=200)
def explain(analysis_id: str, payload: ExplainRequest, request: Request) -> ExplanationPayload:
    repository: AnalysisRepository = request.app.state.repository
    row = repository.get_analysis(analysis_id)
    if row is None:
        raise MorphoJudgeError(ErrorCode.ANALYSIS_NOT_FOUND, f"analysis not found: {analysis_id}")

    label, evidence_ids, adjacency = _get_subject(
        repository, analysis_id, payload.subject_type, payload.subject_id
    )
    if payload.evidence_ids:
        evidence_ids = list(payload.evidence_ids)
    evidence_ids = list(dict.fromkeys(evidence_ids))  # 去重保序
    if not evidence_ids:
        raise MorphoJudgeError(
            ErrorCode.EXPLAIN_EVIDENCE_NOT_FOUND,
            "empty evidence: the model may only explain existing evidence",
            retryable=False,
        )
    evidence_rows = _resolve_evidence_rows(repository, analysis_id, evidence_ids)

    provider, consent = _build_provider(payload, repository, analysis_id)

    try:
        outcome = run_explanation(
            repository,
            analysis_id=analysis_id,
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
            subject_label=label,
            evidence_rows=evidence_rows,
            adjacency=adjacency,
            provider=provider,
            consent=consent,
            consent_endpoint=consent.endpoint if consent is not None else None,
        )
    except ValueError as error:
        raise MorphoJudgeError(
            ErrorCode.EXPLAIN_EVIDENCE_NOT_FOUND, str(error), retryable=False
        ) from error

    saved = repository.get_explanation(analysis_id, outcome.explanation_id)
    assert saved is not None
    return _row_to_payload(saved)


@router.get("/{analysis_id}/explain", response_model=list[ExplanationPayload])
def list_explanations(
    analysis_id: str,
    request: Request,
    subject_type: Annotated[str | None, Query(pattern="^(finding|node)$")] = None,
    subject_id: str | None = None,
) -> list[ExplanationPayload]:
    repository: AnalysisRepository = request.app.state.repository
    if repository.get_analysis(analysis_id) is None:
        raise MorphoJudgeError(ErrorCode.ANALYSIS_NOT_FOUND, f"analysis not found: {analysis_id}")
    rows = repository.list_explanations(
        analysis_id, subject_type=subject_type, subject_id=subject_id
    )
    return [_row_to_payload(row) for row in rows]


def ollama_list_models(base_url: str) -> list[str]:
    """Fetch available model names from Ollama GET /api/tags."""
    from morphojudge.llm.transport import pin_endpoint, request_pinned
    endpoint = pin_endpoint(base_url, family="local")
    status, body, _ = request_pinned(endpoint, method="GET", path="/api/tags", timeout_seconds=5.0)
    if status == 200 and isinstance(body, dict):
        return sorted(str(m.get("name", "")) for m in body.get("models", []) if m.get("name"))
    return []


@router.get("/{analysis_id}/explain/providers", response_model=ExplainProvidersResponse)
def explain_providers(analysis_id: str, request: Request) -> ExplainProvidersResponse:
    repository: AnalysisRepository = request.app.state.repository
    if repository.get_analysis(analysis_id) is None:
        raise MorphoJudgeError(ErrorCode.ANALYSIS_NOT_FOUND, f"analysis not found: {analysis_id}")
    providers: list[dict[str, Any]] = [
        {"provider": "fake", "model": "fake-echo-1", "available": True, "note": "deterministic offline provider"}
    ]
    url = os.environ.get("MORPHOJUDGE_OLLAMA_URL", "")
    model = os.environ.get("MORPHOJUDGE_OLLAMA_MODEL", "qwen2.5-coder")
    if url:
        ok, reason = OllamaProvider(url, model).available()
        models: list[str] = []
        if ok:
            try:
                models = ollama_list_models(url)
            except Exception:
                models = []
        providers.append(
            {"provider": "ollama", "model": model, "available": ok,
             "note": "" if ok else reason, "models": models}
        )
    else:
        providers.append(
            {"provider": "ollama", "model": None, "available": False,
             "note": "not configured (set MORPHOJUDGE_OLLAMA_URL, e.g. http://host.docker.internal:11434)",
             "models": []}
        )
    providers.append(
        {"provider": "remote", "model": None, "available": True, "note": "requires one-time consent per analysis"}
    )
    return ExplainProvidersResponse(providers=providers)


@router.get("/{analysis_id}/explain/authorizations", response_model=RemoteAuthorizationsPage)
def list_authorizations(analysis_id: str, request: Request) -> RemoteAuthorizationsPage:
    repository: AnalysisRepository = request.app.state.repository
    if repository.get_analysis(analysis_id) is None:
        raise MorphoJudgeError(ErrorCode.ANALYSIS_NOT_FOUND, f"analysis not found: {analysis_id}")
    items = [
        RemoteAuthorizationItem(
            authorization_id=int(row["authorization_id"]),
            provider=str(row["provider"]),
            endpoint_host=str(row["endpoint_host"]),
            scope_evidence_ids=json.loads(str(row["scope_evidence_ids_json"])),
            context_hash=str(row["context_hash"]),
            granted_at=str(row["granted_at"]),
            used_at=row["used_at"],
        )
        for row in repository.list_remote_authorizations(analysis_id)
    ]
    return RemoteAuthorizationsPage(items=items)
