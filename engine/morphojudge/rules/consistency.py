"""一致性规则：仅检查可验证声明（DEP-001 一致性部分）。

比较对象（target 侧）：
- JSDoc @param 名单/类型 vs 实际参数名单/注解类型；
- JSDoc @returns 类型 vs 实际返回注解；
- 明确纯函数声明（措辞白名单）vs 方法的行为边。

可确定差异 → rule hint 记录；一致 → consistent 记录（不产 Finding）；
缺注释/含糊措辞/无法绑定 → unknown 记录并保留原因，绝不猜测等价性。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..pipeline import BuildResult

RULE_PARAM_EXTRA = "CONS-PARAM-EXTRA"
RULE_PARAM_MISSING = "CONS-PARAM-MISSING"
RULE_PARAM_TYPE_MISMATCH = "CONS-PARAM-TYPE-MISMATCH"
RULE_RETURN_TYPE_MISMATCH = "CONS-RETURN-TYPE-MISMATCH"
RULE_PURITY_MISMATCH = "CONS-PURITY-MISMATCH"


@dataclass(frozen=True)
class ConsistencyRecord:
    symbol_id: str
    method_name: str
    path: str
    function_line: int
    outcome: str  # mismatch | consistent | unknown
    rule_id: str | None
    detail: str
    evidence_line: int
    note: str | None = None

    @property
    def sort_key(self):
        return (self.path, self.method_name, self.function_line, self.rule_id or "")


def _normalize_type(text: str | None) -> str | None:
    if text is None:
        return None
    return " ".join(text.split()).lower() or None


def _behavior_edges_of(result: BuildResult, source_id: str) -> list:
    return [
        edge
        for edge in result.software_map.edges
        if edge.source_id == source_id
        and edge.relation.value in ("reads", "writes", "sends")
        and edge.target_id.startswith(("svc:", "data:file:", "data:process:"))
    ]


def analyze_consistency(target_result: BuildResult) -> list[ConsistencyRecord]:
    records: list[ConsistencyRecord] = []
    jsdoc_map = target_result.jsdoc_by_node_id

    for path in sorted(target_result.symbols_by_path):
        for symbol in target_result.symbols_by_path[path]:
            doc = jsdoc_map.get(symbol.node_id)

            def record(outcome, rule_id, detail, evidence_line, note=None):
                records.append(
                    ConsistencyRecord(
                        symbol_id=symbol.node_id,
                        method_name=symbol.name,
                        path=path,
                        function_line=symbol.start_line,
                        outcome=outcome,
                        rule_id=rule_id,
                        detail=detail,
                        evidence_line=evidence_line,
                        note=note,
                    )
                )

            if doc is None or not doc.has_declarations:
                record("unknown", None, "no_verifiable_declaration", symbol.start_line)
                continue

            declared = {param.name: param for param in doc.params}
            actual = {param.name: param for param in symbol.params}
            mismatch_found = False

            for name, param in sorted(declared.items()):
                if name not in actual:
                    record("mismatch", RULE_PARAM_EXTRA,
                           f"jsdoc declares param {name} not present in signature",
                           param.line)
                    mismatch_found = True
            for name, param in sorted(actual.items()):
                if name not in declared:
                    record("mismatch", RULE_PARAM_MISSING,
                           f"signature param {name} missing from jsdoc",
                           param.line or symbol.start_line)
                    mismatch_found = True
                else:
                    doc_type = _normalize_type(declared[name].type_text)
                    actual_type = _normalize_type(param.type_text)
                    if doc_type and actual_type and doc_type != actual_type:
                        record("mismatch", RULE_PARAM_TYPE_MISMATCH,
                               f"param {name}: jsdoc{{{doc_type}}} vs annotation{{{actual_type}}}",
                               declared[name].line)
                        mismatch_found = True

            if doc.returns_type is not None and symbol.return_type is not None:
                doc_return = _normalize_type(doc.returns_type)
                actual_return = _normalize_type(symbol.return_type)
                if doc_return and actual_return and doc_return != actual_return:
                    record("mismatch", RULE_RETURN_TYPE_MISMATCH,
                           f"returns: jsdoc{{{doc_return}}} vs annotation{{{actual_return}}}",
                           doc.returns_line or doc.start_line)
                    mismatch_found = True

            if doc.pure_declared:
                behavior = _behavior_edges_of(target_result, symbol.node_id)
                if behavior:
                    first = min(behavior, key=lambda edge: edge.line or 0)
                    record("mismatch", RULE_PURITY_MISMATCH,
                           "pure declaration coexists with behavior edges: "
                           + ", ".join(sorted({edge.target_id for edge in behavior})),
                           first.line or symbol.start_line,
                           note="declaration_vs_behavior",
                    )
                    mismatch_found = True

            if not mismatch_found:
                record("consistent", None, "checked_declarations_consistent", doc.start_line)

    records.sort(key=lambda item: item.sort_key)
    return records
