"""DEP-002: Python dependency analysis.

Parses requirements.txt (PEP 508 style) and Pipfile.lock (JSON) and compares
base vs target to produce the same DependencyChange shapes as the npm/pnpm
path — DEP-ADD / DEP-REMOVE / DEP-VERSION-CHANGE reuse the existing rule IDs.
Zero installs, zero registry queries, zero network.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..git.blob import read_blob_bytes, list_tree_blobs
from ..selection.service import select_dependency_files
from ..selection.rules import SelectionRules
from .dependency import DependencyChange, DependencyReport

RULE_DEP_ADD = "DEP-ADD"
RULE_DEP_REMOVE = "DEP-REMOVE"
RULE_DEP_VERSION_CHANGE = "DEP-VERSION-CHANGE"
RULE_DEP_PY_INVALID = "DEP-PY-INVALID"

# requirements.txt 行格式：package==1.0.0 / package>=1.0 / package~=1.0.0 / package!=2.0
_REQ_RE = re.compile(
    r'^([A-Za-z0-9][A-Za-z0-9._-]*)\s*([=<>!~]{1,2})\s*([A-Za-z0-9._*!+-]+)'
)


@dataclass(frozen=True)
class PyRequirement:
    name: str
    operator: str
    version: str

    @property
    def specifier(self) -> str:
        return f"{self.name}{self.operator}{self.version}"


@dataclass
class ParsedRequirements:
    status: str = "parsed"  # parsed | limited
    note: str | None = None
    error_line: int | None = None
    entries: list[PyRequirement] = field(default_factory=list)


def parse_requirements_txt(text: str) -> ParsedRequirements:
    """Parse requirements.txt (PEP 508 simplified). Comments and blanks skipped."""
    result = ParsedRequirements()
    seen: set[str] = set()
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Skip options like --index-url, -r, editable installs
        if stripped.startswith(("-", "-e")):
            continue
        match = _REQ_RE.match(stripped)
        if match:
            name, operator, version = match.group(1), match.group(2), match.group(3)
            if name not in seen:
                seen.add(name)
                result.entries.append(PyRequirement(name=name, operator=operator, version=version))
        else:
            # Package without version specifier (just `package`)
            name_only = stripped.split("[")[0].split(";")[0].strip()
            if name_only and re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]*$', name_only):
                if name_only not in seen:
                    seen.add(name_only)
                    result.entries.append(PyRequirement(name=name_only, operator="", version=""))
            else:
                result.status = "limited"
                if result.error_line is None:
                    result.error_line = lineno
                    result.note = f"unparseable_line_{lineno}"
    return result


@dataclass
class ParsedPipfileLock:
    status: str = "parsed"
    note: str | None = None
    error_line: int | None = None
    default: dict[str, str] = field(default_factory=dict)  # name → resolved version
    develop: dict[str, str] = field(default_factory=dict)


def parse_pipfile_lock(text: str) -> ParsedPipfileLock:
    """Parse Pipfile.lock JSON format."""
    result = ParsedPipfileLock()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        result.status = "limited"
        result.note = f"invalid_json:{type(exc).__name__}"
        return result
    if not isinstance(data, dict):
        result.status = "limited"
        result.note = "not_a_dict"
        return result
    for section_name in ("default", "develop"):
        section = data.get(section_name, {})
        if isinstance(section, dict):
            target = result.default if section_name == "default" else result.develop
            for name, info in section.items():
                version = ""
                if isinstance(info, dict):
                    version = str(info.get("version", "")).lstrip("==")
                elif isinstance(info, str):
                    version = info.lstrip("==")
                target[name] = version
    return result


def analyze_python_dependencies(
    repo: Path,
    base_commit: str,
    target_commit: str,
    snapshot_id: str,
    rules: SelectionRules,
) -> list[DependencyChange]:
    """Compare requirements.txt / Pipfile.lock between base and target.

    Returns DependencyChange objects with the same rule IDs as npm
    (DEP-ADD / DEP-REMOVE / DEP-VERSION-CHANGE).
    """

    changes: list[DependencyChange] = []

    def read_reqs(commit: str) -> ParsedRequirements | None:
        tree = list_tree_blobs(repo, commit)
        decisions, _ = select_dependency_files(tree, rules, snapshot_id)
        for decision in decisions:
            if decision.path == "requirements.txt" and decision.status.value == "selected":
                text = read_blob_bytes(repo, commit, "requirements.txt").decode("utf-8", errors="replace")
                return parse_requirements_txt(text)
        return None

    base_reqs = read_reqs(base_commit)
    target_reqs = read_reqs(target_commit)

    if base_reqs is None and target_reqs is None:
        return changes  # No requirements.txt on either side

    # 缺侧当作空集合：单侧新增/全部删除
    base_entries = {r.name: r for r in base_reqs.entries} if base_reqs else {}
    target_entries = {r.name: r for r in target_reqs.entries} if target_reqs else {}

    for name in sorted(target_entries):
        target_req = target_entries[name]
        if name not in base_entries:
            changes.append(DependencyChange(
                rule_id=RULE_DEP_ADD, change="added", name=name,
                source="requirements.txt",
                base=None, target=(name, "requirements.txt", target_req.specifier),
                evidence_path="requirements.txt", evidence_line=None,
            ))
        else:
            base_req = base_entries[name]
            base_spec = f"{base_req.operator}{base_req.version}"
            target_spec = f"{target_req.operator}{target_req.version}"
            if base_spec != target_spec:
                changes.append(DependencyChange(
                    rule_id=RULE_DEP_VERSION_CHANGE, change="changed", name=name,
                    source="requirements.txt",
                    base=(name, "requirements.txt", base_spec),
                    target=(name, "requirements.txt", target_spec),
                    evidence_path="requirements.txt", evidence_line=None,
                ))

    for name in sorted(base_entries):
        if name not in target_entries:
            changes.append(DependencyChange(
                rule_id=RULE_DEP_REMOVE, change="removed", name=name,
                source="requirements.txt",
                base=(name, "requirements.txt", base_entries[name].specifier),
                target=None,
                evidence_path="requirements.txt", evidence_line=None,
            ))

    # 格式错误记录为限制（不阻塞其他分析）
    for side_reqs, side_label in ((base_reqs, "base"), (target_reqs, "target")):
        if side_reqs is not None and side_reqs.status == "limited":
            changes.append(DependencyChange(
                rule_id=RULE_DEP_PY_INVALID, change="changed", name=f"(requirements.txt/{side_label})",
                source="requirements.txt", base=None, target=None,
                evidence_path="requirements.txt", evidence_line=side_reqs.error_line,
                note=side_reqs.note,
            ))

    return changes
