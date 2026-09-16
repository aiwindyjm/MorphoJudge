"""B02-R2-01 公开回归：类型引用边（accepts/returns）必须指向类型注解
所在的精确行，而不是方法首行。场景与私有审计脚本等价但不复制其内容。
"""

from __future__ import annotations

from morphojudge.parser import relations as rel
from morphojudge.parser.extract import extract_contracts, extract_symbols
from morphojudge.parser.typescript import parse_source
from morphojudge.pipeline import build_contract_usage_edges

SNAP_ID = "a" * 64


def _build(sources: dict[str, str]):
    parsed_by_path = {
        path: parse_source(path, text.encode("utf-8")) for path, text in sources.items()
    }
    symbols = {p: extract_symbols(s) for p, s in parsed_by_path.items()}
    contracts = {p: extract_contracts(s) for p, s in parsed_by_path.items()}
    index = rel.build_import_index(parsed_by_path)
    edges = build_contract_usage_edges(SNAP_ID, symbols, contracts, index)
    return edges, symbols, contracts


def test_multiline_signature_lines_are_exact():
    source = (
        "interface Input {}\n"
        "interface Output {}\n"
        "export function transform(\n"
        "    value: Input\n"
        "):\n"
        "    Output\n"
        "{\n"
        "    return {} as Output;\n"
        "}\n"
    )
    edges, _, _ = _build({"src/transform.ts": source})
    accepts = [e for e in edges if e.relation == "accepts" and e.target_id == "contract:src/transform.ts:Input"]
    returns = [e for e in edges if e.relation == "returns" and e.target_id == "contract:src/transform.ts:Output"]
    assert accepts and accepts[0].line == 4, "参数类型注解在第 4 行"
    assert returns and returns[0].line == 6, "返回类型注解在第 6 行"
    assert all(e.file_path == "src/transform.ts" for e in edges)


def test_same_type_referenced_twice_gets_distinct_lines():
    source = (
        "interface Pair {}\n"
        "export function combine(\n"
        "    left: Pair,\n"
        "    right: Pair\n"
        "): Pair {\n"
        "    return {} as Pair;\n"
        "}\n"
    )
    edges, _, _ = _build({"src/pair.ts": source})
    accepts = [
        e for e in edges
        if e.relation == "accepts" and e.target_id == "contract:src/pair.ts:Pair"
    ]
    assert sorted(e.line for e in accepts) == [3, 4], "两个参数各自的注解行"
    returns = [
        e for e in edges
        if e.relation == "returns" and e.target_id == "contract:src/pair.ts:Pair"
    ]
    assert returns and returns[0].line == 5


def test_unicode_prefix_keeps_line_numbers_byte_accurate():
    source = (
        "const 说明 = \"日本語🎉 多字节前缀\";\n"
        "// 注释行一\n"
        "// 注释行二\n"
        "interface Result {}\n"
        "export function compute(\n"
        "    input: Result\n"
        "): Result {\n"
        "    return {} as Result;\n"
        "}\n"
    )
    edges, _, _ = _build({"src/unicode.ts": source})
    accepts = [e for e in edges if e.relation == "accepts"]
    returns = [e for e in edges if e.relation == "returns"]
    assert accepts and accepts[0].line == 6
    assert returns and returns[0].line == 7


def test_imported_contract_reference_resolves_with_local_line():
    schema = "export interface LoginResponse {\n  sessionId: string;\n}\n"
    consumer = (
        "import { LoginResponse } from \"@/lib/schema\";\n"
        "export function refresh(\n"
        "    previous: LoginResponse\n"
        "): LoginResponse {\n"
        "    return previous;\n"
        "}\n"
    )
    edges, _, _ = _build({"src/lib/schema.ts": schema, "src/refresh.ts": consumer})
    target = "contract:src/lib/schema.ts:LoginResponse"
    accepts = [e for e in edges if e.relation == "accepts" and e.target_id == target]
    returns = [e for e in edges if e.relation == "returns" and e.target_id == target]
    assert accepts and accepts[0].line == 3 and accepts[0].file_path == "src/refresh.ts"
    assert returns and returns[0].line == 4


def test_edges_never_point_at_method_first_line_when_annotation_differs():
    """方法首行 ≠ 注解行时，任何类型引用边不得使用方法首行。"""
    source = (
        "interface A {}\n"
        "export function slow(\n"
        "    x: A\n"
        "): A {\n"
        "    return x;\n"
        "}\n"
    )
    edges, symbols, _ = _build({"src/slow.ts": source})
    symbol = symbols["src/slow.ts"][0]
    assert symbol.start_line == 2
    for edge in edges:
        if edge.relation in ("accepts", "returns"):
            assert edge.line != symbol.start_line, "类型引用边不得指向方法首行"


# ---------------------------------------------------------------------------
# r3 复核反例：同一注解内多个类型引用必须逐引用精确定位
# ---------------------------------------------------------------------------


def _references(sources):
    edges, _, _ = _build(sources)
    return sorted(
        (edge.relation.value, edge.target_id.rsplit(":", 1)[-1], edge.line)
        for edge in edges
    )


def test_union_members_get_their_own_lines():
    source = (
        "interface Alpha {}\n"          # 1
        "interface Beta {}\n"           # 2
        "export function pick(\n"       # 3
        "  value:\n"                    # 4
        "    Alpha |\n"                 # 5
        "    Beta\n"                    # 6
        "):\n"                          # 7
        "  Alpha |\n"                   # 8
        "  Beta\n"                      # 9
        "{ return value; }\n"           # 10
    )
    assert _references({"src/union.ts": source}) == [
        ("accepts", "Alpha", 5),
        ("accepts", "Beta", 6),
        ("returns", "Alpha", 8),
        ("returns", "Beta", 9),
    ]


def test_intersection_members_get_their_own_lines():
    source = (
        "interface Left {}\n"       # 1
        "interface Right {}\n"      # 2
        "function combine(x:\n"     # 3
        "  Left &\n"                # 4
        "  Right\n"                 # 5
        ") {}\n"                    # 6
    )
    assert _references({"src/inter.ts": source}) == [
        ("accepts", "Left", 4),
        ("accepts", "Right", 5),
    ]


def test_nested_generic_argument_gets_its_own_line():
    source = (
        "interface Item {}\n"       # 1
        "function batch(x:\n"       # 2
        "  Array<\n"                # 3
        "    Item\n"                # 4
        "  >\n"                     # 5
        ") {}\n"                    # 6
    )
    assert _references({"src/generic.ts": source}) == [("accepts", "Item", 4)]


def test_repeated_type_in_one_annotation_yields_distinct_edges():
    source = (
        "interface Dup {}\n"        # 1
        "function twice(x:\n"       # 2
        "  Dup |\n"                 # 3
        "  Dup\n"                   # 4
        ") {}\n"                    # 5
    )
    result = _references({"src/dup.ts": source})
    assert result == [("accepts", "Dup", 3), ("accepts", "Dup", 4)]
    edges, _, _ = _build({"src/dup.ts": source})
    edge_ids = [edge.id for edge in edges]
    assert len(edge_ids) == len(set(edge_ids)), "不同源码位置必须可区分"


def test_comments_strings_and_property_keys_are_not_type_references():
    source = (
        "interface Real {}\n"                                   # 1
        "function guarded(x:\n"                                 # 2
        "  Real | /* block comment Real */ \"literal\" \n"      # 3
        "  | { size: Real; label: string }\n"                   # 4（属性键非引用；Real 是）
        ") {}\n"                                                # 5
    )
    result = _references({"src/negative.ts": source})
    # 第 3 行的 Real 记为第 3 行；"literal" 字符串与注释不产生引用；
    # 第 4 行对象成员的 value 类型 Real 是引用，属性键 size/label 不是
    assert ("accepts", "Real", 3) in result
    assert ("accepts", "Real", 4) in result
    assert all(name == "Real" for _, name, _ in result), "字符串/属性键/注释不得产生伪类型引用"


def test_qualified_type_name_is_one_reference_not_three():
    source = (
        "namespace Outer { export interface Inner {} }\n"  # 1（v0.1 不解析命名空间成员）
        "interface Outer {}\n"                              # 2
        "function use(x: Outer.Inner) {}\n"                 # 3
    )
    result = _references({"src/qualified.ts": source})
    # 限定名整体记录；Outer.Inner 不匹配本地契约 Outer，因此无边——位置诚实不冒充
    assert result == []
