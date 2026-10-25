"""Blanking (edge defect) detector: stage 1, high recall.

Per frame and per edge, one tap in the single decode pass compares, PIXEL BY
PIXEL, the outermost row/column ("outer") with the row/column `max_depth` px
in ("inner"):

  crop strip of max_depth+1 px (exact=1) -> format=yuv444p -> shuffleplanes=0:0:0
  -> split -> crop outer / inner line -> lut2 -> signalstats
  lut2 (8-bit, x = outer pixel, y = inner pixel):
    Y = 255 if x <= black_luma and y - x >= min_contrast   ("defect" pixel)
    U = 255 if x <= black_luma and y <= matte_luma         ("matte" pixel)
    V = max(x, y)                                          (edge brightness)
  keys: YAVG/255 = defect coverage, UAVG/255 = matte coverage, VHIGH = brightness.

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
statistic means ">= 50% black". Binarising first makes YAVG the coverage, and
the per-pixel contrast against the inner line keeps dark picture (flat dark
content continues inward) from counting.

Verified on ffmpeg 6.1.1 (`ffmpeg -h filter=crop|signalstats|metadata|split|
lut2|shuffleplanes`): crop needs exact=1 (1px-wide crop on 4:2:2 fails without
it); signalstats on a 1px-wide 4:2:2 crop segfaults and reports native bit
depth, hence format=yuv444p (8-bit, source range kept); lut2 x/y = input 0/1,
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

# Tuning knobs (profile params). Luma values are 8-bit code values (tv black = 16).
DEFAULTS = {
    "coverage": 0.5,             # share of the edge line that must be black
    "coverage_tolerance": 0.02,  # slack for codec ringing at mask transitions (planted 50% measured 0.486-0.499)
    "black_luma": 32,            # outer pixel <= this is black (ProRes ringing lifts black pixels to ~29-32)
    "matte_luma": 20,            # inner pixel <= this is black (matte), and black-frame level
    "min_contrast": 4,           # inner - outer pixel difference for a "defect" pixel
    "dark_frame_luma": 48,       # all edges VHIGH <= this: frame too dark to judge a "matte"
    "max_depth": 16,             # px: inner line offset; deeper black = "matte"
    "min_run_frames": 1,         # shortest run reported
    "persistent_fraction": 0.9,  # run covering this share of the edge's frames = persistent
    "fast_frame_step": 5,        # quality: fast examines every Nth file frame only
    "class_severity": {          # severity per class, capped by the profile `severity`
        "edge": "FAIL",
        "edge_persistent": "FAIL",
        "matte": "WARN",
        "matte_persistent": "INFO",
    },
}


def params_with_defaults(params: dict | None) -> dict:
    p = {**DEFAULTS, **(params or {})}
    p["class_severity"] = {**DEFAULTS["class_severity"],
                           **((params or {}).get("class_severity") or {})}
    return p


def _geometry(edge: str, d: int) -> tuple[str, str, str]:
    """(strip crop, outer-line crop, inner-line crop) inside the strip."""
    if edge == "top":
        return f"w=iw:h={d + 1}:x=0:y=0", "w=iw:h=1:x=0:y=0", f"w=iw:h=1:x=0:y={d}"
    if edge == "bottom":
        return f"w=iw:h={d + 1}:x=0:y=ih-{d + 1}", f"w=iw:h=1:x=0:y={d}", "w=iw:h=1:x=0:y=0"
    if edge == "left":
        return f"w={d + 1}:h=ih:x=0:y=0", "w=1:h=ih:x=0:y=0", f"w=1:h=ih:x={d}:y=0"
    return f"w={d + 1}:h=ih:x=iw-{d + 1}:y=0", f"w=1:h=ih:x={d}:y=0", "w=1:h=ih:x=0:y=0"


def chains(params: dict | None, frame_step: int = 1) -> dict[str, str]:
    """{tap name (= edge): filter chain}. Each chain takes one video input and ends
    in signalstats; internal labels are prefixed `bk<edge>` (unique in the graph).
    frame_step > 1 (quality: fast) prepends `select='not(mod(n,step))'`: only file
    frames 0, step, 2*step, ... are measured (n = input frame number from 0)."""
    p = params_with_defaults(params)
    sel = f"select='not(mod(n,{frame_step}))'," if frame_step > 1 else ""
    bl, ml, mc = p["black_luma"], p["matte_luma"], p["min_contrast"]
    out = {}
    for e in EDGES:
        strip, outer, inner = _geometry(e, int(p["max_depth"]))
        a, b = f"bk{e}a", f"bk{e}b"
        out[e] = (f"{sel}crop={strip}:exact=1,format=yuv444p,shuffleplanes=0:0:0,split[{a}][{b}];"
                  f"[{a}]crop={outer}:exact=1[{a}o];[{b}]crop={inner}:exact=1[{b}o];"
                  f"[{a}o][{b}o]lut2=c0='255*lte(x,{bl})*gte(y-x,{mc})':"
                  f"c1='255*lte(x,{bl})*lte(y,{ml})':c2='max(x,y)',signalstats")
    return out


Stats = dict[str, dict[int, tuple[float, float, float]]]   # edge -> frame -> (defect, matte, level)


def stats_from_records(records: dict[str, list]) -> Stats:
    """Engine MetaRecords {tap: [MetaRecord(frame, pts_time, values)]} -> stats."""
    out = {}
    for e in EDGES:
        out[e] = {r.frame: (float(r.values[K_DEFECT]) / 255, float(r.values[K_MATTE]) / 255,
                            float(r.values[K_LEVEL]))
                  for r in records.get(e, []) if all(k in r.values for k in KEYS)}
    return out


def classify(stats: Stats, params: dict | None = None):
    """Per frame and edge: "edge", "matte", None (picture) or "skip".
    Returns {edge: [(frame, label), ...]} in frame order."""
    p = params_with_defaults(params)
    cov = p["coverage"] - p["coverage_tolerance"]
    frames = sorted(set.intersection(*(set(v) for v in stats.values())))
    out = {e: [] for e in EDGES}
    for f in frames:
        s = {e: stats[e][f] for e in EDGES}
        black = all(v[2] <= p["matte_luma"] for v in s.values())
        dark = all(v[2] <= p["dark_frame_luma"] for v in s.values())
        for e, (defect, matte, _) in s.items():
            if black:
                lab = "skip"
            elif defect >= cov:
                lab = "edge"
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
        return [c for c in cands if c.evidence[0]["possible_frame_end"]
                - c.evidence[0]["possible_frame_start"] + 1 >= p["min_run_frames"]]
    return [c for c in cands if c.evidence[0]["frames"] >= p["min_run_frames"]]


def _candidate(edge, kind, frames, stats, p, counted, stream, frame_step=1):
    cov = [stats[edge][f][0 if kind == "edge" else 1] for f in frames]
    n = len(frames)
    mean = sum(cov) / n
    ev = {
        "edge": edge, "kind": kind, "persistent": n >= p["persistent_fraction"] * counted,
        "frames": n, "coverage_mean": round(mean, 3), "coverage_max": round(max(cov), 3),
        "depth_px": f"1-{p['max_depth']}" if kind == "edge" else f">{p['max_depth']}",
    }
    if frame_step > 1:   # sampled: no exact run length, state the boundary uncertainty
        del ev["frames"]
        ev.update(frame_step=frame_step, frames_sampled=n,
                  possible_frame_start=max(0, frames[0] - (frame_step - 1)),
                  possible_frame_end=frames[-1] + frame_step - 1,
                  boundary_uncertainty_frames=frame_step - 1)
    return Candidate(CHECK_ID, stream, frames[0], frames[-1],
                     # score: mean coverage for edges; mattes are likely intentional -> halved
                     round(mean if kind == "edge" else mean / 2, 3), [ev])


def evaluate(candidates: list[Candidate], params: dict | None = None,
             cap: Severity | str = Severity.FAIL, check_id: str = CHECK_ID) -> list[Finding]:
    """Verdict step: candidates -> Findings. Severity = class_severity for the
    candidate's class, capped at `cap` (the profile's `severity`). A verifier
    may later only lower it; raw_severity keeps this value."""
    p = params_with_defaults(params)
    cap = Severity.parse(cap)
    out = []
    for c in candidates:
        ev = c.evidence[0]
        cls = ev["kind"] + ("_persistent" if ev["persistent"] else "")
        sev = min(Severity.parse(p["class_severity"][cls]), cap)
        what = ("black edge (non-picture)" if ev["kind"] == "edge"
                else "black bar (matte/letterbox/pillarbox?)")
        if "frame_step" in ev:
            n = (f"seen on {ev['frames_sampled']} sampled frame(s) (fast: 1 in "
                 f"{ev['frame_step']} frames; true start/end up to "
                 f"{ev['boundary_uncertainty_frames']} frame(s) outside the range shown)")
        else:
            n = f"{ev['frames']} frame(s)"
        out.append(Finding(
            check_id, sev,
            f"{ev['edge']}: {what}, {ev['coverage_max']:.0%} of line, {n}"
            + (", persistent" if ev["persistent"] else ""),
            stream=c.stream, frame_start=c.frame_start, frame_end=c.frame_end,
            candidate=c, evidence=c.evidence))
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
    parts = [f"[0:v:{stream}]split=5" + "".join(f"[bk{e}]" for e in EDGES) + "[bkout]"]
    files = {}
    for e, chain in chains(params).items():
        prints = []
        for k in KEYS:
            f = Path(workdir) / f"blanking_{e}_{k.rsplit('.', 1)[1]}.txt"
            if not re.match(r"^[A-Za-z0-9_./-]+$", str(f)):
                raise ValueError(f"unsafe path for a filtergraph: {f}")
            files[(e, k)] = f
            prints.append(f"metadata=mode=print:key={k}:file={f}")
        parts.append(f"[bk{e}]{chain},{','.join(prints)},nullsink")
    # ffmpeg 6.1 rejects a graph with zero outputs: one passthrough branch is mapped
    subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-v", "error", "-y", "-i", media,
                    "-filter_complex", ";".join(parts), "-map", "[bkout]", "-f", "null", "-"],
                   check=True)
    raw = {key: parse_metadata(f.read_text(), key[1]) for key, f in files.items()}
    stats = {e: {fr: (raw[(e, K_DEFECT)][fr] / 255, raw[(e, K_MATTE)][fr] / 255,
                      raw[(e, K_LEVEL)][fr])
                 for fr in raw[(e, K_DEFECT)]} for e in EDGES}
    return detect(stats, params, stream)
