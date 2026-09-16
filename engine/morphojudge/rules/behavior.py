"""BEH-001: behavior rules.

Consumes the base/target SoftwareMaps, aligns methods by (path, name,
normalized body fingerprint) — renames are mapped through the diff — and
classifies every behavior edge as existing / new / deleted. Signals are
static clues only: kind=fact findings describe that a call site exists,
never that it executed or is a vulnerability. Candidate/unresolved edges
keep their resolution.

Permission checks are recognized ONLY through manifest-declared permission
modules (explicit configuration, not short-name heuristics like isAdmin).
A removed guard call is a change hint (BEH-PERM-GUARD-REMOVED, rule_hint),
never an auth-bypass conclusion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..contracts.domain import EvidenceSide, MapEdge
from ..parser.extract import Symbol
from ..pipeline import BuildResult

RULE_NETWORK = "BEH-NETWORK"
RULE_SHELL = "BEH-SHELL"
RULE_FILE = "BEH-FILE"
RULE_PERM_CHECK = "BEH-PERM-CHECK"
RULE_PERM_GUARD_REMOVED = "BEH-PERM-GUARD-REMOVED"

CHANGE_EXISTING = "existing"
CHANGE_NEW = "new"
CHANGE_DELETED = "deleted"

_METHOD_ID_RE = re.compile(r"^method:([^:]+):([^:]+):(\d+)$")


@dataclass(frozen=True)
class BehaviorSignal:
    category: str        # FindingCategory value
    rule_id: str
    change: str          # existing | new | deleted
    resolution: str      # resolved | candidate | unresolved (edge resolution)
    impact: str          # low | medium
    method_path: str     # 证据侧所在文件（对齐后路径）
    method_name: str
    evidence_side: EvidenceSide
    evidence_path: str
    evidence_line: int
    target_label: str | None
    note: str | None
    target_node_id: str | None = None  # target 侧方法节点（影响路径起点；已删除方法为 None）

    @property
    def sort_key(self) -> tuple:
        return (self.rule_id, self.change, self.method_path, self.method_name, self.evidence_line, self.target_label or "")


@dataclass(frozen=True)
class MethodPair:
    path: str
    name: str
    base_symbol: Symbol | None
    target_symbol: Symbol | None
    exact: bool  # True = body fingerprint identical


def _mapped_path(path: str, rename_map: dict[str, str]) -> str:
    return rename_map.get(path, path)


def _all_symbols(result: BuildResult) -> list[Symbol]:
    symbols: list[Symbol] = []
    for path in sorted(result.symbols_by_path):
        symbols.extend(result.symbols_by_path[path])
    return symbols


def align_methods(
    base_result: BuildResult, target_result: BuildResult, rename_map: dict[str, str]
) -> tuple[list[MethodPair], list[str]]:
    """Pair base/target methods by (mapped path, name, body fingerprint).

    Fingerprint-equal pairs are 'exact' (whitespace-only edits keep them
    paired). Same (path, name) with different fingerprints pairs as evolved
    (exact=False) when unambiguous (one leftover per side); otherwise the
    leftovers stay standalone and a limitation note is recorded instead of
    guessing a pairing.
    """

    notes: list[str] = []
    groups: dict[tuple[str, str], dict[str, list[Symbol]]] = {}
    for symbol in _all_symbols(base_result):
        key = (_mapped_path(symbol.path, rename_map), symbol.name)
        groups.setdefault(key, {}).setdefault("base", []).append(symbol)
    for symbol in _all_symbols(target_result):
        key = (symbol.path, symbol.name)
        groups.setdefault(key, {}).setdefault("target", []).append(symbol)

    pairs: list[MethodPair] = []
    for (path, name) in sorted(groups):
        base_symbols = sorted(groups[(path, name)].get("base", []), key=lambda s: s.start_line)
        target_symbols = sorted(groups[(path, name)].get("target", []), key=lambda s: s.start_line)

        remaining_base = list(base_symbols)
        remaining_target = list(target_symbols)
        # 先做指纹精确配对
        for base_symbol in list(remaining_base):
            match = next(
                (t for t in remaining_target if t.body_fingerprint and t.body_fingerprint == base_symbol.body_fingerprint),
                None,
            )
            if match is not None:
                pairs.append(MethodPair(path, name, base_symbol, match, exact=True))
                remaining_base.remove(base_symbol)
                remaining_target.remove(match)
        # 剩余：一对一才算演化配对，歧义保留限制
        if len(remaining_base) == 1 and len(remaining_target) == 1:
            pairs.append(MethodPair(path, name, remaining_base[0], remaining_target[0], exact=False))
        else:
            for symbol in remaining_base:
                pairs.append(MethodPair(path, name, symbol, None, exact=False))
            for symbol in remaining_target:
                pairs.append(MethodPair(path, name, None, symbol, exact=False))
            if remaining_base and remaining_target:
                notes.append(f"ambiguous_same_name_methods:{path}:{name}")
    return pairs, notes


def _edges_by_source(result: BuildResult) -> dict[str, list[MapEdge]]:
    index: dict[str, list[MapEdge]] = {}
    for edge in result.software_map.edges:
        index.setdefault(edge.source_id, []).append(edge)
    return index


def _behavior_key(edge: MapEdge, permission_modules: set[str]) -> tuple[str, str, str] | None:
    """(category, rule_id, label) for a behavior edge; None = not behavior."""

    if edge.relation.value == "sends" and edge.target_id.startswith("svc:"):
        return ("behavior_network", RULE_NETWORK, edge.target_id)
    if edge.relation.value == "sends" and edge.target_id.startswith("data:process:"):
        return ("behavior_shell", RULE_SHELL, edge.target_id)
    if edge.relation.value in ("reads", "writes") and edge.target_id.startswith("data:file:"):
        return ("behavior_file", RULE_FILE, edge.target_id)
    if edge.relation.value == "calls" and edge.resolution.value == "resolved":
        match = _METHOD_ID_RE.match(edge.target_id)
        if match and match.group(1) in permission_modules:
            return ("behavior_permission", RULE_PERM_CHECK, edge.target_id)
    return None


def collect_behavior_signals(
    base_result: BuildResult,
    target_result: BuildResult,
    rename_map: dict[str, str],
    permission_modules: list[str],
) -> tuple[list[BehaviorSignal], list[str]]:
    """Classify behavior edges across the two snapshot sides.

    Evidence side is carried per signal: NEW/existing evidence resolves on
    the target commit, deleted evidence on the base commit (the analyzer
    derives the commit from EvidenceSide).
    """

    permission_modules_set = set(permission_modules)
    notes: list[str] = []
    pairs, alignment_notes = align_methods(base_result, target_result, rename_map)
    notes.extend(alignment_notes)

    base_edges = _edges_by_source(base_result)
    target_edges = _edges_by_source(target_result)
    signals: list[BehaviorSignal] = []
    seen: set[tuple] = set()

    def emit(
        category: str,
        rule_id: str,
        change: str,
        resolution: str,
        impact: str,
        method_path: str,
        method_name: str,
        side: EvidenceSide,
        edge: MapEdge,
        label: str,
        note: str | None,
    ) -> None:
        key = (rule_id, change, method_path, method_name, edge.file_path, edge.line, label)
        if key in seen:
            return
        seen.add(key)
        signals.append(
            BehaviorSignal(
                category=category,
                rule_id=rule_id,
                change=change,
                resolution=resolution,
                impact=impact,
                method_path=method_path,
                method_name=method_name,
                evidence_side=side,
                evidence_path=edge.file_path or method_path,
                evidence_line=edge.line or 1,
                target_label=label,
                note=note,
                target_node_id=pair.target_symbol.node_id if pair.target_symbol is not None else None,
            )
        )

    for pair in pairs:
        base_side_edges: list[MapEdge] = []
        if pair.base_symbol is not None:
            base_side_edges = base_edges.get(pair.base_symbol.node_id, [])
        target_side_edges: list[MapEdge] = []
        if pair.target_symbol is not None:
            target_side_edges = target_edges.get(pair.target_symbol.node_id, [])

        base_keys: dict[tuple, MapEdge] = {}
        for edge in base_side_edges:
            key = _behavior_key(edge, permission_modules_set)
            if key is not None:
                base_keys.setdefault(key, edge)
        target_keys: dict[tuple, MapEdge] = {}
        for edge in target_side_edges:
            key = _behavior_key(edge, permission_modules_set)
            if key is not None:
                target_keys.setdefault(key, edge)

        method_path = pair.path
        method_name = pair.name

        for key, edge in sorted(target_keys.items(), key=lambda kv: (kv[1].line or 0, kv[0])):
            category, rule_id, label = key
            change = CHANGE_EXISTING if key in base_keys else CHANGE_NEW
            note = edge.note
            emit(category, rule_id, change, edge.resolution.value, "low",
                 method_path, method_name, EvidenceSide.NEW, edge, label, note)

        for key, edge in sorted(base_keys.items(), key=lambda kv: (kv[1].line or 0, kv[0])):
            if key in target_keys:
                continue
            category, rule_id, label = key
            if category == "behavior_permission":
                # 守卫调用消失：变更提示，不是鉴权绕过结论
                note = "guard_call_removed"
                if pair.target_symbol is None:
                    note = "guard_call_removed:method_removed"
                emit(category, RULE_PERM_GUARD_REMOVED, CHANGE_DELETED,
                     edge.resolution.value, "medium", method_path, method_name,
                     EvidenceSide.OLD, edge, label, note)
            else:
                emit(category, rule_id, CHANGE_DELETED, edge.resolution.value, "low",
                     method_path, method_name, EvidenceSide.OLD, edge, label, edge.note)

    signals.sort(key=lambda signal: signal.sort_key)
    return signals, notes
