"""Report assembly (versioned, schema-validated JSON) and console output."""
from __future__ import annotations

import json
import platform
from collections import Counter

import jsonschema

from .. import __version__, log
from ..model import SCHEMA_VERSION, Finding, Severity, exit_code, file_verdict
from ..schemas import REPORT_SCHEMA

LOG = log.get("report")


def _span(fs: list[Finding]) -> dict:
    a = [f for f in fs if f.frame_start is not None]
    if not a:
        return {"frame_start": None, "frame_end": None, "timecode_start": None, "timecode_end": None}
    lo = min(a, key=lambda f: f.frame_start)
    hi = max(a, key=lambda f: f.frame_end if f.frame_end is not None else f.frame_start)
    end = hi.frame_end is not None
    return {"frame_start": lo.frame_start, "frame_end": hi.frame_end if end else hi.frame_start,
            "timecode_start": lo.timecode_start,
            "timecode_end": hi.timecode_end if end else hi.timecode_start}


def cap_findings(findings: list[Finding], limit_for) -> tuple[list[Finding], list[dict], list[dict]]:
    """Findings cap per check id (`limit_for(check_id)` -> N, 0 = unlimited).
    Returns (listed, suppressed, waived_by_category):
    - findings WAIVED by a category waiver are not listed one by one (one entry per check and
      waiver with the count) unless the check is unlimited; they never count against the cap;
      per-finding WAIVED findings and PASS/SKIPPED results are always listed;
    - of the remaining problem findings (INFO..ERROR) of a check, the first N by severity
      (worst first), then frame, are listed (in the original report order); the rest become
      one `suppressed` entry {check_id, count, worst_severity, by_severity, limit, span}.
    Callers count and judge on the FULL list; this only shapes what is listed."""
    listed_ids, suppressed, waived = set(), [], []
    by_check: dict[str, list[Finding]] = {}
    for f in findings:
        by_check.setdefault(f.check_id, []).append(f)
    for cid, fs in by_check.items():
        n = limit_for(cid)
        cat = {}
        for f in fs:
            if f.waiver_ref and (f.waiver or {}).get("type") == "category" and n:
                cat.setdefault(f.waiver_ref, []).append(f)
            elif f.waiver_ref or f.severity < Severity.INFO:
                listed_ids.add(id(f))
        for wid, wf in cat.items():
            waived.append({"check_id": cid, "waiver_id": wid, "count": len(wf),
                           "worst_raw_severity": max(f.raw_severity for f in wf).name, **_span(wf)})
        probs = [f for f in fs if not f.waiver_ref and f.severity >= Severity.INFO]
        if n and len(probs) > n:
            probs.sort(key=lambda f: (-f.severity, -1 if f.frame_start is None else f.frame_start))
            keep, rest = probs[:n], probs[n:]
            suppressed.append({"check_id": cid, "count": len(rest), "limit": n,
                               "worst_severity": max(f.severity for f in rest).name,
                               "by_severity": dict(Counter(f.severity.name for f in rest)),
                               **_span(rest)})
        else:
            keep = probs
        listed_ids.update(id(f) for f in keep)
    return [f for f in findings if id(f) in listed_ids], suppressed, waived


def build_report(files: list[dict], profile_name: str, profile_shas: list[str], spec_path: str,
                 spec_sha: str, ffmpeg_version: str, fail_on: str, waivers: dict | None = None,
                 max_findings: int = 0, check_limits: dict | None = None,
                 job: dict | None = None, mode: str | None = None,
                 quality: str = "profile", job_id: str | None = None) -> dict:
    """`max_findings` (0 = unlimited, the default for library callers) and `check_limits`
    {check_id: N} shape the listed findings (cap_findings); worst, exit code and counts
    always come from ALL findings. File and run verdicts: SKIPPED alone counts as PASS
    (model.file_verdict). `mode`/`quality`: run mode (D26) and `cmr check --quality`
    ("profile" = not given), recorded in the summary (1.3). `job_id`: job.yaml `job.id` (D31,
    summary 1.4; None without one)."""
    verdicts = [file_verdict([Severity[f["worst"]]]) for f in files]
    worst = max(verdicts, default=Severity.PASS)
    # a WAIVED finding (severity lowered to PASS, raw_severity kept) has its own count
    counts = Counter("WAIVED" if fd.waiver_ref else fd.severity.name
                     for f in files for fd in f["findings"])
    limits = check_limits or {}
    out_files = []
    for f, verdict in zip(files, verdicts):
        if verdict.name != f["worst"]:
            LOG.info("file verdict PASS: its only results besides PASS are SKIPPED")
        listed, sup, wcat = cap_findings(f["findings"], lambda c: limits.get(c, max_findings))
        for s in sup:
            LOG.info("cap: %s %d finding(s) suppressed (limit %d, worst %s, %s)", s["check_id"],
                     s["count"], s["limit"], s["worst_severity"], s["by_severity"])
        for w in wcat:
            LOG.info("cap: %s %d finding(s) waived by category %s, summarised", w["check_id"],
                     w["count"], w["waiver_id"])
        out_files.append({**f, "worst": verdict.name, "findings": [fd.to_dict() for fd in listed],
                          "suppressed": sup, "waived_by_category": wcat})
    report = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "cmr", "version": __version__, "ffmpeg": ffmpeg_version,
                 "python": platform.python_version()},
        "profile": {"name": profile_name, "sha256": profile_shas},
        "spec": {"path": spec_path, "sha256": spec_sha, **({"job": job} if job else {})},
        "files": out_files,
        "summary": {"worst": worst.name, "fail_on": fail_on, "exit_code": exit_code(worst, fail_on),
                    "counts": {**{s.name: counts.get(s.name, 0) for s in Severity},
                               "WAIVED": counts.get("WAIVED", 0)},
                    "mode": mode, "quality": quality, "job_id": job_id,
                    "max_findings": max_findings, "check_limits": limits,
                    "suppressed": sum(s["count"] for f in out_files for s in f["suppressed"]),
                    "waived_by_category": sum(w["count"] for f in out_files
                                              for w in f["waived_by_category"])},
    }
    if waivers is not None:
        report["waivers"] = waivers
    # round-trip through JSON so Fractions etc. become strings, then validate
    report = json.loads(json.dumps(report, default=str))
    jsonschema.validate(report, REPORT_SCHEMA)
    return report


def to_json(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=False) + "\n"
