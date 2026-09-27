"""RPT-001: report generation and export (Batch-07).

GET /v1/analyses/{id}/report?format=json  → complete structured report
GET /v1/analyses/{id}/report?format=markdown → PRD §3.3 six-section report

All content comes from already-committed SQLite artifacts; nothing is
recomputed or fetched from the host filesystem. Source snippets are the
archived EvidenceAnchor.snippet text, never re-read from disk.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from ...contracts.domain import SCHEMA_VERSION
from ...contracts.errors import ErrorCode, MorphoJudgeError
from ...db.repository import AnalysisRepository

router = APIRouter(prefix="/v1/analyses", tags=["report"])

CATEGORY_LABELS = {
    "behavior_network": "网络访问", "behavior_shell": "系统命令", "behavior_file": "文件操作",
    "behavior_permission": "权限线索", "dependency": "依赖变化", "consistency": "一致性", "structure": "结构",
}
IMPACT_LABELS = {"high": "高影响", "medium": "中影响", "low": "低影响", "unknown": "影响未知"}
KIND_LABELS = {"fact": "事实", "rule_hint": "规则提示"}
REVIEW_LABELS = {"unreviewed": "待复核", "needs-investigation": "需调查", "confirmed": "已确认", "dismissed": "误报"}


def _build_report_data(repository: AnalysisRepository, analysis_id: str) -> dict:
    """Assemble the full report document from committed SQLite artifacts."""
    row = repository.get_analysis(analysis_id)
    if row is None:
        raise MorphoJudgeError(ErrorCode.ANALYSIS_NOT_FOUND, f"analysis not found: {analysis_id}")
    status = str(row["status"])
    if status not in ("completed", "completed_with_limits"):
        raise MorphoJudgeError(
            ErrorCode.ANALYSIS_NOT_READY,
            "report requires a completed analysis",
            details={"status": status},
        )
    document = repository.result_document(analysis_id)
    if document is None:
        raise MorphoJudgeError(
            ErrorCode.ANALYSIS_NOT_READY,
            "report artifacts are not persisted",
            details={"status": status},
        )
    identity = document.get("snapshot", {}).get("identity", {})
    summary = repository.rules_document(analysis_id) or document

    findings_total, findings_rows = repository.list_findings(analysis_id, limit=500, offset=0)
    findings = []
    review_map = {str(r["finding_id"]): r for r in repository.list_reviews(analysis_id)}
    for fr in findings_rows:
        f = json.loads(str(fr["evidence_ids_json"]))
        review = review_map.get(str(fr["finding_id"]))
        findings.append({
            "id": str(fr["finding_id"]), "category": str(fr["category"]),
            "category_label": CATEGORY_LABELS.get(str(fr["category"]), str(fr["category"])),
            "kind": str(fr["kind"]), "kind_label": KIND_LABELS.get(str(fr["kind"]), str(fr["kind"])),
            "impact": str(fr["impact"]), "impact_label": IMPACT_LABELS.get(str(fr["impact"]), str(fr["impact"])),
            "rule_id": fr["rule_id"], "unresolved_reason": fr["unresolved_reason"],
            "evidence_ids": f, "review_state": str(fr["review_state"]),
            "review_state_label": REVIEW_LABELS.get(str(fr["review_state"]), str(fr["review_state"])),
            "review_note": str(review["note"]) if review and review["note"] else "",
        })

    evidence_map = {}
    for eid in {e for finding in findings for e in finding["evidence_ids"]}:
        er = repository.get_evidence(analysis_id, eid)
        if er:
            evidence_map[eid] = {
                "id": str(er["evidence_id"]), "path": str(er["path"]), "side": str(er["side"]),
                "start_line": int(er["start_line"]), "end_line": int(er["end_line"]),
                "snippet": str(er["snippet"]), "rule_id": er["rule_id"], "commit": er["commit_sha"],
            }

    decisions_total, decisions_rows = repository.list_decisions(analysis_id, limit=500, offset=0)
    coverage_decisions = [{
        "path": str(d["path"]), "status": str(d["status"]), "reason": str(d["reason"]), "rule_id": d["rule_id"],
    } for d in decisions_rows]

    explanations = []
    for exp_row in repository.list_explanations(analysis_id):
        exp = dict(exp_row)
        explanations.append({
            "id": str(exp.get("explanation_id", "")), "subject_type": str(exp.get("subject_type", "")),
            "subject_id": str(exp.get("subject_id", "")), "provider": str(exp.get("provider", "")),
            "model": str(exp.get("model", "")), "status": str(exp.get("status", "")),
            "claims": json.loads(exp.get("claims_json", "[]")), "uncertainty": str(exp.get("uncertainty", "")),
            "errors": json.loads(exp.get("errors_json", "[]")),
        })

    impact_paths = document.get("impact_paths", [])
    for path in impact_paths:
        path.setdefault("limit_note", None)

    diff_files = []
    for entry in (document.get("diff") or {}).get("files", []):
        diff_files.append({
            "path": entry.get("path", ""), "status": entry.get("status", ""),
            "old_path": entry.get("old_path"), "additions": entry.get("additions"),
            "deletions": entry.get("deletions"), "binary": entry.get("binary", False),
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_id": analysis_id,
        "status": status,
        "identity": {
            "snapshot_id": identity.get("snapshot_id"), "base_commit": identity.get("base_commit"),
            "target_commit": identity.get("target_commit"), "rules_version": identity.get("rules_version"),
            "base_ref": str(row["base_ref"]), "target_ref": str(row["target_ref"]),
        },
        "stage_coverage": document.get("stage_coverage", []),
        "coverage": {
            "summary": (summary or document).get("selection_summary", {}),
            "decisions": coverage_decisions,
        },
        "findings": findings,
        "evidence": evidence_map,
        "impact_paths": impact_paths,
        "explanations": explanations,
        "reviews": [{
            "finding_id": str(r["finding_id"]), "state": str(r["state"]),
            "state_label": REVIEW_LABELS.get(str(r["state"]), str(r["state"])),
            "note": str(r["note"]), "updated_at": str(r["updated_at"]),
        } for r in repository.list_reviews(analysis_id)],
        "limits": document.get("limits", []),
        "diff_files": diff_files,
        "counts": {
            "findings": findings_total,
            "evidence": repository.evidence_count(analysis_id),
            "explanations": len(explanations),
            "reviews": len(review_map),
            "changed_files": len(diff_files),
        },
    }


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _md_fence(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {"ts": "typescript", "tsx": "tsx", "js": "javascript", "jsx": "jsx",
            "py": "python", "json": "json", "yaml": "yaml", "md": "markdown"}.get(ext, "")


def _render_markdown(report: dict) -> str:
    """PRD §3.3 six-section report. No '安全'/'无风险'/'漏洞' conclusion words."""
    ident = report["identity"]
    counts = report["counts"]
    lines: list[str] = []
    lines.append(f"# MorphoJudge 审计报告")
    lines.append("")
    lines.append(f"分析 ID：`{report['analysis_id']}`")
    lines.append(f"快照：`{ident.get('snapshot_id', 'N/A')}`")
    lines.append(f"对比：`{ident.get('base_ref', '?')} → {ident.get('target_ref', '?')}`")
    lines.append(f"提交：`{str(ident.get('base_commit', ''))[:12]}… → {str(ident.get('target_commit', ''))[:12]}…`")
    lines.append(f"状态：**{report['status']}**")
    lines.append(f"契约版本：{report['schema_version']}")
    lines.append("")

    # === 1. 变更摘要 ===
    lines.append("## 1. 变更摘要")
    lines.append("")
    lines.append(f"- 变更文件：{counts['changed_files']} 个")
    lines.append(f"- 发现：{counts['findings']} 条（含未精确定位项）")
    lines.append(f"- 证据锚点：{counts['evidence']} 个")
    lines.append(f"- 模型解释：{counts['explanations']} 条")
    lines.append(f"- 人工复核：{counts['reviews']} 条")
    for df in report["diff_files"]:
        add = f"+{df['additions']}" if df.get("additions") is not None else "?"
        dele = f"-{df['deletions']}" if df.get("deletions") is not None else "?"
        lines.append(f"  - `{df['path']}`（{df['status']}，{add}/{dele}）")
    lines.append("")

    # === 2. 高影响关注项 ===
    lines.append("## 2. 高影响关注项")
    lines.append("")
    high = [f for f in report["findings"] if f["impact"] in ("high", "medium")]
    if high:
        for f in high:
            lines.append(f"### {f['impact_label']} · {f['category_label']} · {f['rule_id'] or f['id']}")
            lines.append(f"- 类型：{f['kind_label']}；复核状态：{f['review_state_label']}")
            if f["unresolved_reason"]:
                lines.append(f"- **证据未能精确定位**：{f['unresolved_reason']}")
            elif f["evidence_ids"]:
                lines.append(f"- 证据：{', '.join(f['evidence_ids'])}")
            lines.append("")
    else:
        lines.append("未发现高影响关注项。**未发现不等于安全**；请结合覆盖限制判断。")
        lines.append("")

    # === 3. 证据 ===
    lines.append("## 3. 证据")
    lines.append("")
    for eid, ev in report["evidence"].items():
        side_label = "旧侧" if ev["side"] == "old" else "新侧" if ev["side"] == "new" else "上下文"
        lines.append(f"### {eid}")
        lines.append(f"- 文件：`{ev['path']}`（{side_label}，行 {ev['start_line']}–{ev['end_line']}）")
        if ev.get("commit"):
            lines.append(f"- 提交：`{ev['commit'][:12]}…`")
        if ev.get("rule_id"):
            lines.append(f"- 规则：`{ev['rule_id']}`")
        lines.append("")
        lang = _md_fence(ev["path"])
        lines.append(f"```{lang}")
        lines.append(ev["snippet"])
        lines.append("```")
        lines.append("")

    # === 4. 模型解释 ===
    lines.append("## 4. 模型解释")
    lines.append("")
    if report["explanations"]:
        for exp in report["explanations"]:
            lines.append(f"### {exp['id']}（{exp['provider']} / {exp['model']}）")
            lines.append(f"- 主体：{exp['subject_type']} `{exp['subject_id']}`")
            lines.append(f"- 状态：{exp['status']}")
            for claim in exp.get("claims", []):
                kind_label = {"restatement": "证据复述", "inference": "模型推断", "unknown": "未知项"}.get(claim.get("kind", ""), claim.get("kind", ""))
                lines.append(f"- **{kind_label}**：{claim.get('text', '')}")
                if claim.get("evidence_ids"):
                    lines.append(f"  - 引用：{', '.join(claim['evidence_ids'])}")
            if exp.get("uncertainty"):
                lines.append(f"- 不确定项：{exp['uncertainty']}")
            if exp.get("errors"):
                lines.append(f"- 错误：{'; '.join(exp['errors'])}")
            lines.append("")
    else:
        lines.append("本次分析未生成模型解释（解释为可选独立任务）。")
        lines.append("")

    # === 5. 人工复核 ===
    lines.append("## 5. 人工复核")
    lines.append("")
    if report["reviews"]:
        lines.append("| 发现 | 复核状态 | 备注 | 更新时间 |")
        lines.append("| --- | --- | --- | --- |")
        for r in report["reviews"]:
            lines.append(f"| `{r['finding_id']}` | {r['state_label']} | {_md_escape(r['note'][:50])} | {r['updated_at'][:19]} |")
        lines.append("")
        lines.append("> **注意**：`已确认` 表示该发现描述成立，不代表软件整体安全。")
    else:
        lines.append("尚无人工复核记录。")
    lines.append("")

    # === 6. 覆盖限制 ===
    lines.append("## 6. 覆盖限制")
    lines.append("")
    lines.append("### 阶段覆盖")
    lines.append("")
    lines.append("| 阶段 | 状态 | 完成 | 失败 | 受限 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for sc in report["stage_coverage"]:
        lines.append(f"| {sc['stage']} | {sc['status']} | {sc['completed']} | {sc['failed']} | {sc['limited']} |")
    lines.append("")
    lines.append("### 文件选择决策")
    lines.append("")
    lines.append("| 文件 | 状态 | 原因 | 规则 |")
    lines.append("| --- | --- | --- | --- |")
    for d in report["coverage"]["decisions"]:
        lines.append(f"| `{_md_escape(d['path'])}` | {d['status']} | {_md_escape(d['reason'][:60])} | {d['rule_id'] or '—'} |")
    lines.append("")
    if report["limits"]:
        lines.append("### 覆盖限制与未检查范围")
        lines.append("")
        for limit in report["limits"]:
            lines.append(f"- {limit}")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("> 本报告由 MorphoJudge 确定性分析引擎生成。模型解释为独立可选任务，不改变确定性事实。未发现不等于安全；静态线索不代表实际执行。")
    return "\n".join(lines)


@router.get("/{analysis_id}/report")
def get_report(
    analysis_id: str,
    request: Request,
    format: Annotated[str, Query(pattern="^(json|markdown)$")] = "json",
) -> Response:
    repository: AnalysisRepository = request.app.state.repository
    report = _build_report_data(repository, analysis_id)
    if format == "markdown":
        markdown = _render_markdown(report)
        return Response(
            content=markdown,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="morphojudge-report-{analysis_id.replace(":", "-")}.md"'},
        )
    return Response(
        content=json.dumps(report, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="morphojudge-report-{analysis_id.replace(":", "-")}.json"'},
    )
