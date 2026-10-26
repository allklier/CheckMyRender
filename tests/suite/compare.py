"""Compare a cmr JSON report (one file) with suite expectations and with a stored baseline.

Pure functions, no I/O: unit-tested in tests/unit/test_suite_compare.py with synthetic reports.
Expectation format (expected.yaml `runs.<id>`; see tests/CLAUDE.md "QC test suite"):
  exit: int | null            expected exit code (null = not checked yet)
  findings: [{check, severity, kind?, edge?, channel?, frames?: [a, b], count?, status?, note?}]
  absent: [check ids]         must report no problem finding (INFO..ERROR)
  other: fail | allow         unlisted problem findings fail the run (default fail)
Severity `pass`/`skipped` in an expectation matches a PASS/SKIPPED result of that check.
Strict cross-platform mode (`compare_strict`, CLI `compare.py --strict A.json B.json`): see below.
"""
from __future__ import annotations

import re

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


# --- strict cross-platform mode (D32: the same input checked on Linux and on the Mac) ---------
# Tolerance 0 on check / severity / locator / frame ranges; numbers in evidence, expected and
# actual: integers (pixel statistics, frame counts) exactly equal; loudness and true peak
# (LUFS / LU / dBTP) within 0.05; other dB levels (peak, RMS, dBFS) within 0.1; any other float
# exactly equal. OCR confidences are never compared (different tesseract/leptonica builds, D32).
# Ignored stamps: `tool` (versions), `profile`/`spec` (paths and hashes), the file path (files
# are paired by base name), run ids/dates; messages are compared with numbers masked (the
# numbers themselves are compared through evidence/actual).
STRICT_LOUDNESS_TOL = 0.05
STRICT_DB_TOL = 0.1
_STRICT_IGNORE_TOP = ("tool", "profile", "spec")
_FINDING_KEYS = ("raw_severity", "stream", "timecode_start", "timecode_end", "quality", "verifier",
                 "waiver_ref", "waiver")
_LOUD = re.compile(r"lufs|(^|_)lu($|_)|lra|true_peak|dbtp", re.I)
_DB = re.compile(r"db|peak|rms|level", re.I)
_NUM = re.compile(r"[-+]?\d+(\.\d+)?")
_PTR = re.compile(r"0x[0-9a-f]+")


def strict_tolerance(check_id: str, key: str) -> float | None:
    """Allowed |a - b| for a float at `key` (None = not compared)."""
    k = key.lower()
    if "conf" in k:
        return None
    if _LOUD.search(k) or (k in ("expected", "actual") and check_id == "audio.loudness"):
        return STRICT_LOUDNESS_TOL
    if _DB.search(k) or (k in ("expected", "actual") and str(check_id).startswith("audio.")):
        return STRICT_DB_TOL
    return 0.0


def _strict_diff(a, b, where: str, check_id: str, key: str, out: list[str]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                out.append(f"{where}.{k}: only on {'A' if k in a else 'B'}")
            else:
                _strict_diff(a[k], b[k], f"{where}.{k}", check_id, str(k), out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{where}: {len(a)} vs {len(b)} items")
        for i, (x, y) in enumerate(zip(a, b)):
            _strict_diff(x, y, f"{where}[{i}]", check_id, key, out)
    elif (isinstance(a, (int, float)) and isinstance(b, (int, float))
          and not isinstance(a, bool) and not isinstance(b, bool)):
        if isinstance(a, int) and isinstance(b, int):
            if a != b and "conf" not in key.lower():
                out.append(f"{where}: {a} vs {b}")
            return
        tol = strict_tolerance(check_id, key)
        if tol is not None and abs(a - b) > tol + 1e-9:
            out.append(f"{where}: {a} vs {b} (tolerance {tol})")
    elif isinstance(a, str) and isinstance(b, str):
        if _PTR.sub("0x#", a) != _PTR.sub("0x#", b):      # ffmpeg log prefixes carry pointers
            out.append(f"{where}: {a!r} vs {b!r}")
    elif a != b:
        out.append(f"{where}: {a!r} vs {b!r}")


def _strict_key(f: dict) -> tuple:
    return _key(f) + (f.get("frame_start"), f.get("frame_end"))


def compare_strict(a: dict, b: dict) -> list[str]:
    """Every difference between two reports of the same input(s) (A = e.g. Linux, B = Mac).
    Empty list = equivalent. Files are paired by base name; findings by check, severity,
    locator and exact frame range."""
    out: list[str] = []
    _strict_diff(a.get("summary") or {}, b.get("summary") or {}, "summary", "", "summary", out)
    for k in sorted((set(a) | set(b)) - set(_STRICT_IGNORE_TOP) - {"files", "summary"}):
        _strict_diff(a.get(k), b.get(k), k, "", k, out)

    def by_name(r):
        return {str(f.get("path", "")).replace("\\", "/").rsplit("/", 1)[-1]: f for f in r.get("files") or []}
    fa, fb = by_name(a), by_name(b)
    for name in sorted(set(fa) | set(fb)):
        if name not in fa or name not in fb:
            out.append(f"file {name}: only on {'A' if name in fa else 'B'}")
            continue
        x, y = fa[name], fb[name]
        if x.get("identity") != y.get("identity"):
            out.append(f"file {name}: identity differs (not the same input bytes)")
        for k in sorted((set(x) | set(y)) - {"path", "identity", "findings"}):
            _strict_diff(x.get(k), y.get(k), f"{name}.{k}", "", k, out)
        left = list(y.get("findings") or [])
        for f in x.get("findings") or []:
            j = next((j for j, g in enumerate(left) if _strict_key(g) == _strict_key(f)), None)
            if j is None:
                out.append(f"{name}: only on A: {describe(f)}")
                continue
            g = left.pop(j)
            cid, tag = f.get("check_id", ""), f"{name}: {describe(f)}"
            if _NUM.sub("#", str(f.get("message"))) != _NUM.sub("#", str(g.get("message"))):
                out.append(f"{tag}: message {f.get('message')!r} vs {g.get('message')!r}")
            for k in _FINDING_KEYS:
                _strict_diff(f.get(k), g.get(k), f"{tag} {k}", cid, k, out)
            for k in ("expected", "actual"):
                _strict_diff(f.get(k), g.get(k), f"{tag} {k}", cid, k, out)
            _strict_diff(f.get("evidence"), g.get("evidence"), f"{tag} evidence", cid, "evidence", out)
            _strict_diff(f.get("candidate"), g.get("candidate"), f"{tag} candidate", cid, "candidate", out)
        out += [f"{name}: only on B: {describe(g)}" for g in left]
    return out


if __name__ == "__main__":     # python tests/suite/compare.py --strict A.json B.json
    import json
    import sys
    if len(sys.argv) != 4 or sys.argv[1] != "--strict":
        sys.exit("usage: compare.py --strict REPORT_A.json REPORT_B.json")
    with open(sys.argv[2]) as fa, open(sys.argv[3]) as fb:
        diffs = compare_strict(json.load(fa), json.load(fb))
    print("\n".join(diffs) if diffs else "equivalent (strict)")
    sys.exit(1 if diffs else 0)
