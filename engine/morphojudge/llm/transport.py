"""Shared HTTP transport for providers (SSRF-safe by construction).

No dynamic URL strings are ever handed to a request helper:
1. the endpoint URL is parsed, scheme pinned to http/https;
2. the hostname is resolved NOW and every address must satisfy the
   caller's family rule (loopback-only for local, public-only for remote);
3. we connect to the RESOLVED IP directly (Host header carries the
   original name), so a DNS change between check and connect (rebinding)
   cannot redirect the socket;
4. http.client never follows redirects, so a 3xx cannot bounce the request
   elsewhere; any 3xx is surfaced as a provider error.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlparse

LOCALHOST_NAMES = ("localhost", "ip6-localhost", "ip6-loopback")


class EndpointRejected(Exception):
    """Endpoint violates the URL boundary for this provider family."""


@dataclass(frozen=True)
class PinnedEndpoint:
    scheme: str  # http | https
    host_header: str  # original hostname (may carry port)
    port: int
    ip: str  # validated resolved address we actually dial


def _resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as error:
        raise EndpointRejected(f"cannot resolve provider host: {host}") from error
    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    if not addresses:
        raise EndpointRejected(f"provider host has no usable addresses: {host}")
    return addresses


def pin_endpoint(url: str, *, family: str) -> PinnedEndpoint:
    """Validate + resolve an endpoint for the given family and pin the IP.

    family="local": every resolved address must be loopback (a local
    provider can never exfiltrate to LAN/public).
    family="remote": every resolved address must be public (loopback,
    private, link-local, reserved, unspecified and multicast are refused).
    """

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise EndpointRejected("provider endpoint must be http:// or https://")
    if parsed.username or parsed.password:
        raise EndpointRejected("provider endpoint must not embed credentials")
    host = parsed.hostname or ""
    if not host:
        raise EndpointRejected("provider endpoint has no host")
    default_port = 443 if parsed.scheme == "https" else 80
    port = parsed.port or default_port
    addresses = _resolve(host)
    for address in addresses:
        if family == "local":
            ok = address.is_loopback
        else:
            ok = not (
                address.is_loopback
                or address.is_private
                or address.is_link_local
                or address.is_reserved
                or address.is_unspecified
                or address.is_multicast
            )
        if not ok:
            raise EndpointRejected(
                f"provider host resolves to an address not allowed for a {family} provider: {address}"
            )
    # 拨号使用第一个已验证地址；Host 头保留原始主机名。
    dial_ip = str(addresses[0])
    host_header = parsed.netloc if parsed.port else f"{host}:{port}"
    return PinnedEndpoint(
        scheme=parsed.scheme, host_header=host_header, port=port, ip=dial_ip
    )


def request_pinned(
    endpoint: PinnedEndpoint,
    *,
    method: str,
    path: str,
    body: dict | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[int, bytes | dict | None, int]:
    """One request to the pinned IP. Returns (status, parsed_json_or_none, duration_ms).

    http.client follows no redirects: any 3xx is returned as-is and the
    caller must treat it as an error. `path` is always a literal constant
    at the call site ("/api/tags", "/api/generate").
    """

    payload = None
    headers = {"host": endpoint.host_header, "accept": "application/json"}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["content-type"] = "application/json"
    connection = (
        http.client.HTTPSConnection(endpoint.ip, endpoint.port, timeout=timeout_seconds)
        if endpoint.scheme == "https"
        else http.client.HTTPConnection(endpoint.ip, endpoint.port, timeout=timeout_seconds)
    )
    started = time.monotonic()
    try:
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        duration_ms = int((time.monotonic() - started) * 1000)
        if not (200 <= response.status < 300):
            return response.status, None, duration_ms
        try:
            return response.status, json.loads(raw.decode("utf-8")), duration_ms
        except (ValueError, UnicodeDecodeError):
            return response.status, None, duration_ms
    finally:
        connection.close()
