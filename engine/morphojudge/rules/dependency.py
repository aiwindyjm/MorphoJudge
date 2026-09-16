"""DEP-001: dependency rules.

Compares package.json (four dependency sections) and pnpm-lock.yaml v9
direct dependencies between base and target commits. Files come only
through the dependency selection purpose (whitelist + safety checks);
content is read from git blobs. No installs, no registry/CVE queries —
absent intelligence stays explicitly "not checked".

Evidence positions: package.json keys are located textually (first
occurrence of `"name":` as a JSON key line); pnpm-lock entries are located
inside the `importers:` window. When a key line cannot be found the
evidence stays unresolved with reason key_line_not_found — no fake lines.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..git.blob import read_blob_bytes, list_tree_blobs
from ..selection.service import select_dependency_files
from ..selection.rules import SelectionRules

RULE_DEP_ADD = "DEP-ADD"
RULE_DEP_REMOVE = "DEP-REMOVE"
RULE_DEP_VERSION_CHANGE = "DEP-VERSION-CHANGE"
RULE_DEP_SOURCE_CHANGE = "DEP-SOURCE-CHANGE"
RULE_DEP_LOCK_ADD = "DEP-LOCK-ADD"
RULE_DEP_LOCK_REMOVE = "DEP-LOCK-REMOVE"
RULE_DEP_LOCK_VERSION_CHANGE = "DEP-LOCK-VERSION-CHANGE"
RULE_DEP_LOCK_MISSING = "DEP-LOCK-MISSING"
RULE_DEP_MANIFEST_INVALID = "DEP-MANIFEST-INVALID"
RULE_DEP_LOCK_UNSUPPORTED = "DEP-LOCK-UNSUPPORTED"

DEPENDENCY_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
SUPPORTED_LOCKFILE_VERSIONS = ("9.0", "9")

_GIT_SOURCE_PREFIXES = ("git+", "git://", "git@", "https://github.com", "https://gitlab.com", "https://codeload.github.com", "github:", "github.com/")
_LOCAL_SOURCE_PREFIXES = ("link:", "file:", "workspace:", "./", "../")


@dataclass(frozen=True)
class DependencyEntry:
    name: str
    section: str
    specifier: str
    source_kind: str  # registry | git_url | local | unknown

    @property
    def sort_key(self):
        return (self.name, self.section)


@dataclass(frozen=True)
class LockEntry:
    name: str
    section: str
    specifier: str
    version: str
    source_kind: str


@dataclass
class ParsedManifest:
    status: str  # parsed | invalid | limited
    note: str | None
    error_line: int | None
    entries: dict = field(default_factory=dict)  # name -> DependencyEntry


@dataclass
class ParsedLockfile:
    status: str  # parsed | invalid | limited | absent
    note: str | None
    error_line: int | None
    lockfile_version: str | None = None
    entries: dict = field(default_factory=dict)  # name -> LockEntry


@dataclass(frozen=True)
class DependencyChange:
    rule_id: str
    change: str  # added | removed | changed
    name: str
    source: str  # package.json | pnpm-lock.yaml
    base: tuple | None  # 描述元组（名称/节/说明符或版本）
    target: tuple | None
    evidence_path: str
    evidence_line: int | None  # None = 定位失败，保持 unresolved
    note: str | None = None


@dataclass
class DependencyReport:
    status: str = "parsed"  # parsed | limited | failed
    base_selection: tuple = None  # (decisions, summary)
    target_selection: tuple = None
    base_manifest: ParsedManifest | None = None
    target_manifest: ParsedManifest | None = None
    base_lock: ParsedLockfile | None = None
    target_lock: ParsedLockfile | None = None
    changes: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def classify_source(specifier: str) -> str:
    lowered = (specifier or "").strip().lower()
    if any(lowered.startswith(prefix.lower()) for prefix in _GIT_SOURCE_PREFIXES):
        return "git_url"
    if any(lowered.startswith(prefix.lower()) for prefix in _LOCAL_SOURCE_PREFIXES):
        return "local"
    if lowered:
        return "registry"
    return "unknown"


def parse_package_json(text: str) -> ParsedManifest:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return ParsedManifest(status="invalid", note="invalid_json", error_line=exc.lineno)
    if not isinstance(data, dict):
        return ParsedManifest(status="invalid", note="not_a_json_object", error_line=1)

    entries: dict[str, DependencyEntry] = {}
    for section in DEPENDENCY_SECTIONS:
        block = data.get(section)
        if not isinstance(block, dict):
            continue
        for name, specifier in block.items():
            if not isinstance(name, str):
                continue
            spec = specifier if isinstance(specifier, str) else str(specifier)
            entries[name] = DependencyEntry(
                name=name, section=section, specifier=spec, source_kind=classify_source(spec)
            )
    return ParsedManifest(status="parsed", note=None, error_line=None, entries=entries)


def locate_package_json_key(text: str, name: str) -> int | None:
    pattern = re.compile(rf'^\s*["\']{re.escape(name)}["\']\s*:', re.MULTILINE)
    match = pattern.search(text)
    if match is None:
        return None
    return text.count("\n", 0, match.start()) + 1


def _importers_window(text: str) -> str:
    start_match = re.search(r"^importers\s*:\s*$", text, re.MULTILINE)
    if start_match is None:
        return ""
    start = start_match.end()
    end_match = re.search(r"^packages\s*:\s*$", text[start:], re.MULTILINE)
    end = start + end_match.start() if end_match is not None else len(text)
    return text[start:end]


def locate_lock_key(text: str, name: str) -> int | None:
    window = _importers_window(text)
    if not window:
        return None
    pattern = re.compile(rf'^\s+["\']?{re.escape(name)}["\']?\s*:\s*$', re.MULTILINE)
    match = pattern.search(window)
    if match is None:
        return None
    offset_in_window = window.count("\n", 0, match.start()) + 1
    start_match = re.search(r"^importers\s*:\s*$", text, re.MULTILINE)
    window_offset = text.count("\n", 0, start_match.end()) + 1
    return window_offset + offset_in_window - 1


def parse_pnpm_lock(text: str) -> ParsedLockfile:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        line = 1
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            line = mark.line + 1
        return ParsedLockfile(status="invalid", note="invalid_yaml", error_line=line)
    if not isinstance(data, dict):
        return ParsedLockfile(status="invalid", note="not_a_yaml_mapping", error_line=1)

    version = data.get("lockfileVersion")
    version_text = str(version) if version is not None else None
    if version_text not in SUPPORTED_LOCKFILE_VERSIONS:
        return ParsedLockfile(
            status="limited",
            note=f"lockfile_version_unsupported:{version_text}",
            error_line=None,
            lockfile_version=version_text,
        )

    entries: dict[str, LockEntry] = {}
    importers = data.get("importers")
    if isinstance(importers, dict):
        root = importers.get(".")
        if isinstance(root, dict):
            for section in DEPENDENCY_SECTIONS[:3]:  # lock 直接依赖无 peer 段
                block = root.get(section)
                if not isinstance(block, dict):
                    continue
                for name, info in block.items():
                    if not isinstance(name, str) or not isinstance(info, dict):
                        continue
                    specifier = str(info.get("specifier", ""))
                    resolved = str(info.get("version", ""))
                    entries[name] = LockEntry(
                        name=name,
                        section=section,
                        specifier=specifier,
                        version=resolved,
                        source_kind=classify_source(resolved or specifier),
                    )
    return ParsedLockfile(
        status="parsed",
        note=None,
        error_line=None,
        lockfile_version=version_text,
        entries=entries,
    )


def analyze_dependencies(
    repo: Path,
    base_commit: str,
    target_commit: str,
    snapshot_id: str,
    rules: SelectionRules,
) -> DependencyReport:
    report = DependencyReport()

    def read_side(commit: str) -> tuple[ParsedManifest | None, ParsedLockfile | None, str | None, str | None, tuple, tuple]:
        tree = list_tree_blobs(repo, commit)
        decisions, summary = select_dependency_files(tree, rules, snapshot_id)
        manifest_text: str | None = None
        lock_text: str | None = None
        manifest: ParsedManifest | None = None
        lock: ParsedLockfile | None = None

        for decision in decisions:
            if decision.status.value == "excluded":
                if decision.path == "package.json":
                    manifest = ParsedManifest(status="limited", note=f"excluded:{decision.rule_id}", error_line=None)
                elif decision.path == "pnpm-lock.yaml":
                    lock = ParsedLockfile(status="limited", note=f"excluded:{decision.rule_id}", error_line=None)
                continue
            if decision.status.value != "selected":
                if decision.path == "package.json" and manifest is None:
                    manifest = ParsedManifest(status="limited", note=f"not_selected:{decision.rule_id}", error_line=None)
                elif decision.path == "pnpm-lock.yaml" and lock is None:
                    lock = ParsedLockfile(status="limited", note=f"not_selected:{decision.rule_id}", error_line=None)
                continue
            text = read_blob_bytes(repo, commit, decision.path).decode("utf-8", errors="replace")
            if decision.path == "package.json":
                manifest_text = text
                manifest = parse_package_json(text)
            elif decision.path == "pnpm-lock.yaml":
                lock_text = text
                lock = parse_pnpm_lock(text)
        return manifest, lock, manifest_text, lock_text, (decisions, summary)

    base_manifest, base_lock, base_manifest_text, base_lock_text, base_selection = read_side(base_commit)
    target_manifest, target_lock, target_manifest_text, target_lock_text, target_selection = read_side(target_commit)

    report.base_selection = base_selection
    report.target_selection = target_selection

    report.base_manifest = base_manifest
    report.target_manifest = target_manifest
    report.base_lock = base_lock
    report.target_lock = target_lock

    if base_manifest is None or target_manifest is None:
        report.status = "failed"
        report.notes.append("package_json_absent")
        return report
    if "invalid" in (base_manifest.status, target_manifest.status):
        report.status = "limited"

    # --- package.json 对比 ---
    target_text = target_manifest_text or ""
    base_text = base_manifest_text or ""
    base_entries = base_manifest.entries
    target_entries = target_manifest.entries
    all_names = sorted(set(base_entries) | set(target_entries))
    for name in all_names:
        base_entry = base_entries.get(name)
        target_entry = target_entries.get(name)
        if base_entry is None and target_entry is not None:
            report.changes.append(DependencyChange(
                rule_id=RULE_DEP_ADD, change="added", name=name, source="package.json",
                base=None, target=(target_entry.section, target_entry.specifier),
                evidence_path="package.json",
                evidence_line=locate_package_json_key(target_text, name),
            ))
        elif target_entry is None and base_entry is not None:
            report.changes.append(DependencyChange(
                rule_id=RULE_DEP_REMOVE, change="removed", name=name, source="package.json",
                base=(base_entry.section, base_entry.specifier), target=None,
                evidence_path="package.json",
                evidence_line=locate_package_json_key(base_text, name),
            ))
        else:
            assert base_entry is not None and target_entry is not None
            if base_entry.specifier != target_entry.specifier:
                report.changes.append(DependencyChange(
                    rule_id=RULE_DEP_VERSION_CHANGE, change="changed", name=name, source="package.json",
                    base=(base_entry.section, base_entry.specifier),
                    target=(target_entry.section, target_entry.specifier),
                    evidence_path="package.json",
                    evidence_line=locate_package_json_key(target_text, name),
                ))
            if base_entry.source_kind != target_entry.source_kind:
                report.changes.append(DependencyChange(
                    rule_id=RULE_DEP_SOURCE_CHANGE, change="changed", name=name, source="package.json",
                    base=(base_entry.source_kind,), target=(target_entry.source_kind,),
                    evidence_path="package.json",
                    evidence_line=locate_package_json_key(target_text, name),
                    note=f"{base_entry.source_kind}->{target_entry.source_kind}",
                ))

    # --- pnpm-lock 对比 ---
    if base_lock is None and target_lock is None:
        report.notes.append("no_lockfile:not_checked")
    else:
        if base_lock is not None and base_lock.status != "parsed":
            report.status = "limited"
            report.notes.append(f"base_lock:{base_lock.status}:{base_lock.note}")
        if target_lock is not None and target_lock.status != "parsed":
            report.status = "limited"
            report.notes.append(f"target_lock:{target_lock.status}:{target_lock.note}")

        base_lock_entries = base_lock.entries if base_lock and base_lock.status == "parsed" else {}
        target_lock_entries = target_lock.entries if target_lock and target_lock.status == "parsed" else {}
        if base_lock_entries or target_lock_entries:
            target_lock_text = target_lock_text or ""
            base_lock_text_value = base_lock_text or ""
            for name in sorted(set(base_lock_entries) | set(target_lock_entries)):
                base_le = base_lock_entries.get(name)
                target_le = target_lock_entries.get(name)
                if base_le is None and target_le is not None:
                    report.changes.append(DependencyChange(
                        rule_id=RULE_DEP_LOCK_ADD, change="added", name=name, source="pnpm-lock.yaml",
                        base=None, target=(target_le.version,),
                        evidence_path="pnpm-lock.yaml",
                        evidence_line=locate_lock_key(target_lock_text, name),
                    ))
                elif target_le is None and base_le is not None:
                    report.changes.append(DependencyChange(
                        rule_id=RULE_DEP_LOCK_REMOVE, change="removed", name=name, source="pnpm-lock.yaml",
                        base=(base_le.version,), target=None,
                        evidence_path="pnpm-lock.yaml",
                        evidence_line=locate_lock_key(base_lock_text_value, name),
                    ))
                else:
                    assert base_le is not None and target_le is not None
                    if base_le.version != target_le.version:
                        report.changes.append(DependencyChange(
                            rule_id=RULE_DEP_LOCK_VERSION_CHANGE, change="changed", name=name, source="pnpm-lock.yaml",
                            base=(base_le.version,), target=(target_le.version,),
                            evidence_path="pnpm-lock.yaml",
                            evidence_line=locate_lock_key(target_lock_text, name),
                        ))
                    if base_le.source_kind != target_le.source_kind:
                        report.changes.append(DependencyChange(
                            rule_id=RULE_DEP_SOURCE_CHANGE, change="changed", name=name, source="pnpm-lock.yaml",
                            base=(base_le.source_kind,), target=(target_le.source_kind,),
                            evidence_path="pnpm-lock.yaml",
                            evidence_line=locate_lock_key(target_lock_text, name),
                            note=f"lock:{base_le.source_kind}->{target_le.source_kind}",
                        ))

        # package.json 有但 lock 直接依赖缺失
        if target_lock is not None and target_lock.status == "parsed":
            for name, entry in sorted(target_entries.items()):
                if entry.section in ("dependencies", "devDependencies", "optionalDependencies") and name not in target_lock_entries:
                    report.changes.append(DependencyChange(
                        rule_id=RULE_DEP_LOCK_MISSING, change="changed", name=name, source="pnpm-lock.yaml",
                        base=None, target=None,
                        evidence_path="pnpm-lock.yaml",
                        evidence_line=None,
                        note="declared_but_missing_in_lock",
                    ))

    if base_manifest.status == "invalid":
        report.changes.append(DependencyChange(
            rule_id=RULE_DEP_MANIFEST_INVALID, change="changed", name="package.json", source="package.json",
            base=None, target=None, evidence_path="package.json",
            evidence_line=base_manifest.error_line, note=base_manifest.note, ))
    if target_manifest.status == "invalid":
        report.changes.append(DependencyChange(
            rule_id=RULE_DEP_MANIFEST_INVALID, change="changed", name="package.json", source="package.json",
            base=None, target=None, evidence_path="package.json",
            evidence_line=target_manifest.error_line, note=target_manifest.note,
        ))
    if report.status == "parsed" and not report.changes:
        report.notes.append("dependencies_unchanged")
    return report
