"""Hard-coded secret detection (heuristic). Matched values are masked before they are stored."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from app.services.analysis.base import RawFinding, SourceIndex


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern[str]
    severity: str
    description: str
    # Index of the regex group holding the secret value.
    group: int = 0
    min_entropy: float = 0.0


RULES = (
    Rule("private-key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"), "high",
         "私鑰內容"),
    Rule("aws-access-key", re.compile(r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b"), "high", "AWS Access Key ID", 1),
    Rule("github-token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,})\b"), "high", "GitHub Token", 1),
    Rule("slack-token", re.compile(r"\b(xox[abposr]-[A-Za-z0-9-]{10,})\b"), "high", "Slack Token", 1),
    Rule("openai-key", re.compile(r"\b(sk-[A-Za-z0-9_-]{32,})\b"), "high", "OpenAI 樣式 API Key", 1),
    Rule("google-api-key", re.compile(r"\b(AIza[0-9A-Za-z_-]{35})\b"), "high", "Google API Key", 1),
    Rule("jwt", re.compile(r"\b(eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b"), "medium",
         "JWT", 1),
    Rule("connection-string-password",
         re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*)://[^\s:/@'\"]+:([^\s@'\"]{4,})@"), "high",
         "連線字串含密碼", 1),
    Rule("hardcoded-password",
         # Key names containing the keyword anywhere (API_TOKEN, db_password, "clientSecret": ...).
         re.compile(r"""(?i)\b[\w.-]*(?:password|passwd|pwd|secret|token|api_?key)\w*['"]?"""
                    r"""\s*[:=]\s*['"]([^'"\s]{6,})['"]"""),
         "medium", "疑似硬編碼密碼/金鑰", 1, 2.5),
)
PLACEHOLDER = re.compile(
    r"(?i)^(?:x+|\*+|changeme|change_me|password|secret|example|sample|dummy|test|testing|placeholder|"
    r"your[_-]?\w*|<[^>]+>|\$\{[^}]+\}|\{\{[^}]+\}\}|%\(?\w+\)?s?|none|null|todo|fixme|\.\.\.)$"
)
SKIP_PATH = re.compile(r"(?i)(^|/)(test|tests|__tests__|fixtures?|examples?|docs?)/|\.(md|lock)$|package-lock\.json$")


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    n = len(value)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def mask(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-2:]}（長度 {len(value)}）"


def scan_secrets(index: SourceIndex) -> list[RawFinding]:
    findings: list[RawFinding] = []
    for f in index.files.values():
        in_test_path = bool(SKIP_PATH.search(f.rel))
        for lineno, line in enumerate(f.lines, start=1):
            if len(line) > 2000:
                continue
            for rule in RULES:
                for m in rule.pattern.finditer(line):
                    value = m.group(rule.group)
                    if rule.group and PLACEHOLDER.match(value):
                        continue
                    if rule.min_entropy and shannon_entropy(value) < rule.min_entropy:
                        continue
                    severity = "low" if in_test_path else rule.severity
                    findings.append(RawFinding(
                        tool="builtin-secrets", rule_id=rule.id, severity=severity, category="secret",
                        message=f"{rule.description}：{mask(value)}", file_path=f.rel, line=lineno,
                        column=m.start(rule.group) + 1, confidence="heuristic",
                        extra={"in_test_path": in_test_path},
                    ))
    return findings
