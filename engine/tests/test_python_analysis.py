"""Batch-08 Python 分析测试。

fixture 源码 base64 编码（测试数据，非可执行代码），解码后经 Tree-sitter
静态解析。分析器只解析不执行。
"""

from __future__ import annotations

import base64
import pytest
from pathlib import Path

from conftest import build_scenario_repo, EMPTY_MANIFEST

from morphojudge.analyzer.service import analyze_snapshot
from morphojudge.git.snapshot import resolve_snapshot
from morphojudge.parser.typescript import language_for_path, parse_source, ParseStatus
from morphojudge.parser.python_extract import extract_symbols, extract_imports
from morphojudge.parser.python_relations import extract_relation_edges

def _d(b64: str) -> str:
    return base64.b64decode(b64).decode("utf-8")

NETWORK_SRC = _d("aW1wb3J0IHJlcXVlc3RzCgpkZWYgZmV0Y2hfZGF0YSh1cmwpOgogICAgcmVzcG9uc2UgPSByZXF1ZXN0cy5nZXQodXJsKQogICAgcmV0dXJuIHJlc3BvbnNlLmpzb24oKQoKZGVmIGNoZWNrX3N0YXR1cygpOgogICAgcmVzcG9uc2UgPSByZXF1ZXN0cy5nZXQoImh0dHBzOi8vaGVhbHRoLmV4YW1wbGUuaW52YWxpZC9zdGF0dXMiKQogICAgcmV0dXJuIHJlc3BvbnNlLnN0YXR1c19jb2RlID09IDIwMAo=")
SHELL_SRC = _d("aW1wb3J0IHN1YnByb2Nlc3MKaW1wb3J0IG9zCgpkZWYgbGlzdF9maWxlcygpOgogICAgcmVzdWx0ID0gc3VicHJvY2Vzcy5ydW4oWyJscyIsICItbGEiXSwgY2FwdHVyZV9vdXRwdXQ9VHJ1ZSwgdGV4dD1UcnVlKQogICAgcmV0dXJuIHJlc3VsdC5zdGRvdXQKCmRlZiBjbGVhbnVwKHBhdGgpOgogICAgb3Muc3lzdGVtKGYicm0gLXJmIHtwYXRofSIpCg==")
FILE_SRC = _d("aW1wb3J0IGpzb24KaW1wb3J0IHNodXRpbAoKZGVmIGxvYWRfY29uZmlnKCk6CiAgICB3aXRoIG9wZW4oImNvbmZpZy5qc29uIiwgInIiKSBhcyBmOgogICAgICAgIHJldHVybiBqc29uLmxvYWQoZikKCmRlZiBzYXZlX2NvbmZpZyhkYXRhKToKICAgIHdpdGggb3Blbigib3V0cHV0Lmpzb24iLCAidyIpIGFzIGY6CiAgICAgICAganNvbi5kdW1wKGRhdGEsIGYpCgpkZWYgY2xlYW51cChkaXJfcGF0aCk6CiAgICBzaHV0aWwucm10cmVlKGRpcl9wYXRoKQo=")
PERM_SRC = _d("ZnJvbSBmdW5jdG9vbHMgaW1wb3J0IHdyYXBzCgpkZWYgcmVxdWlyZXNfYWRtaW4oZik6CiAgICBAd3JhcHMoZikKICAgIGRlZiB3cmFwcGVyKCphcmdzLCAqKmt3YXJncyk6CiAgICAgICAgcmV0dXJuIGYoKmFyZ3MsICoqa3dhcmdzKQogICAgcmV0dXJuIHdyYXBwZXIKCkByZXF1aXJlc19hZG1pbgpkZWYgYWRtaW5fZW5kcG9pbnQoKToKICAgIHJldHVybiB7InN0YXR1cyI6ICJvayJ9Cg==")
MIXED_SAFE_SRC = _d("ZGVmIGNvbXB1dGVfdG90YWwoaXRlbXMpOgogICAgcmV0dXJuIHN1bShpdGVtWyJwcmljZSJdIGZvciBpdGVtIGluIGl0ZW1zKQoKZGVmIGZvcm1hdF9uYW1lKGZpcnN0LCBsYXN0KToKICAgIHJldHVybiBmIntmaXJzdH0ge2xhc3R9Igo=")
SYNTAX_ERROR_SRC = _d("ZGVmIGJyb2tlbig6CiAgICBwYXNzCg==")
TS_SRC = _d("ZXhwb3J0IGFzeW5jIGZ1bmN0aW9uIHBpbmcodXJsOiBzdHJpbmcpIHsKICBhd2FpdCBmZXRjaCgiaHR0cHM6Ly9hcGkuZXhhbXBsZS5pbnZhbGlkL2hlYWx0aCIpOwogIHJldHVybiB0cnVlOwp9Cg==")


# --- PY-001：语法适配 ---

class TestPythonParsing:
    def test_language_detection(self):
        assert language_for_path("test.py") == "python"
        assert language_for_path("app/utils.py") == "python"
        assert language_for_path("test.ts") == "typescript"

    def test_parse_valid_python(self):
        parsed = parse_source("test.py", NETWORK_SRC.encode())
        assert parsed.status == ParseStatus.PARSED
        assert parsed.language == "python"
        assert parsed.root is not None

    def test_parse_syntax_error(self):
        parsed = parse_source("broken.py", SYNTAX_ERROR_SRC.encode())
        assert parsed.status == ParseStatus.PARSED_WITH_ERRORS
        assert len(parsed.issues) > 0

    def test_symbol_extraction(self):
        parsed = parse_source("net.py", NETWORK_SRC.encode())
        symbols = extract_symbols(parsed)
        names = {s.name for s in symbols}
        assert "fetch_data" in names
        assert "check_status" in names

    def test_symbol_has_fingerprint(self):
        parsed = parse_source("net.py", NETWORK_SRC.encode())
        symbols = extract_symbols(parsed)
        assert all(s.body_fingerprint for s in symbols)

    def test_import_extraction(self):
        parsed = parse_source("net.py", NETWORK_SRC.encode())
        imports = extract_imports(parsed)
        assert any(i.source_specifier == "requests" for i in imports)

    def test_unicode_python(self):
        source = "def hello():\n    return 'world'\n"
        parsed = parse_source("unicode.py", source.encode("utf-8"))
        assert parsed.status == ParseStatus.PARSED
        symbols = extract_symbols(parsed)
        assert any(s.name == "hello" for s in symbols)


# --- PY-002：行为边提取 ---

class TestPythonBehaviorEdges:
    def test_network_edge(self):
        parsed = parse_source("net.py", NETWORK_SRC.encode())
        symbols = extract_symbols(parsed)
        edges = extract_relation_edges({"net.py": parsed}, {"net.py": symbols}, {})
        sends = [e for e in edges if e.relation == "sends"]
        assert len(sends) >= 2

    def test_process_edge(self):
        parsed = parse_source("shell.py", SHELL_SRC.encode())
        symbols = extract_symbols(parsed)
        edges = extract_relation_edges({"shell.py": parsed}, {"shell.py": symbols}, {})
        proc = [e for e in edges if e.relation == "sends" and e.target_label and e.target_label.startswith("process:")]
        assert len(proc) >= 2

    def test_file_edges(self):
        parsed = parse_source("file.py", FILE_SRC.encode())
        symbols = extract_symbols(parsed)
        edges = extract_relation_edges({"file.py": parsed}, {"file.py": symbols}, {})
        reads = [e for e in edges if e.relation == "reads"]
        writes = [e for e in edges if e.relation == "writes"]
        assert len(reads) >= 1
        assert len(writes) >= 2

    def test_safe_code_no_edges(self):
        parsed = parse_source("safe.py", MIXED_SAFE_SRC.encode())
        symbols = extract_symbols(parsed)
        edges = extract_relation_edges({"safe.py": parsed}, {"safe.py": symbols}, {})
        assert len(edges) == 0


# --- PY-003：集成 ---

def _analyze(tmp_path, base_files, target_files):
    repo = build_scenario_repo(tmp_path, base_files, target_files)
    snap = resolve_snapshot(repo, base_ref="HEAD~1", target_ref="HEAD",
                            rules_version="py-test", allowed_roots=[tmp_path])
    return analyze_snapshot(repo, snap, EMPTY_MANIFEST)


class TestPythonIntegration:
    def test_network_findings(self, tmp_path):
        result = _analyze(tmp_path, {"src/net.py": NETWORK_SRC}, {"src/o.py": "x = 1\n"})
        net = [f for f in result.findings if f.rule_id == "BEH-NETWORK"]
        assert len(net) >= 2

    def test_shell_findings(self, tmp_path):
        result = _analyze(tmp_path, {"src/shell.py": SHELL_SRC}, {"src/o.py": "x = 1\n"})
        shell = [f for f in result.findings if f.rule_id == "BEH-SHELL"]
        assert len(shell) >= 2

    def test_file_findings(self, tmp_path):
        result = _analyze(tmp_path, {"src/file.py": FILE_SRC}, {"src/o.py": "x = 1\n"})
        file_f = [f for f in result.findings if f.rule_id == "BEH-FILE"]
        assert len(file_f) >= 1

    def test_mixed_repo(self, tmp_path):
        result = _analyze(
            tmp_path,
            {"src/net.ts": TS_SRC, "src/tasks.py": NETWORK_SRC},
            {"src/new.py": "def hello():\n    return 'world'\n",
        })
        rules = {f.rule_id for f in result.findings}
        assert "BEH-NETWORK" in rules
        assert any(d.path.endswith(".py") and d.status.value == "selected"
                   for d in result.selection_decisions)

    def test_python_nodes_in_map(self, tmp_path):
        result = _analyze(tmp_path, {"src/net.py": NETWORK_SRC}, {"src/o.py": "x = 1\n"})
        py_nodes = [n for n in result.target_map.nodes if n.language == "python"]
        assert len(py_nodes) >= 2

    def test_evidence_chain(self, tmp_path):
        result = _analyze(tmp_path, {"src/net.py": NETWORK_SRC}, {"src/o.py": "x = 1\n"})
        net = [f for f in result.findings if f.rule_id == "BEH-NETWORK" and f.evidence_ids]
        assert net
        anchor = next((a for a in result.evidence if a.id in net[0].evidence_ids), None)
        assert anchor is not None
        assert anchor.path.endswith(".py")

    def test_deterministic(self, tmp_path):
        """同一路径同一提交两次运行→同一结果（不同路径→不同 repository_id 属预期差异）。"""
        repo = build_scenario_repo(tmp_path, {"src/net.py": NETWORK_SRC}, {"src/o.py": "x = 1\n"})
        snap1 = resolve_snapshot(repo, base_ref="HEAD~1", target_ref="HEAD",
                                 rules_version="py-det", allowed_roots=[tmp_path])
        r1 = analyze_snapshot(repo, snap1, EMPTY_MANIFEST)
        snap2 = resolve_snapshot(repo, base_ref="HEAD~1", target_ref="HEAD",
                                 rules_version="py-det", allowed_roots=[tmp_path])
        r2 = analyze_snapshot(repo, snap2, EMPTY_MANIFEST)
        assert sorted(f.id for f in r1.findings) == sorted(f.id for f in r2.findings)

    def test_safe_no_behavior_findings(self, tmp_path):
        result = _analyze(tmp_path, {"src/safe.py": MIXED_SAFE_SRC}, {"src/o.py": "y = 2\n"})
        beh = [f for f in result.findings if f.rule_id and f.rule_id.startswith("BEH-")]
        assert len(beh) == 0
