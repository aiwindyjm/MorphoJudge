"""B03-05：一致性规则——只检查可验证声明的正反例。

可确定差异 → rule_hint；一致 → consistent（不产 Finding）；缺声明/
含糊措辞 → unknown 保留原因。纯函数声明 vs 行为边按声明措辞白名单判定。
"""

from __future__ import annotations

from conftest import analyze_repo, build_scenario_repo


def _records(result):
    return result.consistency_records


def _mismatches(result, rule_id):
    return [f for f in result.findings if f.rule_id == rule_id]


DOC_PARAM_EXTRA = (
        "/**\n"
        " * @param {string} name\n"
        " * @param {number} ghost\n"
        " */\n"
        "export function greet(name: string) {\n"
        "  return name;\n"
        "}\n"
)

DOC_PARAM_MISSING = (
        "/**\n"
        " * @param {number} a\n"
        " */\n"
        "export function add(a: number, b: number) {\n"
        "  return a + b;\n"
        "}\n"
)

DOC_TYPE_MISMATCH = (
        "/**\n"
        " * @param {string} value\n"
        " * @returns {string}\n"
        " */\n"
        "export function cast(value: number): number {\n"
        "  return value;\n"
        "}\n"
)

DOC_CONSISTENT = (
        "/**\n"
        " * @param {number} a\n"
        " * @param {number} b\n"
        " * @returns {number}\n"
        " */\n"
        "export function add(a: number, b: number): number {\n"
        "  return a + b;\n"
        "}\n"
)

PURE_VIOLATION = (
        "/**\n"
        " * Pure function: no side effects.\n"
        " * @param {string} url\n"
        " */\n"
        "export async function load(url: string) {\n"
        "  await fetch(url);\n"
        "}\n"
)

VAGUE_DECLARATION = (
        "// This should be safe and fast, probably fine.\n"
        "export function maybe(a: number) {\n"
        "  return a;\n"
        "}\n"
)


def _analyze(tmp_path, name, source):
    repo = build_scenario_repo(
        tmp_path / name, {"src/mod.ts": source}, {"src/marker.txt": "m\n"}
    )
    return analyze_repo(repo, [tmp_path / name])


def test_extra_jsdoc_param_is_mismatch(tmp_path):
    result = _analyze(tmp_path, "extra", DOC_PARAM_EXTRA)
    assert _mismatches(result, "CONS-PARAM-EXTRA")
    anchor = next(
        a for a in result.evidence
        if a.id in _mismatches(result, "CONS-PARAM-EXTRA")[0].evidence_ids
    )
    assert "@param" in anchor.snippet and "ghost" in anchor.snippet, "差异定位到声明行"


def test_missing_jsdoc_param_is_mismatch(tmp_path):
    result = _analyze(tmp_path, "missing", DOC_PARAM_MISSING)
    assert _mismatches(result, "CONS-PARAM-MISSING")


def test_type_mismatch_param_and_return(tmp_path):
    result = _analyze(tmp_path, "types", DOC_TYPE_MISMATCH)
    assert _mismatches(result, "CONS-PARAM-TYPE-MISMATCH")
    assert _mismatches(result, "CONS-RETURN-TYPE-MISMATCH")


def test_consistent_declaration_produces_no_finding(tmp_path):
    result = _analyze(tmp_path, "consistent", DOC_CONSISTENT)
    assert [r for r in _records(result) if r.outcome == "consistent"]
    assert not [f for f in result.findings if f.rule_id and f.rule_id.startswith("CONS-")], \
        "正常一致不得误报"


def test_pure_declaration_with_behavior_is_mismatch(tmp_path):
    result = _analyze(tmp_path, "pure", PURE_VIOLATION)
    purity = _mismatches(result, "CONS-PURITY-MISMATCH")
    assert purity
    anchor = next(a for a in result.evidence if a.id in purity[0].evidence_ids)
    assert anchor.start_line == 6, "证据指向 fetch 行（行为侧），不是函数首行（第 5 行）"


def test_vague_text_stays_unknown(tmp_path):
    result = _analyze(tmp_path, "vague", VAGUE_DECLARATION)
    unknown = [r for r in _records(result) if r.outcome == "unknown"]
    assert unknown and all(r.detail == "no_verifiable_declaration" for r in unknown)
    assert not [f for f in result.findings if f.rule_id and f.rule_id.startswith("CONS-")], \
        "含糊自然语言不得判定为一致或差异"


def test_no_jsdoc_stays_unknown(tmp_path):
    result = _analyze(tmp_path, "nojsdoc", "export function plain(a: number) {\n  return a;\n}\n")
    assert [r for r in _records(result) if r.outcome == "unknown"]
    assert not [f for f in result.findings if f.rule_id and f.rule_id.startswith("CONS-")]


def test_jsdoc_attaches_only_to_adjacent_function(tmp_path):
    source = (
        "/**\n"
        " * @param {number} a\n"
        " */\n"
        "export function documented(a: number) {\n"
        "  return a;\n"
        "}\n"
        "\n"
        "export function undocumented(x: number) {\n"
        "  return x;\n"
        "}\n"
    )
    result = _analyze(tmp_path, "adjacent", source)
    by_name = {r.method_name: r for r in _records(result)}
    assert by_name["documented"].outcome == "consistent"
    assert by_name["undocumented"].outcome == "unknown", "注释不得跨函数归属"


def test_multibranch_return_still_comparable(tmp_path):
    source = (
        "/**\n"
        " * @returns {number}\n"
        " */\n"
        "export function pick(flag: boolean): string {\n"
        "  if (flag) {\n"
        "    return \"a\";\n"
        "  }\n"
        "  return \"b\";\n"
        "}\n"
    )
    result = _analyze(tmp_path, "branch", source)
    assert _mismatches(result, "CONS-RETURN-TYPE-MISMATCH"), "多分支返回类型仍按注解比较"
