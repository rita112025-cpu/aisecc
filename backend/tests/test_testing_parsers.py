from __future__ import annotations

from pathlib import Path

import pytest

from app.services.testing.failures import analyse, classify, compare_runs, first_location
from app.services.testing.reports import (
    parse_cobertura,
    parse_istanbul_summary,
    parse_junit,
    parse_trx,
)

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="5">
  <testcase classname="tests.test_calc" name="test_add" file="tests/test_calc.py" time="0.01"/>
  <testcase classname="tests.test_calc" name="test_div" time="0.02">
    <failure message="assert 1 == 2">tests/test_calc.py:10: AssertionError</failure>
  </testcase>
  <testcase classname="tests.test_calc" name="test_io" time="0.5"><error message="boom">Traceback
  File "C:/proj/src/io.py", line 7, in read
FileNotFoundError: x</error></testcase>
  <testcase classname="tests.test_calc" name="test_skip"><skipped message="windows only"/></testcase>
  <testcase classname="tests.test_calc" name="test_add" time="0.01"/>
</testsuite></testsuites>
"""

TRX = """<?xml version="1.0" encoding="UTF-8"?>
<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"><Results>
<UnitTestResult testName="Calc.Adds" outcome="Passed" duration="00:00:00.0123"/>
<UnitTestResult testName="Calc.Divides" outcome="Failed" duration="00:00:01.5">
  <Output><ErrorInfo><Message>Assert.Equal() Failure</Message>
  <StackTrace>at Calc.cs:line 12</StackTrace></ErrorInfo></Output>
</UnitTestResult>
<UnitTestResult testName="Calc.Later" outcome="NotExecuted"/>
</Results></TestRun>
"""


def _w(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_junit_statuses_and_dedupe(tmp_path: Path) -> None:
    r = parse_junit(_w(tmp_path, "j.xml", JUNIT))
    assert r.counts() == {"total": 5, "passed": 2, "failed": 1, "skipped": 1, "errors": 1}
    names = [c.name for c in r.cases]
    assert names[0] == "tests.test_calc::test_add" and names[-1] == "tests.test_calc::test_add [#2]"
    assert r.warnings and r.cases[1].message.startswith("assert 1 == 2")


def test_parse_junit_invalid_xml(tmp_path: Path) -> None:
    import xml.etree.ElementTree as ET

    with pytest.raises(ET.ParseError):
        parse_junit(_w(tmp_path, "bad.xml", "<testsuite><testcase"))


def test_parse_trx(tmp_path: Path) -> None:
    r = parse_trx(_w(tmp_path, "r.trx", TRX))
    assert [(c.name, c.status) for c in r.cases] == [
        ("Calc.Adds", "passed"), ("Calc.Divides", "failed"), ("Calc.Later", "skipped")]
    assert r.cases[1].duration_sec == 1.5 and "Assert.Equal" in r.cases[1].message


def test_parse_coverage_formats(tmp_path: Path) -> None:
    cob = _w(tmp_path, "c.xml", '<coverage line-rate="0.8125" lines-valid="16" lines-covered="13">'
                                 '<packages><package><classes><class filename="src\\calc.py" line-rate="0.5"/>'
                                 "</classes></package></packages></coverage>")
    c = parse_cobertura(cob)
    assert c["percent"] == 81.25 and c["files"] == [{"file": "src/calc.py", "line_rate": 50.0}]
    ist = _w(tmp_path, "s.json", '{"total":{"lines":{"total":10,"covered":7,"pct":70}},'
                                  '"C:/p/a.ts":{"lines":{"pct":70}}}')
    assert parse_istanbul_summary(ist)["percent"] == 70
    with pytest.raises(ValueError):
        parse_cobertura(_w(tmp_path, "n.xml", "<other/>"))


@pytest.mark.parametrize(("msg", "cat"), [
    ("AssertionError: assert 1 == 2", "assertion"),
    ("ModuleNotFoundError: No module named 'x'", "import"),
    ("Error: Test timed out in 5000ms", "timeout"),
    ("connect ECONNREFUSED 127.0.0.1:5432", "connection"),
    ("TypeError: undefined is not a function", "type"),
    ("Snapshot `x` mismatched", "snapshot"),
    ("weird", "other"),
    (None, "other"),
])
def test_classify(msg: str | None, cat: str) -> None:
    assert classify(msg)[0] == cat


def test_first_location_prefers_project_frames() -> None:
    msg = ('File "C:/py/site-packages/lib.py", line 3\nFile "C:/proj/src/app.py", line 42\n')
    assert first_location(msg) == {"file": "C:/proj/src/app.py", "line": 42}
    assert first_location("at Object.<anonymous> (src/sum.test.ts:12:5)") == {"file": "src/sum.test.ts", "line": 12}
    assert first_location("no frames") is None


def test_analyse_groups(tmp_path: Path) -> None:
    r = parse_junit(_w(tmp_path, "j.xml", JUNIT))
    groups = analyse([{"name": c.name, "status": c.status, "message": c.message} for c in r.cases])
    assert {g["category"]: g["count"] for g in groups} == {"assertion": 1, "file_not_found": 1}
    io = next(g for g in groups if g["category"] == "file_not_found")
    assert io["tests"][0]["location"] == {"file": "C:/proj/src/io.py", "line": 7}


def test_compare_runs() -> None:
    a = {"t1": "passed", "t2": "failed", "t3": "failed", "t4": "passed", "t5": "skipped"}
    b = {"t1": "failed", "t2": "passed", "t3": "error", "t6": "passed", "t5": "passed"}
    d = compare_runs(a, b)
    assert d["newly_failing"] == ["t1"] and d["fixed"] == ["t2"] and d["still_failing"] == ["t3"]
    assert d["removed"] == ["t4"] and d["added"] == ["t6"] and d["status_changed"] == ["t5"]
