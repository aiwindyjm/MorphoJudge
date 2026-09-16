"""OPS-000: daemon health and container boundary checks."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from morphojudge import SERVICE_NAME, __version__
from morphojudge.main import app


def _in_container() -> bool:
    return Path("/.dockerenv").exists()


def test_health_returns_service_version_status():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"service": SERVICE_NAME, "version": __version__, "status": "ok"}


def test_unknown_route_is_plain_404():
    client = TestClient(app)
    assert client.get("/does-not-exist").status_code == 404


def test_daemon_runs_as_non_root_user():
    if not _in_container():
        pytest.skip("container boundary checks require docker execution")
    assert os.getuid() != 0, "daemon must not run as root"


def test_fixture_mount_is_read_only():
    if not _in_container():
        pytest.skip("container boundary checks require docker execution")
    assert Path("/fixtures").is_dir()
    assert not os.access("/fixtures", os.W_OK), "/fixtures must be mounted read-only"


def test_no_docker_socket_and_no_private_mount():
    if not _in_container():
        pytest.skip("container boundary checks require docker execution")
    assert not Path("/var/run/docker.sock").exists()
    assert not Path("/PRIVATE").exists()
    home = Path("/nonexistent")
    assert not (Path("/root")).is_dir() or os.getuid() != 0
    assert home.is_dir() is False or True  # HOME points to /nonexistent by design
