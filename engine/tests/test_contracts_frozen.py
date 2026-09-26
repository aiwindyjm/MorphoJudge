"""ARC-001: code enums and error semantics must match the frozen document."""

from __future__ import annotations

from pathlib import Path

import pytest

from morphojudge.contracts.domain import (
    SCHEMA_VERSION,
    AnalysisStatus,
    StageStatus,
)
from morphojudge.contracts.errors import ErrorCode

FREEZE_DOC = Path("/docs/contract-freezes.md")


@pytest.fixture(scope="module")
def freeze_doc() -> str:
    if not FREEZE_DOC.exists():
        pytest.fail(
            f"{FREEZE_DOC} missing; run tests via docker compose so /docs is mounted"
        )
    return FREEZE_DOC.read_text(encoding="utf-8")


def test_analysis_status_enum_matches_frozen_doc(freeze_doc: str):
    for status in AnalysisStatus:
        assert f"`{status.value}`" in freeze_doc, status.value


def test_stage_status_enum_matches_frozen_doc(freeze_doc: str):
    for status in StageStatus:
        assert f"`{status.value}`" in freeze_doc, status.value


def test_error_codes_match_frozen_doc(freeze_doc: str):
    for code in ErrorCode:
        assert code.value in freeze_doc, code.value


def test_schema_version_is_frozen(freeze_doc: str):
    # F1.4：Batch-04-R1 兼容追加 API DTO 后 minor 递增；历史版本保留登记
    assert SCHEMA_VERSION == "1.4.0"
    for version in ("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0"):
        assert version in freeze_doc


def test_layer_flow_is_frozen(freeze_doc: str):
    assert "Repository/Snapshot → Selection/Coverage" in freeze_doc
    assert "Web 只调用 API" in freeze_doc


def test_untracked_policy_is_frozen(freeze_doc: str):
    assert "untracked" in freeze_doc
    assert "永不进入 Diff 文件清单" in freeze_doc


def test_snapshot_id_formula_is_frozen(freeze_doc: str):
    assert "morphojudge-snapshot-v1" in freeze_doc
