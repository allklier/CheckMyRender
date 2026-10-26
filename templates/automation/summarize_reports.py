#!/usr/bin/env python3
"""Morning summary of a folder of CheckMyRender JSON reports (from overnight_qc.sh).

    python3 summarize_reports.py REPORT_DIR [--all]

Prints one block per file: verdict, exit code, job, counts, and every finding that is not
PASS (WAIVED findings only with --all). Reads the newest report of each folder:
REPORT_DIR/*/cmr_report_latest.json (one folder per file, as overnight_qc.sh writes them) and
REPORT_DIR/cmr_report_latest.json (a single `cmr check -o REPORT_DIR`). Reports of older cmr
versions (cmr_report.json) are read too. Needs only Python 3 (no extra packages).
"""
import json
import sys
from pathlib import Path

LATEST, OLD = "cmr_report_latest.json", "cmr_report.json"


def find_reports(root: Path) -> list:
    """The newest report per folder: cmr_report_latest.json, else an old cmr_report.json."""
    out = []
    for d in [root] + sorted(p for p in root.iterdir() if p.is_dir()):
        for name in (LATEST, OLD):
            if (d / name).is_file():
                out.append(d / name)
                break
    return out


def main(argv):
    if not argv:
        print(__doc__)
        return 64
    root, show_all = Path(argv[0]), "--all" in argv[1:]
    reports = find_reports(root) if root.is_dir() else []
    if not reports:
        print(f"no {LATEST} under {root}")
        return 1
    worst_exit = 0
    for rp in reports:
        rep = json.loads(rp.read_text())
        s = rep["summary"]
        worst_exit = max(worst_exit, s["exit_code"])
        counts = ", ".join(f"{k} {v}" for k, v in s["counts"].items() if v)
        job = f", job {s['job_id']}" if s.get("job_id") else ""
        for f in rep["files"]:
            print(f"{f['worst']:7} exit {s['exit_code']}  {f['path']}")
            print(f"        profile {rep['profile']['name']}{job}, {counts}")
            for fd in f["findings"]:
                waived = fd.get("waiver_ref") is not None
                if fd["severity"] == "PASS" and not (waived and show_all):
                    continue
                sev = "WAIVED" if waived else fd["severity"]
                where = ""
                if fd.get("timecode_start"):
                    where = f" @ {fd['timecode_start']} (frame {fd['frame_start']})"
                print(f"  {sev:7} {fd['check_id']}: {fd['message']}{where}")
            for x in f.get("suppressed", []):
                print(f"  ({x['count']} more {x['check_id']} findings not listed; "
                      f"worst {x['worst_severity']})")
        print()
    print(f"{len(reports)} report(s); worst exit code {worst_exit}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
