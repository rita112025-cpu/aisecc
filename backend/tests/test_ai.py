from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.services.ai.providers import (
    AIError,
    AINotConfigured,
    OllamaProvider,
    OpenAICompatibleProvider,
    build_provider,
)
from app.services.ai.schema import InvalidAIResponse, parse_response
from app.services.settings_store import AppSettings
from tests.conftest import git
from tests.test_api_projects import wait_job

FIX = {"summary": "避免除以零", "root_cause": "b 可能為 0", "risk": "low",
       "edits": [{"path": "src/calc.py", "search": "    return a / b", "replace": "    return a / b if b else 0"}]}


# ---- schema --------------------------------------------------------------------------------

def test_parse_valid_and_fenced() -> None:
    assert parse_response(json.dumps(FIX)).edits[0].path == "src/calc.py"
    assert parse_response("```json\n" + json.dumps(FIX) + "\n```").summary == "避免除以零"


@pytest.mark.parametrize("raw", [
    "not json", "[]", "{}", json.dumps(FIX | {"edits": []}),
    json.dumps(FIX | {"edits": [FIX["edits"][0] | {"path": "../outside.py"}]}),
    json.dumps(FIX | {"edits": [FIX["edits"][0] | {"path": "C:/Windows/x.py"}]}),
    json.dumps(FIX | {"edits": [FIX["edits"][0] | {"path": ".git/config"}]}),
    json.dumps(FIX | {"edits": [FIX["edits"][0]] * 11}),
    json.dumps(FIX | {"edits": [FIX["edits"][0] | {"evil": 1}]}),
    json.dumps(FIX | {"risk": "none"}),
])
def test_parse_rejects(raw: str) -> None:
    with pytest.raises(InvalidAIResponse):
        parse_response(raw)


# ---- providers -----------------------------------------------------------------------------

def test_ollama_generate_and_status() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/generate":
            body = json.loads(req.content)
            assert body["format"] == "json" and body["stream"] is False
            return httpx.Response(200, json={"response": json.dumps(FIX)})
        return httpx.Response(200, json={"models": [{"name": "qwen2.5-coder:latest"}]})

    p = OllamaProvider("http://127.0.0.1:11434", "qwen2.5-coder", 5, httpx.MockTransport(handler))
    assert json.loads(p.generate("x"))["risk"] == "low"
    assert p.status()["model_available"] is True and p.external is False


def test_ollama_errors() -> None:
    p = OllamaProvider("http://127.0.0.1:11434", "m", 5, httpx.MockTransport(lambda r: httpx.Response(500, text="x")))
    with pytest.raises(AIError):
        p.generate("x")

    def timeout(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(AIError, match="逾時"):
        OllamaProvider("http://127.0.0.1:11434", "m", 1, httpx.MockTransport(timeout)).generate("x")
    bad = OllamaProvider("http://127.0.0.1:11434", "m", 5, httpx.MockTransport(lambda r: httpx.Response(200, text="?")))
    with pytest.raises(AIError):
        bad.generate("x")
    with pytest.raises(AINotConfigured):
        OllamaProvider("http://10.0.0.5:11434", "m", 5)


def test_build_provider_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AINotConfigured):
        build_provider(AppSettings())
    with pytest.raises(AINotConfigured):
        build_provider(AppSettings(ai_provider="ollama"))
    ext = AppSettings(ai_provider="openai_compatible", external_ai_base_url="https://api.example.com/v1",
                      external_ai_model="m")
    with pytest.raises(AINotConfigured, match="允許外部 AI"):
        build_provider(ext)
    monkeypatch.setenv("AISECC_EXTERNAL_AI_KEY", "k-123")
    p = build_provider(ext.model_copy(update={"allow_external_ai": True}))
    assert isinstance(p, OpenAICompatibleProvider) and p.external and p.api_key == "k-123"


def test_openai_compatible_sends_bearer() -> None:
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(FIX)}}]})

    p = OpenAICompatibleProvider("https://api.example.com/v1", "m", "k", 5, httpx.MockTransport(handler))
    assert json.loads(p.generate("x"))["summary"] and seen["auth"] == "Bearer k"


# ---- end-to-end repair workflow ------------------------------------------------------------

class FakeProvider:
    name, model, external = "fake", "fake-model", False

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0) if self.responses else "{}"

    def status(self) -> dict:
        return {"provider": "fake", "model": self.model}


@pytest.fixture
def ai_client(settings, git_repo: Path):  # noqa: ANN001, ANN201
    from app.main import create_app

    app = create_app(settings.model_copy(update={"frontend_dist": settings.data_dir / "none"}))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.app = app
        pid = c.post("/api/projects", json={"path": str(git_repo)}).json()["id"]
        yield c, pid, git_repo


def _propose(c: TestClient, pid: int, provider: FakeProvider, **body) -> dict:
    c.app.state.ai_provider_factory = lambda: provider
    r = c.post(f"/api/projects/{pid}/repairs", json={"target_file": "src/calc.py", "instruction": "修正除以零"} | body)
    assert r.status_code == 202, r.text
    wait_job(c, r.json()["job_id"], 120)
    return c.get(f"/api/repairs/{r.json()['proposal_id']}").json()


def test_not_configured_keeps_other_modules_working(ai_client) -> None:  # noqa: ANN001
    c, pid, _ = ai_client
    assert c.get("/api/ai/status").json()["configured"] is False
    r = c.post(f"/api/projects/{pid}/repairs", json={"target_file": "src/calc.py"})
    assert r.status_code == 409 and "未設定" in r.json()["detail"]
    assert wait_job(c, c.post(f"/api/projects/{pid}/scan").json()["job_id"])["status"] == "succeeded"


def test_propose_apply_revert(ai_client, settings) -> None:  # noqa: ANN001
    c, pid, repo = ai_client
    original = (repo / "src" / "calc.py").read_bytes()
    head = git(repo, "rev-parse", "HEAD").strip()
    p = _propose(c, pid, FakeProvider(json.dumps(FIX)))
    assert p["status"] == "validated", p
    assert "+    return a / b if b else 0" in p["diff"] and p["risk"]["level"] in ("low", "medium", "high")
    assert (repo / "src" / "calc.py").read_bytes() == original  # nothing applied yet
    assert Path(settings.workspaces_dir / f"repair-{p['id']}").is_dir()

    sha = p["validation"]["diff_sha256"]
    assert c.post(f"/api/repairs/{p['id']}/apply", json={"diff_sha256": sha}).status_code == 422
    assert c.post(f"/api/repairs/{p['id']}/apply", json={"confirm": True, "diff_sha256": "0" * 64}).status_code == 409
    r = c.post(f"/api/repairs/{p['id']}/apply", json={"confirm": True, "diff_sha256": sha})
    assert r.status_code == 200, r.text
    assert b"if b else 0" in (repo / "src" / "calc.py").read_bytes()
    assert git(repo, "rev-parse", "HEAD").strip() == head  # never commits
    assert not (settings.workspaces_dir / f"repair-{p['id']}").exists()

    assert c.post(f"/api/repairs/{p['id']}/revert").json()["status"] == "reverted"
    assert (repo / "src" / "calc.py").read_bytes() == original
    assert c.post(f"/api/repairs/{p['id']}/revert").status_code == 409


def test_apply_refused_if_user_modified_file(ai_client) -> None:  # noqa: ANN001
    c, pid, repo = ai_client
    p = _propose(c, pid, FakeProvider(json.dumps(FIX)))
    (repo / "src" / "calc.py").write_text("# 使用者的修改\n", encoding="utf-8")
    r = c.post(f"/api/repairs/{p['id']}/apply", json={"confirm": True, "diff_sha256": p["validation"]["diff_sha256"]})
    assert r.status_code == 409
    assert (repo / "src" / "calc.py").read_text(encoding="utf-8") == "# 使用者的修改\n"


def test_revert_refused_if_changed_after_apply(ai_client) -> None:  # noqa: ANN001
    c, pid, repo = ai_client
    p = _propose(c, pid, FakeProvider(json.dumps(FIX)))
    c.post(f"/api/repairs/{p['id']}/apply", json={"confirm": True, "diff_sha256": p["validation"]["diff_sha256"]})
    (repo / "src" / "calc.py").write_text("# later edit\n", encoding="utf-8")
    assert c.post(f"/api/repairs/{p['id']}/revert").status_code == 409
    assert (repo / "src" / "calc.py").read_text(encoding="utf-8") == "# later edit\n"


def test_apply_refused_after_new_commit(ai_client) -> None:  # noqa: ANN001
    c, pid, repo = ai_client
    p = _propose(c, pid, FakeProvider(json.dumps(FIX)))
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-q", "-m", "more")
    r = c.post(f"/api/repairs/{p['id']}/apply", json={"confirm": True, "diff_sha256": p["validation"]["diff_sha256"]})
    assert r.status_code == 409 and "HEAD" in r.json()["detail"]


def test_dirty_repo_rejected(ai_client) -> None:  # noqa: ANN001
    c, pid, repo = ai_client
    (repo / "notes.txt").write_text("untracked", encoding="utf-8")
    c.app.state.ai_provider_factory = lambda: FakeProvider(json.dumps(FIX))
    r = c.post(f"/api/projects/{pid}/repairs", json={"target_file": "src/calc.py"})
    assert r.status_code == 400 and "未提交" in r.json()["detail"]


@pytest.mark.parametrize(("response", "reason"), [
    ("這不是 JSON", "JSON"),
    (json.dumps(FIX | {"edits": [FIX["edits"][0] | {"path": "../../outside.py"}]}), "路徑"),
    (json.dumps(FIX | {"edits": [FIX["edits"][0] | {"path": ".gitignore", "search": "*.tmp"}]}), None),
    (json.dumps(FIX | {"edits": [FIX["edits"][0] | {"path": "new_file.py"}]}), "追蹤"),
    (json.dumps(FIX | {"edits": [FIX["edits"][0] | {"search": "not present"}]}), "0 次"),
    (json.dumps(FIX | {"edits": [FIX["edits"][0] | {"replace": "    return a / (b"}]}), None),
])
def test_invalid_ai_output_never_touches_project(ai_client, settings, response: str, reason: str | None) -> None:  # noqa: ANN001
    c, pid, repo = ai_client
    before = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts}
    p = _propose(c, pid, FakeProvider(response))
    if "gitignore" in response:
        assert p["status"] == "validated"  # editing another tracked file is allowed
    else:
        assert p["status"] == "invalid", p
        if reason:
            assert reason in (p["error"] or "")
        assert not (settings.workspaces_dir / f"repair-{p['id']}").exists()
    after = {p_: p_.read_bytes() for p_ in repo.rglob("*") if p_.is_file() and ".git" not in p_.parts}
    assert before == after
    assert not (repo.parent / "outside.py").exists()


def test_ai_call_budget(ai_client) -> None:  # noqa: ANN001
    c, pid, _ = ai_client
    fake = FakeProvider("bad", "bad", json.dumps(FIX))
    p = _propose(c, pid, fake)
    assert p["status"] == "invalid" and len(fake.prompts) == 1  # default budget = 1 call
    from app.services.settings_store import save_settings

    save_settings({"max_ai_calls_per_proposal": 2})
    fake2 = FakeProvider("bad", json.dumps(FIX))
    p2 = _propose(c, pid, fake2)
    assert p2["status"] == "validated" and len(fake2.prompts) == 2 and "rejected" in fake2.prompts[1]


def test_prompt_redacts_secrets(ai_client) -> None:  # noqa: ANN001
    c, pid, repo = ai_client
    (repo / "src" / "calc.py").write_text(
        'TOKEN = "ghp_' + "a1B2" * 10 + '"\n\ndef div(a, b):\n    return a / b\n', encoding="utf-8")
    git(repo, "commit", "-qam", "token")
    fake = FakeProvider(json.dumps(FIX))
    _propose(c, pid, fake)
    assert "a1B2a1B2a1B2" not in fake.prompts[0] and "div(a, b)" in fake.prompts[0]


def test_reject_removes_workspace_but_not_linked_venv(ai_client, settings) -> None:  # noqa: ANN001
    c, pid, repo = ai_client
    venv = repo / ".venv"
    (venv / "Scripts").mkdir(parents=True)
    (venv / "KEEP.txt").write_text("must survive", encoding="utf-8")
    (repo / ".gitignore").write_text("ignored_dir/\n*.tmp\n.venv/\n", encoding="utf-8")
    git(repo, "commit", "-qam", "ignore venv")
    body = {"framework": "pytest", "argv": [sys.executable, "-m", "pytest", "-q"], "cwd": "."}
    c.put(f"/api/projects/{pid}/test-commands/pytest", json=body)
    cmd = c.get(f"/api/projects/{pid}/test-commands").json()[0]
    c.post(f"/api/projects/{pid}/test-commands/pytest/approve", json={"content_hash": cmd["content_hash"]})
    p = _propose(c, pid, FakeProvider(json.dumps(FIX)), test_command_key="pytest")
    assert p["status"] == "validated" and p["validation"]["test_status"] == "passed", p
    assert p["test_run_id"] is not None
    run = c.get(f"/api/test-runs/{p['test_run_id']}").json()
    assert f"repair-{p['id']}" in run["workdir"]
    assert c.post(f"/api/repairs/{p['id']}/reject").json()["status"] == "rejected"
    assert not (settings.workspaces_dir / f"repair-{p['id']}").exists()
    assert (venv / "KEEP.txt").read_text(encoding="utf-8") == "must survive"


def test_syntax_error_patch_invalid(ai_client) -> None:  # noqa: ANN001
    c, pid, _ = ai_client
    bad = FIX | {"edits": [FIX["edits"][0] | {"replace": "    return (a / b"}]}
    p = _propose(c, pid, FakeProvider(json.dumps(bad)))
    assert p["status"] == "invalid" and "calc.py" in p["error"]
