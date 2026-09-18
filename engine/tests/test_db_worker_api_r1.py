"""Batch-04-R1 修复矩阵：对应审计 B04-R1-01～07 的公开回归测试。

不复制探针样例值；每项测试针对同类根因。覆盖组：DB 原子性/引用完整性、
任务调度互斥、生命周期与取消线性化、checkpoint 恢复、API 精确路由与
Schema 消费、SQLite↔HTTP 事实一致、隐私边界、映射配置语义。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_db_worker_api import (
    NET_SOURCE,
    Harness,
    _create_payload,
    harness,
    make_net_repo,
    register_and_create,
    setup_api_repo,
    wait_for_terminal,
)

from conftest import build_scenario_repo

from morphojudge.api.schemas import availability_for  # noqa: F401 — 契约存在性
from morphojudge.contracts import export
from morphojudge.db import database
from morphojudge.db.database import Migration, current_version, migrate
from morphojudge.db.repository import AnalysisRepository
from morphojudge.git.snapshot import repository_id_for
from morphojudge.main import create_app
from morphojudge.settings import DaemonSettings
from morphojudge.worker.analysis import (
    AnalysisRunner,
    AnalysisWorker,
    ManifestStatus,
    load_manifest,
    sanitize_reason,
)

TERMINAL = ("completed", "completed_with_limits", "failed", "cancelled")


def direct_worker(harness: Harness, on_stage_start=None, manifest_dir=None) -> AnalysisWorker:
    return AnalysisWorker(
        harness.repository,
        allowed_roots=(harness.root,),
        manifest_dir=manifest_dir if manifest_dir is not None else None,
        on_stage_start=on_stage_start,
    )


# ---------------------------------------------------------------------------
# DB（B04-R1-01）
# ---------------------------------------------------------------------------


def test_migration_partial_failure_rolls_back_with_existing_data(tmp_path: Path):
    """成功 DDL + 成功 DML 之后故障：旧 schema、旧数据与版本一并保留。"""

    db = tmp_path / "data" / "mj.sqlite"
    db.parent.mkdir(parents=True)
    assert migrate(db) == 2
    with database.writer(db) as connection:
        connection.execute(
            "INSERT INTO repositories (repository_id, name, canonical_path, registered_at)"
            " VALUES ('a' * 64, 'r', '/repo', '2026-01-01T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO analyses (analysis_id, idempotency_key, request_hash,"
            " repository_id, base_ref, target_ref, rules_version, status,"
            " created_at, updated_at)"
            " VALUES ('analysis:existing', 'k', 'h', 'a' * 64, 'b', 't', 'v',"
            " 'completed', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )

    def partially_broken(connection_) -> None:
        connection_.execute("CREATE TABLE audit_partial(id INTEGER PRIMARY KEY)")
        connection_.execute("INSERT INTO audit_partial(id) VALUES (1)")
        connection_.execute("CREATE TABLE broken_after_valid_ddl(")

    with pytest.raises(RuntimeError, match="0003"):
        migrate(db, migrations=(Migration(3, "0003_partial", partially_broken),))
    assert current_version(db) == 2
    with database.reader(db) as connection:
        leaked = connection.execute(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE name = ?",
            ("audit_partial",),
        ).fetchone()["n"]
        analyses = connection.execute("SELECT COUNT(*) AS n FROM analyses").fetchone()["n"]
    assert leaked == 0, "成功执行过的 DDL/DML 也不得在失败后残留"
    assert analyses == 1, "已有数据不受失败迁移影响"

    def fixed(connection_) -> None:
        connection_.execute("CREATE TABLE audit_ok(id INTEGER PRIMARY KEY)")

    assert migrate(db, migrations=(Migration(3, "0003_fixed", fixed),)) == 3
    with database.reader(db) as connection:
        kept = connection.execute("SELECT COUNT(*) AS n FROM analyses").fetchone()["n"]
    assert kept == 1, "重试迁移保留旧数据"


def test_migration_rejects_newer_schema(tmp_path: Path):
    db = tmp_path / "data" / "mj.sqlite"
    db.parent.mkdir(parents=True)

    def future(connection_) -> None:
        connection_.execute("CREATE TABLE future_only(id INTEGER PRIMARY KEY)")

    migrate(db, migrations=(Migration(1, "0001", lambda c: None), Migration(2, "0002", future)))
    with pytest.raises(RuntimeError, match="newer than supported"):
        migrate(db, migrations=(Migration(1, "0001", lambda c: None),))  # 只支持到 v1
    with database.writer(db) as connection:
        connection.execute(
            "INSERT INTO future_only(id) VALUES (1)"
        ) if False else None
    assert current_version(db) == 2


def test_review_integrity_rejected_at_db_boundary(harness: Harness, tmp_path: Path):
    repo = make_net_repo(tmp_path)
    analysis_id = register_and_create(harness.repository, repo)
    direct_worker(harness).run(analysis_id)
    total, rows = harness.repository.list_findings(analysis_id, limit=5, offset=0)
    assert total > 0
    real_finding = rows[0]["finding_id"]

    # 悬空 finding 的 review 被复合外键拒绝
    with pytest.raises(sqlite3.IntegrityError):
        with database.writer(harness.db_path) as connection:
            connection.execute(
                "INSERT INTO reviews (analysis_id, finding_id, state, note, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (analysis_id, "finding:nonexistent", "confirmed", "", "2026-01-01T00:00:00Z"),
            )
    # 跨分析引用：不同 snapshot 的 finding id 不存在于本分析命名空间
    other_source = NET_SOURCE.replace("api.example.invalid", "other.example.invalid")
    other_repo = build_scenario_repo(
        tmp_path / "cross", {"src/net.ts": other_source}, {"src/other.ts": "x\n"}
    )
    other_canonical = str(other_repo.resolve())
    harness.repository.register_repository(
        repository_id_for(other_canonical), other_repo.name, other_canonical
    )
    other_id = "analysis:cross-snapshot"
    harness.repository.create_analysis(
        analysis_id=other_id,
        idempotency_key="cross",
        request_hash="cross",
        repository_id=repository_id_for(other_canonical),
        base_ref="HEAD~1",
        target_ref="HEAD",
        rules_version="morphojudge-v0.1",
    )
    direct_worker(harness).run(other_id)
    with pytest.raises(sqlite3.IntegrityError):
        with database.writer(harness.db_path) as connection:
            connection.execute(
                "INSERT INTO reviews (analysis_id, finding_id, state, note, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (other_id, real_finding, "confirmed", "", "2026-01-01T00:00:00Z"),
            )
    listed = harness.repository.list_reviews(analysis_id)
    assert listed == [], "被拒绝的写入不得部分提交"


def test_report_commit_rejects_dangling_evidence(harness: Harness, tmp_path: Path):
    repo = make_net_repo(tmp_path)
    analysis_id = register_and_create(harness.repository, repo)

    def crash_at_report(_: str, stage: str) -> None:
        if stage == "report":
            raise SystemExit("stop before report")

    with pytest.raises(SystemExit):
        direct_worker(harness, on_stage_start=crash_at_report).run(analysis_id)
    document = harness.repository.rules_document(analysis_id)
    assert document is not None
    tampered = json.loads(json.dumps(document))
    tampered["findings"][0]["evidence_ids"] = ["evidence:missing-anchor"]
    with pytest.raises(ValueError, match="missing"):
        harness.repository.commit_report_checkpoint(
            analysis_id=analysis_id,
            snapshot_id=tampered["snapshot"]["identity"]["snapshot_id"],
            document=tampered,
            final_status="completed_with_limits",
        )
    # 事务回滚：findings/evidence/analysis_results 均未写入
    assert harness.repository.result_document(analysis_id) is None
    total, _ = harness.repository.list_findings(analysis_id, limit=10, offset=0)
    assert total == 0
    assert harness.repository.evidence_count(analysis_id) == 0


# ---------------------------------------------------------------------------
# 调度（B04-R1-02）
# ---------------------------------------------------------------------------


def test_concurrent_submit_same_id_executes_once(harness: Harness):
    """提交原子化验证：即使线程池提交本身很慢（注册临界区被拉长），
    同一 analysis 的并发提交也只有一个成功、worker 只执行一次。"""

    release = threading.Event()
    calls: list[str] = []
    lock = threading.Lock()

    class BlockingWorker:
        repository = harness.repository

        def run(self, analysis_id: str) -> str:
            with lock:
                calls.append(analysis_id)
            release.wait(5)
            return "completed"

    runner = AnalysisRunner(BlockingWorker(), concurrency=2)  # type: ignore[arg-type]
    real_pool = runner._pool

    class SlowSubmitPool:
        """真实布局中 check+submit+register 处于同一把锁；用慢提交放大
        临界区，验证第二个提交者在锁外等待后必然看到已注册任务。"""

        def submit(self, fn):
            time.sleep(0.15)
            return real_pool.submit(fn)

        def shutdown(self, **kwargs):
            real_pool.shutdown(**kwargs)

    runner._pool = SlowSubmitPool()
    try:
        with ThreadPoolExecutor(max_workers=2) as submitters:
            futures = [submitters.submit(runner.submit, "same-analysis") for _ in range(2)]
            accepted = [f.result(timeout=8) for f in futures]
        release.set()
    finally:
        release.set()
        runner.shutdown(wait=True)
    assert sorted(accepted) == [False, True], "同一 analysis 的并发提交只允许一个成功"
    assert len(calls) == 1, "worker 只能执行一次"


def test_second_runner_and_late_failure_cannot_disturb_terminal(
    harness: Harness, tmp_path: Path
):
    repo = make_net_repo(tmp_path)
    analysis_id = register_and_create(harness.repository, repo)
    runner_one = AnalysisRunner(direct_worker(harness), concurrency=1)
    try:
        assert runner_one.submit(analysis_id)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if harness.repository.analysis_status(analysis_id) in TERMINAL:
                break
            time.sleep(0.05)
    finally:
        runner_one.shutdown(wait=True)
    terminal = harness.repository.analysis_status(analysis_id)
    assert terminal in ("completed", "completed_with_limits")

    # 迟到的 fail_analysis 不能改写终态
    assert harness.repository.fail_analysis(analysis_id, "late_duplicate_failure") is False
    assert harness.repository.analysis_status(analysis_id) == terminal
    # 第二个 Runner 重提交：worker 发现终态直接返回，不重复执行
    runner_two = AnalysisRunner(direct_worker(harness), concurrency=1)
    try:
        assert runner_two.submit(analysis_id)
        time.sleep(0.3)
    finally:
        runner_two.shutdown(wait=True)
    assert harness.repository.analysis_status(analysis_id) == terminal
    total, _ = harness.repository.list_findings(analysis_id, limit=500, offset=0)
    assert total == len(harness.repository.result_document(analysis_id)["findings"]), "结果不重复写"


def test_cancel_during_report_transaction_linearizes(harness: Harness, tmp_path: Path):
    """取消标记在 report 提交前落库 → 取消获胜，产物保留、可读且标 partial。

    用直接创建（不经 API 后台 runner），保证取消注入点精确落在
    rules 完成之后、report 提交之前。
    """

    repo = setup_api_repo(harness, tmp_path, name="cancel-linearize")
    canonical = str(repo.resolve())
    payload_repository = repository_id_for(canonical)
    analysis_id = "analysis:cancel-linearize"
    harness.repository.create_analysis(
        analysis_id=analysis_id,
        idempotency_key="cancel-linearize",
        request_hash="cancel-linearize",
        repository_id=payload_repository,
        base_ref="HEAD~1",
        target_ref="HEAD",
        rules_version="morphojudge-v0.1",
    )

    # 取消在 report 事务执行期间到达（包裹 commit：先落取消标记再提交）
    original_commit = harness.repository.commit_report_checkpoint

    def cancel_then_commit(**kwargs):
        harness.repository.request_cancel(analysis_id)
        return original_commit(**kwargs)

    harness.repository.commit_report_checkpoint = cancel_then_commit  # type: ignore[method-assign]
    try:
        status = direct_worker(harness).run(analysis_id)
    finally:
        harness.repository.commit_report_checkpoint = original_commit  # type: ignore[method-assign]
    assert status == "cancelled", "取消标记先于 report 提交落库则取消获胜"
    row = harness.repository.get_analysis(analysis_id)
    assert bool(row["cancel_requested"])
    # report 产物保留并可读，可用性标 partial
    assert harness.repository.result_document(analysis_id) is not None
    findings = harness.client.get(f"/v1/analyses/{analysis_id}/findings")
    assert findings.status_code == 200
    body = findings.json()
    assert body["availability"]["artifacts"] == "partial"
    assert body["availability"]["analysis_status"] == "cancelled"
    # 提交后再取消 → 明确不可取消
    again = harness.client.post(f"/v1/analyses/{analysis_id}/cancel")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "ANALYSIS_CONFLICT"


# ---------------------------------------------------------------------------
# 生命周期 / 恢复（B04-R1-03）
# ---------------------------------------------------------------------------


def test_report_write_failure_closes_stage_and_keeps_partial_queries(
    harness: Harness, tmp_path: Path
):
    repo = setup_api_repo(harness, tmp_path, name="report-failure")
    payload = _create_payload(repo)
    analysis_id = harness.client.post("/v1/analyses", json=payload).json()["analysis_id"]
    wait_for_terminal(harness.client, analysis_id)

    # 直接构造第二个分析并让 report 写入抛错（写入层故障，非进程崩溃）
    failing_repo = make_net_repo(tmp_path / "failing", name="scenario")
    failing_id = register_and_create(harness.repository, failing_repo, key="report-write-fail")

    original = harness.repository.commit_report_checkpoint

    def failing_commit(**kwargs):
        raise sqlite3.OperationalError("injected report write failure")

    harness.repository.commit_report_checkpoint = failing_commit  # type: ignore[method-assign]
    try:
        status = direct_worker(harness).run(failing_id)
    finally:
        harness.repository.commit_report_checkpoint = original  # type: ignore[method-assign]
    assert status == "failed"
    stages = {row["stage"]: row["status"] for row in harness.repository.stage_records(failing_id)}
    assert stages["report"] == "failed", "失败必须关闭 running 阶段，不得悬挂"
    assert stages["behavior"] == "completed" and stages["parse"] == "completed"
    row = harness.repository.get_analysis(failing_id)
    assert row["failure_reason"].startswith("report_write_failed:")

    # 部分结果可观测：coverage/summary/impact-paths 200（partial），findings 409
    coverage = harness.client.get(f"/v1/analyses/{failing_id}/coverage")
    assert coverage.status_code == 200
    assert coverage.json()["availability"]["artifacts"] == "partial"
    summary = harness.client.get(f"/v1/analyses/{failing_id}/summary")
    assert summary.status_code == 200
    assert summary.json()["limits"], "失败分析的已提交限制仍可见"
    paths = harness.client.get(f"/v1/analyses/{failing_id}/impact-paths")
    assert paths.status_code == 200
    findings = harness.client.get(f"/v1/analyses/{failing_id}/findings")
    assert findings.status_code == 409
    assert findings.json()["error"]["code"] == "ANALYSIS_NOT_READY"


def test_crash_after_parse_resumes_without_reparse(harness: Harness, tmp_path: Path):
    repo = make_net_repo(tmp_path, name="crash-parse")
    analysis_id = register_and_create(harness.repository, repo)

    def crash_at_rules(_: str, stage: str) -> None:
        if stage == "rules":
            raise SystemExit("daemon died after parse checkpoint")

    with pytest.raises(SystemExit):
        direct_worker(harness, on_stage_start=crash_at_rules).run(analysis_id)

    parse_before = json.dumps(
        harness.repository.load_stage_output(analysis_id, "parse"), sort_keys=True
    )
    decision_total, decisions_before = harness.repository.list_decisions(
        analysis_id, limit=500, offset=0
    )
    assert decision_total > 0
    assert harness.repository.load_stage_output(analysis_id, "rules") is None

    status = direct_worker(harness).run(analysis_id)
    assert status in ("completed", "completed_with_limits")
    parse_after = json.dumps(
        harness.repository.load_stage_output(analysis_id, "parse"), sort_keys=True
    )
    assert parse_after == parse_before, "恢复不得重写 parse checkpoint"
    decision_total_after, _ = harness.repository.list_decisions(
        analysis_id, limit=500, offset=0
    )
    assert decision_total_after == decision_total

    # 与一次性全量运行结果一致（确定性）
    fresh_id = register_and_create(harness.repository, repo, key="fresh-run")
    direct_worker(harness).run(fresh_id)
    a = [row["finding_id"] for row in harness.repository.list_findings(analysis_id, limit=500, offset=0)[1]]
    b = [row["finding_id"] for row in harness.repository.list_findings(fresh_id, limit=500, offset=0)[1]]
    assert a == b


# ---------------------------------------------------------------------------
# API 精确路由 / Schema 消费（B04-R1-04 / B04-R1-05）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fixture_analysis(fixture_repo, fixture_roots, tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("r1-fixture-data")
    settings = DaemonSettings(
        db_path=data_dir / "mj.sqlite",
        data_dir=data_dir,
        repository_roots=tuple(fixture_roots),
        manifest_dir=Path("/fixtures/manifests"),
        worker_concurrency=1,
    )
    with TestClient(create_app(settings)) as client:
        payload = {
            "repository_id": repository_id_for(str(fixture_repo.resolve())),
            "base_ref": "HEAD~1",
            "target_ref": "HEAD",
        }
        created = client.post("/v1/analyses", json=payload)
        assert created.status_code == 201, created.text
        final = wait_for_terminal(client, created.json()["analysis_id"], timeout=120)
        yield client, final


def test_specified_routes_exist_with_schema_version(fixture_analysis):
    client, final = fixture_analysis
    analysis_id = final["analysis_id"]

    state = client.get(f"/v1/analyses/{analysis_id}")
    assert state.status_code == 200
    assert state.json()["schema_version"] == "1.3.0"

    canonical = client.get(f"/v1/analyses/{analysis_id}/software-map")
    alias = client.get(f"/v1/analyses/{analysis_id}/map")
    assert canonical.status_code == 200 and alias.status_code == 200
    assert canonical.json()["map"] == alias.json()["map"]

    paths = client.get(f"/v1/analyses/{analysis_id}/impact-paths")
    assert paths.status_code == 200
    assert paths.json()["total"] > 0
    assert paths.json()["items"][0]["resolution"] in ("resolved", "candidate", "unresolved")

    summary = client.get(f"/v1/analyses/{analysis_id}/summary")
    assert summary.status_code == 200
    body = summary.json()
    assert body["diff_files"], "summary 保留 Diff 来源"
    assert body["manifest"]["status"] == "loaded"
    assert len(body["manifest"]["digest"]) == 64

    # 未知路由统一错误 envelope
    missing = client.get("/v1/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "ROUTE_NOT_FOUND"


def test_coverage_exposes_stage_limits_and_manifest(fixture_analysis):
    client, final = fixture_analysis
    coverage = client.get(f"/v1/analyses/{final['analysis_id']}/coverage")
    assert coverage.status_code == 200
    body = coverage.json()
    assert body["stage_coverage"], "coverage 必须包含全阶段覆盖"
    stages = {item["stage"]: item for item in body["stage_coverage"]}
    assert {"selection", "parse", "behavior_rules", "dependency"} <= set(stages)
    assert "no_lockfile:not_checked" in body["limits"], "未检查范围必须显式可见"
    assert body["manifest"]["status"] == "loaded"


def test_http_payloads_validate_against_published_schema(fixture_analysis):
    """实际 HTTP 响应按发布 Schema 验证（不是仅有导出）。"""

    client, final = fixture_analysis
    analysis_id = final["analysis_id"]
    schema = json.loads(
        Path("/packages/contracts/schema.json").read_text(encoding="utf-8")
    )
    definitions = schema["definitions"]

    from jsonschema import Draft202012Validator

    checks = {
        f"/v1/analyses/{analysis_id}": "AnalysisResponse",
        f"/v1/analyses/{analysis_id}/findings?limit=3": "FindingsPage",
        f"/v1/analyses/{analysis_id}/coverage": "CoverageResponse",
        f"/v1/analyses/{analysis_id}/software-map": "MapResponse",
        f"/v1/analyses/{analysis_id}/impact-paths": "ImpactPathsPage",
        f"/v1/analyses/{analysis_id}/summary": "SummaryResponse",
        "/v1/repositories": "RepositoriesPage",
    }
    for url, model in checks.items():
        response = client.get(url)
        assert response.status_code == 200, url
        validator = Draft202012Validator({"$ref": f"#/definitions/{model}"})
        resolver_definitions = {"definitions": definitions}
        validator = Draft202012Validator(
            {**resolver_definitions, "$ref": f"#/definitions/{model}"},
        )
        errors = list(validator.iter_errors(response.json()))
        assert not errors, f"{url} failed schema {model}: {errors[0].message}"


def test_options_contract_affects_identity_and_is_validated(harness: Harness, tmp_path: Path):
    repo = setup_api_repo(harness, tmp_path, name="options")
    payload = _create_payload(repo)
    default_created = harness.client.post("/v1/analyses", json=payload)
    assert default_created.status_code == 201

    with_options = dict(payload)
    with_options["options"] = {"impact_max_depth": 2, "impact_max_nodes": 50}
    other = harness.client.post("/v1/analyses", json=with_options)
    assert other.status_code == 201
    assert other.json()["analysis_id"] != default_created.json()["analysis_id"], \
        "options 参与幂等身份：不同选项是不同分析"
    final = wait_for_terminal(harness.client, other.json()["analysis_id"])
    assert final["status"] in ("completed", "completed_with_limits")
    row = harness.repository.get_analysis(other.json()["analysis_id"])
    assert json.loads(row["options_json"]) == with_options["options"]

    invalid = dict(payload)
    invalid["options"] = {"impact_max_depth": 99999}
    rejected = harness.client.post("/v1/analyses", json=invalid)
    assert rejected.status_code == 400


def test_sqlite_http_consistency_no_candidate_upgrade(fixture_analysis):
    client, final = fixture_analysis
    analysis_id = final["analysis_id"]
    document = client.app.state.repository.rules_document(analysis_id)

    page = client.get(f"/v1/analyses/{analysis_id}/findings", params={"limit": 500}).json()
    http_ids = [item["finding"]["id"] for item in page["items"]]
    db_ids = [
        row["finding_id"]
        for row in client.app.state.repository.list_findings(
            analysis_id, limit=500, offset=0
        )[1]
    ]
    assert http_ids == db_ids
    assert page["total"] == len(document["findings"])

    paths = client.get(f"/v1/analyses/{analysis_id}/impact-paths").json()
    doc_resolutions = [item["resolution"] for item in document["impact_paths"]]
    http_resolutions = [item["resolution"] for item in paths["items"]]
    assert http_resolutions == doc_resolutions, "HTTP 不得升级/降级候选状态"
    assert all(r in ("resolved", "candidate", "unresolved") for r in http_resolutions)


# ---------------------------------------------------------------------------
# 隐私（B04-R1-06）
# ---------------------------------------------------------------------------


def test_validation_errors_do_not_echo_input_or_paths(harness: Harness, tmp_path: Path):
    repo = setup_api_repo(harness, tmp_path, name="privacy")
    payload = _create_payload(repo)
    # 合成标记（非真实凭据）；拆分构造避免被密钥扫描误报。
    secret = "SYNTHETIC_NOT_A_REAL_" + "TOKEN_xyzzy"
    invalid = dict(payload)
    invalid["api_key"] = secret
    invalid["absolute_path"] = "/etc/shadow"
    response = harness.client.post("/v1/analyses", json=invalid)
    assert response.status_code == 400
    assert secret not in response.text
    assert "/etc/shadow" not in response.text
    details = response.json()["error"]["details"]["errors"]
    assert details, "校验错误必须携带可定位的字段位置"
    for item in details:
        assert set(item) == {"loc", "type"}, "不得回显 input/ctx 原文"

    repos = harness.client.get("/v1/repositories")
    assert repos.status_code == 200
    text = repos.text
    assert "canonical_path" not in text
    assert str(tmp_path) not in text, "仓库列表不得暴露内部绝对路径"


def test_failure_reason_is_sanitized(harness: Harness, tmp_path: Path):
    repo = make_net_repo(tmp_path, name="sanitize")
    analysis_id = register_and_create(
        harness.repository, repo, base_ref="/absolute/path/ref"
    )
    status = direct_worker(harness).run(analysis_id)
    assert status == "failed"
    row = harness.repository.get_analysis(analysis_id)
    assert "/absolute/path" not in (row["failure_reason"] or "")
    assert "<path>" in row["failure_reason"]

    assert sanitize_reason("boom at C:\\Users\\x\\secret.txt end") == "boom at <path> end"
    assert sanitize_reason("x" * 600).startswith("x" * 500)


# ---------------------------------------------------------------------------
# 映射配置（B04-R1-07）
# ---------------------------------------------------------------------------


def test_manifest_states_are_distinguished(tmp_path: Path):
    repo = make_net_repo(tmp_path / "manifests", name="scenario")
    manifests = tmp_path / "manifests" / "configs"
    manifests.mkdir(parents=True, exist_ok=True)

    # 缺失
    outcome = load_manifest(manifests, repo)
    assert outcome.status == ManifestStatus.ABSENT
    # 损坏 JSON
    (manifests / "scenario-manifest.json").write_text("{ invalid", encoding="utf-8")
    outcome = load_manifest(manifests, repo)
    assert outcome.status == ManifestStatus.PARSE_FAILED and outcome.digest
    # 结构不合法
    (manifests / "scenario-manifest.json").write_text(
        json.dumps({"human_feature_mapping": 5}), encoding="utf-8"
    )
    assert load_manifest(manifests, repo).status == ManifestStatus.SCHEMA_INVALID
    # 非法 Unicode 字节
    (manifests / "scenario-manifest.json").write_bytes(b'{"x": "\xff\xfe"}')
    assert load_manifest(manifests, repo).status == ManifestStatus.PARSE_FAILED
    # 正常加载
    valid = {"human_feature_mapping": [], "required_entities": {"permission_modules": ["auth"]}}
    (manifests / "scenario-manifest.json").write_text(json.dumps(valid), encoding="utf-8")
    outcome = load_manifest(manifests, repo)
    assert outcome.status == ManifestStatus.LOADED
    assert outcome.digest == hashlib.sha256(json.dumps(valid).encode()).hexdigest()


def test_invalid_manifest_is_visible_limit_not_silent_empty(harness: Harness, tmp_path: Path):
    repo = make_net_repo(tmp_path / "invalid-manifest", name="scenario")
    manifests = tmp_path / "invalid-manifest" / "cfg"
    manifests.mkdir(parents=True, exist_ok=True)
    (manifests / "scenario-manifest.json").write_text("{ broken", encoding="utf-8")

    analysis_id = register_and_create(harness.repository, repo, key="broken-manifest")
    data_dir = tmp_path / "invalid-manifest" / "data"
    worker = AnalysisWorker(
        harness.repository, allowed_roots=(tmp_path / "invalid-manifest",), manifest_dir=manifests
    )
    status = worker.run(analysis_id)
    assert status in ("completed", "completed_with_limits"), "配置损坏不静默、不崩溃"

    row = harness.repository.get_analysis(analysis_id)
    assert row["manifest_status"] == "parse_failed"
    assert row["manifest_digest"], "损坏也要绑定内容摘要"
    document = harness.repository.rules_document(analysis_id)
    assert document["manifest"]["status"] == "parse_failed"
    assert "manifest:parse_failed" in document["limits"], "配置失败必须进入覆盖限制"


def test_recovery_does_not_swap_manifest_input(harness: Harness, tmp_path: Path):
    base = tmp_path / "swap"
    repo = make_net_repo(base, name="scenario")
    manifests = base / "cfg"
    manifests.mkdir(parents=True, exist_ok=True)
    manifest_path = manifests / "scenario-manifest.json"
    manifest_path.write_text(
        json.dumps({"human_feature_mapping": []}), encoding="utf-8"
    )
    original_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    analysis_id = register_and_create(harness.repository, repo, key="swap-input")

    def crash_at_rules(_: str, stage: str) -> None:
        if stage == "rules":
            raise SystemExit("stop")

    worker = AnalysisWorker(
        harness.repository, allowed_roots=(base,), manifest_dir=manifests
    )
    with pytest.raises(SystemExit):
        AnalysisWorker(
            harness.repository, allowed_roots=(base,), manifest_dir=manifests,
            on_stage_start=crash_at_rules,
        ).run(analysis_id)
    assert harness.repository.get_analysis(analysis_id)["manifest_digest"] == original_digest

    # 恢复前篡改配置：rules 阶段输入必须来自 parse checkpoint，不换输入
    manifest_path.write_text(json.dumps({"required_entities": {"permission_modules": ["evil"]}}))
    status = AnalysisWorker(
        harness.repository, allowed_roots=(base,), manifest_dir=manifests
    ).run(analysis_id)
    assert status in ("completed", "completed_with_limits")
    document = harness.repository.rules_document(analysis_id)
    assert document["manifest"]["digest"] == original_digest, "恢复不得换用新配置"
    parse_state = harness.repository.load_stage_output(analysis_id, "parse")
    assert parse_state["manifest"]["permission_modules"] == []
