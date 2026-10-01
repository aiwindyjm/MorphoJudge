"""DEP-002: Python dependency analysis tests (requirements.txt / Pipfile.lock)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from conftest import build_scenario_repo, EMPTY_MANIFEST

from morphojudge.rules.python_dependency import (
    parse_requirements_txt, parse_pipfile_lock, analyze_python_dependencies,
    RULE_DEP_ADD, RULE_DEP_REMOVE, RULE_DEP_VERSION_CHANGE, RULE_DEP_PY_INVALID,
)
from morphojudge.analyzer.service import analyze_snapshot
from morphojudge.git.snapshot import resolve_snapshot


# --- Parsing tests ---

class TestRequirementsParsing:
    def test_valid_entries(self):
        reqs = parse_requirements_txt("flask==2.3.2\nrequests>=2.28\npytest~=7.4\n")
        assert reqs.status == "parsed"
        assert len(reqs.entries) == 3
        assert reqs.entries[0].name == "flask"
        assert reqs.entries[0].operator == "=="
        assert reqs.entries[0].version == "2.3.2"

    def test_comments_and_blanks(self):
        reqs = parse_requirements_txt("# Comment\n\nflask==1.0\n  # Indented comment\n\n")
        assert len(reqs.entries) == 1
        assert reqs.entries[0].name == "flask"

    def test_no_version(self):
        reqs = parse_requirements_txt("requests\n")
        assert len(reqs.entries) == 1
        assert reqs.entries[0].name == "requests"
        assert reqs.entries[0].operator == ""
        assert reqs.entries[0].version == ""

    def test_duplicate_deduped(self):
        reqs = parse_requirements_txt("flask==1.0\nflask==2.0\n")
        assert len(reqs.entries) == 1

    def test_unparseable_line_limited(self):
        reqs = parse_requirements_txt("flask==1.0\n???invalid???\n")
        assert reqs.status == "limited"
        assert reqs.error_line is not None

    def test_options_skipped(self):
        reqs = parse_requirements_txt("--index-url https://pypi.org\nflask==1.0\n")
        assert len(reqs.entries) == 1
        assert reqs.entries[0].name == "flask"

    def test_empty_file(self):
        reqs = parse_requirements_txt("")
        assert reqs.status == "parsed"
        assert len(reqs.entries) == 0


class TestPipfileLockParsing:
    def test_valid(self):
        data = {
            "default": {"flask": {"version": "==2.3.2"}, "requests": {"version": "==2.31.0"}},
            "develop": {"pytest": {"version": "==7.4.3"}},
        }
        lock = parse_pipfile_lock(json.dumps(data))
        assert lock.status == "parsed"
        assert lock.default["flask"] == "2.3.2"
        assert lock.default["requests"] == "2.31.0"
        assert lock.develop["pytest"] == "7.4.3"

    def test_invalid_json(self):
        lock = parse_pipfile_lock("{not json")
        assert lock.status == "limited"
        assert "invalid_json" in lock.note

    def test_empty(self):
        lock = parse_pipfile_lock("{}")
        assert lock.status == "parsed"
        assert len(lock.default) == 0


# --- Integration tests (base vs target comparison) ---

class TestDependencyChanges:
    def _analyze(self, tmp_path, base_reqs, target_reqs):
        base_files: dict = {"requirements.txt": base_reqs} if base_reqs is not None else {"README.md": "# base\n"}
        if target_reqs is not None:
            target_files: dict = {"requirements.txt": target_reqs}
        elif base_reqs is not None:
            target_files = {"requirements.txt": None}  # 显式删除
        else:
            target_files = {"src/__init__.py": ""}
        # 确保始终有变更可提交
        if set(base_files) == set(target_files) and base_files == target_files:
            target_files["src/__init__.py"] = ""
        if not any(base_files.get(k) != v for k, v in target_files.items()):
            target_files["src/__init__.py"] = ""
        repo = build_scenario_repo(tmp_path, base_files, target_files)
        snap = resolve_snapshot(repo, base_ref="HEAD~1", target_ref="HEAD",
                                rules_version="py-dep-test", allowed_roots=[tmp_path])
        return analyze_snapshot(repo, snap, EMPTY_MANIFEST)

    def test_add_package(self, tmp_path):
        result = self._analyze(tmp_path, "flask==1.0\n", "flask==1.0\nrequests==2.0\n")
        assert result.dependency_report is not None
        added = [c for c in result.dependency_report.changes if c.rule_id == "DEP-ADD"]
        assert any(c.name == "requests" for c in added)
        # Finding 也应存在（evidence 可能 unresolved 但 finding 不丢）
        dep_add = [f for f in result.findings if f.rule_id == "DEP-ADD"]
        assert len(dep_add) >= 1

    def test_remove_package(self, tmp_path):
        result = self._analyze(tmp_path, "flask==1.0\nrequests==2.0\n", "flask==1.0\n")
        dep_remove = [f for f in result.findings if f.rule_id == "DEP-REMOVE"]
        assert len(dep_remove) >= 1

    def test_version_change(self, tmp_path):
        result = self._analyze(tmp_path, "flask==1.0\nrequests==2.0\n", "flask==1.0\nrequests==2.31.0\n")
        dep_change = [f for f in result.findings if f.rule_id == "DEP-VERSION-CHANGE"]
        assert len(dep_change) >= 1

    def test_no_change(self, tmp_path):
        result = self._analyze(tmp_path, "flask==1.0\n", "flask==1.0\n")
        if result.dependency_report:
            dep_changes = [c for c in result.dependency_report.changes
                           if c.rule_id in (RULE_DEP_ADD, RULE_DEP_REMOVE, RULE_DEP_VERSION_CHANGE)]
        else:
            dep_changes = []
        assert len(dep_changes) == 0

    def test_no_requirements(self, tmp_path):
        result = self._analyze(tmp_path, None, None)
        if result.dependency_report:
            dep_changes = [c for c in result.dependency_report.changes
                           if c.rule_id in (RULE_DEP_ADD, RULE_DEP_REMOVE, RULE_DEP_VERSION_CHANGE)]
        else:
            dep_changes = []
        assert len(dep_changes) == 0

    def test_new_requirements_file(self, tmp_path):
        """No requirements.txt in base → all packages are new."""
        result = self._analyze(tmp_path, None, "flask==2.0\n")
        assert result.dependency_report is not None
        added = [c for c in result.dependency_report.changes if c.rule_id == "DEP-ADD"]
        assert len(added) >= 1

    def test_requirements_deleted(self, tmp_path):
        """requirements.txt in base but not target → all packages removed."""
        result = self._analyze(tmp_path, "flask==2.0\n", None)
        assert result.dependency_report is not None
        removed = [c for c in result.dependency_report.changes if c.rule_id == "DEP-REMOVE"]
        assert len(removed) >= 1

    def test_python_dependency_note(self, tmp_path):
        """分析含 requirements.txt 的仓库时，notes 含 python_dependencies_analyzed。"""
        result = self._analyze(tmp_path, "flask==1.0\n", "flask==1.0\nrequests==2.0\n")
        assert any("python_dependencies_analyzed" in note for note in result.limits)
