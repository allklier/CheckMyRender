"""Blanking (edge defect) detector: stage 1, high recall.

Per frame and per edge, one tap in the single decode pass looks, PIXEL BY PIXEL
along the edge line, for a SHARP STEP from black to picture within the outer
max_depth px (v3; v1 compared the outer pixel with the pixel max_depth px in, which
flagged dark picture that brightens inward, e.g. a dark curtain or stage floor):

  crop strip of max_depth+1 px (exact=1) -> format=yuv444p -> shuffleplanes=0:0:0
  -> split -> crops -> lut2 stages (details: chains()) -> signalstats
  defect pixel = outer <= black_luma and adjacent - outer >= min_step     (1 px deep)
              or outer <= matte_luma and, at some depth k < max_depth,
                 s[k] <= matte_luma and s[k+1] - s[k] >= min_step        (2..max_depth)
  matte pixel  = outer and inner (max_depth px in) <= matte_luma
  level        = max(outer, inner)
  keys: YAVG/255 = defect coverage, UAVG/255 = matte coverage, VHIGH = brightness.
Why: a blanking/mask defect is a hard edge (digital black next to picture);
dark picture is a gradient: neighbouring pixels differ by 0-2 code values (real
stage-play H.264, measured), so a per-neighbour step separates them where the
outer-vs-16px contrast did not. Deeper (2+ px) bands must be true black
(<= matte_luma): planted bands measured 13-18, dark picture bands 20-29.

Coverage = fraction of the edge line's pixels. Per frame and edge:
  frame black (all four edges VHIGH <= matte_luma)       -> skip (black-frame check)
  defect coverage >= coverage - coverage_tolerance       -> "edge" (1..max_depth px deep)
  matte coverage  >= coverage - coverage_tolerance       -> "matte" (deeper bar:
      letterbox/pillarbox, deep blanking); skip if the whole frame is dark
      (all edges VHIGH <= dark_frame_luma, e.g. inside a fade)
Consecutive frames with the same label form one Candidate; skip frames neither
break nor extend a run. A run covering >= persistent_fraction of the edge's
non-skip frames is marked persistent (consistent over time: intentional matte,
or a whole-clip 1px column).

quality: fast measures file frames 0, s, 2s, ... only (s = fast_frame_step, default
5; `select` before the crops). A defect lasting >= s consecutive frames is always
seen; a shorter one is seen only if it covers a multiple of s. Sampled runs report
the sampled first..last frame plus the possible true extent (+- s-1 frames).

Why per-pixel: signalstats only offers YMIN/YLOW(10th pct)/YAVG/YHIGH(90th pct)/
YMAX for luma (no median; full key list printed on ffmpeg 6.1.1), so no strip
statistic means ">= 50% black". Binarising first makes YAVG the coverage.

Verified on ffmpeg 6.1.1 (`ffmpeg -h filter=crop|signalstats|metadata|split|
lut2|shuffleplanes`; 5.1.10 on Rocky 9 runs the same graph): crop needs exact=1 (1px-wide crop on 4:2:2 fails without
it); signalstats on a 1px-wide 4:2:2 crop segfaults and reports native bit
depth, hence format=yuv444p (8-bit; `scale=out_range=tv` first: ffmpeg 7.1 keeps full
range in that conversion, 5.1/6.1 mapped yuvj to tv); lut2 x/y = input 0/1,
c0..c2 per plane; YAVG prints with 3 decimals.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from cmr.model import Candidate, Finding, Severity

CHECK_ID = "video.blanking"
EDGES = ("top", "bottom", "left", "right")
K_DEFECT, K_MATTE, K_LEVEL = ("lavfi.signalstats.YAVG", "lavfi.signalstats.UAVG",
                              "lavfi.signalstats.VHIGH")
KEYS = (K_DEFECT, K_MATTE, K_LEVEL)
DARK = "_dark"   # tap name suffix: soft (dark-edge) coverage tap per edge, key K_DEFECT only

# Tuning knobs (profile params). Luma values are 8-bit code values (tv black = 16).
DEFAULTS = {
    "coverage": 0.5,             # share of the edge line that must be black
    "coverage_tolerance": 0.02,  # slack for codec ringing at mask transitions (planted 50% measured 0.486-0.499)
    "black_luma": 32,            # outer pixel <= this is black (ProRes ringing lifts black pixels to ~29-32)
    "matte_luma": 20,            # true black: inner matte pixel, 2+ px deep defects, black-frame level
    "min_step": 6,               # neighbour step black -> picture for a sharp "edge" pixel (dark picture: 0-2)
    "min_contrast": 4,           # inner - outer for a "dark_edge" pixel (soft: no sharp step needed)
    "dark_frame_luma": 48,       # all edges VHIGH <= this: frame too dark to judge a "matte"
    "max_depth": 16,             # px searched for the step; deeper black = "matte"
    "min_run_frames": 1,         # shortest run reported
    "persistent_fraction": 0.9,  # run covering this share of the edge's frames = persistent
    "fast_frame_step": 5,        # quality: fast examines every Nth file frame only
    "merge_gap_frames": 250,     # dark_edge / matte runs of one edge closer than this merge
    "dark_edge_mode": "flag",    # dark_edge findings: flag (class severity) | info | hide (count only)
    "class_severity": {          # severity per class, capped by the profile `severity`
        "edge": "FAIL",
        "edge_persistent": "FAIL",
        "dark_edge": "FAIL",          # dark_edge_mode decides flag/info/hide
        "dark_edge_persistent": "FAIL",
        "matte": "WARN",
        "matte_persistent": "INFO",
    },
}


def params_with_defaults(params: dict | None) -> dict:
    p = {**DEFAULTS, **(params or {})}
    p["class_severity"] = {**DEFAULTS["class_severity"],
                           **((params or {}).get("class_severity") or {})}
    return p


def _geometry(edge: str, d: int) -> dict[str, str]:
    """Crops: `strip` (outer d+1 px, from the frame) and, inside the strip, `outer` /
    `adj` / `inner` line (depth 0 / 1 / d), `near` (depths 0..d-1) and `far` (depths
    1..d): pixel k of `near` and of `far` are neighbours along the depth axis."""
    if edge in ("top", "bottom"):
        ln, blk = (lambda y: f"w=iw:h=1:x=0:y={y}"), (lambda y: f"w=iw:h={d}:x=0:y={y}")
        strip = f"w=iw:h={d + 1}:x=0:y=" + ("0" if edge == "top" else f"ih-{d + 1}")
    else:
        ln, blk = (lambda x: f"w=1:h=ih:x={x}:y=0"), (lambda x: f"w={d}:h=ih:x={x}:y=0")
        strip = f"w={d + 1}:h=ih:x=" + ("0" if edge == "left" else f"iw-{d + 1}") + ":y=0"
    if edge in ("top", "left"):
        return dict(strip=strip, outer=ln(0), adj=ln(1), inner=ln(d), near=blk(0), far=blk(1))
    return dict(strip=strip, outer=ln(d), adj=ln(d - 1), inner=ln(0), near=blk(1), far=blk(0))


def _max_reduce(edge: str, d: int, src: str, pre: str) -> tuple[list[str], str]:
    """Filtergraph segments reducing a d px deep flag block to one line by per-pixel
    max along the depth axis (lut2 max of two overlapping halves, repeated)."""
    segs, n, i = [], d, 0
    while n > 1:
        h = (n + 1) // 2
        a, b, o = f"{pre}m{i}a", f"{pre}m{i}b", f"{pre}m{i}"
        if edge in ("top", "bottom"):
            ca, cb = f"w=iw:h={h}:x=0:y=0", f"w=iw:h={h}:x=0:y={n - h}"
        else:
            ca, cb = f"w={h}:h=ih:x=0:y=0", f"w={h}:h=ih:x={n - h}:y=0"
        segs.append(f"[{src}]split[{a}][{b}];[{a}]crop={ca}:exact=1[{a}c];"
                    f"[{b}]crop={cb}:exact=1[{b}c];[{a}c][{b}c]lut2=c0='max(x,y)':"
                    f"c1='max(x,y)':c2='max(x,y)'[{o}]")
        src, n, i = o, h, i + 1
    return segs, src


def chains(params: dict | None, frame_step: int = 1) -> dict[str, str]:
    """{tap name: filter chain}: per edge one tap `<edge>` (keys KEYS) and one tap
    `<edge>_dark` (key K_DEFECT only). Each chain takes one video input and ends in
    signalstats; internal labels are prefixed `bk<edge>` / `bd<edge>` (unique).
    frame_step > 1 (quality: fast) prepends `select='not(mod(n,step))'`: only file
    frames 0, step, 2*step, ... are measured (n = input frame number from 0).
    Per pixel of the edge line (o = outer, a = adjacent, i = inner pixel, s[k] = pixel
    k px in), lut2 stages (8-bit):
      [l] (o, i): c0 = 255 if o <= matte_luma, 128 if o <= black_luma, else 0;
                  c1 = matte pixel (o <= black_luma, i <= matte_luma); c2 = max(o, i)
      [a] (o, a): c0 = 255 if o <= black_luma and a - o >= min_step   (1 px defect)
      [s] (s[k], s[k+1]) for k = 0..d-1: 255 if s[k] <= matte_luma and
                  s[k+1] - s[k] >= min_step; max-reduced over k to one line
      defect = [a] or ([l] c0 == 255 and [s])                   (2..max_depth px)
    `_dark` tap (v1 rule, kept for review): Y = 255 if o <= black_luma and
      i - o >= min_contrast: black-ish outer pixel, brighter inside, no sharp step
      required. Frames where only this covers the line are class `dark_edge`."""
    p = params_with_defaults(params)
    bl, ml, ms, mc = p["black_luma"], p["matte_luma"], p["min_step"], p["min_contrast"]
    d = int(p["max_depth"])
    sel = f"select='not(mod(n,{frame_step}))'," if frame_step > 1 else ""
    out = {}
    for e in EDGES:
        g, k = _geometry(e, d), f"bk{e}"
        red, top = _max_reduce(e, d, f"{k}s", k)
        out[e] = ";".join([
            f"{sel}crop={g['strip']}:exact=1,scale=out_range=tv,format=yuv444p,shuffleplanes=0:0:0,"
            f"split=5[{k}n][{k}f][{k}o][{k}i][{k}a]",
            f"[{k}n]crop={g['near']}:exact=1[{k}nc];[{k}f]crop={g['far']}:exact=1[{k}fc];"
            f"[{k}nc][{k}fc]lut2=c0='255*lte(x,{ml})*gte(y-x,{ms})':c1='0':c2='0'[{k}s]",
            *red,
            f"[{k}o]split[{k}o1][{k}o2];[{k}o1]crop={g['outer']}:exact=1[{k}oc];"
            f"[{k}i]crop={g['inner']}:exact=1[{k}ic];"
            f"[{k}oc][{k}ic]lut2=c0='255*lte(x,{ml})+128*gt(x,{ml})*lte(x,{bl})':"
            f"c1='255*lte(x,{bl})*lte(y,{ml})':c2='max(x,y)'[{k}l]",
            f"[{k}o2]crop={g['outer']}:exact=1[{k}o2c];[{k}a]crop={g['adj']}:exact=1[{k}ac];"
            f"[{k}o2c][{k}ac]lut2=c0='255*lte(x,{bl})*gte(y-x,{ms})'[{k}j]",
            f"[{k}l][{k}j]lut2=c0='if(gt(y,0),255,if(eq(x,255),128,0))':c1='x':c2='x'[{k}lj]",
            f"[{k}lj][{top}]lut2=c0='255*(eq(x,255)+eq(x,128)*gt(y,0))':c1='x':c2='x',"
            f"signalstats"])
        k = f"bd{e}"
        out[e + DARK] = (f"{sel}crop={g['strip']}:exact=1,scale=out_range=tv,format=yuv444p,shuffleplanes=0:0:0,"
                         f"split[{k}o][{k}i];[{k}o]crop={g['outer']}:exact=1[{k}oc];"
                         f"[{k}i]crop={g['inner']}:exact=1[{k}ic];"
                         f"[{k}oc][{k}ic]lut2=c0='255*lte(x,{bl})*gte(y-x,{mc})',signalstats")
    return out


def tap_keys(name: str) -> tuple[str, ...]:
    return (K_DEFECT,) if name.endswith(DARK) else KEYS


# edge -> frame -> (defect, matte, level[, dark]); dark = soft (v1) coverage, 0 if absent
Stats = dict[str, dict[int, tuple[float, ...]]]


def stats_from_records(records: dict[str, list]) -> Stats:
    """Engine MetaRecords {tap: [MetaRecord(frame, pts_time, values)]} -> stats."""
    out = {}
    for e in EDGES:
        dark = {r.frame: float(r.values[K_DEFECT]) / 255 for r in records.get(e + DARK, [])
                if K_DEFECT in r.values}
        out[e] = {r.frame: (float(r.values[K_DEFECT]) / 255, float(r.values[K_MATTE]) / 255,
                            float(r.values[K_LEVEL]), dark.get(r.frame, 0.0))
                  for r in records.get(e, []) if all(k in r.values for k in KEYS)}
    return out


def classify(stats: Stats, params: dict | None = None):
    """Per frame and edge: "edge" (sharp step), "dark_edge" (soft: dark picture or
    soft-edged black, for review), "matte", None (picture) or "skip".
    Returns {edge: [(frame, label), ...]} in frame order."""
    p = params_with_defaults(params)
    cov = p["coverage"] - p["coverage_tolerance"]
    frames = sorted(set.intersection(*(set(v) for v in stats.values())))
    out = {e: [] for e in EDGES}
    for f in frames:
        s = {e: stats[e][f] for e in EDGES}
        black = all(v[2] <= p["matte_luma"] for v in s.values())
        dark = all(v[2] <= p["dark_frame_luma"] for v in s.values())
        for e, (defect, matte, _, *dk) in s.items():
            if black:
                lab = "skip"
            elif defect >= cov:
                lab = "edge"
            elif dk and dk[0] >= cov:
                lab = "dark_edge"
            elif matte >= cov:
                lab = "skip" if dark else "matte"
            else:
                lab = None
            out[e].append((f, lab))
    return out


def detect(stats: Stats, params: dict | None = None, stream: int = 0,
           frame_step: int = 1) -> list[Candidate]:
    """Stage 1: per-frame stats -> Candidates (frame_end inclusive).
    frame_step > 1: stats hold every step-th frame only (quality: fast). Consecutive
    samples with the same label join into one run; the candidate's frame range is the
    first..last SAMPLED frame and the true boundaries may lie up to step-1 frames
    outside it (evidence possible_frame_start/end). min_run_frames is compared with
    that largest possible extent, so a sampled hit is never dropped as too short."""
    p = params_with_defaults(params)
    cands = []
    for e, seq in classify(stats, p).items():
        counted = sum(1 for _, lab in seq if lab != "skip")
        run = None  # [kind, [frames]]
        for f, lab in seq + [(None, None)]:
            if lab == "skip":
                continue
            if run and lab != run[0]:
                cands.append(_candidate(e, run[0], run[1], stats, p, counted, stream, frame_step))
                run = None
            if lab:
                run = run or [lab, []]
                run[1].append(f)
    if frame_step > 1:
        cands = [c for c in cands if c.evidence[0]["possible_frame_end"]
                 - c.evidence[0]["possible_frame_start"] + 1 >= p["min_run_frames"]]
    else:
        cands = [c for c in cands if c.evidence[0]["frames"] >= p["min_run_frames"]]
    return _merge(cands, p["merge_gap_frames"])


MERGED_KINDS = ("dark_edge", "matte")
DARK_EDGE_MODES = ("flag", "info", "hide")   # profile param dark_edge_mode


def _merge(cands: list[Candidate], gap: int) -> list[Candidate]:
    """Consolidate review classes: runs of the same edge and kind (dark_edge, matte;
    never `edge`) whose gap (next start - previous end - 1) is <= `gap` frames become
    one Candidate spanning first..last run. Evidence keeps the run count, the frames
    actually flagged (`frames` or `frames_sampled`, summed), coverage max and the
    frame-weighted mean; persistent if any run was. Sharp `edge` runs stay separate
    (frame-exact). Output order: edge, frame_start."""
    out, last = [], {}
    for c in sorted(cands, key=lambda c: (EDGES.index(c.evidence[0]["edge"]), c.frame_start)):
        ev = c.evidence[0]
        key = (ev["edge"], ev["kind"])
        prev = last.get(key)
        if (ev["kind"] in MERGED_KINDS and prev is not None
                and c.frame_start - prev.frame_end - 1 <= gap):
            pe = prev.evidence[0]
            nk = "frames" if "frames" in pe else "frames_sampled"
            n0, n1 = pe[nk], ev[nk]
            pe["coverage_mean"] = round((pe["coverage_mean"] * n0 + ev["coverage_mean"] * n1)
                                        / (n0 + n1), 3)
            pe["coverage_max"] = max(pe["coverage_max"], ev["coverage_max"])
            pe[nk] = n0 + n1
            pe["runs"] = pe.get("runs", 1) + 1
            pe["persistent"] = pe["persistent"] or ev["persistent"]
            if "possible_frame_end" in ev:
                pe["possible_frame_end"] = ev["possible_frame_end"]
            prev.frame_end = c.frame_end
            prev.score = max(prev.score, c.score)
            continue
        out.append(c)
        last[key] = c
    return out


def _candidate(edge, kind, frames, stats, p, counted, stream, frame_step=1):
    cov = [stats[edge][f][{"edge": 0, "matte": 1, "dark_edge": 3}[kind]] for f in frames]
    n = len(frames)
    mean = sum(cov) / n
    ev = {
        "edge": edge, "kind": kind, "persistent": n >= p["persistent_fraction"] * counted,
        "frames": n, "coverage_mean": round(mean, 3), "coverage_max": round(max(cov), 3),
        "depth_px": f">{p['max_depth']}" if kind == "matte" else f"1-{p['max_depth']}",
    }
    if frame_step > 1:   # sampled: no exact run length, state the boundary uncertainty
        del ev["frames"]
        ev.update(frame_step=frame_step, frames_sampled=n,
                  possible_frame_start=max(0, frames[0] - (frame_step - 1)),
                  possible_frame_end=frames[-1] + frame_step - 1,
                  boundary_uncertainty_frames=frame_step - 1)
    return Candidate(CHECK_ID, stream, frames[0], frames[-1],
                     # score: mean coverage for sharp edges; dark edges / mattes are
                     # likely picture or intentional -> halved
                     round(mean if kind == "edge" else mean / 2, 3), [ev])


def evaluate(candidates: list[Candidate], params: dict | None = None,
             cap: Severity | str = Severity.FAIL, check_id: str = CHECK_ID) -> list[Finding]:
    """Verdict step: candidates -> Findings. Severity = class_severity for the
    candidate's class, capped at `cap` (the profile's `severity`). A verifier
    may later only lower it; raw_severity keeps this value.
    `dark_edge` findings follow the profile param dark_edge_mode: `flag` (default) =
    class severity as above, `info` = INFO, `hide` = not reported, but counted (with
    their frame ranges) in a PASS finding, or in one INFO finding when other findings
    exist, so nothing is silently lost."""
    p = params_with_defaults(params)
    cap = Severity.parse(cap)
    mode = p["dark_edge_mode"]
    if mode not in DARK_EDGE_MODES:
        raise ValueError(f"dark_edge_mode {mode!r} (allowed: {', '.join(DARK_EDGE_MODES)})")
    out, hidden = [], []
    for c in candidates:
        ev = c.evidence[0]
        cls = ev["kind"] + ("_persistent" if ev["persistent"] else "")
        sev = min(Severity.parse(p["class_severity"][cls]), cap)
        if ev["kind"] == "dark_edge":
            if mode == "hide":
                hidden.append({"edge": ev["edge"], "frame_start": c.frame_start,
                               "frame_end": c.frame_end, "coverage_max": ev["coverage_max"]})
                continue
            if mode == "info":
                sev = Severity.INFO
        what = {"edge": "black edge (non-picture, sharp step)",
                "dark_edge": "dark edge (dark picture or soft black edge? review)",
                "matte": "black bar (matte/letterbox/pillarbox?)"}[ev["kind"]]
        if "frame_step" in ev:
            n = (f"seen on {ev['frames_sampled']} sampled frame(s) (fast: 1 in "
                 f"{ev['frame_step']} frames; true start/end up to "
                 f"{ev['boundary_uncertainty_frames']} frame(s) outside the range shown)")
        else:
            n = f"{ev['frames']} frame(s)"
        if ev.get("runs", 1) > 1:
            n += f" in {ev['runs']} runs (merged, gaps <= {p['merge_gap_frames']} frames)"
        out.append(Finding(
            check_id, sev,
            f"{ev['edge']}: {what}, {ev['coverage_max']:.0%} of line, {n}"
            + (", persistent" if ev["persistent"] else ""),
            stream=c.stream, frame_start=c.frame_start, frame_end=c.frame_end,
            candidate=c, evidence=c.evidence))
    if hidden:
        msg = (f"{len(hidden)} dark_edge finding(s) hidden (dark_edge_mode: hide; dark "
               f"picture or soft black at the frame edge), frame ranges in evidence")
        out.append(Finding(check_id, Severity.INFO if out else Severity.PASS,
                           msg if out else "ok; " + msg, evidence=[{"hidden_dark_edge": hidden}]))
    return out


_FRAME = re.compile(r"^frame:(\d+)\s")


def parse_metadata(text: str, key: str) -> dict[int, float]:
    """Parse standalone metadata=print output into {frame counter: value}."""
    vals, frame = {}, None
    for line in text.splitlines():
        m = _FRAME.match(line)
        if m:
            frame = int(m.group(1))
        elif line.startswith(key + "=") and frame is not None:
            vals[frame] = float(line.split("=", 1)[1])
    return vals


def run_standalone(media: str, params: dict | None, workdir: str, stream: int = 0) -> list[Candidate]:
    """Tuning harness: decode `media` with only this check's chains (no core engine,
    no spec). Frame = ffmpeg's metadata frame counter (0-based, decode order)."""
    ch = chains(params)
    parts = [f"[0:v:{stream}]split={len(ch) + 1}" + "".join(f"[in{t}]" for t in ch) + "[bkout]"]
    files = {}
    for e, chain in ch.items():
        prints = []
        for k in tap_keys(e):
            f = Path(workdir) / f"blanking_{e}_{k.rsplit('.', 1)[1]}.txt"
            if not re.match(r"^[A-Za-z0-9_./-]+$", str(f)):
                raise ValueError(f"unsafe path for a filtergraph: {f}")
            files[(e, k)] = f
            prints.append(f"metadata=mode=print:key={k}:file={f}")
        parts.append(f"[in{e}]{chain},{','.join(prints)},nullsink")
    # ffmpeg 6.1 rejects a graph with zero outputs: one passthrough branch is mapped
    subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-v", "error", "-y", "-i", media,
                    "-filter_complex", ";".join(parts), "-map", "[bkout]", "-f", "null", "-"],
                   check=True)
    raw = {key: parse_metadata(f.read_text(), key[1]) for key, f in files.items()}
    stats = {e: {fr: (raw[(e, K_DEFECT)][fr] / 255, raw[(e, K_MATTE)][fr] / 255,
                      raw[(e, K_LEVEL)][fr], raw[(e + DARK, K_DEFECT)].get(fr, 0) / 255)
                 for fr in raw[(e, K_DEFECT)]} for e in EDGES}
    return detect(stats, params, stream)
