"""LLM-002: provider output validation.

The provider response must be a JSON object with claims whose evidence_ids
exist IN THE CURRENT CONTEXT (same analysis). Validation failures produce
a persisted Explanation with status=failed — never a fake `completed` and
never a mutation of the deterministic graph.

Command-like output (shell blocks, sudo, chained shell metacharacters) is
rejected: model text is data, and data that tries to be a command never
reaches the UI as an explanation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .context import EvidenceContext

_KINDS = {"restatement", "inference", "unknown"}

_COMMAND_PATTERNS = (
    re.compile(r"```(bash|sh|shell|powershell|cmd)"),
    re.compile(r"^\s*(sudo|rm\s+-rf|curl\s+[^\s]+\s*\|\s*(ba)?sh|wget\s+.*\|\s*(ba)?sh)\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\b(?:&&|\|\|)\s*(?:rm|sudo|curl|wget|chmod|chown)\b"),
    re.compile(r"\bnc\s+-e\b|\bmkfifo\b.*\bnc\b", re.IGNORECASE),
)


class ProviderClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4_000)
    evidence_ids: list[str] = Field(min_length=1)
    kind: str

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        if value not in _KINDS:
            raise ValueError(f"claim kind must be one of {sorted(_KINDS)}")
        return value


class ProviderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[ProviderClaim] = Field(min_length=1)
    uncertainty: str = Field(default="", max_length=2_000)


@dataclass
class ValidationOutcome:
    ok: bool
    claims: list[dict[str, Any]] = field(default_factory=list)
    uncertainty: str = ""
    errors: list[str] = field(default_factory=list)


def looks_like_command(text: str) -> bool:
    return any(pattern.search(text) for pattern in _COMMAND_PATTERNS)


def validate_provider_output(raw: Any, context: EvidenceContext) -> ValidationOutcome:
    known = {item.evidence_id for item in context.evidence}

    if not isinstance(raw, dict):
        return ValidationOutcome(ok=False, errors=["provider output is not a JSON object"])
    try:
        output = ProviderOutput.model_validate(raw)
    except Exception as error:  # noqa: BLE001 — pydantic 错误统一降级为失败原因
        return ValidationOutcome(ok=False, errors=[f"schema: {type(error).__name__}"])

    claims: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, claim in enumerate(output.claims):
        missing = [evidence_id for evidence_id in claim.evidence_ids if evidence_id not in known]
        if missing:
            errors.append(f"claim[{index}] references unknown evidence: {missing[0]}")
            continue
        if looks_like_command(claim.text):
            errors.append(f"claim[{index}] contains command-like text; rejected as data")
            continue
        claims.append(
            {"text": claim.text, "evidence_ids": list(claim.evidence_ids), "kind": claim.kind}
        )
    if errors:
        # 任一 claim 违规即整条解释失败：不挑拣“看起来没问题”的部分冒充完成。
        return ValidationOutcome(ok=False, errors=errors)
    if not claims:
        return ValidationOutcome(ok=False, errors=["no valid claims"])
    if looks_like_command(output.uncertainty):
        return ValidationOutcome(ok=False, errors=["uncertainty contains command-like text"])
    return ValidationOutcome(ok=True, claims=claims, uncertainty=output.uncertainty)
