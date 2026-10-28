from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings.model_copy(update={"frontend_dist": settings.data_dir / "no-dist"}))
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        yield c


def wait_job(client: TestClient, job_id: str, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish")


def test_health(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"


def test_rejects_foreign_host_header(client: TestClient) -> None:
    r = client.get("/api/health", headers={"host": "evil.example.com"})
    assert r.status_code == 403


def test_rejects_cross_site_post(client: TestClient, git_repo: Path) -> None:
    r = client.post("/api/projects", json={"path": str(git_repo)}, headers={"origin": "https://evil.example.com"})
    assert r.status_code == 403
    assert client.get("/api/projects").json() == []


def test_add_list_get_project(client: TestClient, git_repo: Path) -> None:
    r = client.post("/api/projects", json={"path": str(git_repo)})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["name"] == "範例 專案 repo" and p["is_git"] is True and p["allow_project_tools"] is False
    assert [x["id"] for x in client.get("/api/projects").json()] == [p["id"]]
    assert client.get(f"/api/projects/{p['id']}").json()["path"] == str(git_repo.resolve())


def test_add_duplicate_project_rejected(client: TestClient, git_repo: Path) -> None:
    assert client.post("/api/projects", json={"path": str(git_repo)}).status_code == 201
    r = client.post("/api/projects", json={"path": str(git_repo).upper()})
    assert r.status_code == 400 and "已經加入" in r.json()["detail"]


@pytest.mark.parametrize("bad", ["relative/path", "Z:\\definitely\\missing\\dir", ""])
def test_add_invalid_path(client: TestClient, bad: str) -> None:
    r = client.post("/api/projects", json={"path": bad})
    assert r.status_code in (400, 422)


def test_add_file_instead_of_dir(client: TestClient, git_repo: Path) -> None:
    r = client.post("/api/projects", json={"path": str(git_repo / "pyproject.toml")})
    assert r.status_code == 400


def test_get_missing_project_404(client: TestClient) -> None:
    assert client.get("/api/projects/999").status_code == 404


def test_patch_and_delete_project_keeps_files(client: TestClient, git_repo: Path) -> None:
    pid = client.post("/api/projects", json={"path": str(git_repo)}).json()["id"]
    r = client.patch(f"/api/projects/{pid}", json={"name": "新名稱", "allow_project_tools": True})
    assert r.json()["name"] == "新名稱" and r.json()["allow_project_tools"] is True
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(f"/api/projects/{pid}").status_code == 404
    assert (git_repo / "src" / "calc.py").exists()


def test_tree_and_traversal_blocked(client: TestClient, git_repo: Path) -> None:
    pid = client.post("/api/projects", json={"path": str(git_repo)}).json()["id"]
    names = {i["name"] for i in client.get(f"/api/projects/{pid}/tree").json()["items"]}
    assert {"src", "tests", "pyproject.toml"} <= names
    assert client.get(f"/api/projects/{pid}/tree", params={"path": "src"}).json()["items"][0]["name"] == "calc.py"
    assert client.get(f"/api/projects/{pid}/tree", params={"path": "../.."}).status_code == 400


def test_git_endpoints(client: TestClient, git_repo: Path) -> None:
    pid = client.post("/api/projects", json={"path": str(git_repo)}).json()["id"]
    (git_repo / "src" / "calc.py").write_text("changed\n", encoding="utf-8")
    info = client.get(f"/api/projects/{pid}/git").json()
    assert info["dirty"] and info["branch"] == "main"
    assert client.get(f"/api/projects/{pid}/git/log").json()[0]["subject"] == "init"
    d = client.get(f"/api/projects/{pid}/git/diff").json()
    assert "+changed" in d["diff"]
    assert client.get(f"/api/projects/{pid}/git/diff", params={"base": "--evil"}).status_code == 400


def test_git_endpoint_empty_repo(client: TestClient, tmp_path: Path) -> None:
    from tests.conftest import git

    repo = tmp_path / "empty repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    pid = client.post("/api/projects", json={"path": str(repo)}).json()["id"]
    r = client.get(f"/api/projects/{pid}/git")
    assert r.status_code == 200 and r.json()["head"] is None and r.json()["is_repo"] is True


def test_inventory_scan_job_and_compare(client: TestClient, git_repo: Path) -> None:
    pid = client.post("/api/projects", json={"path": str(git_repo)}).json()["id"]
    j1 = client.post(f"/api/projects/{pid}/scan").json()["job_id"]
    assert wait_job(client, j1)["status"] == "succeeded"
    (git_repo / "requirements.txt").write_text("flask==3.0.0\n", encoding="utf-8")
    j2 = client.post(f"/api/projects/{pid}/scan").json()["job_id"]
    assert wait_job(client, j2)["status"] == "succeeded"
    scans = client.get("/api/scans", params={"project_id": pid}).json()
    assert len(scans) == 2 and scans[0]["health_score"] is not None
    diff = client.get("/api/scans/compare", params={"a": scans[1]["id"], "b": scans[0]["id"]}).json()["diff"]
    assert diff["dependencies_added"] == ["pypi:flask"]
    assert diff["file_count"] == {"from": 4, "to": 5}
    log = client.get(f"/api/jobs/{j1}/log").json()
    assert "檔案數" in log["text"] and log["done"] is True
    assert client.get(f"/api/projects/{pid}").json()["languages"] == ["python"]


def test_scan_missing_project_path(client: TestClient, tmp_path: Path) -> None:
    d = tmp_path / "gone"
    d.mkdir()
    pid = client.post("/api/projects", json={"path": str(d)}).json()["id"]
    d.rmdir()
    assert client.post(f"/api/projects/{pid}/scan").status_code == 410
    assert client.get("/api/projects").json()[0]["path_exists"] is False


def test_cancel_unknown_and_finished_job(client: TestClient, git_repo: Path) -> None:
    assert client.post("/api/jobs/nope/cancel").status_code == 404
    pid = client.post("/api/projects", json={"path": str(git_repo)}).json()["id"]
    jid = client.post(f"/api/projects/{pid}/scan").json()["job_id"]
    wait_job(client, jid)
    assert client.post(f"/api/jobs/{jid}/cancel").status_code == 409


def test_jobs_survive_app_restart(settings: Settings, git_repo: Path) -> None:
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        pid = c.post("/api/projects", json={"path": str(git_repo)}).json()["id"]
        jid = c.post(f"/api/projects/{pid}/scan").json()["job_id"]
        wait_job(c, jid)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as c:
        assert c.get(f"/api/jobs/{jid}").json()["status"] == "succeeded"
        assert len(c.get("/api/scans").json()) == 1
