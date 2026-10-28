"""Strict validation of AI responses. Anything that does not match is rejected, never "repaired"."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MAX_EDITS = 10
MAX_TEXT = 20_000


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    search: str = Field(min_length=1, max_length=MAX_TEXT)
    replace: str = Field(max_length=MAX_TEXT)

    @field_validator("path")
    @classmethod
    def _path(cls, v: str) -> str:
        v = v.strip().replace("\\", "/")
        if v.startswith("./"):
            v = v[2:]
        if not v or ":" in v or v.startswith("/") or ".." in v.split("/") or v.split("/")[0] == ".git":
            raise ValueError(f"不合法的檔案路徑：{v}")
        return v


class RepairResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = Field(min_length=1, max_length=2000)
    root_cause: str = Field(default="", max_length=4000)
    risk: Literal["low", "medium", "high"] = "medium"
    edits: list[Edit] = Field(min_length=1, max_length=MAX_EDITS)


class InvalidAIResponse(ValueError):
    pass


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def parse_response(raw: str) -> RepairResponse:
    text = raw.strip()
    m = _FENCE.match(text)
    if m:
        text = m.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidAIResponse(f"AI 回傳不是合法 JSON：{exc.msg}（第 {exc.lineno} 行）") from exc
    if not isinstance(data, dict):
        raise InvalidAIResponse("AI 回傳的 JSON 不是物件")
    try:
        return RepairResponse.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5])
        raise InvalidAIResponse(f"AI 回傳格式不符：{problems}") from exc
