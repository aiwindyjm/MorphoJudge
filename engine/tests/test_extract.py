"""PARSE-002: symbols, routes, events, contracts, feature mapping."""

from __future__ import annotations

import pytest

from morphojudge.parser.extract import (
    extract_contracts,
    extract_page_events,
    extract_routes,
    extract_symbols,
    load_feature_mapping,
    route_pattern_for,
)
from morphojudge.parser.typescript import parse_source


def _parse(path: str, text: str):
    return parse_source(path, text.encode("utf-8"))


def test_symbols_include_functions_methods_and_const_arrows():
    source = """import { x } from "./x";

export function alpha(a: number): string {
  return String(a);
}

function beta() {}

class Service {
  process(input: string): void {}
}

export const gamma = (n: number): number => n + 1;
"""
    parsed = _parse("src/svc.ts", source)
    symbols = extract_symbols(parsed)
    names = {symbol.name: symbol for symbol in symbols}
    assert {"alpha", "beta", "process", "gamma"} <= set(names)
    assert names["alpha"].exported is True
    assert names["beta"].exported is False
    assert names["process"].kind == "method"
    assert names["process"].enclosing_class == "Service"
    assert names["gamma"].kind == "const"
    assert names["gamma"].return_type == "number"
    assert names["alpha"].params[0].name == "a"
    assert names["alpha"].params[0].type_text == "number"
    assert names["alpha"].node_id == "method:src/svc.ts:alpha:3"


def test_same_name_symbols_in_different_files_have_distinct_ids():
    a = extract_symbols(_parse("src/a.ts", "export function dup() {}\n"))
    b = extract_symbols(_parse("src/b.ts", "export function dup() {}\n"))
    assert a[0].node_id != b[0].node_id
    assert "src/a.ts" in a[0].node_id and "src/b.ts" in b[0].node_id


def test_route_patterns_from_paths():
    routes = extract_routes(
        [
            "src/pages/login/page.tsx",
            "src/pages/orders/page.tsx",
            "src/pages/orders/[id]/page.tsx",
            "src/lib/util.ts",
            "src/pages/admin/settings/page.tsx",
            # pages-router 平铺文件（Next.js 真实约定）
            "src/pages/admin/audit-log.tsx",
            "src/pages/about.tsx",
            # 下划线开头不是路由
            "src/pages/_app.tsx",
        ]
    )
    patterns = {route.pattern: route.path for route in routes}
    assert patterns["/login"] == "src/pages/login/page.tsx"
    assert patterns["/orders/[id]"] == "src/pages/orders/[id]/page.tsx"
    assert patterns["/admin/settings"] == "src/pages/admin/settings/page.tsx"
    assert patterns["/admin/audit-log"] == "src/pages/admin/audit-log.tsx"
    assert patterns["/about"] == "src/pages/about.tsx"
    assert "/_app" not in patterns
    assert "src/lib/util.ts" not in patterns.values()
    assert route_pattern_for("src/lib/util.ts") is None


def test_page_events_from_default_export_return_object():
    source = """import { submitLogin } from "@/lib/auth";

export function helperLocal() {}

export default function LoginPage() {
  return {
    tag: "LoginPageFixture",
    submit: submitLogin,
    onLocal: helperLocal,
  };
}
"""
    parsed = _parse("src/pages/login/page.tsx", source)
    events = extract_page_events(parsed)
    handlers = {event.event: event.handler for event in events}
    assert handlers["submit"] == "submitLogin"
    assert handlers["onLocal"] == "helperLocal"
    assert "tag" not in handlers, "string-literal pairs are not handlers"


def test_no_default_export_means_no_events():
    parsed = _parse("src/lib/plain.ts", "export function only() {}\n")
    assert extract_page_events(parsed) == []


def test_contracts_interfaces_and_type_aliases():
    source = """export interface LoginResponse {
  sessionId: string;
}

export type Role = "admin" | "member";
"""
    parsed = _parse("src/lib/schema.ts", source)
    contracts = extract_contracts(parsed)
    by_name = {contract.name: contract for contract in contracts}
    assert set(by_name) == {"LoginResponse", "Role"}
    assert by_name["LoginResponse"].kind == "interface"
    assert by_name["Role"].kind == "type_alias"
    assert by_name["LoginResponse"].start_line == 1
    assert by_name["Role"].node_id == "contract:src/lib/schema.ts:Role"


def test_feature_mapping_requires_human_confirmation():
    manifest = {
        "human_feature_mapping": [
            {"page": "src/pages/x/page.tsx", "feature_id": "F-x", "feature": "X", "confirmed_by": "human", "events": ["a"]},
            {"page": "src/pages/y/page.tsx", "feature_id": "F-y", "feature": "Y", "confirmed_by": "model", "events": []},
        ]
    }
    with pytest.raises(ValueError):
        load_feature_mapping(manifest)

    good = {
        "human_feature_mapping": [
            {"page": "src/pages/x/page.tsx", "feature_id": "F-x", "feature": "X", "confirmed_by": "human", "events": ["a", "b"]}
        ]
    }
    mappings = load_feature_mapping(good)
    assert mappings[0].events == ("a", "b")
    assert mappings[0].node_id == "feature:F-x"


def test_symbols_from_partial_tree_do_not_crash_on_broken_file():
    parsed = _parse("src/broken/invalid.ts", "export function broken(: string {\n  const x = ;\n}\n")
    symbols = extract_symbols(parsed)  # 只要求不崩溃；可能为空或部分
    assert isinstance(symbols, list)
