"""B03-06：Evidence Resolver——精确回到 commit/path/行，片段与 blob 一致；
越界/截断/binary/错误提交不得冒充准确证据。
"""

from __future__ import annotations

from conftest import build_scenario_repo, run_git

from morphojudge.contracts.domain import EvidenceSide
from morphojudge.evidence.resolver import (
    EvidenceRequest,
    EvidenceResolver,
    EvidenceStatus,
)
from morphojudge.git.runner import check_git

UNICODE_SOURCE = (
    "line1 ascii\n"
    "第二行 中文 🎉\n"
    "line3\r\n"
    "line4\r\n"
)


def _setup(tmp_path, name, source=UNICODE_SOURCE):
    repo = build_scenario_repo(
        tmp_path / name, {"src/u.ts": source}, {"src/marker.txt": "m\n"}
    )
    head = run_git(repo, "rev-parse", "HEAD").strip()
    resolver = EvidenceResolver(repo, "a" * 64)
    return repo, head, resolver


def test_resolved_anchor_snippet_matches_blob_bytes(tmp_path):
    repo, head, resolver = _setup(tmp_path, "ok")
    request = EvidenceRequest(head, EvidenceSide.NEW, "src/u.ts", 2, 3, "TEST")
    resolution = resolver.resolve(request)
    assert resolution.status == EvidenceStatus.RESOLVED
    anchor = resolution.anchor
    assert anchor.commit == head and anchor.path == "src/u.ts"
    assert anchor.start_line == 2 and anchor.end_line == 3
    assert anchor.snippet == "第二行 中文 🎉\nline3\r"
    assert anchor.rule_id == "TEST"


def test_crlf_lines_are_preserved_verbatim(tmp_path):
    repo, head, resolver = _setup(tmp_path, "crlf")
    request = EvidenceRequest(head, EvidenceSide.NEW, "src/u.ts", 3, 4, "TEST")
    resolution = resolver.resolve(request)
    assert resolution.status == EvidenceStatus.RESOLVED
    assert resolution.anchor.snippet == "line3\r\nline4\r", "原始文本语义，不改写换行"


def test_start_line_out_of_range_is_unresolved(tmp_path):
    repo, head, resolver = _setup(tmp_path, "oor")
    resolution = resolver.resolve(
        EvidenceRequest(head, EvidenceSide.NEW, "src/u.ts", 99, 100, "TEST")
    )
    assert resolution.status == EvidenceStatus.UNRESOLVED
    assert resolution.reason == "line_out_of_range"
    assert resolution.anchor is None


def test_end_line_out_of_range_is_not_clipped(tmp_path):
    repo, head, resolver = _setup(tmp_path, "endoor")
    resolution = resolver.resolve(
        EvidenceRequest(head, EvidenceSide.NEW, "src/u.ts", 2, 999, "TEST")
    )
    assert resolution.status == EvidenceStatus.UNRESOLVED
    assert resolution.reason == "end_line_out_of_range", "不得裁剪后冒充完整证据"


def test_invalid_range_rejected(tmp_path):
    repo, head, resolver = _setup(tmp_path, "inv")
    resolution = resolver.resolve(
        EvidenceRequest(head, EvidenceSide.NEW, "src/u.ts", 5, 2, "TEST")
    )
    assert resolution.status == EvidenceStatus.UNRESOLVED
    assert resolution.reason == "invalid_line_range"


def test_oversize_snippet_range_unresolved(tmp_path):
    big = "".join(f"line {i}\n" for i in range(1, 101))
    repo, head, resolver = _setup(tmp_path, "big", big)
    resolution = resolver.resolve(
        EvidenceRequest(head, EvidenceSide.NEW, "src/u.ts", 1, 100, "TEST")
    )
    assert resolution.status == EvidenceStatus.UNRESOLVED
    assert resolution.reason == "snippet_range_exceeds_budget"


def test_missing_path_at_commit(tmp_path):
    repo, head, resolver = _setup(tmp_path, "miss")
    resolution = resolver.resolve(
        EvidenceRequest(head, EvidenceSide.NEW, "src/does-not-exist.ts", 1, 1, "TEST")
    )
    assert resolution.status == EvidenceStatus.UNRESOLVED
    assert resolution.reason == "path_missing_at_commit"


def test_binary_file_is_rejected(tmp_path):
    import base64

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    repo = build_scenario_repo(
        tmp_path / "bin", {"src/m.ts": "export const a = 1;\n"}, {"assets/x.png": "REPLACED"}
    )
    # 直接把二进制写入 blob：借助 index hash 不可行，改用第二提交替换法——
    # 简化：构造真实二进制仓库
    from pathlib import Path

    repo2 = tmp_path / "bin2"
    repo2.mkdir(parents=True)
    run_git(repo2, "init", "-b", "main")
    (repo2 / ".gitattributes").write_text("* -text\n", encoding="utf-8", newline="")
    (repo2 / "src").mkdir()
    (repo2 / "src" / "m.ts").write_text("export const a = 1;\n", encoding="utf-8")
    (repo2 / "assets").mkdir()
    (repo2 / "assets" / "x.png").write_bytes(png)
    run_git(repo2, "add", "-A")
    run_git(repo2, "commit", "-m", "base", date="2026-05-01T08:00:00+00:00")
    run_git(repo2, "commit", "--allow-empty", "-m", "target", date="2026-05-02T08:00:00+00:00")
    head = run_git(repo2, "rev-parse", "HEAD").strip()
    resolver = EvidenceResolver(repo2, "b" * 64)
    resolution = resolver.resolve(
        EvidenceRequest(head, EvidenceSide.NEW, "assets/x.png", 1, 1, "TEST")
    )
    assert resolution.status == EvidenceStatus.UNRESOLVED
    assert resolution.reason == "binary_file"


def test_unsafe_path_rejected(tmp_path):
    repo, head, resolver = _setup(tmp_path, "unsafe")
    resolution = resolver.resolve(
        EvidenceRequest(head, EvidenceSide.NEW, "../escape.ts", 1, 1, "TEST")
    )
    assert resolution.status == EvidenceStatus.UNRESOLVED
    assert resolution.reason == "unsafe_path"


def test_invalid_commit_is_unresolved_not_crash(tmp_path):
    repo, _, resolver = _setup(tmp_path, "badcommit")
    resolution = resolver.resolve(
        EvidenceRequest("z" * 40, EvidenceSide.NEW, "src/u.ts", 1, 1, "TEST")
    )
    assert resolution.status == EvidenceStatus.UNRESOLVED
    assert resolution.reason and resolution.reason.startswith("blob_read_failed:")


def test_identical_requests_dedupe_to_same_anchor(tmp_path):
    repo, head, resolver = _setup(tmp_path, "dedupe")
    first = resolver.resolve(EvidenceRequest(head, EvidenceSide.NEW, "src/u.ts", 1, 1, "TEST"))
    second = resolver.resolve(EvidenceRequest(head, EvidenceSide.NEW, "src/u.ts", 1, 1, "TEST"))
    assert first.anchor.id == second.anchor.id
    assert first is second


def test_deleted_side_anchor_resolves_on_base_commit(fixture_repo, fixture_roots):
    """真实 fixture：legacy-format.ts 只存在于 base——old 侧证据必须可解析。"""
    base = run_git(fixture_repo, "rev-parse", "HEAD~1").strip()
    resolver = EvidenceResolver(fixture_repo, "c" * 64)
    resolution = resolver.resolve(
        EvidenceRequest(base, EvidenceSide.OLD, "src/lib/legacy-format.ts", 1, 3, "TEST")
    )
    assert resolution.status == EvidenceStatus.RESOLVED
    assert resolution.anchor.commit == base
    assert resolution.anchor.side == EvidenceSide.OLD


def test_renamed_file_exists_on_both_sides_with_own_paths(fixture_repo, fixture_roots):
    base = run_git(fixture_repo, "rev-parse", "HEAD~1").strip()
    target = run_git(fixture_repo, "rev-parse", "HEAD").strip()
    resolver = EvidenceResolver(fixture_repo, "d" * 64)
    old_side = resolver.resolve(
        EvidenceRequest(base, EvidenceSide.OLD, "src/lib/old-notify.ts", 6, 6, "TEST")
    )
    new_side = resolver.resolve(
        EvidenceRequest(target, EvidenceSide.NEW, "src/lib/notify.ts", 6, 6, "TEST")
    )
    assert old_side.status == EvidenceStatus.RESOLVED
    assert new_side.status == EvidenceStatus.RESOLVED
    assert old_side.anchor.snippet == new_side.anchor.snippet, "rename 两侧同内容行一致"
