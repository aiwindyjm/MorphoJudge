"""Batch-04-R2 回归矩阵：对应审计 B04-R2-01～05。

不复制探针数值，验证同类不变量：
- 迁移：v1 合法 review 保留 / 悬空拒绝且原样保留 / v2 幂等 / 未来版本拒绝；
- 所有权：双 Runner 重叠只执行一次 / 异常释放 / 不同 ID 并行 / 同 ID 不同库；
- 取消：report 先赢 409 无副作用 / 排队取消 CAS / 各阶段中断后取消无悬挂 running；
- 配置：manifest 子项深度校验（错型/缺字段/null/未人工确认/Unicode 合法）；
- 隐私：非法查询枚举不回显原值，合法过滤不回归。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from test_db_worker_api import (
    Harness,
    _create_payload,
    harness,
    make_net_repo,
    register_and_create,
    setup_api_repo,
    wait_for_terminal,
)

from morphojudge.db import database
from morphojudge.db.database import MIGRATIONS, Migration, current_version, migrate
from morphojudge.git.snapshot import repository_id_for
from morphojudge.worker import analysis as worker_module
from morphojudge.worker.analysis import (
    AnalysisRunner,
    AnalysisWorker,
    ManifestStatus,
    load_manifest,
    permission_modules_of,
)

TERMINAL = ("completed", "completed_with_limits", "failed", "cancelled")


def direct_worker(harness: Harness, on_stage_start=None, manifest_dir=None) -> AnalysisWorker:
    return AnalysisWorker(
        harness.repository,
        allowed_roots=(harness.root,),
        manifest_dir=manifest_dir,
        on_stage_start=on_stage_start,
    )


def _stage_map(harness: Harness, analysis_id: str) -> dict[str, str]:
    return {
        row["stage"]: row["status"]
        for row in harness.repository.stage_records(analysis_id)
    }


# ---------------------------------------------------------------------------
# B04-R2-01：迁移无损
# ---------------------------------------------------------------------------


def _seed_v1_rows(db: Path, *, with_finding: bool) -> None:
    with database.writer(db) as connection:
        connection.execute(
            "INSERT INTO repositories (repository_id, name, canonical_path, registered_at)"
            " VALUES ('r1', 'r', '/synthetic/repo', '2026-01-01T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO analyses (analysis_id, idempotency_key, request_hash,"
            " repository_id, base_ref, target_ref, rules_version, status,"
            " created_at, updated_at)"
            " VALUES ('a1', 'k', 'h', 'r1', 'b', 't', 'v', 'completed',"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        if with_finding:
            connection.execute(
                "INSERT INTO findings (analysis_id, finding_id, snapshot_id, category,"
                " kind, impact, reliability, unresolved_reason, evidence_ids_json)"
                " VALUES ('a1', 'finding:real', 's' * 64, 'behavior_network', 'fact',"
                " 'low', 'deterministic', NULL, '[]')"
            )


def test_migration_keeps_valid_v1_reviews(tmp_path: Path):
    db = tmp_path / "v1-valid.sqlite"
    migrate(db, MIGRATIONS[:1])
    _seed_v1_rows(db, with_finding=True)
    with database.writer(db) as connection:
        connection.execute(
            "INSERT INTO reviews (analysis_id, finding_id, state, note, updated_at)"
            " VALUES ('a1', 'finding:real', 'confirmed', '人工备注 keep', '2026-01-02T00:00:00Z')"
        )
    assert migrate(db) == 2
    with database.reader(db) as connection:
        rows = connection.execute("SELECT * FROM reviews").fetchall()
    assert len(rows) == 1
    assert rows[0]["note"] == "人工备注 keep"
    assert rows[0]["state"] == "confirmed"


def test_migration_refuses_dangling_v1_reviews_and_preserves_them(tmp_path: Path):
    db = tmp_path / "v1-dangling.sqlite"
    migrate(db, MIGRATIONS[:1])
    _seed_v1_rows(db, with_finding=True)
    with database.writer(db) as connection:
        connection.execute(
            "INSERT INTO reviews (analysis_id, finding_id, state, note, updated_at)"
            " VALUES ('a1', 'finding:orphan', 'needs-investigation', '不可自动删除的人工记录', '2026-01-02T00:00:00Z')"
        )
    with pytest.raises(RuntimeError, match="refused: 1 review rows"):
        migrate(db)
    assert current_version(db) == 1, "拒绝迁移后版本不得前进"
    with database.reader(db) as connection:
        rows = connection.execute("SELECT * FROM reviews").fetchall()
        columns = [description[1] for description in connection.execute("SELECT * FROM reviews LIMIT 1").description]
        broken = connection.execute(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE name = ?", ("reviews_new",)
        ).fetchone()["n"]
    assert len(rows) == 1 and rows[0]["note"] == "不可自动删除的人工记录"
    assert "manifest_status" not in columns, "旧表结构保持 v1 原样"
    assert broken == 0, "回滚后不得残留迁移中间表"
    with pytest.raises(RuntimeError):
        migrate(db)  # 重试同样拒绝，直到人工处理
    assert current_version(db) == 1


def test_migration_mixed_valid_and_dangling_refused_atomically(tmp_path: Path):
    db = tmp_path / "v1-mixed.sqlite"
    migrate(db, MIGRATIONS[:1])
    _seed_v1_rows(db, with_finding=True)
    with database.writer(db) as connection:
        for finding_id, note in (
            ("finding:real", "valid note"),
            ("finding:ghost", "dangling note"),
        ):
            connection.execute(
                "INSERT INTO reviews (analysis_id, finding_id, state, note, updated_at)"
                " VALUES ('a1', ?, 'confirmed', ?, '2026-01-02T00:00:00Z')",
                (finding_id, note),
            )
    with pytest.raises(RuntimeError, match="refused: 1 review rows"):
        migrate(db)
    with database.reader(db) as connection:
        count = connection.execute("SELECT COUNT(*) AS n FROM reviews").fetchone()["n"]
    assert count == 2, "混合数据整体拒绝：合法行也原样保留"


def test_migration_v2_idempotent_and_future_rejected(tmp_path: Path):
    db = tmp_path / "fresh.sqlite"
    assert migrate(db) == 2
    assert migrate(db) == 2, "已升级 v2 的库重复启动是幂等 no-op"

    def future(connection_) -> None:
        connection_.execute("CREATE TABLE future_only(id INTEGER PRIMARY KEY)")

    newer = tmp_path / "newer.sqlite"
    migrate(newer, (Migration(1, "0001", lambda c: None), Migration(2, "0002", lambda c: None), Migration(3, "0003_future", future)))
    with pytest.raises(RuntimeError, match="newer than supported"):
        migrate(newer)


# ---------------------------------------------------------------------------
# B04-R2-02：执行所有权
# ---------------------------------------------------------------------------


def test_two_live_runners_execute_analysis_once(harness: Harness, tmp_path: Path):
    """两个真实 Runner 重叠提交同一分析：真实解析入口只执行一次。"""

    repo = make_net_repo(tmp_path)
    analysis_id = register_and_create(harness.repository, repo, key="r2-overlap")
    entered: list[int] = []
    barrier = threading.Barrier(2)
    original_collect = worker_module.collect_map_inputs

    def count_collect(*args, **kwargs):
        entered.append(threading.get_ident())
        try:
            barrier.wait(timeout=2)
        except threading.BrokenBarrierError:
            pass  # 非持有者不会进入：屏障超时后继续，正是所有权生效的证据
        return original_collect(*args, **kwargs)

    runners = [
        AnalysisRunner(direct_worker(harness), concurrency=1) for _ in range(2)
    ]
    with patch.object(worker_module, "collect_map_inputs", count_collect):
        accepted = [runner.submit(analysis_id) for runner in runners]
        for runner in runners:
            runner.shutdown(wait=True)  # 在补丁作用域内等待执行完成
    assert sorted(accepted) == [True, True], "每个 Runner 各自接受提交（实例注册表独立）"
    assert len(entered) == 1, "同一数据库同一分析只能有一个真实执行者"
    assert harness.repository.analysis_status(analysis_id) in ("completed", "completed_with_limits")


def test_exception_path_releases_execution_ownership(harness: Harness, tmp_path: Path):
    repo = make_net_repo(tmp_path)
    analysis_id = register_and_create(harness.repository, repo, key="r2-exception-release")

    def crash_in_collect(*args, **kwargs):
        raise SystemExit("owner died mid-parse")

    with patch.object(worker_module, "collect_map_inputs", crash_in_collect):
        with pytest.raises(SystemExit):
            direct_worker(harness).run(analysis_id)
    # 异常退出后所有权必须释放：同进程后续执行不被永久拒绝。
    status = direct_worker(harness).run(analysis_id)
    assert status in ("completed", "completed_with_limits")


def test_different_analyses_run_in_parallel(harness: Harness, tmp_path: Path):
    repo_a = make_net_repo(tmp_path / "a", name="scenario")
    repo_b = make_net_repo(tmp_path / "b", name="scenario")
    id_a = register_and_create(harness.repository, repo_a, key="parallel-a")
    id_b = register_and_create(harness.repository, repo_b, key="parallel-b")
    both = threading.Barrier(2)
    original_collect = worker_module.collect_map_inputs

    def require_both(*args, **kwargs):
        both.wait(timeout=5)  # 两个分析必须同时处于执行中才算并行
        return original_collect(*args, **kwargs)

    results: dict[str, str] = {}

    def run_one(analysis_id: str) -> None:
        results[analysis_id] = direct_worker(harness).run(analysis_id)

    try:
        with patch.object(worker_module, "collect_map_inputs", require_both):
            threads = [
                threading.Thread(target=run_one, args=(analysis_id,))
                for analysis_id in (id_a, id_b)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
    finally:
        pass
    assert results[id_a] in ("completed", "completed_with_limits")
    assert results[id_b] in ("completed", "completed_with_limits")


def test_same_analysis_id_in_different_databases_both_execute(tmp_path: Path):
    from morphojudge.db.repository import AnalysisRepository

    outcomes = {}
    barrier = threading.Barrier(2)
    original_collect = worker_module.collect_map_inputs

    def wait_both(*args, **kwargs):
        barrier.wait(timeout=5)
        return original_collect(*args, **kwargs)

    def run_in(database_index: int) -> None:
        base = tmp_path / f"db{database_index}"
        base.mkdir()
        db = base / "mj.sqlite"
        migrate(db)
        repository = AnalysisRepository(db)
        repo = make_net_repo(base, name="scenario")
        canonical = str(repo.resolve())
        repository.register_repository(repository_id_for(canonical), repo.name, canonical)
        analysis_id = "analysis:same-id-different-db"
        repository.create_analysis(
            analysis_id=analysis_id,
            idempotency_key=f"db{database_index}",
            request_hash=f"db{database_index}",
            repository_id=repository_id_for(canonical),
            base_ref="HEAD~1",
            target_ref="HEAD",
            rules_version="morphojudge-v0.1",
        )
        worker = AnalysisWorker(repository, allowed_roots=(base,))
        outcomes[database_index] = worker.run(analysis_id)

    threads = [threading.Thread(target=run_in, args=(index,)) for index in (0, 1)]
    try:
        with patch.object(worker_module, "collect_map_inputs", wait_both):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
    finally:
        pass
    assert outcomes[0] in ("completed", "completed_with_limits")
    assert outcomes[1] in ("completed", "completed_with_limits"), \
        "同 ID 不同数据库互不串扰：各自执行并完成"


# ---------------------------------------------------------------------------
# B04-R2-02b / B04-R2-03：取消裁决与阶段关闭
# ---------------------------------------------------------------------------


def test_cancel_after_report_wins_returns_conflict_without_side_effects(
    harness: Harness, tmp_path: Path
):
    repo = setup_api_repo(harness, tmp_path, name="cancel-after-report")
    payload = _create_payload(repo)
    created = harness.client.post("/v1/analyses", json=payload)
    analysis_id = created.json()["analysis_id"]
    wait_for_terminal(harness.client, analysis_id)
    before = harness.repository.get_analysis(analysis_id)

    runner = harness.client.app.state.runner
    worker = direct_worker(harness)
    captured: dict[str, str] = {}

    def complete_then_cancel_pending(target_id: str) -> bool:
        captured["id"] = target_id
        worker.run(target_id)  # report 在取消写入之前提交终态
        return False

    with patch.object(runner, "cancel_pending", complete_then_cancel_pending):
        response = harness.client.post(f"/v1/analyses/{analysis_id}/cancel")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ANALYSIS_CONFLICT"
    after = harness.repository.get_analysis(analysis_id)
    assert not bool(after["cancel_requested"]), "取消未获接受不得写标记"
    assert after["updated_at"] == before["updated_at"], "终态行不得被取消路径改写"
    stages = _stage_map(harness, analysis_id)
    assert all(status == "completed" for status in stages.values())


def test_cancel_flag_before_report_still_wins(harness: Harness, tmp_path: Path):
    """既有方向的回归：标记先落库 → 取消生效、产物保留（不因新逻辑丢失）。"""

    repo = setup_api_repo(harness, tmp_path, name="cancel-flag-wins")
    canonical = str(repo.resolve())
    analysis_id = "analysis:r2-flag-wins"
    harness.repository.create_analysis(
        analysis_id=analysis_id,
        idempotency_key="r2-flag-wins",
        request_hash="r2-flag-wins",
        repository_id=repository_id_for(canonical),
        base_ref="HEAD~1",
        target_ref="HEAD",
        rules_version="morphojudge-v0.1",
    )
    original_commit = harness.repository.commit_report_checkpoint

    def cancel_then_commit(**kwargs):
        assert harness.repository.request_cancel(analysis_id) is True
        return original_commit(**kwargs)

    harness.repository.commit_report_checkpoint = cancel_then_commit  # type: ignore[method-assign]
    try:
        status = direct_worker(harness).run(analysis_id)
    finally:
        harness.repository.commit_report_checkpoint = original_commit  # type: ignore[method-assign]
    assert status == "cancelled"
    assert harness.repository.result_document(analysis_id) is not None, "已提交产物保留"


def test_cancel_queued_task_uses_terminal_cas(harness: Harness, tmp_path: Path):
    release = threading.Event()

    class BlockingWorker:
        repository = harness.repository

        def run(self, analysis_id: str) -> str:
            release.wait(5)
            return "completed"

    runner = AnalysisRunner(BlockingWorker(), concurrency=1)  # type: ignore[arg-type]
    try:
        runner.submit("analysis:r2-blocker")
        analysis_id = register_and_create(harness.repository, make_net_repo(tmp_path), key="r2-queued-cancel")
        # 排队中的真实分析（第二个任务）被直接取消：
        assert runner.submit(analysis_id)
        assert runner.cancel_pending(analysis_id)
    finally:
        release.set()
        runner.shutdown(wait=True)
    assert harness.repository.analysis_status(analysis_id) == "queued"
    # 终态 CAS：排队任务取消后进入 cancelled，且阶段全部 skipped。
    assert harness.repository.apply_cancel(analysis_id) is True
    assert harness.repository.analysis_status(analysis_id) == "cancelled"
    stages = _stage_map(harness, analysis_id)
    assert "running" not in stages.values()


@pytest.mark.parametrize("interrupt_at", ["git", "parse", "rules", "report"])
def test_cancel_after_interrupt_closes_running_stages(
    harness: Harness, tmp_path: Path, interrupt_at: str
):
    """中断（进程死亡）→ 取消 → 恢复执行：不留 running，产物不丢，终态不再动。"""

    repo = make_net_repo(tmp_path, name=f"interrupt-{interrupt_at}")
    analysis_id = register_and_create(harness.repository, repo, key=f"r2-interrupt-{interrupt_at}")

    def interrupt(*args, **kwargs):
        raise SystemExit("simulated process death mid-run")

    patch_target = {
        "parse": "collect_map_inputs",
        "rules": "evaluate_rules",
    }
    if interrupt_at == "parse":
        with patch.object(worker_module, "collect_map_inputs", interrupt):
            with pytest.raises(SystemExit):
                direct_worker(harness).run(analysis_id)
    elif interrupt_at == "rules":
        with patch.object(worker_module, "evaluate_rules", interrupt):
            with pytest.raises(SystemExit):
                direct_worker(harness).run(analysis_id)
    else:
        def interrupt_hook(_: str, stage: str) -> None:
            if stage == interrupt_at:
                raise SystemExit("simulated process death at stage boundary")

        with pytest.raises(SystemExit):
            direct_worker(harness, on_stage_start=interrupt_hook).run(analysis_id)

    # 中断后保存取消标记（进程已死，无法在边界协作取消）。
    assert harness.repository.request_cancel(analysis_id) is True
    # 恢复执行：第一个检查点即生效取消。
    status = direct_worker(harness).run(analysis_id)
    assert status == "cancelled"
    stages = _stage_map(harness, analysis_id)
    assert "running" not in stages.values(), f"{interrupt_at} 中断后取消不得留 running: {stages}"
    closed = [stage for stage, value in stages.items() if value == "skipped"]
    assert closed, "未开始/未完成的阶段应关闭为 skipped 并注明原因"
    skipped_details = {
        row["stage"]: row["detail"]
        for row in harness.repository.stage_records(analysis_id)
        if row["status"] == "skipped"
    }
    assert skipped_details
    assert all(detail == "cancelled_by_user" for detail in skipped_details.values()), \
        "关闭原因统一记录，completed 阶段的原有 detail 不受影响"
    completed = [stage for stage, value in stages.items() if value == "completed"]
    if interrupt_at in ("rules", "report"):
        assert "git" in completed and "parse" in completed, "已提交 checkpoint 的阶段保留 completed"
        assert harness.repository.load_stage_output(analysis_id, "parse") is not None, "已提交产物不丢"
    # cancelled 是终态：再次执行不改任何行。
    before = json.dumps(dict(harness.repository.get_analysis(analysis_id)), sort_keys=True, default=str)
    assert direct_worker(harness).run(analysis_id) == "cancelled"
    after = json.dumps(dict(harness.repository.get_analysis(analysis_id)), sort_keys=True, default=str)
    assert before == after


# ---------------------------------------------------------------------------
# B04-R2-04：manifest 深层校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        {"human_feature_mapping": [7]},
        {"human_feature_mapping": [None]},
        {"human_feature_mapping": [{}]},
        {"human_feature_mapping": [{"page": "src/x.tsx", "feature_id": "F", "confirmed_by": "human"}]},
        {"human_feature_mapping": [{"page": "src/x.tsx", "feature_id": "F", "feature": "f", "events": [3], "confirmed_by": "human"}]},
        {"human_feature_mapping": [{"page": "src/x.tsx", "feature_id": "F", "feature": "f", "events": "submit", "confirmed_by": "human"}]},
        {"human_feature_mapping": [{"page": "src/x.tsx", "feature_id": "F", "feature": "f", "confirmed_by": "model"}]},
        {"human_feature_mapping": [{"page": None, "feature_id": "F", "feature": "f", "confirmed_by": "human"}]},
        {"required_entities": {"permission_modules": [{}]}},
        {"required_entities": {"permission_modules": [7]}},
        {"required_entities": {"permission_modules": "src/lib/auth"}},
    ],
)
def test_manifest_invalid_children_are_schema_invalid(tmp_path: Path, content):
    # content3：缺必需字段 feature（events 是消费方可选项，缺省合法，另行覆盖）
    repo = make_net_repo(tmp_path / f"case-{abs(hash(json.dumps(content, sort_keys=True)))}", name="scenario")
    manifests = tmp_path / f"mf-{abs(hash(json.dumps(content, sort_keys=True)))}"
    manifests.mkdir(parents=True, exist_ok=True)
    (manifests / "scenario-manifest.json").write_text(json.dumps(content), encoding="utf-8")
    outcome = load_manifest(manifests, repo)
    assert outcome.status == ManifestStatus.SCHEMA_INVALID
    assert outcome.digest, "非法配置仍保留内容摘要"


def test_manifest_legal_unicode_mapping_loads(tmp_path: Path):
    repo = make_net_repo(tmp_path / "unicode", name="scenario")
    manifests = tmp_path / "unicode-mf"
    manifests.mkdir(parents=True)
    legal = {
        "description": "描述性元数据不受校验限制",
        "human_feature_mapping": [
            {
                "page": "src/pages/登录/page.tsx",
                "feature_id": "F-登录",
                "feature": "用户登录",
                "events": ["提交登录"],
                "confirmed_by": "human",
            },
            {
                # events 是消费方可选项：缺省合法（映射存在但无事件绑定）
                "page": "src/pages/订单/page.tsx",
                "feature_id": "F-订单",
                "feature": "订单查询",
                "confirmed_by": "human",
            },
        ],
        "required_entities": {"permission_modules": ["src/lib/auth.ts"]},
    }
    (manifests / "scenario-manifest.json").write_text(
        json.dumps(legal, ensure_ascii=False), encoding="utf-8"
    )
    outcome = load_manifest(manifests, repo)
    assert outcome.status == ManifestStatus.LOADED
    assert permission_modules_of(outcome.manifest) == ["src/lib/auth.ts"]


def test_invalid_manifest_children_complete_as_limit(harness: Harness, tmp_path: Path):
    base = tmp_path / "invalid-children"
    repo = make_net_repo(base, name="scenario")
    manifests = base / "cfg"
    manifests.mkdir(parents=True)
    (manifests / "scenario-manifest.json").write_text(
        json.dumps({"required_entities": {"permission_modules": [{}]}}), encoding="utf-8"
    )
    analysis_id = register_and_create(harness.repository, repo, key="r2-invalid-children")
    status = AnalysisWorker(
        harness.repository, allowed_roots=(base,), manifest_dir=manifests
    ).run(analysis_id)
    assert status in ("completed", "completed_with_limits"), "配置非法是限制不是崩溃"
    row = harness.repository.get_analysis(analysis_id)
    assert row["manifest_status"] == "schema_invalid"
    assert row["manifest_digest"]
    document = harness.repository.rules_document(analysis_id)
    assert "manifest:schema_invalid" in document["limits"]
    assert document["manifest"]["status"] == "schema_invalid"


# ---------------------------------------------------------------------------
# B04-R3-01：显式 JSON null ≠ 键缺省
# ---------------------------------------------------------------------------

_R3_BASE_MAPPING = {
    "page": "src/pages/checkout/page.tsx",
    "feature_id": "F-checkout",
    "feature": "结账",
    "confirmed_by": "human",
    "events": ["submitCheckout"],
}


@pytest.mark.parametrize(
    "name,content",
    [
        ("mapping_null", {"human_feature_mapping": None}),
        ("entities_null", {"required_entities": None}),
        ("modules_null", {"required_entities": {"permission_modules": None}}),
        ("events_null", {"human_feature_mapping": [dict(_R3_BASE_MAPPING, events=None)]}),
    ],
)
def test_manifest_explicit_null_rejected_end_to_end(
    harness: Harness, tmp_path: Path, name: str, content: dict
):
    """四种显式 null：loader=schema_invalid + digest；进入 Worker 后
    完成但受限（非内部 TypeError），库内保留 schema_invalid+digest，
    limits 含 manifest:schema_invalid，无事实升级。"""

    base = tmp_path / f"r3-{name}"
    repo = make_net_repo(base, name="scenario")
    manifests = base / "cfg"
    manifests.mkdir(parents=True)
    (manifests / "scenario-manifest.json").write_text(json.dumps(content), encoding="utf-8")

    outcome = load_manifest(manifests, repo)
    assert outcome.status == ManifestStatus.SCHEMA_INVALID
    assert outcome.digest, "非法配置仍保留内容摘要"

    analysis_id = register_and_create(harness.repository, repo, key=f"r3-{name}")
    status = AnalysisWorker(
        harness.repository, allowed_roots=(base,), manifest_dir=manifests
    ).run(analysis_id)
    assert status in ("completed", "completed_with_limits"), \
        "显式 null 必须分流为限制，不得让分析内部 TypeError 失败"
    row = harness.repository.get_analysis(analysis_id)
    assert row["manifest_status"] == "schema_invalid"
    assert row["manifest_digest"] == outcome.digest, "持久化摘要与 loader 一致"
    assert row["failure_reason"] is None, "不得以内部异常冒充配置分类"
    document = harness.repository.rules_document(analysis_id)
    assert document is not None
    assert "manifest:schema_invalid" in document["limits"]


@pytest.mark.parametrize(
    "name,content",
    [
        ("all_omitted", {}),
        ("events_omitted", {"human_feature_mapping": [{k: v for k, v in _R3_BASE_MAPPING.items() if k != "events"}]}),
        ("empty_containers", {"human_feature_mapping": [], "required_entities": {"permission_modules": []}}),
        ("empty_entities", {"required_entities": {}}),
        ("events_empty", {"human_feature_mapping": [dict(_R3_BASE_MAPPING, events=[])]}),
    ],
)
def test_manifest_missing_and_empty_stay_legal(
    harness: Harness, tmp_path: Path, name: str, content: dict
):
    """控制组：键缺省与空容器合法——可选表示可缺省/可空，不因 null 修复收紧。"""

    base = tmp_path / f"r3-legal-{name}"
    repo = make_net_repo(base, name="scenario")
    manifests = base / "cfg"
    manifests.mkdir(parents=True)
    (manifests / "scenario-manifest.json").write_text(json.dumps(content), encoding="utf-8")

    outcome = load_manifest(manifests, repo)
    assert outcome.status == ManifestStatus.LOADED
    analysis_id = register_and_create(harness.repository, repo, key=f"r3-legal-{name}")
    status = AnalysisWorker(
        harness.repository, allowed_roots=(base,), manifest_dir=manifests
    ).run(analysis_id)
    assert status in ("completed", "completed_with_limits")
    row = harness.repository.get_analysis(analysis_id)
    assert row["manifest_status"] == "loaded"
    document = harness.repository.rules_document(analysis_id)
    assert "manifest:schema_invalid" not in document["limits"]


# ---------------------------------------------------------------------------
# B04-R2-05：查询参数不回显
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def readable_analysis(fixture_repo, fixture_roots, tmp_path_factory):
    from fastapi.testclient import TestClient
    from morphojudge.main import create_app
    from morphojudge.settings import DaemonSettings

    data_dir = tmp_path_factory.mktemp("r2-api-data")
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


@pytest.mark.parametrize(
    "endpoint,query",
    [("coverage", "status"), ("findings", "category")],
)
def test_invalid_query_values_not_echoed(readable_analysis, endpoint, query):
    client, final = readable_analysis
    analysis_id = final["analysis_id"]
    marker = "R2_SYNTHETIC_MARKER_" + "x" * 180  # 长值 + 合成标记
    path_like = "/synthetic/absolute/path/value"
    for bad_value in (marker, path_like):
        response = client.get(
            f"/v1/analyses/{analysis_id}/{endpoint}", params={query: bad_value}
        )
        assert response.status_code == 400
        assert bad_value not in response.text
        assert marker[:20] not in response.text
        error = response.json()["error"]
        assert error["code"] == "INVALID_INPUT"
        assert set(error["details"]) == {"field", "reason"}
        assert error["details"]["reason"] == "unknown_enum_value"


def test_legal_filters_still_work(readable_analysis):
    client, final = readable_analysis
    analysis_id = final["analysis_id"]
    coverage = client.get(
        f"/v1/analyses/{analysis_id}/coverage", params={"status": "selected"}
    )
    assert coverage.status_code == 200
    assert all(
        decision["status"] == "selected"
        for decision in coverage.json()["decisions"]
    )
    findings = client.get(
        f"/v1/analyses/{analysis_id}/findings", params={"category": "behavior_network"}
    )
    assert findings.status_code == 200
    assert findings.json()["total"] > 0
    assert all(
        item["finding"]["category"] == "behavior_network"
        for item in findings.json()["items"]
    )
