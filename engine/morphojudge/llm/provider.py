"""LLM-001/003: provider protocol and URL boundary enforcement.

Two families, with OPPOSITE host rules:
- LOCAL (ollama): the endpoint MUST be loopback — a mis-configured "local"
  provider can never exfiltrate to LAN/public hosts.
- REMOTE: the endpoint MUST be a public host and each request requires a
  per-analysis one-time consent; loopback/private/reserved hosts are
  rejected (SSRF bar).

Both families accept http/https only. These checks run before any socket
is opened and are unit-tested.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

from .context import EvidenceContext


class ProviderConfigError(Exception):
    """Provider endpoint configuration violates the URL boundary."""


def _resolve_host(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as error:
        raise ProviderConfigError(f"cannot resolve provider host: {host}") from error
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    if not addresses:
        raise ProviderConfigError(f"provider host has no usable addresses: {host}")
    return addresses


def validate_local_url(url: str) -> str:
    """Local provider endpoints: http/https AND loopback-only."""

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ProviderConfigError("local provider URL must be http/https")
    host = parsed.hostname or ""
    if host in ("localhost",) or (host and host.endswith(".localhost")):
        return url
    for address in _resolve_host(host):
        if address.is_loopback:
            return url
    raise ProviderConfigError(
        "local provider URL must point at a loopback address (localhost/127.0.0.0/8/::1)"
    )


def validate_remote_url(url: str) -> str:
    """Remote provider endpoints: http/https AND public-only.

    Rejects loopback, private, link-local, reserved and unspecified
    addresses so an authorized "remote" call can never target the local
    machine or the internal network.
    """

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ProviderConfigError("remote provider URL must be http/https")
    host = parsed.hostname or ""
    if host in ("localhost",) or (host and host.endswith(".localhost")):
        raise ProviderConfigError("remote provider URL must not be localhost")
    for address in _resolve_host(host):
        if (
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_reserved
            or address.is_unspecified
            or address.is_multicast
        ):
            raise ProviderConfigError(
                "remote provider URL must be a public address "
                "(loopback/private/reserved are rejected)"
            )
    return url


@dataclass
class ProviderResult:
    """Raw provider output BEFORE validation; errors carry a stable reason."""

    provider: str
    model: str
    raw: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int | None = None


class ExplainProvider(Protocol):
    name: str
    model: str

    def available(self) -> tuple[bool, str]:
        """Cheap availability probe; (False, reason) → 503, no fallback."""

        ...

    def generate(self, context: EvidenceContext) -> ProviderResult:
        ...
