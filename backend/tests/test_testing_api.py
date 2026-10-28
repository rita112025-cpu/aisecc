from __future__ import annotations

import sys
import time
from pathlib import Path

import psutil
import pytest
from fastapi.testclient import TestClient

from app.db import read_session
from app.models import Job, TestRun
from tests.test_api_projects import client, wait_job  # noqa: F401

TESTS = '''
import os, time, pathlib

def test_ok():
    assert 1 + 1 == 2

def test_bad():
    assert {value} == 2

def test_skip():
    import pytest
    pytest.skip("略過示範")

def test_env_isolated():
    assert "AISECC_SECRET_FOR_TEST" not in os.environ
'''
SLOW = '''
import subprocess, sys, time, pathlib

def test_slow():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    pathlib.Path("child.pid").write_text(str(child.pid))
    time.sleep(120)
'''


@pytest.fixture
def pyproj(tmp_path: Path) -> Path:
    root = tmp_path / "測試 專案"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_demo.py").write_text(TESTS.format(value=3), encoding="utf-8")
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (root / "requirements.txt").write_text("pytest==8.3.5\npytest-cov==6.1.1\n", encoding="utf-8")
    return root


def _setup(client: TestClient, root: Path, argv: list[str] | None = None, timeout: int = 120,  # noqa: F811
           key: str = "pytest") -> int:
    pid = client.post("/api/projects", json={"path": str(root)}).json()["id"]
    body = {"framework": "pytest", "argv": argv or [sys.executable, "-m", "pytest", "tests/test_demo.py"],
            "cwd": ".", "timeout_sec": timeout}
    r = client.put(f"/api/projects/{pid}/test-commands/{key}", json=body)
    assert r.status_code == 200, r.text
    return pid


def _approve(client: TestClient, pid: int, key: str = "pytest") -> None:  # noqa: F811
    cmd = next(c for c in client.get(f"/api/projects/{pid}/test-commands").json() if c["key"] == key)
    r = client.post(f"/api/projects/{pid}/test-commands/{key}/approve", json={"content_hash": cmd["content_hash"]})
    assert r.status_code == 200 and r.json()["approved"] is True


def test_detect_suggests_unapproved_commands(client: TestClient, pyproj: Path) -> None:  # noqa: F811
    pid = client.post("/api/projects", json={"path": str(pyproj)}).json()["id"]
    cmds = client.post(f"/api/projects/{pid}/test-commands/detect").json()
    assert [c["key"] for c in cmds] == ["pytest"]
    assert cmds[0]["approved"] is False and cmds[0]["argv"][1:] == ["-m", "pytest"]
    assert not list(pyproj.rglob("*.xml")) and not (pyproj / ".pytest_cache").exists()


def test_unapproved_and_modified_commands_refused(client: TestClient, pyproj: Path) -> None:  # noqa: F811
    pid = _setup(client, pyproj)
    assert client.post(f"/api/projects/{pid}/test-runs", json={"command_key": "pytest"}).status_code == 403
    wrong = client.post(f"/api/projects/{pid}/test-commands/pytest/approve", json={"content_hash": "0" * 64})
    assert wrong.status_code == 409
    _approve(client, pid)
    # editing after approval revokes it
    client.put(f"/api/projects/{pid}/test-commands/pytest",
               json={"framework": "pytest", "argv": [sys.executable, "-m", "pytest", "-x"], "cwd": "."})
    assert client.post(f"/api/projects/{pid}/test-runs", json={"command_key": "pytest"}).status_code == 403


def test_command_validation(client: TestClient, pyproj: Path) -> None:  # noqa: F811
    pid = client.post("/api/projects", json={"path": str(pyproj)}).json()["id"]
    base = {"framework": "pytest", "argv": ["python"]}
    assert client.put(f"/api/projects/{pid}/test-commands/x", json=base | {"cwd": "../.."}).status_code == 400
    assert client.put(f"/api/projects/{pid}/test-commands/x", json=base | {"framework": "bash"}).status_code == 422
    assert client.put(f"/api/projects/{pid}/test-commands/BAD KEY", json=base).status_code == 400
    assert client.put(f"/api/projects/{pid}/test-commands/x", json=base | {"argv": []}).status_code == 422


def test_run_results_failure_analysis_rerun_and_compare(client: TestClient, pyproj: Path,  # noqa: F811
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AISECC_SECRET_FOR_TEST", "x")
    pid = _setup(client, pyproj)
    _approve(client, pid)
    job = wait_job(client, client.post(f"/api/projects/{pid}/test-runs",
                                       json={"command_key": "pytest", "coverage": True}).json()["job_id"], 120)
    assert job["status"] == "succeeded", job
    run = client.get(f"/api/test-runs/{job['result']['test_run_id']}").json()
    assert (run["status"], run["total"], run["passed"], run["failed"], run["skipped"]) == ("failed", 4, 2, 1, 1)
    assert run["failure_analysis"][0]["category"] == "assertion"
    assert run["coverage_percent"] is not None  # pytest-cov listed in requirements and available
    results = client.get(f"/api/test-runs/{run['id']}/results").json()
    assert results["items"][0]["status"] == "failed" and "test_bad" in results["items"][0]["name"]
    log = client.get(f"/api/jobs/{job['id']}/log").json()["text"]
    assert "test_bad" in log and "[結束]" in log
    assert not (pyproj / ".pytest_cache").exists()  # -p no:cacheprovider

    rerun_job = wait_job(client, client.post(f"/api/test-runs/{run['id']}/rerun-failed").json()["job_id"], 120)
    rerun = client.get(f"/api/test-runs/{rerun_job['result']['test_run_id']}").json()
    assert rerun["total"] == 1 and rerun["failed"] == 1 and rerun["rerun_of_id"] == run["id"]

    (pyproj / "tests" / "test_demo.py").write_text(TESTS.format(value=2), encoding="utf-8")
    fixed = wait_job(client, client.post(f"/api/projects/{pid}/test-runs",
                                         json={"command_key": "pytest"}).json()["job_id"], 120)
    diff = client.get("/api/test-runs/compare", params={"a": run["id"], "b": fixed["result"]["test_run_id"]}).json()
    assert diff["diff"]["fixed"] == ["tests.test_demo::test_bad"]


def test_coverage_unavailable_is_explained(client: TestClient, pyproj: Path) -> None:  # noqa: F811
    (pyproj / "requirements.txt").write_text("pytest==8.3.5\n", encoding="utf-8")
    pid = _setup(client, pyproj)
    _approve(client, pid)
    job = wait_job(client, client.post(f"/api/projects/{pid}/test-runs",
                                       json={"command_key": "pytest", "coverage": True}).json()["job_id"], 120)
    run = client.get(f"/api/test-runs/{job['result']['test_run_id']}").json()
    assert run["coverage_percent"] is None and "pytest-cov" in run["coverage"]["unavailable"]


def test_timeout_kills_tree(client: TestClient, pyproj: Path) -> None:  # noqa: F811
    (pyproj / "tests" / "test_demo.py").write_text(SLOW, encoding="utf-8")
    pid = _setup(client, pyproj, timeout=8)
    _approve(client, pid)
    job = wait_job(client, client.post(f"/api/projects/{pid}/test-runs", json={"command_key": "pytest"}).json()[
        "job_id"], 60)
    assert job["status"] == "timeout"
    with read_session() as s:
        assert s.query(TestRun).one().status == "timeout"
    child = int((pyproj / "child.pid").read_text())
    time.sleep(0.5)
    assert not psutil.pid_exists(child)


def test_cancel_and_duplicate_run(client: TestClient, pyproj: Path) -> None:  # noqa: F811
    (pyproj / "tests" / "test_demo.py").write_text(SLOW, encoding="utf-8")
    pid = _setup(client, pyproj)
    _approve(client, pid)
    jid = client.post(f"/api/projects/{pid}/test-runs", json={"command_key": "pytest"}).json()["job_id"]
    dup = client.post(f"/api/projects/{pid}/test-runs", json={"command_key": "pytest"})
    assert dup.status_code == 409 and dup.json()["job_id"] == jid
    deadline = time.monotonic() + 30
    while not (pyproj / "child.pid").exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert client.post(f"/api/jobs/{jid}/cancel").status_code == 200
    assert wait_job(client, jid, 30)["status"] == "cancelled"
    with read_session() as s:
        assert s.query(TestRun).one().status == "cancelled"
        assert s.get(Job, jid).pid is not None


def test_missing_executable_reports_error(client: TestClient, pyproj: Path) -> None:  # noqa: F811
    pid = _setup(client, pyproj, argv=["definitely-missing-python-xyz", "-m", "pytest"])
    _approve(client, pid)
    job = wait_job(client, client.post(f"/api/projects/{pid}/test-runs", json={"command_key": "pytest"}).json()[
        "job_id"], 30)
    run = client.get(f"/api/test-runs/{job['result']['test_run_id']}").json()
    assert run["status"] == "error" and run["total"] == 0


def test_scans_never_execute_tests(client: TestClient, pyproj: Path) -> None:  # noqa: F811
    marker = pyproj / "EXECUTED"
    (pyproj / "conftest.py").write_text(f"import pathlib\npathlib.Path(r'{marker}').write_text('x')\n",
                                        encoding="utf-8")
    pid = client.post("/api/projects", json={"path": str(pyproj)}).json()["id"]
    wait_job(client, client.post(f"/api/projects/{pid}/scan").json()["job_id"])
    wait_job(client, client.post(f"/api/projects/{pid}/analyze").json()["job_id"], 180)
    client.post(f"/api/projects/{pid}/test-commands/detect")
    assert not marker.exists()
    with read_session() as s:
        assert s.query(TestRun).count() == 0
