"""Project health report. The score is a transparent heuristic; every deduction is listed."""

from __future__ import annotations

from typing import Any

SEVERITY_PENALTY = {"critical": 15, "high": 8, "medium": 3, "low": 1, "info": 0}
MAX_FINDING_PENALTY = 50


def health_report(
    inventory: dict[str, Any],
    latest_analysis: dict[str, Any] | None = None,
    latest_test: dict[str, Any] | None = None,
) -> dict[str, Any]:
    score = 100.0
    deductions: list[dict[str, Any]] = []
    notes: list[str] = []

    def deduct(points: float, reason: str) -> None:
        nonlocal score
        if points <= 0:
            return
        score -= points
        deductions.append({"points": round(points, 1), "reason": reason})

    det = inventory.get("detection", {})
    git = inventory.get("git", {})
    if not git.get("is_repo"):
        deduct(10, "不是 Git 儲存庫，無法追蹤變更")
    elif git.get("dirty") is None:
        notes.append("Git 工作目錄狀態未知（儲存庫設定受限，需授權後才讀取）")
    elif git.get("dirty"):
        n = sum(git.get("counts", {}).values())
        deduct(min(10, 2 + n * 0.2), f"工作目錄有 {n} 個未提交變更")
    if not det.get("test_frameworks"):
        deduct(15, "未偵測到測試框架")
    deps = det.get("dependencies", [])
    unpinned = [d for d in deps if not d.get("pinned_version") and d.get("ecosystem") == "pypi"]
    if unpinned:
        deduct(min(10, len(unpinned) * 0.5), f"{len(unpinned)} 個 Python 依賴未固定版本")
    if inventory.get("truncated"):
        notes.append("檔案數超過上限，掃描結果不完整")
    if det.get("warnings"):
        deduct(min(5, len(det["warnings"])), f"{len(det['warnings'])} 個設定檔無法解析")

    if latest_analysis is None:
        notes.append("尚未執行靜態分析，分數未計入程式碼問題")
    else:
        counts = latest_analysis.get("open_by_severity", {})
        penalty = sum(SEVERITY_PENALTY.get(sev, 0) * n for sev, n in counts.items())
        deduct(min(MAX_FINDING_PENALTY, penalty), f"靜態分析未處理問題：{counts}")
        missing = [t for t, st in latest_analysis.get("tool_status", {}).items()
                   if st.get("state") in ("not_installed", "needs_authorization", "error")]
        if missing:
            notes.append(f"以下工具未執行，結果不代表通過：{', '.join(sorted(missing))}")

    if latest_test is None:
        notes.append("尚未執行測試")
    elif latest_test.get("failed", 0) or latest_test.get("errors", 0):
        deduct(15, f"最近一次測試失敗 {latest_test.get('failed', 0)}、錯誤 {latest_test.get('errors', 0)}")

    score = max(0.0, round(score, 1))
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "F"
    return {"score": score, "grade": grade, "deductions": deductions, "notes": notes}
