"""AI repair workflow. The AI never touches the real project: edits are produced and validated in an
isolated clone; only an explicit, hash-confirmed approval copies them over, with backups for revert."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db import read_session, write_session
from app.models import Finding, Project, RepairProposal, utcnow
from app.services import audit, gitinfo
from app.services.ai import patch, risk, workspace
from app.services.ai.prompt import build_prompt
from app.services.ai.providers import AIError, AIProvider, build_provider
from app.services.ai.schema import InvalidAIResponse, parse_response
from app.services.analysis.secrets import redact
from app.services.fswalk import read_text, resolve_inside
from app.services.jobs import JobCancelled, JobContext, JobTimeout
from app.services.settings_store import load_settings
from app.services.testing.runner import run_tests

ProviderFactory = Callable[[], AIProvider]


class RepairError(ValueError):
    pass


def _clean_repo(root: Path, allow_repo_config: bool) -> str:
    """The whole working tree must be clean so the workspace equals what the user has on disk."""
    info = gitinfo.get_info(root, allow_repo_config)
    if not info.is_repo:
        raise RepairError("AI 修復僅支援 Git 專案")
    if info.dirty is None:
        raise RepairError("無法確認工作目錄狀態（儲存庫設定受限），不允許 AI 修復")
    if info.dirty:
        raise RepairError("工作目錄有未提交的修改；請先提交或暫存，以免覆蓋您的變更")
    if not info.head:
        raise RepairError("儲存庫尚無任何 commit")
    return info.head


def _tracked(repo: Path) -> set[str]:
    return {p for p in gitinfo.run_git(repo, "ls-files", "-z").split("\x00") if p}


def create_proposal(project_id: int, *, finding_id: int | None, target_file: str | None, line: int | None,
                    instruction: str, test_command_key: str | None, provider_name: str) -> int:
    with read_session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise RepairError("專案不存在")
        root, allow = Path(project.path), project.allow_project_tools
        issue = None
        if finding_id is not None:
            f = s.get(Finding, finding_id)
            if f is None or f.project_id != project_id:
                raise RepairError("發現項目不存在或不屬於此專案")
            target_file, line = f.file_path, f.line
            issue = f"[{f.tool} {f.rule_id}] {f.message}"
    if not target_file:
        raise RepairError("需指定要修復的檔案")
    head = _clean_repo(root, allow)
    try:
        resolve_inside(root, target_file)
    except (ValueError, FileNotFoundError) as exc:
        raise RepairError(str(exc)) from exc
    if target_file not in _tracked(root):
        raise RepairError(f"{target_file} 不是 Git 追蹤中的檔案")
    with write_session() as s:
        p = RepairProposal(project_id=project_id, finding_id=finding_id, provider=provider_name, status="generating",
                           target_file=target_file, instruction=instruction[:4000], base_commit=head,
                           validation={"line": line, "issue": issue, "test_command_key": test_command_key})
        s.add(p)
        s.flush()
        audit.record("repair.create", target_type="repair", target_id=p.id, project_id=project_id,
                     detail={"file": target_file, "finding_id": finding_id}, session=s)
        return p.id


def finalise_unstarted(proposal_id: int) -> None:
    """A repair job cancelled while still queued never runs generate(), so nothing would ever leave
    'generating' (and the user could not reject it). Only called for jobs that never started."""
    with write_session() as s:
        p = s.get(RepairProposal, proposal_id)
        if p is not None and p.status == "generating":
            p.status, p.error = "failed", "使用者在開始前取消"
            audit.record("repair.cancelled_unstarted", target_type="repair", target_id=proposal_id,
                         project_id=p.project_id, session=s)


def _update(pid: int, **fields: Any) -> None:
    with write_session() as s:
        p = s.get(RepairProposal, pid)
        for k, v in fields.items():
            setattr(p, k, v)


def generate(proposal_id: int, ctx: JobContext, provider_factory: ProviderFactory | None = None) -> dict[str, Any]:
    settings = load_settings()
    with read_session() as s:
        p = s.get(RepairProposal, proposal_id)
        project = s.get(Project, p.project_id)
        project_id, root, allow = project.id, Path(project.path), project.allow_project_tools
        target, base, meta = p.target_file, p.base_commit, dict(p.validation or {})
        instruction = p.instruction
    _update(proposal_id, job_id=ctx.job_id)
    ws = get_settings().workspaces_dir / f"repair-{proposal_id}"
    try:
        provider = (provider_factory or (lambda: build_provider(settings)))()
        _update(proposal_id, model=provider.model)
        if _clean_repo(root, allow) != base:
            raise RepairError("HEAD 已變更，請重新建立修復建議")
        file_text = read_text(root / target)
        ctx.progress(0.1, "建立隔離工作區")
        workspace.create(root, ws, base)
        _update(proposal_id, workspace_path=str(ws))
        tracked = _tracked(ws)
        issue = meta.get("issue") or "使用者指定的修改"
        error: str | None = None
        changes: list[patch.FileChange] = []
        parsed = None
        for attempt in range(settings.max_ai_calls_per_proposal):
            ctx.progress(0.2 + 0.2 * attempt, f"呼叫 AI（第 {attempt + 1} 次）")
            prompt = build_prompt(path=target, file_text=file_text, line=meta.get("line"), issue=issue,
                                  instruction=instruction, previous_error=error)
            raw = provider.generate(prompt)
            _update(proposal_id, raw_response=raw[:100_000])
            ctx.check_cancelled()
            try:
                parsed = parse_response(raw)
                gitinfo.run_git(ws, "checkout", "--quiet", "--", ".")  # reset partial edits of earlier attempts
                changes = patch.apply_edits(ws, parsed.edits, tracked)
                error = None
                break
            except (InvalidAIResponse, patch.PatchError) as exc:
                error = str(exc)
                ctx.log(f"AI 回應被拒絕：{error}")
        if error or parsed is None:
            _update(proposal_id, status="invalid", error=error)
            workspace.remove(ws)
            _update(proposal_id, workspace_path=None)
            return {"proposal_id": proposal_id, "status": "invalid", "error": error}

        # The diff is stored, displayed and hash-confirmed in masked form (secrets never persisted).
        # apply() still writes the real verified bytes from the workspace.
        diff = redact(patch.unified_diff(changes), set())
        syntax = patch.syntax_check(ws, changes)
        test_status, test_run_id = None, None
        key = meta.get("test_command_key")
        not_isolated = workspace.editable_installs(root) if key and not syntax else []
        if not_isolated:
            # Tests would import the original (unpatched) project code: not a validation.
            test_status = "not_isolated"
            meta["not_isolated_reason"] = not_isolated[:20]
            ctx.log(f"依賴資料夾指回原專案，隔離測試不可信，略過：{not_isolated[:5]}")
        elif key and not syntax:
            ctx.progress(0.6, "在隔離工作區執行測試")
            try:
                result = run_tests(project_id, key, ctx, workspace=ws)
                test_status, test_run_id = result["status"], result["test_run_id"]
            except JobTimeout:
                test_status = "timeout"
        ctx.progress(0.9, "評估回歸風險")
        assessment = risk.assess(root, changes, diff, test_status, parsed.risk)
        status = "invalid" if syntax else "validated"
        _update(
            proposal_id, status=status, summary=parsed.summary, root_cause=parsed.root_cause, diff=diff,
            files=[{"path": c.path, "before_sha256": c.before_sha, "after_sha256": c.after_sha} for c in changes],
            validation=meta | {"syntax_errors": syntax, "test_status": test_status, "diff_sha256": patch.sha256(
                diff.encode("utf-8"))},
            risk=assessment, test_run_id=test_run_id, error="; ".join(syntax) or None,
        )
        if status == "invalid":
            workspace.remove(ws)
            _update(proposal_id, workspace_path=None)
        return {"proposal_id": proposal_id, "status": status, "risk": assessment["level"]}
    except JobCancelled:
        _update(proposal_id, status="failed", error="使用者取消")
        workspace.remove(ws)
        raise
    except (AIError, RepairError, workspace.WorkspaceError, OSError, ValueError) as exc:
        _update(proposal_id, status="failed", error=str(exc))
        workspace.remove(ws)
        return {"proposal_id": proposal_id, "status": "failed", "error": str(exc)}


def apply(proposal_id: int, diff_sha256: str, acknowledge_test_status: str | None = None) -> dict[str, Any]:
    with read_session() as s:
        p = s.get(RepairProposal, proposal_id)
        if p is None:
            raise RepairError("修復建議不存在")
        project = s.get(Project, p.project_id)
        root, allow = Path(project.path), project.allow_project_tools
        files, base, ws = list(p.files or []), p.base_commit, p.workspace_path
        status, expected = p.status, (p.validation or {}).get("diff_sha256")
        test_status = (p.validation or {}).get("test_status") or "not_run"
    if status != "validated":
        raise RepairError(f"目前狀態為 {status}，只有已驗證的建議可以套用")
    if not expected or diff_sha256 != expected:
        raise RepairError("確認的 diff 與建議內容不符，請重新檢視")
    if test_status != "passed" and acknowledge_test_status != test_status:
        raise RepairError(f"隔離測試未通過（{test_status}），請確認後再套用")
    if _clean_repo(root, allow) != base:
        raise RepairError("HEAD 已變更，請重新產生修復建議")
    for f in files:
        current = patch.file_sha(resolve_inside(root, f["path"]))
        if current != f["before_sha256"]:
            raise RepairError(f"{f['path']} 在建議產生後已被修改，拒絕覆蓋")
    if not ws or not Path(ws).is_dir():
        raise RepairError("隔離工作區已不存在，請重新產生")
    backup = get_settings().backups_dir / "repairs" / str(proposal_id)
    backup.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    try:
        for f in files:
            dst = resolve_inside(root, f["path"])
            (backup / f["path"]).parent.mkdir(parents=True, exist_ok=True)
            (backup / f["path"]).write_bytes(dst.read_bytes())
            new = (Path(ws) / f["path"]).read_bytes()
            if patch.sha256(new) != f["after_sha256"]:
                raise RepairError(f"工作區中的 {f['path']} 與驗證時不同")
            patch.atomic_write(dst, new)
            written.append(f["path"])
    except (OSError, RepairError) as exc:
        _roll_back(proposal_id, root, backup, written, exc)
        raise RepairError(f"套用失敗，已還原已寫入的檔案：{exc}") from exc
    with write_session() as s:
        p = s.get(RepairProposal, proposal_id)
        p.status, p.applied_at, p.backup_path = "applied", utcnow(), str(backup)
        audit.record("repair.apply", target_type="repair", target_id=proposal_id, project_id=p.project_id,
                     detail={"files": [f["path"] for f in files], "diff_sha256": diff_sha256,
                             "test_status": test_status, "acknowledged": acknowledge_test_status}, session=s)
    workspace.remove(Path(ws))
    _update(proposal_id, workspace_path=None)
    return {"proposal_id": proposal_id, "status": "applied", "files": [f["path"] for f in files]}


def _roll_back(proposal_id: int, root: Path, backup: Path, written: list[str], cause: Exception) -> None:
    """Restore every already-written file; if any restore fails, record exactly what is left."""
    failed = []
    for rel in written:
        try:
            patch.atomic_write(resolve_inside(root, rel), (backup / rel).read_bytes())
        except (OSError, ValueError):
            failed.append(rel)
    with write_session() as s:
        p = s.get(RepairProposal, proposal_id)
        if failed:
            p.status = "apply_failed"
            p.error = f"以下檔案仍為新內容，備份位於 {backup}：{', '.join(failed)}"
        audit.record("repair.apply_failed" if failed else "repair.apply_rolled_back", target_type="repair",
                     target_id=proposal_id, project_id=p.project_id,
                     detail={"written": written, "rollback_failed": failed, "backup_path": str(backup),
                             "cause": str(cause)}, session=s)
    if failed:
        raise RepairError(p.error) from cause


def revert(proposal_id: int) -> dict[str, Any]:
    with read_session() as s:
        p = s.get(RepairProposal, proposal_id)
        if p is None:
            raise RepairError("修復建議不存在")
        root = Path(s.get(Project, p.project_id).path)
        files, status, backup = list(p.files or []), p.status, p.backup_path
    if status != "applied" or not backup:
        raise RepairError("只有已套用的建議可以復原")
    changed = [f["path"] for f in files if patch.file_sha(resolve_inside(root, f["path"])) != f["after_sha256"]]
    if changed:
        raise RepairError(f"以下檔案在套用後又被修改，為避免覆蓋您的變更而拒絕復原：{', '.join(changed)}")
    for f in files:
        patch.atomic_write(resolve_inside(root, f["path"]), (Path(backup) / f["path"]).read_bytes())
    with write_session() as s:
        p = s.get(RepairProposal, proposal_id)
        p.status, p.reverted_at = "reverted", utcnow()
        audit.record("repair.revert", target_type="repair", target_id=proposal_id, project_id=p.project_id,
                     detail={"files": [f["path"] for f in files]}, session=s)
    return {"proposal_id": proposal_id, "status": "reverted"}


def reject(proposal_id: int) -> dict[str, Any]:
    with read_session() as s:
        p = s.get(RepairProposal, proposal_id)
        if p is None:
            raise RepairError("修復建議不存在")
        status, ws, project_id = p.status, p.workspace_path, p.project_id
    if status in ("applied", "generating"):
        raise RepairError(f"狀態為 {status} 的建議不能拒絕")
    if ws:
        workspace.remove(Path(ws))
    with write_session() as s:
        p = s.get(RepairProposal, proposal_id)
        p.status, p.workspace_path = "rejected", None
        audit.record("repair.reject", target_type="repair", target_id=proposal_id, project_id=project_id, session=s)
    return {"proposal_id": proposal_id, "status": "rejected"}
