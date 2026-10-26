#!/usr/bin/env python3
"""QC suite runner: real renders listed in a PRIVATE suite folder (outside the repo).

    PYTHONPATH=src python3 tests/suite/run_suite.py [SUITE_DIR] --tag smoke [--tag ...]
        [--run ID ...] [--slow] [--quality fast|high] [--meta] [--update-baseline]
        [--timeout S] [--max-size-mb N] [--mode background|foreground] [--dry-run]

SUITE_DIR defaults to $CMR_SUITE. Layout and file formats: tests/CLAUDE.md "QC test suite".
Per selected run: `cmr check` via the CLI (one file at a time, --mode background by default,
--max-findings 0, JSON report) into SUITE/runs/<stamp>_<tier>/<id>/, a log <id>.log, then the
report is compared with projects/<project>/expected.yaml (tests/suite/compare.py) and with
baselines/<id>.json when present. With a quality override the keys are `<id>@<quality>`
(expected.yaml entry, falling back to `<id>`; baseline file `<id>@<quality>.json`). Prints one line per run; exit 0 = no FAIL/ERROR verdict.

Quality: passed as `cmr check --quality Q` (sets Q on every enabled check that implements it).
--meta: the runner writes a wrapper profile (`extends: <profile file>`, every check that decodes
(taps or needs decode) and the slate checks disabled) into the run folder, so only probe-level
checks run.
Safety: smoke never runs a file larger than --max-size-mb (default 500, real size from stat);
runs tagged `slow` only with --slow or --run; `needs-tesseract` runs are SKIPPED without
tesseract ($CMR_TESSERACT or PATH).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src"))
import compare  # noqa: E402



def run_group(cmd, fh, env, timeout) -> int:
    """Run cmr in its own session (process group); on timeout the WHOLE group gets SIGTERM, then
    SIGKILL after 10 s, so no ffmpeg child is left running (subprocess.run's timeout kills only
    the direct child). Raises subprocess.TimeoutExpired after cleaning up."""
    p = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    try:
        return p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(p.pid, signal.SIGTERM)       # pgid == pid with start_new_session
        with contextlib.suppress(subprocess.TimeoutExpired):
            p.wait(timeout=10)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(p.pid, signal.SIGKILL)       # whatever of the group is still running
        p.wait()
        raise


def load_yaml(p: Path):
    with open(p) as fh:
        return yaml.safe_load(fh)


def resolve_file(suite: dict, ref: str) -> Path:
    root, _, rel = ref.partition(":")
    base = os.environ.get(f"CMR_SUITE_ROOT_{root.upper()}") or suite["roots"][root]
    return Path(base) / rel


def select(runs: list[dict], tags: list[str], ids: list[str], slow: bool) -> list[dict]:
    out = []
    for r in runs:
        if ids:
            if r["id"] in ids:
                out.append(r)
            continue
        if tags and not set(tags) & set(r.get("tags") or []):
            continue
        if "slow" in (r.get("tags") or []) and not slow:
            continue
        out.append(r)
    return out


def profile_path(suite_dir: Path, name: str) -> Path:
    from cmr.config import find_profile
    if name.endswith((".yaml", ".yml")):
        return (suite_dir / name).resolve()
    return find_profile(name).resolve()


def wrapper_profile(base: Path) -> dict:
    """--meta: a profile that extends `base` with every decoding check disabled."""
    from cmr import engine
    from cmr.config import load_profile_raw
    engine.load_checks()
    merged, _ = load_profile_raw(str(base))
    checks = {}
    for cid, entry in (merged.get("checks") or {}).items():
        if (entry or {}).get("enabled", True) is False:
            continue
        c = engine.REGISTRY.get(cid)
        if c is None:
            continue
        decodes = (type(c).taps is not engine.Check.taps or "decode" in c.needs
                   or cid.startswith("slate."))
        if decodes:
            checks[cid] = {"enabled": False}
    return {"profile_version": 1, "name": f"{merged['name']}+meta",
            "extends": str(base), "description": "suite runner wrapper", "checks": checks or {}}


def tesseract_available() -> bool:
    t = os.environ.get("CMR_TESSERACT")
    return bool((t and Path(t).exists()) or shutil.which("tesseract"))


def run_one(r: dict, args, suite: dict, suite_dir: Path, out_root: Path, expected: dict) -> dict:
    res = {"id": r["id"], "verdict": "SKIP", "exit": None, "secs": 0.0, "notes": []}
    tags = set(r.get("tags") or [])
    if r.get("spec") is None or r.get("profile") is None:
        res["notes"].append("no spec/profile in suite.yaml")
        return res
    if "needs-tesseract" in tags and not tesseract_available() and not args.meta:
        res["notes"].append("needs tesseract")
        return res
    media = resolve_file(suite, r["file"])
    if not media.exists():
        res["verdict"] = "ERROR"
        res["notes"].append(f"media missing: {media}")
        return res
    size_mb = media.stat().st_size / 1e6
    if args.max_size_mb and size_mb > args.max_size_mb and not args.meta:
        res["notes"].append(f"{size_mb:.0f} MB > --max-size-mb {args.max_size_mb}")
        return res
    out = out_root / r["id"]
    out.mkdir(parents=True, exist_ok=True)
    prof = profile_path(suite_dir, r["profile"])
    if args.meta:
        wp = out_root / "profiles" / f"{r['id']}.yaml"
        wp.parent.mkdir(exist_ok=True)
        wp.write_text(yaml.safe_dump(wrapper_profile(prof), sort_keys=False))
        prof = wp
    cmd = [sys.executable, "-m", "cmr", "check", str(media), "--spec", str(suite_dir / r["spec"]),
           "--profile", str(prof), "--mode", args.mode, "--max-findings", "0",
           "--report", "json", "-o", str(out)]
    if args.quality and not args.meta:
        cmd += ["--quality", args.quality]
    # the suite names spec/profile itself; a job file only when the run lists one (never a
    # job.yaml that happens to be in the current folder, D31)
    cmd += ["--job", str(suite_dir / r["job"])] if r.get("job") else ["--no-job"]
    # reviewed findings: a run's `waivers:` file, else projects/<project>/waivers.yaml when it exists
    wf = suite_dir / (r.get("waivers") or f"projects/{r.get('project')}/waivers.yaml")
    if wf.exists():
        cmd += ["--waivers", str(wf)]
    res["cmd"] = cmd
    if args.dry_run:
        res["verdict"] = "DRY"
        return res
    env = dict(os.environ, PYTHONPATH=str(REPO / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""))
    t0 = time.monotonic()
    log = out_root / f"{r['id']}.log"
    try:
        with open(log, "w") as fh:
            fh.write(" ".join(cmd) + "\n")
            fh.flush()
            res["exit"] = run_group(cmd, fh, env,
                                    args.timeout or suite.get("defaults", {}).get("timeout_s", 1800))
    except subprocess.TimeoutExpired:
        res.update(verdict="ERROR", secs=time.monotonic() - t0)
        res["notes"].append("timeout")
        return res
    res["secs"] = time.monotonic() - t0
    rep_path = out / "cmr_report_latest.json"
    if not rep_path.exists():
        res["verdict"] = "ERROR"
        res["notes"].append(f"no report (exit {res['exit']}), see {log}")
        return res
    report = json.loads(rep_path.read_text())
    problems = []
    key = f"{r['id']}@{args.quality}" if args.quality else r["id"]
    exp = expected.get(key, expected.get(r["id"]))
    if exp is None:
        res["notes"].append("no expectation")
    elif not args.meta:                     # meta runs disable checks: expectations do not apply
        problems += compare.compare_expected(report, exp, res["exit"])
    base = suite_dir / "baselines" / f"{key}.json"
    if args.update_baseline:
        base.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(rep_path, base)
        res["notes"].append("baseline updated")
    elif base.exists() and not args.meta:
        problems += compare.compare_baseline(report, json.loads(base.read_text()))
    res["problems"] = problems
    res["verdict"] = "FAIL" if problems else ("PASS" if exp is not None else "NEW")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the private QC suite and compare with expectations")
    ap.add_argument("suite", nargs="?", default=os.environ.get("CMR_SUITE"))
    ap.add_argument("--tag", action="append", default=[], help="smoke | acceptance | meta | ... (OR)")
    ap.add_argument("--run", action="append", default=[], help="run id (repeatable; ignores tags)")
    ap.add_argument("--slow", action="store_true", help="include runs tagged slow")
    ap.add_argument("--quality", choices=["fast", "high"],
                    help="override quality (default: fast for --tag smoke, else the profile's)")
    ap.add_argument("--meta", action="store_true", help="probe-level checks only (no decode pass)")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--timeout", type=int, default=0, help="seconds per run (default suite timeout_s)")
    ap.add_argument("--max-size-mb", type=float, default=None,
                    help="skip larger files (default 500 for smoke, else no limit)")
    ap.add_argument("--mode", choices=["background", "foreground"], default="background")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if not args.suite:
        ap.error("suite folder: argument or $CMR_SUITE")
    suite_dir = Path(args.suite).resolve()
    suite = load_yaml(suite_dir / "suite.yaml")
    smoke = "smoke" in args.tag
    if args.quality is None and smoke:
        args.quality = "fast"
    if args.max_size_mb is None:
        args.max_size_mb = 500 if smoke else 0
    runs = select(suite["runs"], args.tag, args.run, args.slow)
    if not runs:
        print("no runs selected")
        return 2
    tier = "meta" if args.meta else ("_".join(args.tag) or "runs")
    out_root = suite_dir / "runs" / f"{dt.datetime.now():%Y%m%d_%H%M%S}_{tier}"
    out_root.mkdir(parents=True, exist_ok=True)
    expected: dict = {}
    for proj in {r["project"] for r in runs}:
        ep = suite_dir / "projects" / proj / "expected.yaml"
        if ep.exists():
            expected.update((load_yaml(ep) or {}).get("runs") or {})
    results = []
    for r in runs:                          # one file at a time (shared file server)
        res = run_one(r, args, suite, suite_dir, out_root, expected)
        results.append(res)
        print(f"{res['verdict']:5} {res['id']:24} exit={res['exit']!s:4} {res['secs']:6.1f}s  "
              + "; ".join(res["notes"]), flush=True)
        for p in res.get("problems", []):
            print(f"        - {p}")
    (out_root / "summary.json").write_text(json.dumps(results, indent=1, default=str))
    bad = [r for r in results if r["verdict"] in ("FAIL", "ERROR")]
    print(f"{len(results)} runs: " + ", ".join(
        f"{v} {sum(r['verdict'] == v for r in results)}"
        for v in ("PASS", "FAIL", "ERROR", "NEW", "SKIP", "DRY") if any(r['verdict'] == v for r in results))
        + f"; reports in {out_root}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
