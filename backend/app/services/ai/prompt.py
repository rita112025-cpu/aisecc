"""Prompt construction. Context is redacted before it is sent to any provider."""

from __future__ import annotations

from app.services.analysis.secrets import RULES, redact

CONTEXT_LINES = 60

INSTRUCTIONS = """You are a careful senior engineer fixing one issue in a code base.
Respond with ONLY a JSON object, no prose, matching exactly:
{"summary": "<one paragraph, Traditional Chinese>",
 "root_cause": "<Traditional Chinese>",
 "risk": "low" | "medium" | "high",
 "edits": [{"path": "<relative file path>", "search": "<exact existing text>", "replace": "<new text>"}]}
Rules:
- "search" must be copied exactly from the file and occur exactly once; keep it small but unique.
- Only edit the files shown. Do not add new files. At most 10 edits.
- Do not include line numbers in "search" or "replace".
"""


def numbered(text: str, center: int | None) -> tuple[str, int, int]:
    lines = text.replace("\r\n", "\n").split("\n")
    if center is None:
        start, end = 1, min(len(lines), CONTEXT_LINES * 2)
    else:
        start, end = max(1, center - CONTEXT_LINES), min(len(lines), center + CONTEXT_LINES)
    body = "\n".join(f"{n:>5}| {lines[n - 1]}" for n in range(start, end + 1))
    return body, start, end


def build_prompt(*, path: str, file_text: str, line: int | None, issue: str, instruction: str,
                 previous_error: str | None = None) -> str:
    body, start, end = numbered(file_text, line)
    # Secret values found in the context are masked even for local models.
    secrets_found = {m.group(r.group) for r in RULES for m in r.pattern.finditer(body) if r.group}
    parts = [
        INSTRUCTIONS,
        f"Issue: {issue}",
        f"User instruction: {instruction or '(none)'}",
        f"File: {path} (lines {start}-{end}; the prefix 'NNNNN| ' is NOT part of the file)",
        "```",
        body,
        "```",
    ]
    if previous_error:
        parts.append(f"Your previous answer was rejected: {previous_error}. Return corrected JSON only.")
    return redact("\n".join(parts), secrets_found)
