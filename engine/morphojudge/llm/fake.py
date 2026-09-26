"""LLM-002: deterministic Fake provider.

Produces explanations strictly FROM the given context (restatement /
inference / unknown), so the whole explain loop is testable without any
model. Failure modes (transport error, malformed JSON, hallucinated ids,
command text) are produced by tests via monkeypatching `generate`, not via
magic flags in production code.
"""

from __future__ import annotations

from .context import EvidenceContext
from .provider import ProviderResult

FAKE_MODEL = "fake-echo-1"


class FakeProvider:
    name = "fake"
    model = FAKE_MODEL

    def available(self) -> tuple[bool, str]:
        return True, ""

    def generate(self, context: EvidenceContext) -> ProviderResult:
        evidence_ids = [item.evidence_id for item in context.evidence]
        first = context.evidence[0]
        head = first.snippet.splitlines()[0].strip() if first.snippet.splitlines() else ""
        claims = [
            {
                "text": (
                    f"证据 {first.evidence_id} 显示 {first.path} 第 {first.start_line} 行附近存在"
                    f"规则 {first.rule_id or 'n/a'} 关联的代码：{head[:120]}"
                ),
                "evidence_ids": [first.evidence_id],
                "kind": "restatement",
            },
            {
                "text": (
                    "结合该证据的静态特征推断其行为目的需要上游触发点与调用方上下文；"
                    "这是模型推断，不是运行时行为证明。"
                ),
                "evidence_ids": evidence_ids[:2],
                "kind": "inference",
            },
            {
                "text": "未执行目标代码，无法确认该路径在运行时是否实际到达或数据是否敏感。",
                "evidence_ids": [first.evidence_id],
                "kind": "unknown",
            },
        ]
        uncertainty = "模型解释基于受限证据上下文；未覆盖的部分保持未知，不构成安全结论。"
        degraded_note = f" 上下文降级：{len(context.degraded)} 项。" if context.degraded else ""
        return ProviderResult(
            provider=self.name,
            model=self.model,
            raw={"claims": claims, "uncertainty": uncertainty + degraded_note},
        )
