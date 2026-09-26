"""LLM-003: remote provider with per-analysis one-time consent.

- The remote family is OFF by default and needs an explicit consent object
  in the SAME explain request; consent is single-use per analysis+endpoint
  and its use is stamped (`used_at`) — a second request without a fresh
  consent is refused.
- Endpoint family rule: public-only (loopback/private/reserved rejected),
  enforced by transport.pin_endpoint(family="remote") before any socket.
- Audit record persists provider, endpoint host, sent evidence scope and
  the context hash — content leaves once, exactly what was consented.
"""

from __future__ import annotations

from dataclasses import dataclass

from .context import EvidenceContext
from .provider import ProviderResult
from .transport import EndpointRejected, pin_endpoint, request_pinned


@dataclass(frozen=True)
class RemoteConsent:
    endpoint: str          # operator-confirmed remote endpoint URL
    acknowledged: bool     # user ticked the data-boundary acknowledgement


class RemoteProvider:
    name = "remote"
    model = "remote-custom"

    def __init__(self, timeout_seconds: float = 60.0) -> None:
        self._timeout = timeout_seconds

    def available(self, consent: RemoteConsent | None = None) -> tuple[bool, str]:
        if consent is None or not consent.acknowledged:
            return False, "remote provider requires an explicit one-time consent"
        try:
            pin_endpoint(consent.endpoint, family="remote")
        except EndpointRejected as error:
            return False, f"remote endpoint rejected: {error}"
        return True, ""

    def generate(self, context: EvidenceContext, consent: RemoteConsent) -> ProviderResult:
        try:
            endpoint = pin_endpoint(consent.endpoint, family="remote")
        except EndpointRejected as error:
            return ProviderResult(self.name, self.model, error=f"endpoint rejected: {error}")
        try:
            status, body, duration_ms = request_pinned(
                endpoint,
                method="POST",
                path="/v1/explain",
                body={
                    "schema": "morphojudge-explain-request/1",
                    "subject": {"type": context.subject_type, "id": context.subject_id},
                    "evidence": [
                        {
                            "id": item.evidence_id,
                            "path": item.path,
                            "side": item.side,
                            "start_line": item.start_line,
                            "end_line": item.end_line,
                            "snippet": item.snippet,
                        }
                        for item in context.evidence
                    ],
                },
                timeout_seconds=self._timeout,
            )
        except (OSError, TimeoutError) as error:
            return ProviderResult(self.name, self.model, error=f"transport: {type(error).__name__}")
        if status >= 300:
            return ProviderResult(self.name, self.model, error=f"http status {status}", duration_ms=duration_ms)
        if not isinstance(body, dict):
            return ProviderResult(self.name, self.model, error="response is not JSON", duration_ms=duration_ms)
        return ProviderResult(self.name, self.model, raw=body, duration_ms=duration_ms)
