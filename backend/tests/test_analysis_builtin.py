from __future__ import annotations

from pathlib import Path

from app.services.analysis.base import RawFinding, SourceIndex, assign_fingerprints, load_sources
from app.services.analysis.code_metrics import (
    duplication,
    import_cycles,
    js_complexity,
    python_complexity,
    strongly_connected,
    unused_python_candidates,
)
from app.services.analysis.secrets import mask, scan_secrets, shannon_entropy
from app.services.fswalk import walk


def _index(tmp_path: Path, files: dict[str, str]) -> SourceIndex:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return load_sources(tmp_path, walk(tmp_path), 1_000_000)


# ---- secrets -------------------------------------------------------------------------------

def test_secrets_detects_and_masks(tmp_path: Path) -> None:
    idx = _index(tmp_path, {"app/config.py": 'API_KEY = "Zq8vR2kLm9Xw4TnB7pQs"\naws = "AKIAABCDEFGHIJKLMNOP"\n'})
    fs = scan_secrets(idx)
    rules = {f.rule_id for f in fs}
    assert rules == {"hardcoded-password", "aws-access-key"}
    assert all("Zq8vR2kLm9Xw4TnB7pQs" not in f.message and "AKIAABCDEFGHIJKLMNOP" not in f.message for f in fs)
    assert all(f.confidence == "heuristic" for f in fs)


def test_secrets_ignores_placeholders_and_low_entropy(tmp_path: Path) -> None:
    idx = _index(tmp_path, {"a.py": 'password = "changeme"\nsecret = "${SECRET}"\napi_key = "aaaaaaaa"\n'})
    assert scan_secrets(idx) == []


def test_secrets_in_tests_downgraded(tmp_path: Path) -> None:
    idx = _index(tmp_path, {"tests/test_x.py": 'token = "ghp_' + "a1B2" * 10 + '"\n'})
    (f,) = scan_secrets(idx)
    assert f.severity == "low" and f.extra["in_test_path"]


def test_private_key_detected_in_env_file(tmp_path: Path) -> None:
    idx = _index(tmp_path, {"deploy/.env": "KEY=-----BEGIN RSA PRIVATE KEY-----\n"})
    assert [f.rule_id for f in scan_secrets(idx)] == ["private-key"]


def test_entropy_and_mask() -> None:
    assert shannon_entropy("aaaa") == 0
    assert shannon_entropy("abcd") == 2
    assert mask("abc") == "***" and "長度 20" in mask("x" * 20)


# ---- complexity ----------------------------------------------------------------------------

COMPLEX_PY = "def f(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(20)) + "    return -1\n"


def test_python_complexity_and_length(tmp_path: Path) -> None:
    idx = _index(tmp_path, {"m.py": COMPLEX_PY, "ok.py": "def g():\n    return 1\n"})
    fs, errors = python_complexity(idx, complexity_limit=10, length_limit=30)
    assert errors == []
    by_rule = {f.rule_id: f for f in fs}
    assert by_rule["high-complexity"].extra["complexity"] == 21
    assert by_rule["long-function"].file_path == "m.py"


def test_python_syntax_error_reported_not_crash(tmp_path: Path) -> None:
    idx = _index(tmp_path, {"bad.py": "def (:\n"})
    fs, errors = python_complexity(idx, 10, 50)
    assert fs == [] and "bad.py" in errors[0]


def test_js_complexity_heuristic(tmp_path: Path) -> None:
    body = "".join(f"  if (x === {i}) {{ return {i}; }}\n" for i in range(15))
    idx = _index(tmp_path, {"web/a.ts": f"export function pick(x: number) {{\n{body}  return -1;\n}}\n"})
    (f,) = js_complexity(idx, complexity_limit=10, length_limit=500)
    assert f.extra["function"] == "pick" and f.confidence == "heuristic" and f.line == 1


# ---- duplication ---------------------------------------------------------------------------

BLOCK = "".join(f"    value_{i} = compute_something_long(alpha, beta, gamma, {i})\n" for i in range(8))


def test_duplication_across_files(tmp_path: Path) -> None:
    idx = _index(tmp_path, {"a.py": "def a():\n" + BLOCK, "b.py": "def b():\n" + BLOCK, "c.py": "x = 1\n"})
    fs = duplication(idx)
    assert len(fs) == 1 and fs[0].file_path == "b.py" and fs[0].extra["original"]["file"] == "a.py"


def test_no_duplication_for_short_trivial_lines(tmp_path: Path) -> None:
    idx = _index(tmp_path, {"a.py": "x=1\n" * 20, "b.py": "x=1\n" * 20})
    assert duplication(idx) == []


# ---- cycles --------------------------------------------------------------------------------

def test_tarjan_components() -> None:
    g = {"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": {"a"}, "e": set()}
    assert strongly_connected(g) == [["a", "b", "c"]]


def test_python_and_js_import_cycles(tmp_path: Path) -> None:
    idx = _index(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/a.py": "from pkg import b\n",
        "pkg/b.py": "from . import a\n",
        "web/x.ts": "import { y } from './y';\n",
        "web/y.ts": "import { x } from './x';\n",
        "web/z.ts": "import { x } from './x';\n",
    })
    fs = import_cycles(idx)
    assert sorted(f.rule_id for f in fs) == ["import-cycle-js", "import-cycle-python"]
    js = next(f for f in fs if f.rule_id == "import-cycle-js")
    assert js.extra["modules"] == ["web/x.ts", "web/y.ts"]


# ---- unused --------------------------------------------------------------------------------

def test_unused_candidates(tmp_path: Path) -> None:
    idx = _index(tmp_path, {
        "src/lib.py": "def used():\n    pass\n\ndef orphan():\n    pass\n\n@app.get('/')\ndef route():\n    pass\n",
        "src/main.py": "from lib import used\nused()\n",
        "tests/test_lib.py": "def test_nothing():\n    pass\n",
    })
    names = [f.extra["name"] for f in unused_python_candidates(idx)]
    assert names == ["orphan"]


# ---- fingerprints --------------------------------------------------------------------------

def test_fingerprint_stable_when_code_moves(tmp_path: Path) -> None:
    code = 'password = "Zq8vR2kLm9Xw4TnB"\n'
    idx1 = _index(tmp_path / "v1", {"a.py": code})
    idx2 = _index(tmp_path / "v2", {"a.py": "\n\n\n" + code})
    f1 = assign_fingerprints(scan_secrets(idx1), idx1)[0]
    f2 = assign_fingerprints(scan_secrets(idx2), idx2)[0]
    assert f1.line == 1 and f2.line == 4 and f1.fingerprint == f2.fingerprint


def test_fingerprint_occurrence_for_identical_lines(tmp_path: Path) -> None:
    code = 'password = "Zq8vR2kLm9Xw4TnB"\n' * 2
    idx = _index(tmp_path, {"a.py": code})
    fs = assign_fingerprints(scan_secrets(idx), idx)
    assert [f.occurrence for f in fs] == [0, 1] and fs[0].fingerprint == fs[1].fingerprint


def test_fingerprint_changes_when_line_edited(tmp_path: Path) -> None:
    idx = SourceIndex(root=tmp_path)
    a = RawFinding("t", "r", "low", "lint", "m", None)
    b = RawFinding("t", "r", "low", "lint", "different", None)
    fa, fb = assign_fingerprints([a, b], idx)
    assert fa.fingerprint != fb.fingerprint


def test_load_sources_skips_binary_and_large(tmp_path: Path) -> None:
    (tmp_path / "bin.py").write_bytes(b"\x00\x01\x02")
    (tmp_path / "big.py").write_text("x" * 5000)
    (tmp_path / "ok.py").write_text("x = 1\n")
    idx = load_sources(tmp_path, walk(tmp_path), 1000)
    assert set(idx.files) == {"ok.py"} and sorted(idx.skipped) == ["big.py", "bin.py"]
