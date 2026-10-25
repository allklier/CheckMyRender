"""Measure recall / false positives of the blanking detector on generated media.

Usage: PYTHONPATH=src python3 tests/blanking/measure.py MEDIADIR [WORKDIR|--engine]
  --engine: run through the core engine (cmr.engine.run_file) instead of the
            standalone harness; candidates are taken from the findings.

Per clip: planted defects hit (a non-persistent candidate on the same edge
overlapping the defect's frames, any kind; frame-exact = same start/end), false-positive candidates (any
candidate not overlapping a planted defect, excluding persistent mattes on
edges the manifest lists as intentional), and how intentional mattes were
reported. Prints a markdown table.
"""
import json
import sys
import tempfile
from pathlib import Path

from cmr.checks.blanking import detector


def score_clip(clip: dict, cands: list[dict]) -> dict:
    hit, exact, fp, mattes, below = 0, 0, [], [], []
    used = set()
    for d in clip["defects"]:
        if not d.get("expect", True):   # planted below the coverage threshold
            got = any(c.evidence[0]["edge"] == d["edge"] and c.frame_start <= d["end"]
                      and c.frame_end >= d["start"] for c in cands)
            below.append(f"{d['coverage']:.0%}:{'found' if got else 'missed'}")
            continue
        for i, c in enumerate(cands):
            ev = c.evidence[0]
            if (ev["edge"] == d["edge"] and c.frame_start <= d["end"]
                    and c.frame_end >= d["start"]):
                hit += 1
                exact += (c.frame_start, c.frame_end) == (d["start"], d["end"])
                used.add(i)
                break
    for i, c in enumerate(cands):
        ev = c.evidence[0]
        if i in used or any(not d.get("expect", True) and ev["edge"] == d["edge"]
                            and c.frame_start <= d["end"] and c.frame_end >= d["start"]
                            for d in clip["defects"]):
            continue
        overlaps_defect = any(ev["edge"] == d["edge"] and c.frame_start <= d["end"]
                              and c.frame_end >= d["start"] for d in clip["defects"])
        if overlaps_defect:
            continue  # second candidate for an already-hit defect
        if ev["edge"] in clip["mattes"] and ev["kind"] == "matte" and ev["persistent"]:
            mattes.append(ev["edge"])
            continue
        fp.append(f"{ev['edge']}/{ev['kind']}{'/P' if ev['persistent'] else ''}"
                  f"@{c['frame_start']}-{c['frame_end']}")
    return {"planted": sum(d.get("expect", True) for d in clip["defects"]), "hit": hit,
            "exact": exact, "fp": fp, "mattes": mattes, "below": below}


def candidates(path, workdir):
    if workdir != "--engine":
        return detector.run_standalone(str(path), None, workdir)
    from cmr import engine
    res = engine.run_file(path, {}, {"checks": {detector.CHECK_ID: {}}})
    return [f.candidate for f in res["findings"] if f.candidate is not None]


def main(mediadir, workdir=None):
    mediadir = Path(mediadir)
    workdir = workdir or tempfile.mkdtemp()
    man = json.loads((mediadir / "manifest.json").read_text())
    print("| clip | planted | detected | frame-exact | recall | FP | FP detail |"
          " intentional mattes (persistent) | below coverage |")
    print("|---|---|---|---|---|---|---|---|---|")
    tot_p = tot_h = tot_fp = 0
    for name, clip in man["clips"].items():
        cands = candidates(mediadir / clip["file"], workdir)
        r = score_clip(clip, cands)
        tot_p, tot_h, tot_fp = tot_p + r["planted"], tot_h + r["hit"], tot_fp + len(r["fp"])
        rec = f"{r['hit'] / r['planted']:.0%}" if r["planted"] else "n/a"
        print(f"| {name} | {r['planted']} | {r['hit']} | {r['exact']} | {rec} | {len(r['fp'])} | "
              f"{', '.join(r['fp']) or '-'} | {', '.join(r['mattes']) or '-'} | "
              f"{', '.join(r['below']) or '-'} |")
    print(f"| **total** | {tot_p} | {tot_h} | | {tot_h / tot_p:.0%} | {tot_fp} | | | |")


if __name__ == "__main__":
    main(*sys.argv[1:])
