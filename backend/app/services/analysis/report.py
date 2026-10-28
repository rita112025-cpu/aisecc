"""JSON and HTML scan reports written to data/reports."""

from __future__ import annotations

import json
from html import escape
from typing import Any

from app.config import get_settings
from app.db import read_session
from app.models import Finding, Project, Scan

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
CSS = """
body{font-family:"Segoe UI","Microsoft JhengHei",sans-serif;color:#1f2328;margin:24px;line-height:1.5}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin-top:28px}.muted{color:#59636e}
.warn{background:#fff8c5;border:1px solid #d4a72c;padding:8px 12px;border-radius:6px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border-bottom:1px solid #d1d9e0;padding:6px 8px;text-align:left;vertical-align:top}th{background:#f6f8fa}
.sev{font-weight:600;text-transform:uppercase;font-size:12px}.sev-critical{color:#82071e}.sev-high{color:#cf222e}
.sev-medium{color:#9a6700}.sev-low{color:#0969da}.sev-info{color:#59636e}code{font-family:Consolas,monospace}
"""


def report_data(scan_id: int) -> dict[str, Any]:
    with read_session() as s:
        scan = s.get(Scan, scan_id)
        if scan is None:
            raise ValueError("掃描紀錄不存在")
        project = s.get(Project, scan.project_id)
        findings = s.query(Finding).filter_by(scan_id=scan_id).all()
        rows = [{
            "id": f.id, "tool": f.tool, "rule_id": f.rule_id, "severity": f.severity, "category": f.category,
            "confidence": f.confidence, "file": f.file_path, "line": f.line, "column": f.column,
            "message": f.message, "fingerprint": f.fingerprint, "occurrence": f.occurrence,
            "triage_status": f.triage_status, "triage_note": f.triage_note,
        } for f in findings]
        rows.sort(key=lambda r: (SEVERITY_ORDER.get(r["severity"], 9), r["file"] or "", r["line"] or 0))
        return {
            "generated_by": "AI Software Engineering Control Center",
            "disclaimer": (
                "靜態分析結果為疑似問題，需人工確認；"
                "未執行（needs_authorization/not_installed/error）的工具不代表通過。"
            ),
            "project": {"id": project.id, "name": project.name, "path": project.path},
            "scan": {"id": scan.id, "kind": scan.kind, "status": scan.status, "branch": scan.branch,
                     "commit": scan.commit_sha, "created_at": scan.created_at.isoformat(),
                     "finished_at": scan.finished_at.isoformat() if scan.finished_at else None},
            "summary": {k: v for k, v in (scan.summary or {}).items() if k != "tool_status"},
            "tool_status": scan.tool_status,
            "findings": rows,
        }


def write_reports(scan_id: int) -> dict[str, str]:
    data = report_data(scan_id)
    out = get_settings().reports_dir
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"scan-{scan_id}.json"
    html_path = out / f"scan-{scan_id}.html"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(render_html(data), encoding="utf-8")
    return {"json": str(json_path), "html": str(html_path)}


def _e(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def _counts(d: dict[str, Any] | None, sev: bool = False) -> str:
    return "　".join(
        (f'<span class="sev sev-{_e(k)}">{_e(k)}</span> {_e(v)}' if sev else f"{_e(k)} {_e(v)}")
        for k, v in (d or {}).items())


def render_html(data: dict[str, Any]) -> str:
    """Every interpolated value goes through html.escape; there is no raw HTML from data."""
    scan, project, summary = data["scan"], data["project"], data["summary"]
    tools = "".join(
        f"<tr><td>{_e(n)}</td><td>{_e(st.get('state'))}</td><td>{_e(st.get('findings'))}</td>"
        f"<td>{_e(st.get('duration_sec'))}</td><td>{_e(st.get('message'))}</td></tr>"
        for n, st in (data["tool_status"] or {}).items())
    rows = "".join(
        f'<tr><td class="sev sev-{_e(f["severity"])}">{_e(f["severity"])}</td><td>{_e(f["category"])}</td>'
        f'<td>{_e(f["tool"])}<br><code>{_e(f["rule_id"])}</code></td>'
        f'<td><code>{_e(f["file"] or "—")}{":" + _e(f["line"]) if f["line"] else ""}</code></td>'
        f'<td>{_e(f["message"])}</td><td>{_e(f["triage_status"])}'
        f'{"<br><span class=muted>" + _e(f["triage_note"]) + "</span>" if f["triage_note"] else ""}</td>'
        f'<td>{"工具回報" if f["confidence"] == "reported" else "啟發式推測"}</td></tr>'
        for f in data["findings"])
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>掃描報告 #{_e(scan["id"])} — {_e(project["name"])}</title><style>{CSS}</style></head><body>
<h1>靜態分析與安全掃描報告</h1>
<div class="muted">專案：{_e(project["name"])}（<code>{_e(project["path"])}</code>）｜掃描 #{_e(scan["id"])}｜狀態：
{_e(scan["status"])}｜分支：{_e(scan["branch"] or "—")}｜Commit：<code>{_e((scan["commit"] or "—")[:12])}</code>｜
時間：{_e(scan["created_at"])}</div>
<p class="warn">{_e(data["disclaimer"])}</p>
<h2>摘要</h2><table>
<tr><th>總數</th><td>{_e(summary.get("total"))}</td><th>未處理</th><td>{_e(summary.get("open"))}</td></tr>
<tr><th>依嚴重度</th><td colspan="3">{_counts(summary.get("by_severity"), sev=True)}</td></tr>
<tr><th>依類別</th><td colspan="3">{_counts(summary.get("by_category"))}</td></tr></table>
<h2>工具執行狀態</h2><table><tr><th>工具</th><th>狀態</th><th>發現數</th><th>耗時(秒)</th><th>說明</th></tr>{tools}</table>
<h2>發現（{len(data["findings"])}）</h2><table>
<tr><th>嚴重度</th><th>類別</th><th>工具／規則</th><th>位置</th><th>訊息</th><th>判定</th><th>信心</th></tr>{rows}</table>
</body></html>
"""
