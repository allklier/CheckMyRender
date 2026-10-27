"""cmr command line (argparse).

  cmr check [FILE...] [--profile P] [--spec S] [--job J | --job-dir D | --no-job]
            [--deliverables CSV] [--row VALUE]
            [--report json [-o DIR]] [--fail-on warn|fail] [-v] [--quality fast|high]
            [--waivers WAIVERS.yaml] [--max-findings N]
            (report: DIR/cmr_report_<YYYYmmdd-HHMMSS>_<run id>.json + copy cmr_report_latest.json;
             DIR defaults to the current directory)
            D31: a job file (./job.yaml, --job, --job-dir) may name spec, profile, waivers and
            the report folder (`output`, which also turns the JSON report on); options win.
            No FILE: the job file's `input:` picks the file (highest `version` group of the
            spec's job.filename_regex); FILE arguments win over it.
  cmr waive REPORT.json [--finding N ... | --all-check CHECK_ID] --reason TEXT [--expires D]
            [-o WAIVERS.yaml] [--job J | --job-dir D | --no-job] [--any-job]
            (no --finding/--all-check: list waivable findings)
  cmr waive --category CHECK_ID [--class KIND] [--edge E | --channel C] [--frames A-B]
            --max-severity info|warn|fail --reason TEXT [--expires D] [-o WAIVERS.yaml]
            [--job J | --job-dir D | --no-job] [--any-job]
            (in a job: default -o = the job's waiver file, entries stamped `job: <job.id>`)
  cmr probe FILE...
  cmr profiles list | show NAME [--spec S]
  cmr doctor
  cmr logs path | tail [-n N] | collect [-o FILE] [--reports DIR [--last N]]
Every command also takes --log-level debug|info|warn|error|off, --log-file F, --log-private (D29).
Exit codes: 0 pass, 1 warn (unless --fail-on fail), 2 fail, 3 tool/config/usage error.
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import logging
import os
import re
import shutil
import sys
import tarfile
import time
import traceback
from pathlib import Path

import yaml

from . import __version__, engine, log, runmode, waivers
from .config import (JOB_FILE, ConfigError, MissingSpecKey, build_spec, find_job, is_disabled,
                     job_settings, list_profiles, load_profile_raw, pick_input, resolve_profile)
from .media import ProbeError, probe
from .model import EXIT_ERROR, EXIT_PASS, Severity
from .report import build_report, to_json
from .report.console import render, scope as render_scope

DEFAULT_MAX_FINDINGS = 10   # findings listed per check (`cmr check --max-findings`)
REPORT_LATEST = "cmr_report_latest.json"   # copy of the newest report in the -o folder
LOG = log.get("cli")
# option values / bare words that are never private (kept verbatim in the logged command line)
SAFE_OPTS = {"--mode", "--fail-on", "--log-level", "--report", "--filter-threads", "--quality",
             "--max-findings", "--finding", "--max-severity", "--edge", "--channel", "--class",
             "-n", "--last"}
SAFE_WORDS = {"check", "waive", "probe", "profiles", "list", "show", "doctor", "logs", "path",
              "tail", "collect"}


def _profile_name(name: str) -> str:
    """Built-in profile names are not private; a path or own name is redacted."""
    return name if name in list_profiles() else log.redact("path" if "/" in name else "arg", name)


class _Parser(argparse.ArgumentParser):
    """Usage errors exit 3 (tool/config error, D8) instead of argparse's 2, which is FAIL."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, f"{self.prog}: error: {message}\n")


def check_profile(profile: dict, substituted: bool) -> None:
    """Check params against each check's params_schema (engine.validate_profile_params):
    a misspelt or ill-typed param is a ConfigError (exit 3) before anything runs."""
    errs = engine.validate_profile_params(profile, substituted)
    if errs:
        raise ConfigError(f"profile {profile.get('name')!r} params invalid:\n  " + "\n  ".join(errs))


def apply_quality(profile: dict, quality: str) -> tuple[list[str], list[str]]:
    """`cmr check --quality Q`: set `quality: Q` on every enabled check that implements Q
    (overrides the profile's `quality`); checks without that mode keep theirs (high).
    Returns (check ids set to Q, enabled check ids that do not have Q)."""
    engine.load_checks()
    applied, kept = [], []
    checks = profile.get("checks") or {}
    for cid, cfg in checks.items():
        chk = engine.REGISTRY.get(cid)
        if is_disabled(cfg) or chk is None:
            continue
        if quality in chk.qualities:
            checks[cid] = {**(cfg or {}), "quality": quality}
            applied.append(cid)
        else:
            kept.append(cid)
    return applied, kept


def write_report(report: dict, out: Path) -> Path:
    """DIR/cmr_report_<YYYYmmdd-HHMMSS>_<run id>.json (unique: the run id is the log's) plus a
    copy DIR/cmr_report_latest.json (replaced atomically). Returns the unique path."""
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"cmr_report_{time.strftime('%Y%m%d-%H%M%S')}_{log.run_id()}.json"
    text = to_json(report)
    path.write_text(text)
    tmp = out / f".{REPORT_LATEST}.{os.getpid()}.tmp"
    tmp.write_text(text)
    os.replace(tmp, out / REPORT_LATEST)
    return path


def _log_profile(profile: dict) -> None:
    """Resolved profile at DEBUG: per check enabled/severity/quality/cap and params (numbers
    verbatim; strings and structures are redacted in default mode: they can hold spec values)."""
    if not LOG.isEnabledFor(logging.DEBUG):
        return
    for cid, cfg in (profile.get("checks") or {}).items():
        cfg = cfg if isinstance(cfg, dict) else {}
        params = {k: v if v is None or isinstance(v, (bool, int, float)) or log.private()
                  else log.redact("value", json.dumps(v, default=str))
                  for k, v in cfg.items() if k not in engine.RESERVED_KEYS}
        LOG.debug("profile %s: enabled=%s severity=%s quality=%s max_findings=%s params=%s", cid,
                  cfg.get("enabled", True), cfg.get("severity", "fail"), cfg.get("quality", "high"),
                  cfg.get("max_findings", "-"), params)


def _job(a) -> dict | None:
    """job_settings() of the run's job file (D31: --job, --job-dir, else ./job.yaml unless
    --no-job), or None. ConfigError for an explicit job file that is missing or invalid."""
    path = find_job(a.job, a.job_dir, a.no_job)
    return job_settings(path) if path else None


def _config_error(e: ConfigError, spec=None, job=None) -> int:
    """Print + log a config error (missing ${spec.*} keys with the field to fill in, D31)."""
    msg = e.explain(spec, job) if isinstance(e, MissingSpecKey) else str(e)
    LOG.error("config error: %s", log.redact_quoted(msg))
    print(f"config error: {msg}", file=sys.stderr)
    return EXIT_ERROR


def apply_job(a, js: dict | None) -> str | None:
    """Fill the run options the command line left out from the job file (D31; explicit options
    win): spec, profile, waivers (the job's file when it exists), report folder (`output`
    also turns the JSON report on, also when -o moves it). No FILE arguments need the job's
    `input:` block (picked later, pick_input). Returns a usage problem, else None."""
    if js:
        a.spec = a.spec or js["spec"]
        a.profile = a.profile or js["profile"]
        if a.waivers is None and Path(js["waivers"]).is_file():
            a.waivers = js["waivers"]
        elif a.waivers is None and js["waivers_named"]:
            LOG.info("job waiver file %s does not exist yet: no waivers",
                     log.redact("path", js["waivers"]))
        if js["output"]:      # the job wants JSON reports; -o only moves them
            a.output, a.report = a.output or js["output"], a.report or "json"
    if not a.files and not (js and js["input"]):
        return ("no file to check: give FILE(s), or a job file with an `input:` block "
                "(folder + pattern) that says which file to check")
    miss = [o for o, v in (("--spec", a.spec), ("--profile", a.profile)) if not v]
    if not miss:
        return None
    where = (f"the job file {js['path']} names none" if js else
             f"no {JOB_FILE} in the current folder (or use --job / --job-dir)")
    return f"{' and '.join(miss)} required: {where}"


def pick_job_input(a, js: dict | None, spec: dict) -> dict | None:
    """FILE arguments win over the job's `input:` (logged); without them the job input picks
    the one file (config.pick_input; ConfigError = exit 3). Sets a.files; returns the pick."""
    if a.files:
        if js and js["input"]:
            LOG.info("files given on the command line: the job file's `input:` is ignored")
        return None
    sel = pick_input(js["input"], spec.get("job") or {})
    a.files = [sel["path"]]
    LOG.info("input: picked %s (version %d of %d conforming) from %s pattern %s; %d notice(s)",
             log.redact("path", sel["picked"]), sel["version"], sel["conforming"],
             log.redact("path", sel["folder"]), log.redact("arg", sel["pattern"]), len(sel["notices"]))
    for n in sel["notices"]:
        LOG.warning("input notice: %s %s", log.redact("path", n["file"]), log.redact_quoted(n["message"]))
    print(f"input: {sel['picked']} (version {sel['version']}) from {sel['folder']}"
          + (f"; {len(sel['notices'])} file(s) not considered, see NOTICE" if sel["notices"] else ""),
          file=sys.stderr)
    return sel


def cmd_check(a) -> int:
    try:
        js = _job(a)
    except ConfigError as e:
        return _config_error(e)
    problem = apply_job(a, js)
    if problem:
        LOG.error("usage: %s", log.redact_quoted(problem))
        print(f"error: {problem}", file=sys.stderr)
        return EXIT_ERROR
    job_path = js["path"] if js else None
    if js and not (a.job or a.job_dir):
        print(f"job file: {job_path}" + (f" (job {js['id']})" if js["id"] else ""), file=sys.stderr)
    filters = None
    try:
        filters = engine.available_filters(a.ffmpeg)
    except OSError as e:
        LOG.warning("ffmpeg -filters failed: %s", e)
    log.snapshot(LOG, a.ffmpeg, a.ffprobe, filters)
    LOG.info("check: %d file(s) %s; profile %s spec %s job %s (id %s, %s) deliverables %s row %s "
             "waivers %s; mode %s fail-on %s max-findings %s quality %s", len(a.files),
             [log.redact("path", f) for f in a.files], _profile_name(a.profile),
             log.redact("path", a.spec), job_path and log.redact("path", job_path),
             js and js["id"] and log.redact("name", js["id"]),
             "--job" if a.job else "--job-dir" if a.job_dir else "found in cwd" if js else "none",
             a.deliverables and log.redact("path", a.deliverables),
             a.row and log.redact("row", a.row), a.waivers and log.redact("path", a.waivers),
             a.mode, a.fail_on, a.max_findings, a.quality or "profile")
    try:
        with log.phase(LOG, "config load"):
            spec, inputs = build_spec(a.spec, job_path, a.deliverables, a.row)
            spec_sha = inputs["spec"]["sha256"]
            profile, shas = resolve_profile(a.profile, spec)
            check_profile(profile, substituted=True)
            sel = pick_job_input(a, js, spec)
    except ConfigError as e:
        return _config_error(e, a.spec, job_path)
    if a.quality:
        applied, kept = apply_quality(profile, a.quality)
        LOG.info("quality %s (--quality, overrides the profile): set on %s; without a %s mode, "
                 "kept as profiled: %s", a.quality, applied, a.quality, kept)
    LOG.info("inputs: profile %s sha256 %s; spec sha256 %s; job sha256 %s; waivers sha256 %s",
             _profile_name(profile["name"]), [x[:16] for x in shas], spec_sha[:16],
             (inputs.get("job") or {}).get("sha256", "-")[:16],
             log.sha256_file(a.waivers)[:16] if a.waivers else "-")
    _log_profile(profile)
    wlist, foreign, job_id = None, [], js["id"] if js else None
    if a.waivers:
        try:
            wlist = waivers.load(a.waivers)
        except ConfigError as e:
            LOG.error("waiver file error: %s", log.redact_quoted(str(e)))
            print(f"config error: {e}", file=sys.stderr)
            return EXIT_ERROR
        wlist, foreign = waivers.split_job(wlist, job_id)   # D31: only this job's waivers
        LOG.info("waivers: %d loaded (%d category), %d of another job skipped", len(wlist),
                 sum(map(waivers.is_category, wlist)), len(foreign))
    mode_threads = runmode.apply(a.mode)
    threads = a.filter_threads if a.filter_threads is not None else mode_threads
    LOG.info("run mode %s: filter threads %s (%s), %s", a.mode, threads or engine.default_filter_threads(),
             "--filter-threads" if a.filter_threads is not None else "mode" if mode_threads
             else "engine default", log.limits())
    if filters is None:
        filters = engine.available_filters(a.ffmpeg)
    results = [engine.run_file(Path(f), spec, profile, a.ffmpeg, a.ffprobe, filters,
                               threads) for f in a.files]
    wsum = None
    if wlist is not None:   # after the main pass: decode only the waived ranges (D17)
        with log.phase(LOG, "waivers"):
            today, state, ws = datetime.date.today(), {}, waivers.settings(profile)
            for r in results:
                if r["timeline"] is None:
                    continue
                log.set_file(r["path"])
                fp = waivers.Fingerprinter(r["path"], r["timeline"]["fps"], a.ffmpeg, a.ffprobe)
                waivers.apply(r["findings"], wlist,
                              lambda w: fp.fingerprint(w["check_id"], w["stream"], w["fingerprint"]["frames"],
                                                        w["fingerprint"].get("version")),
                              ws, today, state)
                r["worst"] = max((f.severity for f in r["findings"]), default=Severity.PASS).name
            log.set_file(None)
            wsum = waivers.summary(a.waivers, wlist, state, today, foreign)
        LOG.info("waivers: %s", {k: len(wsum[k]) for k in ("applied", "content_changed", "stale",
                                                          "expired", "foreign")})
    # findings cap: an explicit --max-findings wins over profile `max_findings` per check
    if a.max_findings is not None:
        cap, limits = a.max_findings, {}
    else:
        cap = DEFAULT_MAX_FINDINGS
        limits = {cid: cfg["max_findings"] for cid, cfg in (profile.get("checks") or {}).items()
                  if isinstance(cfg, dict) and "max_findings" in cfg}
    with log.phase(LOG, "report build"):
        report = build_report(results, profile["name"], shas, str(a.spec), spec_sha,
                              engine.ffmpeg_version(a.ffmpeg), a.fail_on, wsum, cap, limits,
                              job=inputs.get("job"), mode=a.mode,
                              quality=a.quality or "profile", job_id=job_id, input_sel=sel)
    sys.stdout.write(render(report, a.verbose))
    if a.report == "json":
        out = Path(a.output or ".")
        try:
            with log.phase(LOG, "report write"):
                path = write_report(report, out)
        except OSError as e:
            LOG.error("report write failed: %s", e)
            print(f"error: cannot write the report: {e}", file=sys.stderr)
            return EXIT_ERROR
        LOG.info("report written: %s (+ %s)", log.redact("path", path), REPORT_LATEST)
        print(f"report: {path} (also {out / REPORT_LATEST})")
    s = report["summary"]
    LOG.info("summary: worst %s exit %d counts %s suppressed %d; %s", s["worst"], s["exit_code"],
             {k: v for k, v in s["counts"].items() if v}, s["suppressed"], log.memory())
    return report["summary"]["exit_code"]


def _frames(v: str) -> tuple[int, int]:
    try:
        lo, hi = (int(x) for x in v.split("-"))
    except ValueError:
        raise ValueError(f"--frames {v!r}: use A-B (inclusive file frame numbers)") from None
    if not 0 <= lo <= hi:
        raise ValueError(f"--frames {v!r}: need 0 <= A <= B")
    return lo, hi


def _short(path: str, base: str | None) -> str:
    """A media path for listings (D31): relative to the job folder when inside it, else the
    file's base name."""
    p = Path(os.path.abspath(path))   # not resolve(): a symlinked render stays in the job folder
    try:
        return str(p.relative_to(os.path.abspath(base or Path.cwd())))
    except ValueError:
        return p.name


def _waive_job(a) -> dict | None:
    """Job of a `cmr waive` run (D31): sets the default -o (the job's waiver file, else
    ./waivers.yaml) and `a.stamp` (the job id written into each entry; `--any-job` = "*")."""
    js = _job(a)
    if a.output is None:
        a.output = js["waivers"] if js else "waivers.yaml"
    a.stamp = waivers.ANY_JOB if a.any_job else (js["id"] if js else None)
    return js


def _stamp_note(a) -> str:
    return f" (job {a.stamp})" if a.stamp else ""


def cmd_waive_category(a) -> int:
    """One category waiver (D17 extension): no report, no media, no fingerprint."""
    out = Path(a.output)
    try:
        if not a.reason:
            raise ValueError("--reason is required")
        if not a.max_severity:
            raise ValueError("--max-severity info|warn|fail is required with --category")
        engine.load_checks()
        if a.category not in engine.REGISTRY:
            raise ValueError(f"unknown check id {a.category!r}")
        kind = waivers.media_kind(a.category)
        if a.edge and kind != "video" or a.channel is not None and kind != "audio":
            raise ValueError("--edge applies to video.* checks, --channel to audio.* checks")
        wl = waivers.load(out) if out.exists() else []
        w = waivers.make_category(waivers.next_id(wl), a.category, a.max_severity, a.reason,
                                  datetime.date.today(), kind=a.kind, edge=a.edge,
                                  channel=a.channel, frames=_frames(a.frames) if a.frames else None,
                                  expires=waivers.parse_date(a.expires), job=a.stamp)
        wl.append(w)
        waivers.save(out, wl)
    except (OSError, ValueError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    print(f"{w['id']}: category {render_scope(w)}")
    print(f"waivers: {out}{_stamp_note(a)} (1 added, {len(wl)} total)")
    return EXIT_PASS


def cmd_waive(a) -> int:
    try:
        js = _waive_job(a)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_ERROR
    base = js["dir"] if js else None
    cat_only = [o for o, v in (("--class", a.kind), ("--edge", a.edge), ("--channel", a.channel),
                               ("--frames", a.frames), ("--max-severity", a.max_severity))
                if v is not None]
    if a.category:
        if a.report or a.finding or a.all_check:
            print("error: --category takes no REPORT, --finding or --all-check", file=sys.stderr)
            return EXIT_ERROR
        return cmd_waive_category(a)
    if cat_only:
        print(f"error: {', '.join(cat_only)} only with --category", file=sys.stderr)
        return EXIT_ERROR
    if not a.report:
        print("error: give a REPORT.json, or --category CHECK_ID", file=sys.stderr)
        return EXIT_ERROR
    try:
        rep = json.loads(Path(a.report).read_text())
        if rep.get("schema_version") not in ("1.0", "1.1", "1.2", "1.3", "1.4", "1.5"):
            raise ValueError(f"unsupported report schema_version {rep.get('schema_version')!r}")
        expires = waivers.parse_date(a.expires)
        rjob = rep["summary"].get("job_id")
        if rjob and a.stamp and not a.any_job and rjob != a.stamp:
            raise ValueError(f"the report is from job {rjob!r} but the job file says {a.stamp!r}")
        if not a.any_job:
            a.stamp = a.stamp or rjob
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    allf = [(n, fi, fd) for n, (fi, fd) in enumerate(
        ((fi, fd) for fi in rep["files"] for fd in fi["findings"]), start=1)]
    # findings over the report's cap (schema 1.2) are not in the report, so not waivable here
    for fi in rep["files"]:
        for x in fi.get("suppressed", []):
            if not a.all_check or x["check_id"] == a.all_check:
                print(f"note: {x['count']} {x['check_id']} finding(s) of {_short(fi['path'], base)} were not "
                      f"listed (cap {x['limit']}); re-run `cmr check --max-findings 0` to waive "
                      f"them one by one, or use `cmr waive --category`", file=sys.stderr)
    if not a.finding and not a.all_check:
        for n, fi, fd in allf:
            if waivers.waivable(fd):
                print(f"{n:4} {fd['severity']:5} {fd['check_id']}: {fd['message']} @ "
                      f"{fd['timecode_start']}-{fd['timecode_end']} ({_short(fi['path'], base)})")
        return EXIT_PASS
    if not a.reason:
        print("error: --reason is required to create waivers", file=sys.stderr)
        return EXIT_ERROR
    if a.finding:
        bad = [n for n in a.finding if not (1 <= n <= len(allf)) or not waivers.waivable(allf[n - 1][2])]
        if bad:
            print(f"error: finding(s) {bad} are not waivable (run without --finding to list)",
                  file=sys.stderr)
            return EXIT_ERROR
        pick = [allf[n - 1] for n in a.finding]
    else:
        pick = [x for x in allf if x[2]["check_id"] == a.all_check and waivers.waivable(x[2])]
        if not pick:
            print(f"error: no waivable findings of {a.all_check}", file=sys.stderr)
            return EXIT_ERROR
    out = Path(a.output)
    try:
        wl = waivers.load(out) if out.exists() else []
        today, fps_cache = datetime.date.today(), {}
        for n, fi, fd in pick:
            path = Path(fi["path"])
            if path not in fps_cache:   # the media must be the file the report was made from
                if engine.file_identity(path) != fi["identity"]:
                    raise ValueError(f"{path} differs from the file in the report (identity)")
                fps_cache[path] = waivers.Fingerprinter(path, fi["timeline"]["fps"], a.ffmpeg, a.ffprobe)
            if waivers.media_kind(fd["check_id"]) == "slate":   # exact OCR text (D27)
                fp = waivers.text_fingerprint(fd.get("evidence"))
            else:
                fp = fps_cache[path].for_range(fd["check_id"], fd["stream"], fd["frame_start"],
                                               fd["frame_end"])
            w = waivers.make_waiver(waivers.next_id(wl), fd, fp, a.reason, today, expires, a.stamp)
            wl.append(w)
            print(f"{w['id']}: {fd['check_id']} @ {fd['timecode_start']}-{fd['timecode_end']} "
                  f"(finding {n})")
        waivers.save(out, wl)
    except (OSError, ValueError, ConfigError, waivers.FingerprintError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    print(f"waivers: {out}{_stamp_note(a)} ({len(pick)} added, {len(wl)} total)")
    return EXIT_PASS


def cmd_probe(a) -> int:
    rc = EXIT_PASS
    for f in a.files:
        try:
            print(json.dumps(probe(Path(f), a.ffprobe).to_dict(), indent=2))
        except (OSError, ProbeError) as e:
            print(f"error: {e}", file=sys.stderr)
            rc = EXIT_ERROR
    return rc


def cmd_profiles(a) -> int:
    try:
        if a.action == "list":
            print("\n".join(list_profiles()))
            return EXIT_PASS
        if a.spec:
            spec, _ = build_spec(a.spec, a.job)
            prof, _ = resolve_profile(a.name, spec)
        else:
            prof, _ = load_profile_raw(a.name)
        check_profile(prof, substituted=bool(a.spec))
    except ConfigError as e:
        return _config_error(e, a.spec, a.job)
    engine.load_checks()   # show the effective `quality` of checks that have a fast mode
    for cid, cfg in (prof.get("checks") or {}).items():
        chk = engine.REGISTRY.get(cid)
        if chk is not None and len(chk.qualities) > 1 and isinstance(cfg, dict):
            cfg.setdefault("quality", "high")
    print(yaml.safe_dump(prof, sort_keys=False), end="")
    return EXIT_PASS


TESTED_FFMPEG = "7.1.1"   # the ffmpeg the installer pins on every machine (installer/environment.yml)


def untested_ffmpeg_note(name: str, version_line: str) -> str | None:
    """A WARNING line when `<name> -version`'s first line is not ffmpeg 7.1.x (never a problem)."""
    m = re.search(r" version n?(\d+)\.(\d+)", version_line)
    if m and f"{m[1]}.{m[2]}" == TESTED_FFMPEG.rsplit(".", 1)[0]:
        return None
    got = f"{m[1]}.{m[2]}" if m else "unreadable"
    return (f"WARNING: {name} {got} is not the tested version {TESTED_FFMPEG}: results may differ "
            f"from other machines (the installer installs {TESTED_FFMPEG} unless --use-system-ffmpeg)")


def cmd_doctor(a) -> int:
    log.snapshot(LOG, a.ffmpeg, a.ffprobe)
    ok = True
    for b in (a.ffmpeg, a.ffprobe):
        path = shutil.which(b)
        print(f"{b}: {path or 'NOT FOUND'}")
        ok &= path is not None
    print(f"ffmpeg version: {engine.ffmpeg_version(a.ffmpeg)}")
    for b in (a.ffmpeg, a.ffprobe):   # informational only: never makes doctor fail
        if shutil.which(b):
            note = untested_ffmpeg_note(Path(b).name, engine.ffmpeg_version(b))
            if note:
                print(note)
    from .checks.slate import ocr   # optional: only the slate OCR checks need it (D27)
    try:
        print(f"tesseract (slate checks): {ocr.resolve_bin(ocr.tesseract_bin({}))}")
    except ocr.OCRUnavailable as e:
        print(f"tesseract (slate checks): NOT FOUND - {e}")
    engine.load_checks()
    filters = engine.available_filters(a.ffmpeg) if shutil.which(a.ffmpeg) else set()
    for cid, chk in sorted(engine.REGISTRY.items()):
        missing = [f for f in chk.requires_filters if f not in filters]
        print(f"check {cid}: {'ok' if not missing else 'MISSING filters ' + ', '.join(missing)}")
        ok &= not missing
    for mod, err in engine.IMPORT_ERRORS.items():
        print(f"plugin {mod}: IMPORT ERROR {err}")
        ok = False
    print(f"log: {log.log_path(getattr(a, 'log_file', None))}")
    jf = Path.cwd() / JOB_FILE    # D31: an invalid job.yaml here is a doctor problem (a run would fail)
    if not jf.is_file():
        print(f"{JOB_FILE} in the current folder: none")
    else:
        try:
            js = job_settings(jf)
            print(f"{JOB_FILE} in the current folder: {jf}" + (f" (job {js['id']})" if js["id"] else
                                                                " (no job.id)"))
        except ConfigError as e:
            print(f"{JOB_FILE} in the current folder: {jf} - INVALID: {e}")
            ok = False
    print("OK" if ok else "PROBLEMS FOUND")
    return EXIT_PASS if ok else EXIT_ERROR


def cmd_logs(a) -> int:
    path = log.log_path(getattr(a, "log_file", None))
    if a.action == "path":
        print(path)
        return EXIT_PASS
    if a.action == "tail":
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError as e:
            print(f"error: {e}", file=sys.stderr)
            return EXIT_ERROR
        print("\n".join(lines[-a.n:]))
        return EXIT_PASS
    # collect: logs/ (cmr.log*; never the salt) + environment.txt (+ last N reports)
    out = Path(a.output or f"cmr-support-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz")
    try:
        reports = []
        if a.reports:
            reports = sorted(Path(a.reports).rglob("*.json"), key=lambda f: f.stat().st_mtime)
            reports = reports[-a.last:] if a.last > 0 else []
        env = "\n".join(log.environment(a.ffmpeg, a.ffprobe)) + "\n"
        with tarfile.open(out, "w:gz") as tar:
            for f in sorted(path.parent.glob(path.name + "*")):
                tar.add(f, arcname=f"logs/{f.name}")
            info = tarfile.TarInfo("environment.txt")
            info.size, info.mtime = len(env.encode()), int(time.time())
            tar.addfile(info, io.BytesIO(env.encode()))
            for i, f in enumerate(reports, 1):
                tar.add(f, arcname=f"reports/{i:02d}_{f.name}")
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    LOG.info("support bundle written: %s (%d report(s))", log.redact("path", out), len(reports))
    print(f"support bundle: {out}")
    if reports:
        print("note: the reports are included verbatim (file paths, slate text)")
    return EXIT_PASS


def main(argv=None) -> int:
    # logging options, accepted before or after the subcommand (SUPPRESS: no default clobbering)
    lo = _Parser(add_help=False, argument_default=argparse.SUPPRESS)
    lo.add_argument("--log-level", choices=[k for k in log.LEVELS],
                    help="diagnostic log level (default: env CMR_LOG_LEVEL, else "
                         f"{log.DEFAULT_LEVEL if log.LOG_DEFAULT_ENABLED else 'off'})")
    lo.add_argument("--log-file", metavar="FILE",
                    help="log file (default $CMR_LOG_DIR/cmr.log, else ~/.cache/cmr/logs/cmr.log)")
    lo.add_argument("--log-private", action="store_true",
                    help="log file names, paths and slate text verbatim (default: hashed; "
                         "env CMR_LOG_PRIVATE=1)")
    p = _Parser(prog="cmr", description="Render QC", parents=[lo])
    p.add_argument("--version", action="version", version=f"cmr {__version__}")
    p.add_argument("--ffmpeg", default="ffmpeg")
    p.add_argument("--ffprobe", default="ffprobe")
    sub = p.add_subparsers(dest="cmd", required=True)

    # D31: the job file (./job.yaml, --job, --job-dir) as the entry point of a job
    jo = _Parser(add_help=False)
    jg = jo.add_mutually_exclusive_group()
    jg.add_argument("--job", metavar="JOB.yaml",
                    help=f"job file (default: ./{JOB_FILE} when it exists): job constants, "
                         "deliverables CSV, slate template (merged into the spec), and job.id, "
                         "spec, profile, waivers, output for the run")
    jg.add_argument("--job-dir", metavar="DIR", help=f"use DIR/{JOB_FILE} as the job file")
    jg.add_argument("--no-job", action="store_true", help=f"ignore ./{JOB_FILE}")

    c = sub.add_parser("check", help="check files against a profile + spec", parents=[lo, jo])
    c.add_argument("files", nargs="*", metavar="FILE",
                   help="file(s) to check (default: the one file the job file's `input:` "
                        "picks: highest version number in its folder)")
    c.add_argument("--profile", help="profile name (profiles/NAME.yaml) or path (default: the "
                                     "job file's `profile`)")
    c.add_argument("--spec", help="spec sheet YAML (default: the job file's `spec`)")
    c.add_argument("--deliverables", metavar="CSV",
                   help="deliverables CSV (one row per spot); overrides deliverables.csv")
    c.add_argument("--row", metavar="VALUE",
                   help="select the deliverables row: KEY value, COLUMN=VALUE or #N (1-based)")
    c.add_argument("--report", choices=["json"],
                   help="write a JSON report: DIR/cmr_report_<YYYYmmdd-HHMMSS>_<run id>.json "
                        f"plus a copy DIR/{REPORT_LATEST} (HTML/JUnit = later)")
    c.add_argument("-o", "--output", metavar="DIR",
                   help="report folder (created; default: the job file's `output`, which also "
                        "turns on the JSON report, else the current directory)")
    c.add_argument("--fail-on", choices=["warn", "fail"], default="warn",
                   help="lowest severity that gives a non-zero exit (default warn)")
    c.add_argument("-v", "--verbose", action="store_true", help="also list PASS results")
    c.add_argument("--filter-threads", type=int, metavar="N",
                   help="ffmpeg -filter_complex_threads (default: min(8, CPUs/2))")
    c.add_argument("--mode", choices=runmode.MODES, default="foreground",
                   help="foreground (default): fastest QC; background: lower CPU/IO priority, "
                        "2 filter threads, 16 GB memory cap, so other apps are not disturbed")
    c.add_argument("--quality", choices=["fast", "high"],
                   help="override the profile's `quality` of every check that has this mode "
                        "(checks without a fast mode stay high); recorded in the report")
    c.add_argument("--waivers", metavar="FILE", help="waiver file (YAML) from `cmr waive` "
                   f"(default: the job file's `waivers`, else {JOB_FILE}'s folder/waivers.yaml, "
                   "when it exists)")
    c.add_argument("--max-findings", type=int, metavar="N",
                   help=f"list at most N problem findings per check, summarise the rest (still "
                        f"counted and in the exit code); 0 = all. Default {DEFAULT_MAX_FINDINGS}, "
                        f"or the profile's per-check `max_findings`; this flag overrides both")
    c.set_defaults(func=cmd_check)

    w = sub.add_parser("waive", parents=[lo, jo], help="create waivers: per finding (from a JSON report) or per "
                                     "check category (--category)")
    w.add_argument("report", nargs="?", help="JSON report written by `cmr check --report json`, "
                                             f"e.g. DIR/{REPORT_LATEST} (not with --category)")
    g = w.add_mutually_exclusive_group()
    g.add_argument("--finding", type=int, nargs="+", metavar="N",
                   help="finding number(s); run without --finding/--all-check to list them")
    g.add_argument("--all-check", metavar="CHECK_ID", help="every waivable finding of this check")
    g.add_argument("--category", metavar="CHECK_ID",
                   help="category waiver: every finding of this check (narrowed by --class, "
                        "--edge/--channel, --frames) whose raw severity is <= --max-severity; "
                        "no fingerprint, no report needed")
    w.add_argument("--class", dest="kind", metavar="KIND",
                   help="--category: evidence class, e.g. dark_edge, matte, edge (video.blanking), "
                        "integrated/true_peak/lra (audio.loudness)")
    loc = w.add_mutually_exclusive_group()
    loc.add_argument("--edge", choices=waivers.EDGES, help="--category: picture edge (video checks)")
    loc.add_argument("--channel", type=int, metavar="C", help="--category: audio channel, 1-based")
    w.add_argument("--frames", metavar="A-B", help="--category: inclusive file frame range")
    w.add_argument("--max-severity", choices=waivers.CEILINGS,
                   help="--category (required): waive only findings whose raw severity is at "
                        "most this")
    w.add_argument("--reason", help="why the finding is acceptable (required to write)")
    w.add_argument("--expires", metavar="YYYY-MM-DD", help="optional last day the waiver applies")
    w.add_argument("-o", "--output", help="waiver file (default: the job's waiver file, else "
                                          "./waivers.yaml)")
    w.add_argument("--any-job", action="store_true",
                   help='stamp the new waivers `job: "*"` (they apply to every job; default: '
                        "the job id of the job file, else of the report)")
    w.set_defaults(func=cmd_waive)

    pr = sub.add_parser("probe", help="show the mapped probe data checks see", parents=[lo])
    pr.add_argument("files", nargs="+")
    pr.set_defaults(func=cmd_probe)

    pf = sub.add_parser("profiles", help="list or show profiles", parents=[lo])
    pfs = pf.add_subparsers(dest="action", required=True)
    pfs.add_parser("list", parents=[lo])
    sh = pfs.add_parser("show", parents=[lo], help="show merged profile (substituted when --spec is given)")
    sh.add_argument("name")
    sh.add_argument("--spec")
    sh.add_argument("--job", help="job file merged into --spec")
    pf.set_defaults(func=cmd_profiles)

    d = sub.add_parser("doctor", help="check ffmpeg/ffprobe and filters needed by checks", parents=[lo])
    d.set_defaults(func=cmd_doctor)

    lg = sub.add_parser("logs", parents=[lo], help="diagnostic log: path, tail, support bundle")
    lgs = lg.add_subparsers(dest="action", required=True)
    lgs.add_parser("path", parents=[lo], help="print the log file location")
    t = lgs.add_parser("tail", parents=[lo], help="show the last lines of the log")
    t.add_argument("-n", type=int, default=40, help="number of lines (default 40)")
    co = lgs.add_parser("collect", parents=[lo],
                        help="write a support bundle (tar.gz: logs/, environment.txt, reports/)")
    co.add_argument("-o", "--output", help="bundle file (default ./cmr-support-<timestamp>.tar.gz)")
    co.add_argument("--reports", metavar="DIR", help="also include the newest JSON reports under DIR")
    co.add_argument("--last", type=int, default=5, metavar="N", help="with --reports: how many (default 5)")
    lg.set_defaults(func=cmd_logs)

    a = p.parse_args(argv)
    if a.cmd == "logs" and a.action in ("path", "tail"):
        return a.func(a)      # reading the log does not write to it
    log.setup(getattr(a, "log_level", None), getattr(a, "log_file", None),
              True if getattr(a, "log_private", False) else None)
    t0 = time.monotonic()
    LOG.info("start cmr %s: %s", __version__,
             log.redact_argv(sys.argv[1:] if argv is None else list(argv), SAFE_OPTS,
                             SAFE_WORDS | set(list_profiles())))
    rc = EXIT_ERROR
    try:
        rc = a.func(a)
    except Exception:   # unhandled: full traceback into the log, exit code 3 (D8)
        LOG.exception("unhandled exception")
        traceback.print_exc()
        print(f"cmr: internal error (exit {EXIT_ERROR}); details in the log: "
              f"{log.log_path(getattr(a, 'log_file', None))}", file=sys.stderr)
    except KeyboardInterrupt:
        LOG.warning("interrupted (KeyboardInterrupt)")
        raise
    finally:
        LOG.info("end: exit %s in %.3f s; %s", rc, time.monotonic() - t0, log.memory())
        log.set_file(None)
    return rc


if __name__ == "__main__":
    sys.exit(main())
