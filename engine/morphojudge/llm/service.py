"""LLM-001/002 编排：build context → provider → validate → persist.

不变量：模型失败/校验失败落库为 status=failed 的 Explanation，绝不冒充
completed；确定性图与分析状态机从不被解释写入触碰；provider 失败不自动
切换到其他 provider。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..db.repository import AnalysisRepository
from .context import EvidenceContext, build_evidence_context
from .provider import ProviderResult
from .validation import validate_provider_output


@dataclass(frozen=True)
class ExplainOutcome:
    status: str  # completed | failed
    explanation_id: str
    provider: str
    model: str
    context_hash: str
    claims: list[dict[str, Any]]
    uncertainty: str
    errors: list[str]
    duration_ms: int | None
    authorization_id: int | None


def run_explanation(
    repository: AnalysisRepository,
    *,
    analysis_id: str,
    subject_type: str,
    subject_id: str,
    subject_label: str,
    evidence_rows: list,
    adjacency: list[dict[str, Any]],
    provider,
    consent=None,
    consent_endpoint: str | None = None,
) -> ExplainOutcome:
    try:
        context = build_evidence_context(
            analysis_id=analysis_id,
            subject_type=subject_type,
            subject_id=subject_id,
            subject_label=subject_label,
            evidence_rows=evidence_rows,
            adjacency=adjacency,
        )
    except Exception as error:  # noqa: BLE001 — 构造失败如实返回
        raise ValueError(str(error)) from error

    authorization_id = None
    if provider.name == "remote" and consent is not None and repository is not None:
        from urllib.parse import urlparse

        host = urlparse(consent_endpoint or consent.endpoint).hostname or "unknown"
        authorization_id = repository.record_remote_authorization(
            analysis_id=analysis_id,
            provider=provider.name,
            endpoint_host=host,
            scope_evidence_ids=[item.evidence_id for item in context.evidence],
            context_hash=context.context_hash,
        )

    if provider.name == "remote":
        result: ProviderResult = provider.generate(context, consent)
    else:
        result = provider.generate(context)

    if authorization_id is not None and repository is not None:
        repository.consume_remote_authorization(analysis_id, provider.name)

    if result.error is not None:
        saved = repository.save_explanation(
            analysis_id=analysis_id,
            subject_type=subject_type,
            subject_id=subject_id,
            provider=result.provider,
            model=result.model,
            status="failed",
            claims=[],
            uncertainty="",
            errors=[result.error],
            context_hash=context.context_hash,
            duration_ms=result.duration_ms,
            authorization_id=authorization_id,
        )
        return ExplainOutcome("failed", saved, result.provider, result.model, context.context_hash, [], "", [result.error], result.duration_ms, authorization_id)

    outcome = validate_provider_output(result.raw, context)
    if not outcome.ok:
        saved = repository.save_explanation(
            analysis_id=analysis_id,
            subject_type=subject_type,
            subject_id=subject_id,
            provider=result.provider,
            model=result.model,
            status="failed",
            claims=[],
            uncertainty="",
            errors=outcome.errors,
            context_hash=context.context_hash,
            duration_ms=result.duration_ms,
            authorization_id=authorization_id,
        )
        return ExplainOutcome("failed", saved, result.provider, result.model, context.context_hash, [], "", outcome.errors, result.duration_ms, authorization_id)

    saved = repository.save_explanation(
        analysis_id=analysis_id,
        subject_type=subject_type,
        subject_id=subject_id,
        provider=result.provider,
        model=result.model,
        status="completed",
        claims=outcome.claims,
        uncertainty=outcome.uncertainty,
        errors=[],
        context_hash=context.context_hash,
        duration_ms=result.duration_ms,
        authorization_id=authorization_id,
    )
    return ExplainOutcome("completed", saved, result.provider, result.model, context.context_hash, outcome.claims, outcome.uncertainty, [], result.duration_ms, authorization_id)


def adjacency_for_subject(map_dict: dict | None, subject_id: str) -> list[dict[str, Any]]:
    """主体节点的直接关系（只读、确定性事实，供上下文引用）。"""

    if not map_dict:
        return []
    adjacency: list[dict[str, Any]] = []
    for edge in map_dict.get("edges", []):
        if edge.get("source_id") == subject_id:
            adjacency.append(
                {
                    "relation": edge.get("relation"),
                    "target": edge.get("target_id"),
                    "resolution": edge.get("resolution"),
                }
            )
    return adjacency[:20]
