"""Compare a cmr JSON report (one file) with suite expectations and with a stored baseline.

Pure functions, no I/O: unit-tested in tests/unit/test_suite_compare.py with synthetic reports.
Expectation format (expected.yaml `runs.<id>`; see tests/CLAUDE.md "QC test suite"):
  exit: int | null            expected exit code (null = not checked yet)
  findings: [{check, severity, kind?, edge?, channel?, frames?: [a, b], count?, status?, note?}]
  absent: [check ids]         must report no problem finding (INFO..ERROR)
  other: fail | allow         unlisted problem findings fail the run (default fail)
Severity `pass`/`skipped` in an expectation matches a PASS/SKIPPED result of that check.
"""
from __future__ import annotations

PROBLEM = ("INFO", "WARN", "FAIL", "ERROR")
LOCATORS = ("kind", "edge", "channel")
DEFAULT_TOL = 5


def file_entry(report: dict) -> dict:
    files = report.get("files") or []
    if len(files) != 1:
        raise ValueError(f"expected a report with exactly one file, got {len(files)}")
    return files[0]


def evidence0(f: dict) -> dict:
    ev = f.get("evidence") or (f.get("candidate") or {}).get("evidence") or []
    return ev[0] if ev and isinstance(ev[0], dict) else {}


def is_problem(f: dict) -> bool:
    return f.get("severity") in PROBLEM          # WAIVED findings carry severity PASS


def _overlaps(frames, f: dict, tol: int) -> bool:
    if frames is None:
        return True
    a, b = frames
    fs, fe = f.get("frame_start"), f.get("frame_end")
    if fs is None or fe is None:
        return False
    tol = max(tol, int(evidence0(f).get("boundary_uncertainty_frames") or 0))
    return fs <= b + tol and fe >= a - tol


def matches(exp: dict, f: dict, tol: int = DEFAULT_TOL) -> bool:
    if f.get("check_id") != exp["check"]:
        return False
    if str(f.get("severity", "")).upper() != str(exp["severity"]).upper():
        return False
    ev = evidence0(f)
    for key in LOCATORS:
        if key in exp and exp[key] is not None and str(ev.get(key)) != str(exp[key]):
            return False
    return _overlaps(exp.get("frames"), f, tol)


def describe(f: dict) -> str:
    ev = evidence0(f)
    loc = " ".join(f"{k}={ev[k]}" for k in LOCATORS if ev.get(k) is not None)
    fr = (f" frames {f['frame_start']}-{f['frame_end']}" if f.get("frame_start") is not None else "")
    return f"{f.get('check_id')} {f.get('severity')}{(' ' + loc) if loc else ''}{fr}"


def compare_expected(report: dict, exp: dict, exit_code: int | None = None,
                     tol: int = DEFAULT_TOL) -> list[str]:
    """Problems (empty list = the run meets its expectations)."""
    problems: list[str] = []
    findings = file_entry(report).get("findings") or []
    if exit_code is None:
        exit_code = (report.get("summary") or {}).get("exit_code")
    if exp.get("exit") is not None and exit_code != exp["exit"]:
        problems.append(f"exit code {exit_code}, expected {exp['exit']}")
    claimed: set[int] = set()
    for e in exp.get("findings") or []:
        hit = [i for i, f in enumerate(findings) if matches(e, f, tol)]
        claimed.update(hit)
        want = e.get("count")
        if want is None and not hit:
            problems.append(f"missing: {_exp_str(e)}")
        elif want is not None and len(hit) != int(want):
            problems.append(f"count {len(hit)} != {want}: {_exp_str(e)}")
    for cid in exp.get("absent") or []:
        for f in findings:
            if f.get("check_id") == cid and is_problem(f):
                problems.append(f"must be absent: {describe(f)}")
    if exp.get("other", "fail") == "fail":
        for i, f in enumerate(findings):
            if is_problem(f) and i not in claimed:
                problems.append(f"unexpected: {describe(f)}")
    return problems


def _exp_str(e: dict) -> str:
    loc = " ".join(f"{k}={e[k]}" for k in LOCATORS if e.get(k) is not None)
    fr = f" frames {e['frames'][0]}-{e['frames'][1]}" if e.get("frames") else ""
    return f"{e['check']} {str(e['severity']).upper()}{(' ' + loc) if loc else ''}{fr}"


def _key(f: dict) -> tuple:
    ev = evidence0(f)
    return (f.get("check_id"), f.get("severity")) + tuple(str(ev.get(k)) for k in LOCATORS)


def compare_baseline(report: dict, baseline: dict, tol: int = DEFAULT_TOL) -> list[str]:
    """Problem findings that appeared or disappeared versus the baseline report (same
    check, severity and locator; frame ranges equal within `tol`), plus exit-code change."""
    problems: list[str] = []
    now = [f for f in file_entry(report).get("findings") or [] if is_problem(f)]
    base = [f for f in file_entry(baseline).get("findings") or [] if is_problem(f)]
    e_now = (report.get("summary") or {}).get("exit_code")
    e_base = (baseline.get("summary") or {}).get("exit_code")
    if e_now != e_base:
        problems.append(f"exit code {e_now}, baseline {e_base}")
    left = list(base)
    for f in now:
        j = next((j for j, b in enumerate(left) if _key(b) == _key(f) and _same_range(b, f, tol)), None)
        if j is None:
            problems.append(f"new vs baseline: {describe(f)}")
        else:
            left.pop(j)
    problems += [f"gone vs baseline: {describe(b)}" for b in left]
    return problems


def _same_range(a: dict, b: dict, tol: int) -> bool:
    if a.get("frame_start") is None or b.get("frame_start") is None:
        return a.get("frame_start") == b.get("frame_start")
    return (abs(a["frame_start"] - b["frame_start"]) <= tol
            and abs(a["frame_end"] - b["frame_end"]) <= tol)
