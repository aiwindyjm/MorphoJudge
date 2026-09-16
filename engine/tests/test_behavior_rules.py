"""B03-02 / B03-03：行为规则正反例。

正常合法调用是事实线索（fact/low），不是漏洞结论；注释与字符串中的
危险词、同名局部函数、方法短名称都不得制造行为；动态目标保持
candidate/unresolved，不升级。
"""

from __future__ import annotations

from conftest import analyze_repo, build_scenario_repo

SAFE_MANIFEST = {"human_feature_mapping": []}


def _findings(result, rule_prefix="BEH-"):
    return [f for f in result.findings if f.rule_id and f.rule_id.startswith(rule_prefix)]


def test_legal_call_is_fact_not_vulnerability(tmp_path):
    source = 'export async function health() {\n  const r = await fetch("https://status.example.invalid/ok");\n  return r;\n}\n'
    repo = build_scenario_repo(tmp_path / "legal", {"src/status.ts": source}, {"src/marker.txt": "m\n"})
    result = analyze_repo(repo, [tmp_path / "legal"])
    network = [f for f in _findings(result) if f.rule_id == "BEH-NETWORK"]
    assert network
    assert all(f.kind.value == "fact" for f in network), "合法调用是事实线索"
    assert all(f.impact.value == "low" for f in network), "不得凭调用本身给出高影响"


def test_danger_words_in_comments_and_strings_do_not_trigger(tmp_path):
    source = (
        "// TODO: run child_process exec rm -rf and fetch evil\n"
        "export const note = \"call execSync('rm -rf /') and spawn shells\";\n"
        "export function pure(a: number) {\n  return a + 1;\n}\n"
    )
    repo = build_scenario_repo(tmp_path / "words", {"src/notes.ts": source}, {"src/marker.txt": "m\n"})
    result = analyze_repo(repo, [tmp_path / "words"])
    assert _findings(result) == [], "注释/字符串中的危险词不得制造行为线索"


def test_same_name_local_function_is_not_builtin_behavior(tmp_path):
    source = (
        "function fetch(url: string) {\n  return url;\n}\n"
        "export function execSync(cmd: string) {\n  return cmd;\n}\n"
        "export function go() {\n  return fetch(\"x\") + execSync(\"y\");\n}\n"
    )
    repo = build_scenario_repo(tmp_path / "shadow", {"src/shadow.ts": source}, {"src/marker.txt": "m\n"})
    result = analyze_repo(repo, [tmp_path / "shadow"])
    assert [f for f in _findings(result) if f.rule_id in ("BEH-NETWORK", "BEH-SHELL")] == [], \
        "本地同名函数调用是普通调用，不是网络/Shell 行为"


def test_dynamic_url_stays_candidate_not_resolved(tmp_path):
    source = (
        "export async function dyn(url: string) {\n"
        "  await fetch(url);\n"
        "}\n"
    )
    repo = build_scenario_repo(tmp_path / "dyn", {"src/dyn.ts": source}, {"src/marker.txt": "m\n"})
    result = analyze_repo(repo, [tmp_path / "dyn"])
    network = [f for f in _findings(result) if f.rule_id == "BEH-NETWORK"]
    assert network, "动态地址的网络行为仍要列出"
    edges = [
        e for e in result.target_map.edges
        if e.relation.value == "sends" and e.target_id.startswith("svc:unknown")
    ]
    assert edges and all(e.resolution.value in ("candidate", "unresolved") for e in edges), \
        "动态目标不得升级为 resolved"


def test_registry_dispatch_shell_stays_candidate(tmp_path):
    shell = 'import { execSync } from "node:child_process";\n\nexport const handlers: Record<string, typeof execSync> = {\n  backup: execSync,\n};\n'
    caller = 'import { handlers } from "@/lib/shell";\n\nexport function run(name: string, cmd: string) {\n  const h = handlers[name];\n  return h(cmd, { encoding: "utf-8" });\n}\n'
    repo = build_scenario_repo(
        tmp_path / "registry",
        {"src/lib/shell.ts": shell, "src/caller.ts": caller},
        {"src/marker.txt": "m\n"},
    )
    result = analyze_repo(repo, [tmp_path / "registry"])
    shell_findings = [f for f in _findings(result) if f.rule_id == "BEH-SHELL"]
    assert shell_findings, "注册表分发必须产生 Shell 线索"
    process_edges = [
        e for e in result.target_map.edges
        if e.target_id.startswith("data:process:")
    ]
    assert process_edges and all(e.resolution.value == "candidate" for e in process_edges)


def test_multiline_nested_call_positions_are_exact(tmp_path):
    source = (
        "export async function nested() {\n"
        "  await fetch(\n"
        "    `https://multi.example.invalid/${\"a\"}/x`,\n"
        "    { method: \"POST\" },\n"
        "  );\n"
        "}\n"
    )
    repo = build_scenario_repo(tmp_path / "multi", {"src/multi.ts": source}, {"src/marker.txt": "m\n"})
    result = analyze_repo(repo, [tmp_path / "multi"])
    network = [f for f in _findings(result) if f.rule_id == "BEH-NETWORK"]
    assert network
    anchor = next(a for a in result.evidence if a.id in network[0].evidence_ids)
    assert anchor.start_line == 2, "调用点定位在 call_expression 起始行"
    assert "fetch" in anchor.snippet


def test_permission_module_check_via_manifest(tmp_path):
    permissions = (
        "export function requireRole(role: string, minimum: string): boolean {\n"
        "  return role === minimum;\n"
        "}\n"
    )
    page = (
        'import { requireRole } from "@/lib/perm";\n\n'
        "export async function dashboard(role: string) {\n"
        "  requireRole(role, \"admin\");\n"
        "  return \"ok\";\n"
        "}\n"
    )
    manifest = {
        "human_feature_mapping": [],
        "required_entities": {"permission_modules": ["src/lib/perm.ts"]},
    }
    repo = build_scenario_repo(
        tmp_path / "perm",
        {"src/lib/perm.ts": permissions, "src/pages/admin/page.tsx": page},
        {"src/marker.txt": "m\n"},
    )
    result = analyze_repo(repo, [tmp_path / "perm"], manifest)
    checks = [f for f in _findings(result) if f.rule_id == "BEH-PERM-CHECK"]
    assert checks and all(f.kind.value == "fact" for f in checks)


def test_is_admin_short_name_never_triggers_permission(tmp_path):
    source = (
        "export function isAdmin(user: { role: string }) {\n"
        "  return user.role === \"admin\";\n"
        "}\n"
        "export function panel(user: { role: string }) {\n"
        "  if (!isAdmin(user)) {\n"
        "    return \"denied\";\n"
        "  }\n"
        "  return \"granted\";\n"
        "}\n"
    )
    repo = build_scenario_repo(tmp_path / "shortname", {"src/admin-util.ts": source}, {"src/marker.txt": "m\n"})
    result = analyze_repo(repo, [tmp_path / "shortname"])
    assert [f for f in _findings(result) if f.rule_id.startswith("BEH-PERM")] == [], \
        "短名称 isAdmin 不构成权限检查判定"


def test_removed_guard_is_change_hint_not_bypass(tmp_path):
    permissions = "export function requireRole(role: string, minimum: string): boolean {\n  return role === minimum;\n}\n"
    base_page = (
        'import { requireRole } from "@/lib/perm";\n\n'
        "export async function dashboard(role: string) {\n"
        "  requireRole(role, \"admin\");\n"
        "  return \"ok\";\n"
        "}\n"
    )
    target_page = (
        'import { requireRole } from "@/lib/perm";\n\n'
        "export async function dashboard(role: string) {\n"
        "  return \"ok\";\n"
        "}\n"
    )
    manifest = {
        "human_feature_mapping": [],
        "required_entities": {"permission_modules": ["src/lib/perm.ts"]},
    }
    repo = build_scenario_repo(
        tmp_path / "guard",
        {"src/lib/perm.ts": permissions, "src/dashboard.ts": base_page},
        {"src/dashboard.ts": target_page},
    )
    result = analyze_repo(repo, [tmp_path / "guard"], manifest)
    removed = [f for f in _findings(result) if f.rule_id == "BEH-PERM-GUARD-REMOVED"]
    assert removed, "守卫调用消失必须给出变更提示"
    assert all(f.kind.value == "rule_hint" for f in removed)
    assert all(f.impact.value == "medium" for f in removed)
    anchors = [a for a in result.evidence if a.id in removed[0].evidence_ids]
    assert anchors and anchors[0].side.value == "old", "消失的守卫证据在 base 侧"
    assert "requireRole" in anchors[0].snippet


def test_method_removal_with_guard_also_reports(tmp_path):
    permissions = "export function guard(role: string): boolean {\n  return true;\n}\n"
    base_page = (
        'import { guard } from "@/lib/perm";\n\n'
        "export async function entry(role: string) {\n"
        "  guard(role);\n"
        "  return 1;\n"
        "}\n"
    )
    manifest = {
        "human_feature_mapping": [],
        "required_entities": {"permission_modules": ["src/lib/perm.ts"]},
    }
    repo = build_scenario_repo(
        tmp_path / "gone-method",
        {"src/lib/perm.ts": permissions, "src/entry.ts": base_page},
        {"src/entry.ts": None},
    )
    result = analyze_repo(repo, [tmp_path / "gone-method"], manifest)
    removed = [f for f in _findings(result) if f.rule_id == "BEH-PERM-GUARD-REMOVED"]
    assert removed
    paths = [p for p in result.impact_paths if p.limit_note == "origin_method_absent_on_target_side"]
    assert paths, "方法整体删除时影响路径起点缺失要如实记录"


def test_unicode_source_positions(tmp_path):
    source = (
        "const 说明 = \"日語🎉\";\n"
        "export async function 送信() {\n"
        "  await fetch(\"https://unicode.example.invalid/通知\");\n"
        "}\n"
    )
    repo = build_scenario_repo(tmp_path / "uni", {"src/uni.ts": source}, {"src/marker.txt": "m\n"})
    result = analyze_repo(repo, [tmp_path / "uni"])
    network = [f for f in _findings(result) if f.rule_id == "BEH-NETWORK"]
    assert network
    anchor = next(a for a in result.evidence if a.id in network[0].evidence_ids)
    assert anchor.start_line == 3
    assert "unicode.example.invalid" in anchor.snippet
