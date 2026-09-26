"""LLM-003: local Ollama provider.

- Endpoint from MORPHOJUDGE_OLLAMA_URL; every request re-pins the endpoint
  through transport.pin_endpoint(family="local") — the dial target MUST
  resolve to loopback, so a misconfigured "local" provider can never
  exfiltrate to LAN/public hosts and DNS rebinding cannot redirect it.
- Availability probe: GET /api/tags (short timeout). Missing/errored
  Ollama → reported unavailable; basic report and deterministic graph
  unaffected; no fallback to another provider.
- One POST /api/generate (format=json, stream off). 3xx/timeout/非 JSON
  都是 provider 错误，不是静默成功。
"""

from __future__ import annotations

import json

from .context import EvidenceContext
from .provider import ProviderResult
from .transport import EndpointRejected, pin_endpoint, request_pinned


class OllamaProvider:
    name = "ollama"

    def __init__(self, url: str, model: str, timeout_seconds: float = 60.0) -> None:
        self._url = url
        self.model = model
        self._timeout = timeout_seconds

    def available(self) -> tuple[bool, str]:
        try:
            endpoint = pin_endpoint(self._url, family="local")
        except EndpointRejected as error:
            return False, f"ollama endpoint rejected: {error}"
        try:
            status, body, _ = request_pinned(
                endpoint, method="GET", path="/api/tags", timeout_seconds=min(self._timeout, 5.0)
            )
        except (OSError, TimeoutError) as error:
            return False, f"ollama unreachable: {type(error).__name__}"
        if status != 200 or not isinstance(body, dict):
            return False, f"ollama tags status {status}"
        if self.model not in {str(entry.get("name", "")).split(":")[0] for entry in body.get("models", [])}:
            return False, f"model not present locally: {self.model}"
        return True, ""

    def generate(self, context: EvidenceContext) -> ProviderResult:
        try:
            endpoint = pin_endpoint(self._url, family="local")
        except EndpointRejected as error:
            return ProviderResult(self.name, self.model, error=f"endpoint rejected: {error}")
        try:
            status, body, duration_ms = request_pinned(
                endpoint,
                method="POST",
                path="/api/generate",
                body={
                    "model": self.model,
                    "prompt": context.prompt(),
                    "stream": False,
                    "format": "json",
                },
                timeout_seconds=self._timeout,
            )
        except (OSError, TimeoutError) as error:
            return ProviderResult(self.name, self.model, error=f"transport: {type(error).__name__}")
        if status >= 300:
            return ProviderResult(self.name, self.model, error=f"http status {status}", duration_ms=duration_ms)
        if not isinstance(body, dict):
            return ProviderResult(self.name, self.model, error="response is not JSON", duration_ms=duration_ms)
        raw_text = body.get("response")
        if not isinstance(raw_text, str):
            return ProviderResult(self.name, self.model, error="response missing 'response' text", duration_ms=duration_ms)
        try:
            parsed = json.loads(raw_text)
        except ValueError:
            return ProviderResult(self.name, self.model, error="model output is not JSON", duration_ms=duration_ms)
        return ProviderResult(self.name, self.model, raw=parsed, duration_ms=duration_ms)
