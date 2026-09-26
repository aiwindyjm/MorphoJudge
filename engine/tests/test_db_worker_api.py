"""TEST-DBAPI（Batch-04）：SQLite + Worker + FastAPI 纵向闭环集成测试。

覆盖：
- DB-001：空库迁移、重复迁移、失败迁移回滚、路径边界、动态 SQL 构造禁令；
- ANL-003：happy path 持久化、checkpoint 恢复（模拟崩溃）、协作式取消、
  同 snapshot 重跑不覆盖旧结果、非法 ref 失败保留原因；
- API-001/002：创建/轮询/取消、幂等重放与冲突、结果就绪语义、分页/过滤、
  复核、真实 fixture 端到端。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import build_scenario_repo

from morphojudge.api.schemas import (
    CreateAnalysisRequest,
    analysis_id_for,
    derive_idempotency_key,
    request_hash,
)
from morphojudge.contracts.errors import ErrorCode
from morphojudge.db import database
from morphojudge.db.database import Migration, current_version, migrate
from morphojudge.db.repository import AnalysisRepository
from morphojudge.git.snapshot import repository_id_for
from morphojudge.main import create_app
from morphojudge.settings import DaemonSettings
from morphojudge.worker.analysis import AnalysisRunner, AnalysisWorker

NET_SOURCE = """export async function ping(url: string) {
  await fetch("https://api.example.invalid/health");
  return true;
}
"""

TERMINAL = ("completed", "completed_with_limits", "failed", "cancelled")


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@dataclass
class Harness:
    client: TestClient
    repository: AnalysisRepository
    runner: AnalysisRunner
    root: Path
    db_path: Path


@pytest.fixture
def harness(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings = DaemonSettings(
        db_path=data_dir / "mj.sqlite",
        data_dir=data_dir,
        repository_roots=(tmp_path,),
        manifest_dir=None,
        worker_concurrency=1,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        yield Harness(
            client=client,
            repository=client.app.state.repository,
            runner=client.app.state.runner,
            root=tmp_path,
            db_path=settings.db_path,
        )


def make_net_repo(parent: Path, name: str = "scenario") -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    return build_scenario_repo(
        parent / name,
        {"src/net.ts": NET_SOURCE},
        {"src/other.ts": "export const x = 1;\n"},
    )


def make_net_repo(parent: Path, name: str = "scenario") -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    return build_scenario_repo(
        parent / name,
        {
            "src/net.ts": NET_SOURCE,
            "package.json": '{"name": "net", "dependencies": {}}\n',
        },
        {"src/other.ts": "export const x = 1;\n"},
    )


def setup_api_repo(harness: Harness, tmp_path: Path, name: str = "scenario") -> Path:
    """建仓后按解析路径补注册（daemon 启动扫描只覆盖启动前已存在的仓库；
    build_scenario_repo 会在传入目录下再建一层 scenario/，root 扫描不可依赖）。"""

    repo = make_net_repo(tmp_path, name=name)
    canonical = str(repo.resolve())
    harness.repository.register_repository(repository_id_for(canonical), repo.name, canonical)
    return repo


def register_and_create(
    repository: AnalysisRepository,
    repo: Path,
    *,
    key: str = "k1",
    base_ref: str = "HEAD~1",
    target_ref: str = "HEAD",
) -> str:
    canonical = str(repo.resolve())
    repository.register_repository(repository_id_for(canonical), repo.name, canonical)
    request = CreateAnalysisRequest(
        repository_id=repository_id_for(canonical),
        base_ref=base_ref,
        target_ref=target_ref,
    )
    analysis_id = analysis_id_for(key)
    repository.create_analysis(
        analysis_id=analysis_id,
        idempotency_key=key,
        request_hash=request_hash(request),
        repository_id=request.repository_id,
        base_ref=request.base_ref,
        target_ref=request.target_ref,
        rules_version=request.rules_version,
    )
    return analysis_id


def direct_worker(harness: Harness, on_stage_start=None) -> AnalysisWorker:
    return AnalysisWorker(
        harness.repository,
        allowed_roots=(harness.root,),
        manifest_dir=None,
        on_stage_start=on_stage_start,
    )


def wait_for_terminal(client: TestClient, analysis_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/v1/analyses/{analysis_id}")
        assert body.status_code == 200, body.text
        status = body.json()["status"]
        if status in TERMINAL:
            return body.json()
        time.sleep(0.05)
    pytest.fail(f"analysis {analysis_id} did not reach a terminal state in {timeout}s")


# ---------------------------------------------------------------------------
# DB-001：迁移与边界
# ---------------------------------------------------------------------------


def test_migrate_fresh_db_then_idempotent(tmp_path: Path):
    db = tmp_path / "data" / "mj.sqlite"
    db.parent.mkdir(parents=True)
    assert migrate(db) == 3
    assert current_version(db) == 3
    assert migrate(db) == 3  # 重复迁移不重复应用
    with database.reader(db) as connection:
        names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"analyses", "findings", "evidence", "maps", "decisions", "reviews"} <= names


def test_failed_migration_rolls_back_and_keeps_old_db(tmp_path: Path):
    db = tmp_path / "data" / "mj.sqlite"
    db.parent.mkdir(parents=True)
    migrate(db)

    def _broken(connection) -> None:
        connection.execute("CREATE TABLE broken(oops")

    broken = Migration(4, "0004_broken", _broken)
    with pytest.raises(RuntimeError, match="0004_broken"):
        migrate(db, migrations=(broken,))
    assert current_version(db) == 3
    with database.reader(db) as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE name = ?",
            ("broken",),
        ).fetchone()
    assert row["n"] == 0, "失败迁移不得留下半套 schema"


def test_db_path_must_stay_in_data_dir(tmp_path: Path):
    with pytest.raises(ValueError):
        database.validate_db_path(tmp_path / "outside.sqlite", tmp_path / "data")


def test_no_dynamic_sql_construction_in_db_layer():
    """所有 SQL 文本固定；值一律参数绑定（安全约束回归）。"""

    package = Path(database.__file__).parent
    for source in package.glob("*.py"):
        for line in source.read_text(encoding="utf-8").splitlines():
            if "execute" not in line:
                continue
            assert 'f"' not in line and "f'" not in line, (source.name, line)
            assert "+ where" not in line.lower() and "where + " not in line.lower(), (
                source.name,
                line,
            )


def test_idempotency_key_derivation_is_deterministic():
    request = CreateAnalysisRequest(
        repository_id="a" * 64, base_ref="HEAD~1", target_ref="HEAD"
    )
    assert derive_idempotency_key(None, request) == derive_idempotency_key(None, request)
    assert derive_idempotency_key("client-key-1", request) == "client-key-1"
    other = CreateAnalysisRequest(
        repository_id="b" * 64, base_ref="HEAD~1", target_ref="HEAD"
    )
    assert derive_idempotency_key(None, request) != derive_idempotency_key(None, other)
    with pytest.raises(ValueError):
        derive_idempotency_key("bad\nkey", request)


# ---------------------------------------------------------------------------
# ANL-003：Worker checkpoints / cancel / resume / no-overwrite
# ---------------------------------------------------------------------------


def test_worker_happy_path_persists_all_checkpoints(harness: Harness, tmp_path: Path):
    repo = make_net_repo(tmp_path)
    analysis_id = register_and_create(harness.repository, repo)
    status = direct_worker(harness).run(analysis_id)
    assert status in ("completed", "completed_with_limits")
    if harness.repository.result_document(analysis_id)["limits"]:
        assert status == "completed_with_limits"

    stages = {row["stage"]: row["status"] for row in harness.repository.stage_records(analysis_id)}
    assert stages == {
        "git": "completed",
        "selection": "completed",
        "parse": "completed",
        "behavior": "completed",
        "dependency": "completed",
        "report": "completed",
    }

    total, findings = harness.repository.list_findings(analysis_id, limit=100, offset=0)
    assert total > 0
    network = [row for row in findings if row["rule_id"] == "BEH-NETWORK"]
    assert network, "网络行为必须落库"
    evidence_ids = json.loads(network[0]["evidence_ids_json"])
    if evidence_ids:
        anchor = harness.repository.get_evidence(analysis_id, evidence_ids[0])
        assert anchor is not None and anchor["snippet"]

    assert harness.repository.get_map(analysis_id, "base") is not None
    assert harness.repository.get_map(analysis_id, "target") is not None
    decision_total, _ = harness.repository.list_decisions(analysis_id, limit=10, offset=0)
    assert decision_total > 0
    assert harness.repository.result_document(analysis_id) is not None
    row = harness.repository.get_analysis(analysis_id)
    assert row["snapshot_id"] and len(row["snapshot_id"]) == 64


def test_worker_resumes_after_crash_before_report(harness: Harness, tmp_path: Path):
    repo = make_net_repo(tmp_path)
    analysis_id = register_and_create(harness.repository, repo)

    def crash_at_report(_: str, stage: str) -> None:
        if stage == "report":
            raise SystemExit("simulated daemon crash")

    crashed = direct_worker(harness, on_stage_start=crash_at_report)
    with pytest.raises(SystemExit):
        crashed.run(analysis_id)

    # 崩溃后：git/rules checkpoint 已提交，report 未提交，状态仍可恢复。
    assert harness.repository.load_stage_output(analysis_id, "git") is not None
    assert harness.repository.load_stage_output(analysis_id, "parse") is not None
    assert harness.repository.load_stage_output(analysis_id, "rules") is not None
    assert harness.repository.result_document(analysis_id) is None
    assert harness.repository.analysis_status(analysis_id) == "running"

    resumed = direct_worker(harness).run(analysis_id)  # 模拟重启后的新 worker
    assert resumed in ("completed", "completed_with_limits")
    assert harness.repository.result_document(analysis_id) is not None
    # report 只提交一次：findings 行数与文档一致，无重复。
    document = harness.repository.result_document(analysis_id)
    total, _ = harness.repository.list_findings(analysis_id, limit=500, offset=0)
    assert total == len(document["findings"])


def test_worker_cancel_between_stages_is_terminal(harness: Harness, tmp_path: Path):
    repo = make_net_repo(tmp_path)
    analysis_id = register_and_create(harness.repository, repo)

    def request_cancel_before_rules(_: str, stage: str) -> None:
        if stage == "rules":
            harness.repository.request_cancel(analysis_id)

    status = direct_worker(harness, on_stage_start=request_cancel_before_rules).run(
        analysis_id
    )
    assert status == "cancelled"
    stages = {
        row["stage"]: row["status"]
        for row in harness.repository.stage_records(analysis_id)
    }
    assert stages["git"] == "completed"
    assert stages["selection"] == "completed"
    assert stages["parse"] == "completed"
    assert stages["behavior"] == "skipped"
    assert stages["dependency"] == "skipped"
    assert stages["report"] == "skipped"
    assert harness.repository.result_document(analysis_id) is None
    total, _ = harness.repository.list_findings(analysis_id, limit=10, offset=0)
    assert total == 0, "取消后不得残留 findings"


def test_worker_terminal_analysis_is_never_rerun(harness: Harness, tmp_path: Path):
    repo = make_net_repo(tmp_path)
    analysis_id = register_and_create(harness.repository, repo)
    worker = direct_worker(harness)
    first = worker.run(analysis_id)
    document_before = json.dumps(
        harness.repository.result_document(analysis_id), sort_keys=True
    )
    second = worker.run(analysis_id)  # 终态重入：直接返回，不重跑
    assert first == second
    assert json.dumps(harness.repository.result_document(analysis_id), sort_keys=True) == document_before


def test_rerun_same_snapshot_creates_new_analysis_not_overwrite(
    harness: Harness, tmp_path: Path
):
    repo = make_net_repo(tmp_path)
    first_id = register_and_create(harness.repository, repo, key="run-1")
    second_id = register_and_create(harness.repository, repo, key="run-2")
    worker = direct_worker(harness)
    assert worker.run(first_id) in ("completed", "completed_with_limits")
    first_total, first_rows = harness.repository.list_findings(first_id, limit=500, offset=0)
    first_ids = [row["finding_id"] for row in first_rows]

    assert worker.run(second_id) in ("completed", "completed_with_limits")
    with database.reader(harness.db_path) as connection:
        snapshot_rows = connection.execute("SELECT COUNT(*) AS n FROM snapshots").fetchone()
    assert snapshot_rows["n"] == 1, "同 snapshot 缓存只插入一次"

    second_total, second_rows = harness.repository.list_findings(second_id, limit=500, offset=0)
    assert [row["finding_id"] for row in second_rows] == first_ids, "确定性重跑结果一致"
    # 旧分析行未被覆盖：两个命名空间各自完整保留。
    after_total, after_rows = harness.repository.list_findings(first_id, limit=500, offset=0)
    assert after_total == first_total == second_total
    assert [row["finding_id"] for row in after_rows] == first_ids


def test_worker_invalid_ref_fails_with_reason(harness: Harness, tmp_path: Path):
    repo = make_net_repo(tmp_path)
    analysis_id = register_and_create(
        harness.repository, repo, base_ref="no-such-ref"
    )
    status = direct_worker(harness).run(analysis_id)
    assert status == "failed"
    row = harness.repository.get_analysis(analysis_id)
    assert row["failure_reason"].startswith("REF_NOT_FOUND:")
    stages = {row2["stage"]: row2["status"] for row2 in harness.repository.stage_records(analysis_id)}
    assert stages["git"] == "failed"
    assert stages["report"] == "queued"


def test_runner_recovers_after_restart(harness: Harness, tmp_path: Path):
    repo = make_net_repo(tmp_path)
    analysis_id = register_and_create(harness.repository, repo)
    # 模拟崩溃：状态 running，但没有任何提交产物。
    harness.repository.set_status(analysis_id, "running")
    resumed = AnalysisRunner(
        direct_worker(harness), concurrency=1
    )
    try:
        assert resumed.recover() == [analysis_id]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if harness.repository.analysis_status(analysis_id) in TERMINAL:
                break
            time.sleep(0.05)
        assert harness.repository.analysis_status(analysis_id) in ("completed", "completed_with_limits")
    finally:
        resumed.shutdown(wait=True)


def test_runner_cancel_pending_queued_task(harness: Harness, tmp_path: Path):
    release = threading.Event()

    class _BlockingWorker:
        """占用单槽线程池，让第二个任务保持排队态。"""

        def __init__(self) -> None:
            self.repository = harness.repository

        def run(self, analysis_id: str) -> str:
            release.wait(timeout=10)
            return "completed"

    runner = AnalysisRunner(_BlockingWorker(), concurrency=1)  # type: ignore[arg-type]
    try:
        runner.submit("analysis:blocked")
        assert runner.submit("analysis:queued"), "第二个任务应成功入队"
        assert runner.cancel_pending("analysis:queued"), "排队任务应可被直接取消"
    finally:
        release.set()
        runner.shutdown(wait=True)


# ---------------------------------------------------------------------------
# API-001：创建 / 状态 / 取消 / 幂等
# ---------------------------------------------------------------------------


def _create_payload(repo: Path) -> dict:
    return {
        "repository_id": repository_id_for(str(repo.resolve())),
        "base_ref": "HEAD~1",
        "target_ref": "HEAD",
    }


def test_api_create_poll_and_read_results(harness: Harness, tmp_path: Path):
    repo = setup_api_repo(harness, tmp_path)
    payload = _create_payload(repo)
    created = harness.client.post("/v1/analyses", json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] in ("queued", "running")
    assert body["stages"] == []

    final = wait_for_terminal(harness.client, body["analysis_id"])
    assert final["status"] in ("completed", "completed_with_limits")
    assert final["snapshot_id"] and len(final["snapshot_id"]) == 64
    assert final["counts"]["findings"] > 0

    findings = harness.client.get(f"/v1/analyses/{body['analysis_id']}/findings")
    assert findings.status_code == 200
    items = findings.json()["items"]
    assert any(item["finding"]["rule_id"] == "BEH-NETWORK" for item in items)

    finding = items[0]["finding"]
    if finding["evidence_ids"]:
        anchor = harness.client.get(
            f"/v1/analyses/{body['analysis_id']}/evidence/{finding['evidence_ids'][0]}"
        )
        assert anchor.status_code == 200
        assert anchor.json()["path"]


def test_api_idempotent_replay_same_request(harness: Harness, tmp_path: Path):
    repo = setup_api_repo(harness, tmp_path)
    payload = _create_payload(repo)
    first = harness.client.post("/v1/analyses", json=payload)
    assert first.status_code == 201
    second = harness.client.post("/v1/analyses", json=payload)
    assert second.status_code == 200
    assert second.headers.get("Idempotency-Replayed") == "true"
    assert second.json()["analysis_id"] == first.json()["analysis_id"]
    # 不产生重复任务：总分析数仍为 1。
    with database.reader(harness.db_path) as connection:
        count = connection.execute("SELECT COUNT(*) AS n FROM analyses").fetchone()
    assert count["n"] == 1


def test_api_idempotency_key_conflict_on_different_request(harness: Harness, tmp_path: Path):
    repo = setup_api_repo(harness, tmp_path)
    payload = _create_payload(repo)
    first = harness.client.post(
        "/v1/analyses", json=payload, headers={"Idempotency-Key": "shared-key"}
    )
    assert first.status_code == 201
    payload["target_ref"] = "HEAD~1"  # 同键不同请求
    conflict = harness.client.post(
        "/v1/analyses", json=payload, headers={"Idempotency-Key": "shared-key"}
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "ANALYSIS_CONFLICT"


def test_api_rejects_unknown_repository_and_fields(harness: Harness, tmp_path: Path):
    repo = make_net_repo(tmp_path)
    unknown = _create_payload(repo)
    unknown["repository_id"] = "f" * 64
    response = harness.client.post("/v1/analyses", json=unknown)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REPOSITORY_NOT_REGISTERED"

    bad_field = _create_payload(repo)
    bad_field["shell_command"] = "rm -rf /"  # API 不接受任意命令字段
    response = harness.client.post("/v1/analyses", json=bad_field)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_INPUT"

    response = harness.client.post(
        "/v1/analyses",
        json={"repository_id": "not-hex", "base_ref": "a", "target_ref": "b"},
    )
    assert response.status_code == 400


def test_api_status_404_and_error_envelope(harness: Harness):
    response = harness.client.get("/v1/analyses/analysis:missing")
    assert response.status_code == 404
    envelope = response.json()
    assert envelope["schema_version"] == "1.4.0"
    assert envelope["error"]["code"] == "ANALYSIS_NOT_FOUND"
    assert envelope["error"]["retryable"] is False


def test_api_cancel_flow(harness: Harness, tmp_path: Path):
    repo = setup_api_repo(harness, tmp_path)
    payload = _create_payload(repo)
    created = harness.client.post("/v1/analyses", json=payload).json()
    analysis_id = created["analysis_id"]
    wait_for_terminal(harness.client, analysis_id)

    cancelled = harness.client.post(f"/v1/analyses/{analysis_id}/cancel")
    assert cancelled.status_code == 409
    assert cancelled.json()["error"]["code"] == "ANALYSIS_CONFLICT"

    # 未提交执行的分析：取消标记 → worker 首个检查点即终止。
    repo2 = make_net_repo(tmp_path / "second-case", name="scenario")
    analysis2 = register_and_create(harness.repository, repo2, key="cancel-flag")
    harness.repository.request_cancel(analysis2)
    assert direct_worker(harness).run(analysis2) == "cancelled"
    missing = harness.client.get(f"/v1/analyses/{analysis2}/findings")
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "ANALYSIS_NOT_READY"


# ---------------------------------------------------------------------------
# API-002：结果查询 / 分页 / 过滤 / 复核
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def completed_analysis(fixture_repo: Path, fixture_roots, tmp_path_factory):
    """真实 fixture 的完整 API 闭环（daemon 启动即自动登记 /fixtures 仓库）。"""

    data_dir = tmp_path_factory.mktemp("fixture-data")
    settings = DaemonSettings(
        db_path=data_dir / "mj.sqlite",
        data_dir=data_dir,
        repository_roots=tuple(fixture_roots),
        manifest_dir=Path("/fixtures/manifests"),
        worker_concurrency=1,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        repo = fixture_repo
        payload = {
            "repository_id": repository_id_for(str(repo.resolve())),
            "base_ref": "HEAD~1",
            "target_ref": "HEAD",
        }
        created = client.post("/v1/analyses", json=payload)
        assert created.status_code == 201, created.text
        final = wait_for_terminal(client, created.json()["analysis_id"], timeout=120)
        yield client, final


def test_real_fixture_end_to_end(completed_analysis):
    client, final = completed_analysis
    assert final["status"] == "completed_with_limits", "真实 fixture 有覆盖限制（无锁文件等）"
    assert final["counts"]["findings"] > 0
    assert final["counts"]["evidence"] > 0
    analysis_id = final["analysis_id"]

    findings = client.get(f"/v1/analyses/{analysis_id}/findings", params={"limit": 5})
    assert findings.status_code == 200
    page = findings.json()
    assert page["limit"] == 5 and len(page["items"]) <= 5
    assert page["total"] == final["counts"]["findings"]

    network = client.get(
        f"/v1/analyses/{analysis_id}/findings",
        params={"rule_id": "BEH-NETWORK"},
    ).json()
    assert network["total"] > 0, "真实 fixture 必须有网络行为发现"
    resolved = [
        item for item in network["items"] if item["finding"]["evidence_ids"]
    ]
    assert resolved, "网络发现至少一条带证据"

    detail = client.get(
        f"/v1/analyses/{analysis_id}/findings/{resolved[0]['finding']['id']}"
    )
    assert detail.status_code == 200
    evidence = detail.json()["evidence"]
    assert evidence and "fetch" in evidence[0]["snippet"].lower()

    coverage = client.get(f"/v1/analyses/{analysis_id}/coverage")
    assert coverage.status_code == 200
    assert coverage.json()["summary"]["selected"] > 0

    target_map = client.get(
        f"/v1/analyses/{analysis_id}/map", params={"side": "target"}
    )
    assert target_map.status_code == 200
    assert target_map.json()["map"]["nodes"]


def test_api_pagination_filter_and_validation(completed_analysis):
    client, final = completed_analysis
    analysis_id = final["analysis_id"]

    first = client.get(
        f"/v1/analyses/{analysis_id}/findings", params={"limit": 2, "offset": 0}
    ).json()
    second = client.get(
        f"/v1/analyses/{analysis_id}/findings", params={"limit": 2, "offset": 2}
    ).json()
    assert first["total"] == second["total"]
    if len(first["items"]) == 2 and len(second["items"]) > 0:
        assert first["items"][0]["finding"]["id"] != second["items"][0]["finding"]["id"]

    bad_category = client.get(
        f"/v1/analyses/{analysis_id}/findings", params={"category": "not-a-category"}
    )
    assert bad_category.status_code == 400

    bad_status = client.get(
        f"/v1/analyses/{analysis_id}/coverage", params={"status": "nope"}
    )
    assert bad_status.status_code == 400

    bad_side = client.get(
        f"/v1/analyses/{analysis_id}/map", params={"side": "middle"}
    )
    assert bad_side.status_code == 400


def test_api_reviews_roundtrip(completed_analysis):
    client, final = completed_analysis
    analysis_id = final["analysis_id"]
    finding_id = (
        client.get(f"/v1/analyses/{analysis_id}/findings", params={"limit": 1})
        .json()["items"][0]["finding"]["id"]
    )

    review = client.post(
        f"/v1/analyses/{analysis_id}/reviews",
        json={"finding_id": finding_id, "state": "needs-investigation", "note": "本地备注"},
    )
    assert review.status_code == 201, review.text
    assert review.json()["state"] == "needs-investigation"

    listed = client.get(f"/v1/analyses/{analysis_id}/reviews").json()
    assert listed["total"] == 1
    assert listed["items"][0]["finding_id"] == finding_id

    detail = client.get(f"/v1/analyses/{analysis_id}/findings/{finding_id}")
    assert detail.json()["review_state"] == "needs-investigation"

    # 更新复核状态（upsert），不是追加。
    updated = client.post(
        f"/v1/analyses/{analysis_id}/reviews",
        json={"finding_id": finding_id, "state": "confirmed", "note": ""},
    )
    assert updated.status_code == 201
    assert client.get(f"/v1/analyses/{analysis_id}/reviews").json()["total"] == 1

    unknown = client.post(
        f"/v1/analyses/{analysis_id}/reviews",
        json={"finding_id": "finding:does-not-exist", "state": "confirmed"},
    )
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "FINDING_NOT_FOUND"


def test_api_evidence_404(completed_analysis):
    client, final = completed_analysis
    response = client.get(
        f"/v1/analyses/{final['analysis_id']}/evidence/evidence:missing"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EVIDENCE_NOT_FOUND"
