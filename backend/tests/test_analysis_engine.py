from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import read_session, write_session
from app.models import Finding, Project, Scan
from app.services.analysis import tools as tools_mod
from app.services.analysis.base import RawFinding
from app.services.analysis.engine import AnalysisOptions, persist_findings, run_analysis
from app.services.analysis.tools import ToolContext, authorised
from tests.test_api_projects import client, wait_job  # noqa: F401 - fixture re-export

VULN_PY = (
    "import subprocess\n\n\n"
    "def run(cmd):\n"
    "    return subprocess.call(cmd, shell=True)\n\n\n"
    'API_TOKEN = "Zq8vR2kLm9Xw4TnB7pQs"\n'
)


@pytest.fixture
def py_project(tmp_path: Path) -> Path:
    root = tmp_path / "分析 專案"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "app.py").write_text(VULN_PY, encoding="utf-8")
    (root / "requirements.txt").write_text("requests>=2\n", encoding="utf-8")
    web = root / "web"
    web.mkdir()
    (web / "package.json").write_text('{"devDependencies":{"eslint":"9.0.0"}}', encoding="utf-8")
    (web / "index.js").write_text("export const x = 1;\n", encoding="utf-8")
    return root


def test_project_tier_tools_need_authorization_and_never_spawn(db: Path, py_project: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    spawned: list[list[str]] = []
    real = tools_mod.run_command

    def spy(argv, opts):  # noqa: ANN001
        spawned.append([str(a) for a in argv])
        return real(argv, opts)

    monkeypatch.setattr(tools_mod, "run_command", spy)
    _, status, _ = run_analysis(py_project, AnalysisOptions())
    assert status["eslint"]["state"] == "needs_authorization"
    assert status["npm-audit"]["state"] == "needs_authorization"
    assert status["pip-audit"]["state"] == "needs_authorization"
    assert status["semgrep"]["state"] in ("not_installed", "needs_authorization")
    assert not any("eslint" in " ".join(a) or "npm" in " ".join(a) or "pip-audit" in " ".join(a) for a in spawned)
    assert status["ruff"]["state"] == "ok" and status["bandit"]["state"] == "ok"


def test_authorised_matrix() -> None:
    ctx = ToolContext(Path("."), [], [], False, False, 10)
    tiers = {t.name: authorised(t, ctx)[0] for t in tools_mod.TOOLS}
    assert tiers == {"ruff": True, "bandit": True, "eslint": False, "tsc": False, "npm-audit": False,
                     "pip-audit": False, "semgrep": False}
    ctx2 = ToolContext(Path("."), [], [], True, False, 10)
    assert authorised(next(t for t in tools_mod.TOOLS if t.name == "eslint"), ctx2)[0]
    assert not authorised(next(t for t in tools_mod.TOOLS if t.name == "npm-audit"), ctx2)[0]


def test_real_ruff_and_bandit_findings(db: Path, py_project: Path) -> None:
    findings, status, meta = run_analysis(py_project, AnalysisOptions(tools=["ruff", "bandit"]))
    tools = {f.tool for f in findings}
    assert {"ruff", "bandit", "builtin-secrets"} <= tools
    bandit = [f for f in findings if f.tool == "bandit"]
    assert any(f.rule_id == "B602" and f.file_path == "pkg/app.py" and f.line == 5 for f in bandit)
    assert all(f.file_path and not Path(f.file_path).is_absolute() for f in findings if f.tool in ("ruff", "bandit"))
    assert meta["languages"] == ["javascript", "python"]
    assert "eslint" not in status  # restricted tool list


def test_tool_missing_is_not_pass(db: Path, py_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_mod, "VENV_SCRIPTS", py_project / "nope")
    monkeypatch.setattr(tools_mod.shutil, "which", lambda name: None)
    _, status, _ = run_analysis(py_project, AnalysisOptions(tools=["ruff", "bandit"]))
    assert status["ruff"]["state"] == "not_installed" and status["bandit"]["state"] == "not_installed"


def test_tool_timeout_reported(db: Path, py_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procs import ProcResult

    monkeypatch.setattr(tools_mod, "run_command",
                        lambda argv, opts: ProcResult(list(argv), None, "timeout", 1.0, error="執行逾時"))
    _, status, _ = run_analysis(py_project, AnalysisOptions(tools=["ruff"]))
    assert status["ruff"]["state"] == "timeout"


def test_malformed_tool_output_is_error(db: Path, py_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.procs import ProcResult

    monkeypatch.setattr(tools_mod, "run_command",
                        lambda argv, opts: ProcResult(list(argv), 0, "finished", 0.1, output="not json at all"))
    _, status, _ = run_analysis(py_project, AnalysisOptions(tools=["bandit"]))
    assert status["bandit"]["state"] == "error"


def _scan(project_id: int) -> int:
    with write_session() as s:
        scan = Scan(project_id=project_id, kind="analysis", status="running")
        s.add(scan)
        s.flush()
        return scan.id


def test_persist_is_idempotent_and_keeps_duplicates(db: Path) -> None:
    with write_session() as s:
        p = Project(name="p", path="C:/p", path_key="c:/p")
        s.add(p)
        s.flush()
        pid = p.id
    sid = _scan(pid)
    fs = [RawFinding("t", "r", "high", "secret", "m", "a.py", 1, fingerprint="fp", occurrence=0),
          RawFinding("t", "r", "high", "secret", "m", "a.py", 2, fingerprint="fp", occurrence=1)]
    persist_findings(sid, pid, fs)
    summary = persist_findings(sid, pid, fs)
    assert summary["total"] == 2


def test_api_analyze_triage_diff_and_reports(client: TestClient, py_project: Path) -> None:  # noqa: F811
    pid = client.post("/api/projects", json={"path": str(py_project)}).json()["id"]
    j1 = client.post(f"/api/projects/{pid}/analyze", json={"tools": ["ruff", "bandit"]}).json()["job_id"]
    job = wait_job(client, j1, 120)
    assert job["status"] == "succeeded", job
    sid1 = job["result"]["scan_id"]
    page = client.get("/api/findings", params={"project_id": pid, "category": "secret"}).json()
    assert page["total"] >= 1
    secret = page["items"][0]
    assert "Zq8vR2kLm9Xw4TnB7pQs" not in json.dumps(page, ensure_ascii=False)

    # false positive requires a reason, then is carried forward
    assert client.post(f"/api/findings/{secret['id']}/triage", json={"status": "false_positive"}).status_code == 400
    r = client.post(f"/api/findings/{secret['id']}/triage", json={"status": "false_positive", "note": "測試用假金鑰"})
    assert r.json()["triage_status"] == "false_positive"

    # move code down 3 lines -> same findings, no new/resolved
    (py_project / "pkg" / "app.py").write_text("\n\n\n" + VULN_PY, encoding="utf-8")
    j2 = client.post(f"/api/projects/{pid}/analyze", json={"tools": ["ruff", "bandit"]}).json()["job_id"]
    sid2 = wait_job(client, j2, 120)["result"]["scan_id"]
    diff = client.get("/api/scans/compare", params={"a": sid1, "b": sid2}).json()["diff"]
    assert diff["counts"]["new"] == 0 and diff["counts"]["resolved"] == 0 and diff["counts"]["moved"] > 0
    carried = client.get("/api/findings", params={"scan_id": sid2, "category": "secret"}).json()["items"][0]
    assert carried["triage_status"] == "false_positive" and carried["triage_note"] == "測試用假金鑰"

    # search / filter / sort
    q = client.get("/api/findings", params={"scan_id": sid2, "q": "shell", "sort": "file"}).json()
    assert q["total"] >= 1 and all("shell" in (i["message"] + i["rule_id"]).lower() or "shell" in (
        i["file_path"] or "") for i in q["items"])
    sev = client.get("/api/findings", params={"scan_id": sid2, "severity": "high,critical"}).json()
    assert all(i["severity"] in ("high", "critical") for i in sev["items"])

    # reports
    html = client.get(f"/api/scans/{sid2}/report.html")
    assert html.status_code == 200 and "掃描報告" in html.text and "<script" not in html.text
    js = client.get(f"/api/scans/{sid2}/report.json").json()
    assert js["scan"]["id"] == sid2 and set(js["tool_status"]) >= {"ruff", "bandit", "builtin-secrets"}
    assert any(f["triage_status"] == "false_positive" for f in js["findings"])
    assert client.get(f"/api/scans/{sid2}/report.exe").status_code == 404

    scan = client.get(f"/api/scans/{sid2}").json()
    assert scan["status"] == "completed"  # only ruff/bandit requested and both ran


def test_api_full_scan_is_partial_when_tools_skipped(client: TestClient, py_project: Path) -> None:  # noqa: F811
    pid = client.post("/api/projects", json={"path": str(py_project)}).json()["id"]
    job = wait_job(client, client.post(f"/api/projects/{pid}/analyze").json()["job_id"], 180)
    assert job["status"] == "succeeded"
    scan = client.get(f"/api/scans/{job['result']['scan_id']}").json()
    assert scan["status"] == "partial"
    assert scan["tool_status"]["eslint"]["state"] == "needs_authorization"


def test_api_analyze_rejects_unknown_tool_and_duplicate(client: TestClient, py_project: Path) -> None:  # noqa: F811
    pid = client.post("/api/projects", json={"path": str(py_project)}).json()["id"]
    assert client.post(f"/api/projects/{pid}/analyze", json={"tools": ["rm -rf"]}).status_code == 400
    r1 = client.post(f"/api/projects/{pid}/analyze")
    r2 = client.post(f"/api/projects/{pid}/analyze")
    assert r1.status_code == 202 and r2.status_code in (202, 409)
    if r2.status_code == 409:
        assert r2.json()["job_id"] == r1.json()["job_id"]
    wait_job(client, r1.json()["job_id"], 180)


def test_compare_scans_of_different_projects_rejected(client: TestClient, tmp_path: Path) -> None:  # noqa: F811
    ids = []
    for name in ("a", "b"):
        d = tmp_path / name
        d.mkdir()
        pid = client.post("/api/projects", json={"path": str(d)}).json()["id"]
        ids.append(wait_job(client, client.post(f"/api/projects/{pid}/scan").json()["job_id"])["result"]["scan_id"])
    assert client.get("/api/scans/compare", params={"a": ids[0], "b": ids[1]}).status_code == 400


def test_origin_null_post_rejected(client: TestClient, tmp_path: Path) -> None:  # noqa: F811
    r = client.post("/api/projects", json={"path": str(tmp_path)}, headers={"origin": "null"})
    assert r.status_code == 403


def test_findings_require_scope(client: TestClient) -> None:  # noqa: F811
    assert client.get("/api/findings").status_code == 400
    with read_session() as s:
        assert s.query(Finding).count() == 0
