from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services.jobs import read_log
from tests.test_api_projects import client  # noqa: F401

TEXT = "測試中文 log ✓ end\n"


@pytest.mark.parametrize("limit", range(1, 12))
def test_read_log_never_splits_utf8(tmp_path: Path, limit: int) -> None:
    p = tmp_path / "job.log"
    p.write_bytes(TEXT.encode("utf-8"))
    out, offset = "", 0
    for _ in range(200):
        chunk, offset = read_log(str(p), offset, limit)
        if not chunk:
            break
        assert "�" not in chunk
        out += chunk
    assert out == TEXT


@pytest.mark.parametrize("path", ["/api/health", "/", "/api/projects"])
def test_security_headers(client: TestClient, path: str) -> None:  # noqa: F811
    r = client.get(path)
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "script-src 'self'" in csp
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"


def test_rejected_requests_also_carry_headers(client: TestClient) -> None:  # noqa: F811
    r = client.post("/api/projects", json={"path": "x"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and r.headers["x-frame-options"] == "DENY"


def test_report_html_is_sandboxed(client: TestClient, tmp_path: Path) -> None:  # noqa: F811
    from tests.test_api_projects import wait_job

    d = tmp_path / "rep"
    d.mkdir()
    (d / "a.py").write_text("x = 1\n", encoding="utf-8")
    pid = client.post("/api/projects", json={"path": str(d)}).json()["id"]
    sid = wait_job(client, client.post(f"/api/projects/{pid}/analyze", json={"tools": ["ruff"]}).json()["job_id"],
                   120)["result"]["scan_id"]
    csp = client.get(f"/api/scans/{sid}/report.html").headers["content-security-policy"]
    assert "sandbox" in csp and "default-src 'none'" in csp and "script-src" not in csp
