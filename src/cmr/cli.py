"""cmr command line (argparse).

  cmr check FILE... --profile P --spec S [--report json -o DIR] [--fail-on warn|fail] [-v]
            [--waivers WAIVERS.yaml] [--max-findings N]
  cmr waive REPORT.json [--finding N ... | --all-check CHECK_ID] --reason TEXT [--expires D]
            [-o WAIVERS.yaml]          (no --finding/--all-check: list waivable findings)
  cmr waive --category CHECK_ID [--class KIND] [--edge E | --channel C] [--frames A-B]
            --max-severity info|warn|fail --reason TEXT [--expires D] [-o WAIVERS.yaml]
  cmr probe FILE...
  cmr profiles list | show NAME [--spec S]
  cmr doctor
Exit codes: 0 pass, 1 warn (unless --fail-on fail), 2 fail, 3 tool/config error.
"""
from __future__ import annotations

import argparse
import datetime
import json
import shutil
import sys
from pathlib import Path

import yaml

from . import __version__, engine, runmode, waivers
from .config import ConfigError, list_profiles, load_profile_raw, load_spec, resolve_profile
from .media import ProbeError, probe
from .model import EXIT_ERROR, EXIT_PASS, Severity
from .report import build_report, to_json
from .report.console import render, scope as render_scope

DEFAULT_MAX_FINDINGS = 10   # findings listed per check (`cmr check --max-findings`)


def cmd_check(a) -> int:
    try:
        spec, spec_sha = load_spec(Path(a.spec))
        profile, shas = resolve_profile(a.profile, spec)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_ERROR
    wlist = None
    if a.waivers:
        try:
            wlist = waivers.load(a.waivers)
        except ConfigError as e:
            print(f"config error: {e}", file=sys.stderr)
            return EXIT_ERROR
    mode_threads = runmode.apply(a.mode)
    threads = a.filter_threads if a.filter_threads is not None else mode_threads
    filters = engine.available_filters(a.ffmpeg)
    results = [engine.run_file(Path(f), spec, profile, a.ffmpeg, a.ffprobe, filters,
                               threads) for f in a.files]
    wsum = None
    if wlist is not None:   # after the main pass: decode only the waived ranges (D17)
        today, state, ws = datetime.date.today(), {}, waivers.settings(profile)
        for r in results:
            if r["timeline"] is None:
                continue
            fp = waivers.Fingerprinter(r["path"], r["timeline"]["fps"], a.ffmpeg, a.ffprobe)
            waivers.apply(r["findings"], wlist,
                          lambda w: fp.fingerprint(w["check_id"], w["stream"], w["fingerprint"]["frames"]),
                          ws, today, state)
            r["worst"] = max((f.severity for f in r["findings"]), default=Severity.PASS).name
        wsum = waivers.summary(a.waivers, wlist, state, today)
    # findings cap: an explicit --max-findings wins over profile `max_findings` per check
    if a.max_findings is not None:
        cap, limits = a.max_findings, {}
    else:
        cap = DEFAULT_MAX_FINDINGS
        limits = {cid: cfg["max_findings"] for cid, cfg in (profile.get("checks") or {}).items()
                  if isinstance(cfg, dict) and "max_findings" in cfg}
    report = build_report(results, profile["name"], shas, str(a.spec), spec_sha,
                          engine.ffmpeg_version(a.ffmpeg), a.fail_on, wsum, cap, limits)
    sys.stdout.write(render(report, a.verbose))
    if a.report == "json":
        out = Path(a.output)
        out.mkdir(parents=True, exist_ok=True)
        (out / "cmr_report.json").write_text(to_json(report))
        print(f"report: {out / 'cmr_report.json'}")
    return report["summary"]["exit_code"]


def _frames(v: str) -> tuple[int, int]:
    try:
        lo, hi = (int(x) for x in v.split("-"))
    except ValueError:
        raise ValueError(f"--frames {v!r}: use A-B (inclusive file frame numbers)") from None
    if not 0 <= lo <= hi:
        raise ValueError(f"--frames {v!r}: need 0 <= A <= B")
    return lo, hi


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
                                  expires=waivers.parse_date(a.expires))
        wl.append(w)
        waivers.save(out, wl)
    except (OSError, ValueError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    print(f"{w['id']}: category {render_scope(w)}")
    print(f"waivers: {out} (1 added, {len(wl)} total)")
    return EXIT_PASS


def cmd_waive(a) -> int:
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
        if rep.get("schema_version") not in ("1.0", "1.1", "1.2"):
            raise ValueError(f"unsupported report schema_version {rep.get('schema_version')!r}")
        expires = waivers.parse_date(a.expires)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    allf = [(n, fi, fd) for n, (fi, fd) in enumerate(
        ((fi, fd) for fi in rep["files"] for fd in fi["findings"]), start=1)]
    # findings over the report's cap (schema 1.2) are not in the report, so not waivable here
    for fi in rep["files"]:
        for x in fi.get("suppressed", []):
            if not a.all_check or x["check_id"] == a.all_check:
                print(f"note: {x['count']} {x['check_id']} finding(s) of {fi['path']} were not "
                      f"listed (cap {x['limit']}); re-run `cmr check --max-findings 0` to waive "
                      f"them one by one, or use `cmr waive --category`", file=sys.stderr)
    if not a.finding and not a.all_check:
        for n, fi, fd in allf:
            if waivers.waivable(fd):
                print(f"{n:4} {fd['severity']:5} {fd['check_id']}: {fd['message']} @ "
                      f"{fd['timecode_start']}-{fd['timecode_end']} ({fi['path']})")
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
            fp = fps_cache[path].for_range(fd["check_id"], fd["stream"], fd["frame_start"], fd["frame_end"])
            w = waivers.make_waiver(waivers.next_id(wl), fd, fp, a.reason, today, expires)
            wl.append(w)
            print(f"{w['id']}: {fd['check_id']} @ {fd['timecode_start']}-{fd['timecode_end']} "
                  f"(finding {n})")
        waivers.save(out, wl)
    except (OSError, ValueError, ConfigError, waivers.FingerprintError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    print(f"waivers: {out} ({len(pick)} added, {len(wl)} total)")
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
            spec, _ = load_spec(Path(a.spec))
            prof, _ = resolve_profile(a.name, spec)
        else:
            prof, _ = load_profile_raw(a.name)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_ERROR
    engine.load_checks()   # show the effective `quality` of checks that have a fast mode
    for cid, cfg in (prof.get("checks") or {}).items():
        chk = engine.REGISTRY.get(cid)
        if chk is not None and len(chk.qualities) > 1 and isinstance(cfg, dict):
            cfg.setdefault("quality", "high")
    print(yaml.safe_dump(prof, sort_keys=False), end="")
    return EXIT_PASS


def cmd_doctor(a) -> int:
    ok = True
    for b in (a.ffmpeg, a.ffprobe):
        path = shutil.which(b)
        print(f"{b}: {path or 'NOT FOUND'}")
        ok &= path is not None
    print(f"ffmpeg version: {engine.ffmpeg_version(a.ffmpeg)}")
    engine.load_checks()
    filters = engine.available_filters(a.ffmpeg) if shutil.which(a.ffmpeg) else set()
    for cid, chk in sorted(engine.REGISTRY.items()):
        missing = [f for f in chk.requires_filters if f not in filters]
        print(f"check {cid}: {'ok' if not missing else 'MISSING filters ' + ', '.join(missing)}")
        ok &= not missing
    for mod, err in engine.IMPORT_ERRORS.items():
        print(f"plugin {mod}: IMPORT ERROR {err}")
        ok = False
    print("OK" if ok else "PROBLEMS FOUND")
    return EXIT_PASS if ok else EXIT_ERROR


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cmr", description="Render QC")
    p.add_argument("--version", action="version", version=f"cmr {__version__}")
    p.add_argument("--ffmpeg", default="ffmpeg")
    p.add_argument("--ffprobe", default="ffprobe")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="check files against a profile + spec")
    c.add_argument("files", nargs="+")
    c.add_argument("--profile", required=True, help="profile name (profiles/NAME.yaml) or path")
    c.add_argument("--spec", required=True, help="spec sheet YAML")
    c.add_argument("--report", choices=["json"], help="write a report file (HTML/JUnit = v1.1)")
    c.add_argument("-o", "--output", help="report output directory")
    c.add_argument("--fail-on", choices=["warn", "fail"], default="warn",
                   help="lowest severity that gives a non-zero exit (default warn)")
    c.add_argument("-v", "--verbose", action="store_true", help="also list PASS results")
    c.add_argument("--filter-threads", type=int, metavar="N",
                   help="ffmpeg -filter_complex_threads (default: min(8, CPUs/2))")
    c.add_argument("--mode", choices=runmode.MODES, default="foreground",
                   help="foreground (default): fastest QC; background: lower CPU/IO priority, "
                        "2 filter threads, 16 GB memory cap, so other apps are not disturbed")
    c.add_argument("--waivers", metavar="FILE", help="waiver file (YAML) from `cmr waive`")
    c.add_argument("--max-findings", type=int, metavar="N",
                   help=f"list at most N problem findings per check, summarise the rest (still "
                        f"counted and in the exit code); 0 = all. Default {DEFAULT_MAX_FINDINGS}, "
                        f"or the profile's per-check `max_findings`; this flag overrides both")
    c.set_defaults(func=cmd_check)

    w = sub.add_parser("waive", help="create waivers: per finding (from a JSON report) or per "
                                     "check category (--category)")
    w.add_argument("report", nargs="?", help="cmr_report.json written by `cmr check --report json` "
                                             "(not with --category)")
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
    w.add_argument("-o", "--output", default="waivers.yaml", help="waiver file (default waivers.yaml)")
    w.set_defaults(func=cmd_waive)

    pr = sub.add_parser("probe", help="show the mapped probe data checks see")
    pr.add_argument("files", nargs="+")
    pr.set_defaults(func=cmd_probe)

    pf = sub.add_parser("profiles", help="list or show profiles")
    pfs = pf.add_subparsers(dest="action", required=True)
    pfs.add_parser("list")
    sh = pfs.add_parser("show", help="show merged profile (substituted when --spec is given)")
    sh.add_argument("name")
    sh.add_argument("--spec")
    pf.set_defaults(func=cmd_profiles)

    d = sub.add_parser("doctor", help="check ffmpeg/ffprobe and filters needed by checks")
    d.set_defaults(func=cmd_doctor)

    a = p.parse_args(argv)
    if a.cmd == "check" and a.report and not a.output:
        p.error("--report needs -o DIR")
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
