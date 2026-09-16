"""B03-04：依赖规则——增删/版本/来源变化、pnpm v9、缺失/损坏/不支持锁文件。
零安装、零 registry 查询（安全测试另行用网络探针验证）。
"""

from __future__ import annotations

import json

from conftest import analyze_repo, build_scenario_repo

BASE_PKG = json.dumps(
    {
        "name": "scenario",
        "dependencies": {"alpha": "1.0.0", "gamma": "^2.0.0"},
        "devDependencies": {"tooling": "5.1.0"},
    },
    indent=2,
) + "\n"


def _pkg(dep_changes: dict) -> str:
    data = json.loads(BASE_PKG)
    for key, value in dep_changes.items():
        if value is None:
            for section in ("dependencies", "devDependencies"):
                data.get(section, {}).pop(key, None)
        else:
            placed = False
            for section in ("dependencies", "devDependencies"):
                if key in data.get(section, {}):
                    data[section][key] = value
                    placed = True
            if not placed:
                data.setdefault("dependencies", {})[key] = value
    return json.dumps(data, indent=2) + "\n"


def _rule(result, rule_id):
    return [f for f in result.findings if f.rule_id == rule_id]


def test_added_removed_and_version_changes(tmp_path):
    target = _pkg({"beta": "3.0.0", "alpha": None, "gamma": "^2.1.0"})
    repo = build_scenario_repo(
        tmp_path / "changes", {"package.json": BASE_PKG}, {"package.json": target}
    )
    result = analyze_repo(repo, [tmp_path / "changes"])

    added = _rule(result, "DEP-ADD")
    assert [f for f in added if "beta" in f.id or True]  # 存在
    assert any(
        a for a in result.evidence
        if a.id in added[0].evidence_ids and '"beta"' in a.snippet
    ), "新增依赖证据锚定到 package.json 中的实际键行"

    removed = _rule(result, "DEP-REMOVE")
    assert removed
    anchor = next(a for a in result.evidence if a.id in removed[0].evidence_ids)
    assert anchor.side.value == "old", "删除依赖的证据在 base 侧"

    version = _rule(result, "DEP-VERSION-CHANGE")
    assert version, "版本说明符变化必须记录"


def test_git_url_source_change(tmp_path):
    target = _pkg({"gamma": "github:some-org/some-repo#abc123"})
    repo = build_scenario_repo(
        tmp_path / "giturl", {"package.json": BASE_PKG}, {"package.json": target}
    )
    result = analyze_repo(repo, [tmp_path / "giturl"])
    source_changes = _rule(result, "DEP-SOURCE-CHANGE")
    assert source_changes, "registry -> git URL 来源变化必须记录"
    anchor = next(a for a in result.evidence if a.id in source_changes[0].evidence_ids)
    assert anchor.path == "package.json"
    assert "gamma" in anchor.snippet


def _lock(version: str, deps: dict) -> str:
    importer_lines = []
    for name, spec in deps.items():
        importer_lines.append(f"      {name}:\n        specifier: {spec['specifier']}\n        version: {spec['version']}")
    return (
        "lockfileVersion: '9.0'\n\n"
        "importers:\n\n  .:\n"
        "    dependencies:\n" + "\n".join(importer_lines) + "\n\n"
        "packages:\n\n"
    )


LOCK_BASE = _lock("9.0", {"alpha": {"specifier": "1.0.0", "version": "1.0.0"}})
LOCK_TARGET = _lock("9.0", {"alpha": {"specifier": "1.0.0", "version": "1.0.1"}})


def test_pnpm_v9_lock_version_change(tmp_path):
    repo = build_scenario_repo(
        tmp_path / "lock",
        {"package.json": BASE_PKG, "pnpm-lock.yaml": LOCK_BASE},
        {"pnpm-lock.yaml": LOCK_TARGET},
    )
    result = analyze_repo(repo, [tmp_path / "lock"])
    changes = _rule(result, "DEP-LOCK-VERSION-CHANGE")
    assert changes, "pnpm v9 直接依赖解析版本变化必须记录"
    anchor = next(a for a in result.evidence if a.id in changes[0].evidence_ids)
    assert anchor.path == "pnpm-lock.yaml"
    assert "alpha" in anchor.snippet, "证据锚定到 importers 中的键行"


def test_lock_missing_declared_package(tmp_path):
    empty_lock = "lockfileVersion: '9.0'\n\nimporters:\n\n  .:\n    dependencies: {}\n\npackages:\n"
    repo = build_scenario_repo(
        tmp_path / "lockmiss",
        {"package.json": BASE_PKG, "pnpm-lock.yaml": empty_lock},
        {"src/marker.ts": "export const m = 1;\n"},
    )
    result = analyze_repo(repo, [tmp_path / "lockmiss"])
    missing = _rule(result, "DEP-LOCK-MISSING")
    assert missing, "声明存在而 lock 缺失必须记录"
    assert all(f.unresolved_reason for f in missing), "无法定位行时保持 unresolved 而非伪造行号"


def test_corrupted_lock_is_limited_not_fatal(tmp_path):
    broken = "lockfileVersion: '9.0'\nimporters:\n  .:\n    dependencies:\n\t alpha: [unclosed\n"
    repo = build_scenario_repo(
        tmp_path / "brokenlock",
        {"package.json": BASE_PKG},
        {"pnpm-lock.yaml": broken},
    )
    result = analyze_repo(repo, [tmp_path / "brokenlock"])
    stage = next(s for s in result.stage_coverage if s.stage == "dependency")
    assert stage.status == "completed_with_limits"
    assert any("invalid" in note for note in stage.notes)
    assert _rule(result, "BEH-") or result.findings == []  # 其余阶段不受影响
    assert result.analyzer_errors == []


def test_unsupported_lockfile_version(tmp_path):
    unsupported = "lockfileVersion: '6.0'\n\nimporters: {}\n"
    repo = build_scenario_repo(
        tmp_path / "v6",
        {"package.json": BASE_PKG, "pnpm-lock.yaml": unsupported},
        {"src/marker.ts": "export const m = 1;\n"},
    )
    result = analyze_repo(repo, [tmp_path / "v6"])
    assert any("lockfile_version_unsupported" in note for note in result.limits), \
        "不支持的锁版本必须记录为限制而不是已检查"


def test_missing_lockfile_is_explicitly_not_checked(tmp_path):
    repo = build_scenario_repo(
        tmp_path / "nolock",
        {"package.json": BASE_PKG},
        {"src/marker.ts": "export const m = 1;\n"},
    )
    result = analyze_repo(repo, [tmp_path / "nolock"])
    assert "no_lockfile:not_checked" in result.limits
    assert not any(f.rule_id and f.rule_id.startswith("DEP-LOCK") for f in result.findings), \
        "无锁文件不得伪装成已做锁级检查"


def test_invalid_package_json_records_error_line(tmp_path):
    broken = '{\n  "name": "scenario",\n  "dependencies": { "alpha": \n}\n'
    repo = build_scenario_repo(
        tmp_path / "badjson",
        {"package.json": broken},
        {"src/marker.ts": "export const m = 1;\n"},
    )
    result = analyze_repo(repo, [tmp_path / "badjson"])
    invalid = _rule(result, "DEP-MANIFEST-INVALID")
    assert invalid
    anchor = next(a for a in result.evidence if a.id in invalid[0].evidence_ids)
    assert anchor.start_line >= 3, "JSON 解析错误行可追溯"


def test_unchanged_dependencies_reported_as_unchanged(tmp_path):
    repo = build_scenario_repo(
        tmp_path / "same",
        {"package.json": BASE_PKG},
        {"src/marker.ts": "export const m = 1;\n"},
    )
    result = analyze_repo(repo, [tmp_path / "same"])
    assert "dependencies_unchanged" in result.limits
    assert [f for f in result.findings if f.rule_id and f.rule_id.startswith("DEP-")] == []
