"""PARSE-003: import index, alias resolution, call/SQL/fetch/fs/process edges."""

from __future__ import annotations

from morphojudge.parser import relations as rel
from morphojudge.parser.typescript import parse_source

A_PATH = "src/pages/orders/page.tsx"
QUERIES_PATH = "src/db/queries.ts"


def _files(sources: dict[str, str]) -> dict:
    return {
        path: parse_source(path, text.encode("utf-8"))
        for path, text in sources.items()
    }


def _edges(sources: dict[str, str]):
    from morphojudge.parser.extract import extract_symbols

    parsed_by_path = _files(sources)
    symbols = {path: extract_symbols(parsed) for path, parsed in parsed_by_path.items()}
    index = rel.build_import_index(parsed_by_path)
    return rel.extract_relation_edges(parsed_by_path, symbols, index), index


def test_import_alias_resolution():
    sources = {
        QUERIES_PATH: "export function createOrder() {}\nexport function listOrders() {}\n",
        A_PATH: 'import { createOrder as make } from "@/db/queries";\n\nexport function handler() {\n  make();\n}\n',
    }
    edges, index = _edges(sources)
    ref = index[A_PATH][0]
    assert ref.local_name == "make"
    assert ref.imported_name == "createOrder"
    assert ref.resolved_path == QUERIES_PATH

    call = [e for e in edges if e.relation == "calls" and e.resolution == "resolved"]
    assert call, "aliased cross-file call must resolve"
    assert call[0].target_id and call[0].target_id.startswith(f"method:{QUERIES_PATH}:createOrder")


def test_relative_import_and_extension_resolution():
    sources = {
        "src/lib/notify.ts": "export function notify() {}\n",
        "src/lib/caller.ts": 'import { notify } from "./notify";\nexport function go() {\n  notify();\n}\n',
    }
    edges, index = _edges(sources)
    assert index["src/lib/caller.ts"][0].resolved_path == "src/lib/notify.ts"
    assert any(e.relation == "calls" and e.resolution == "resolved" for e in edges)


def test_import_cycle_does_not_break_extraction():
    sources = {
        "src/a.ts": 'import { b } from "./b";\nexport function a() {\n  b();\n}\n',
        "src/b.ts": 'import { a } from "./a";\nexport function b() {\n  a();\n}\n',
    }
    edges, _ = _edges(sources)
    resolved = [e for e in edges if e.relation == "calls" and e.resolution == "resolved"]
    assert len(resolved) == 2, "cycle a<->b yields two resolved edges without hanging"


def test_sql_read_and_write_edges():
    source = '''import { getDatabase } from "@/db/database";

const SELECT_ORDERS = "SELECT id FROM orders ORDER BY created_at DESC";
const DELETE_ORDER = "DELETE FROM orders WHERE id = ?";

export async function listOrders() {
  const db = getDatabase();
  await db.all(SELECT_ORDERS);
}

export async function deleteOrder(id: string) {
  const db = getDatabase();
  return db.run(DELETE_ORDER, [id]);
}
'''
    edges, _ = _edges({QUERIES_PATH: source})
    reads = [e for e in edges if e.relation == "reads" and e.target_label == "table:orders"]
    writes = [e for e in edges if e.relation == "writes" and e.target_label == "table:orders"]
    assert reads and all(e.resolution == "resolved" for e in reads)
    assert writes and all(e.resolution == "resolved" for e in writes)


def test_fetch_literal_const_and_dynamic():
    source = '''const BASE = "http://api.internal.example.invalid/v1";

export async function literal() {
  await fetch("https://telemetry.example.invalid/collect");
}

export async function templated(path: string) {
  await fetch(`${BASE}${path}`);
}

export async function dynamic(url: string) {
  await fetch(url);
}
'''
    edges, _ = _edges({"src/lib/api-client.ts": source})
    sends = [e for e in edges if e.relation == "sends"]
    resolved = [e for e in sends if e.resolution == "resolved"]
    candidate = [e for e in sends if e.resolution == "candidate"]
    unresolved = [e for e in sends if e.resolution == "unresolved" or e.resolution == "candidate" and e.target_label == "svc:unknown"]
    assert any(e.target_label == "svc:telemetry.example.invalid" for e in resolved)
    assert any(e.target_label == "svc:api.internal.example.invalid" for e in candidate), "const-substituted template stays candidate"
    dynamic_edge = [e for e in sends if e.target_label == "svc:unknown"]
    assert dynamic_edge and dynamic_edge[0].resolution == "candidate"


def test_fs_operations_with_constant_and_dynamic_paths():
    source = '''import { readFile, writeFile, unlink } from "node:fs/promises";

export const EXPORT_DIR = ".fixture-exports";

export async function readFixed() {
  return readFile("./config/static.json", "utf-8");
}

export async function writeDynamic(name: string, content: string) {
  await writeFile(`${EXPORT_DIR}/${name}`, content, "utf-8");
}

export async function removeDynamic(name: string) {
  await unlink(`${EXPORT_DIR}/${name}`);
}
'''
    edges, _ = _edges({"src/lib/files.ts": source})
    reads = [e for e in edges if e.relation == "reads" and e.target_label and e.target_label.startswith("file:")]
    writes = [e for e in edges if e.relation == "writes" and e.target_label and e.target_label.startswith("file:")]
    assert any(e.target_label == "file:./config/static.json" and e.resolution == "resolved" for e in reads)
    assert writes, "writeFile/unlink produce writes edges"
    assert all(e.resolution == "candidate" for e in writes), "dynamic paths stay candidate"


def test_child_process_registry_dispatch_is_candidate():
    shell = '''import { execSync } from "node:child_process";

export const shellHandlers: Record<string, typeof execSync> = {
  runBackup: execSync,
};
'''
    dynamic = '''import { shellHandlers } from "@/lib/shell";

export function invokeShellHandler(name: string, command: string) {
  const handler = shellHandlers[name];
  if (!handler) {
    throw new Error("unknown");
  }
  return handler(command, { encoding: "utf-8" });
}
'''
    edges, _ = _edges({"src/lib/shell.ts": shell, "src/lib/dynamic.ts": dynamic})
    process_edges = [e for e in edges if e.relation == "sends" and (e.target_label or "").startswith("process:")]
    assert process_edges, "registry alias call must produce a process edge"
    assert all(e.resolution == "candidate" for e in process_edges)
    assert any(e.target_label == "process:shellHandlers" for e in process_edges)


def test_subscript_dispatch_is_candidate():
    source = '''const handlers: Record<string, (p: unknown) => void> = {};

export function dispatch(name: string, payload: unknown) {
  const handler = handlers[name];
  if (handler) {
    handler(payload);
  }
}
'''
    edges, _ = _edges({"src/lib/dispatch.ts": source})
    # handlers 不是 child_process 注册表：落到 computed/unresolved 一侧，绝不 resolved
    calls = [e for e in edges if e.relation == "calls" or e.relation == "sends"]
    assert calls, "computed handler call produces an edge"
    assert all(e.resolution in ("candidate", "unresolved") for e in calls)


def test_dynamic_import_is_unresolved():
    source = '''export async function loadModuleByName(moduleName: string) {
  const loaded = await import(moduleName);
  return loaded;
}
'''
    edges, _ = _edges({"src/lib/dynamic.ts": source})
    dyn = [e for e in edges if e.note == "dynamic_import"]
    assert dyn and dyn[0].resolution == "unresolved" and dyn[0].target_id is None


def test_unknown_identifier_call_is_unresolved():
    source = "export function go(n: number) {\n  return mystery(n);\n}\n"
    edges, _ = _edges({"src/x.ts": source})
    unknown = [e for e in edges if e.note == "unknown_identifier"]
    assert unknown and unknown[0].resolution == "unresolved"


def test_third_party_member_call_not_overclaimed():
    source = '''import { useState } from "react";

export function comp() {
  const [x, setX] = useState(0);
  return setX;
}
'''
    edges, _ = _edges({"src/c.tsx": source})
    external = [e for e in edges if (e.note or "").startswith("external_")]
    assert all(e.resolution == "unresolved" for e in external)
    assert not any(e.resolution == "resolved" and e.relation == "calls" for e in edges)


def test_external_builtin_import_kind():
    sources = {
        "src/a.ts": 'import { readFile } from "node:fs/promises";\nimport React from "react";\nexport const x = 1;\n',
    }
    _, index = _edges(sources)
    kinds = {(ref.local_name, ref.external_kind) for ref in index["src/a.ts"]}
    assert ("readFile", "builtin") in kinds
    assert ("React", "package") in kinds
