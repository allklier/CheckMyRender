"""cmr command line (argparse).

  cmr check FILE... --profile P --spec S [--report json -o DIR] [--fail-on warn|fail] [-v]
  cmr probe FILE...
  cmr profiles list | show NAME [--spec S]
  cmr doctor
Exit codes: 0 pass, 1 warn (unless --fail-on fail), 2 fail, 3 tool/config error.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import yaml

from . import __version__, engine
from .config import ConfigError, list_profiles, load_profile_raw, load_spec, resolve_profile
from .media import ProbeError, probe
from .model import EXIT_ERROR, EXIT_PASS
from .report import build_report, to_json
from .report.console import render


def cmd_check(a) -> int:
    try:
        spec, spec_sha = load_spec(Path(a.spec))
        profile, shas = resolve_profile(a.profile, spec)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_ERROR
    filters = engine.available_filters(a.ffmpeg)
    results = [engine.run_file(Path(f), spec, profile, a.ffmpeg, a.ffprobe, filters,
                               a.filter_threads) for f in a.files]
    report = build_report(results, profile["name"], shas, str(a.spec), spec_sha,
                          engine.ffmpeg_version(a.ffmpeg), a.fail_on)
    sys.stdout.write(render(report, a.verbose))
    if a.report == "json":
        out = Path(a.output)
        out.mkdir(parents=True, exist_ok=True)
        (out / "cmr_report.json").write_text(to_json(report))
        print(f"report: {out / 'cmr_report.json'}")
    return report["summary"]["exit_code"]


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
    c.set_defaults(func=cmd_check)

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
