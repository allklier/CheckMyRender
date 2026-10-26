"""video.blanking stage 2: temporal CONTEXT of stage-1 hits (D9 deterministic verifier).

A single frame cannot tell a Ken Burns move over a pillar/letterboxed still, or a cut /
cross-dissolve, from a masking error; the difference is temporal. After the main pass,
for each stage-1 candidate (capped), the frames [start-K, end+K] are seek-decoded once
(8-bit luma, `scale=out_range=tv,format=yuv420p,extractplanes=y`; windows of nearby
candidates share one decode) and three deterministic signals are measured in pure
Python (no numpy):

1. BAND DEPTH per frame and edge: per edge line (row for left/right, column for
   top/bottom) the run of pixels <= black_luma counted from the frame edge, up to
   `context_depth_fraction` of the frame; per frame the median over the lines that have a
   band (left/right: mean of the even-row and odd-row medians, so the two fields of
   interlaced material do not alternate). A Ken Burns move changes the depth steadily
   (band also on context frames, monotone, straight-line fit R^2 high; the band run stops at
   cuts and dissolves); a masking error appears with a constant depth and vanishes abruptly.
2. SYMMETRY: the opposite edge has a band of similar depth in the hit frames (pillarbox /
   letterbox); a masking error is usually one-sided. Reported, used as support only.
3. TRANSITION: own frame differences on a sparse luma sample (every `SAMPLE_STRIDE`-th
   byte): a CUT is a frame-difference spike against the local median; a DISSOLVE (or fade)
   is a run of frames whose change over +-4 frames is linear in time while the picture
   changes. A hit mostly inside a dissolve, or a hit of <= 2 frames at a cut, is a
   transition, but only when the band appears gradually (the outer line's mean luma jumps
   <= transition_max_jump into the first hit frame): a mask switched on during a dissolve
   or at a cut jumps (planted on real texture: 19-48 code values; real transitions 0-6).

Animation guards (from user labels on a real master): a band whose run starts at a cut (an
image cut in with its first move keyframe too loose), ends right before a cut (the mirror:
last keyframe too loose) or whose depth moves fast (> 8 px per frame) is never excused as
animation, however linear the move (evidence `animation_guard`).

Classes (evidence `context`): animation | transition | steady | unknown. The profile maps
them to a severity (`context_severity`); the analyser may only LOWER a finding
(Finding.lower_to, raw_severity kept) and records `finding.verifier`. Detail and the
measurements behind the thresholds: CLAUDE.md in this folder.

Verified ffmpeg facts (6.1.1, `ffmpeg -h filter=extractplanes|metadata|format`, runs):
`format=gray` EXPANDS tv-range luma (Y 30 -> 16, 218 -> 235), `scale=out_range=tv,
format=yuv420p,extractplanes=y` keeps the code values the detector sees (30 / 218; yuvj input
-> tv codes as in the detector chain, which adds `scale=out_range=tv` for ffmpeg 7.1); input seek
`-ss (f-0.5)/fps` makes frame f the first output frame (as for waiver fingerprints).
"""
from __future__ import annotations

import logging
import operator
from collections import Counter
import statistics
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from cmr import log
from cmr.model import Severity

CLASSES = ("animation", "transition", "steady", "unknown")
VERSION = 2               # 2: animation guards cut_onset / fast_move (user labels)
NAME = "blanking.context"
OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}
SAMPLE_STRIDE = 61        # frame-difference sample: every 61st luma byte (~34k at 1080p)
CUT_LOCAL = 12            # cut: median difference of the frames within +-12 (local)
MAX_TRACK = 64            # evidence depth_track only for windows up to this many frames
DISSOLVE_SPAN = 4         # dissolve linearity over +-4 frames (see transitions())

DEFAULTS = {
    "context_analysis": True,        # stage 2 on/off
    "context_frames": 12,            # K: frames of context before and after each hit
    "context_severity": {            # severity per context class; "unchanged" keeps stage 1
        "animation": "info", "transition": "info", "steady": "unchanged", "unknown": "unchanged"},
    "max_context_candidates": 300,   # per file; the rest -> context unknown (logged)
    "context_max_seconds": 600,      # per file; time budget for the seek decodes
    "context_max_span_frames": 300,  # a (merged) candidate longer than this is not analysed
    "context_depth_fraction": 0.125, # band depth searched: 1/8 of width/height (4:3 pillar)
    "context_min_band_frames": 3,    # animation: band seen on >= this many context frames
    "context_min_motion_px": 3,      # animation: depth changes by >= this over the band run
    "context_monotone": 0.8,         # animation: share of depth steps in the main direction
    "context_min_r2": 0.8,           # animation: straight-line fit of the depth explains this
    "context_cut_onset_frames": 2,   # not animation: band run starts <= this many frames after a cut
    "context_cut_end_frames": 2,     # not animation: band run ends <= this many frames before a cut
    "context_max_velocity_px": 8,   # not animation: median depth step above this (px per frame)
    "context_transition_gap": 2,     # hit within this many frames of a cut/dissolve
    "cut_max_hit_frames": 2,         # transition by a cut: only hits this short (cut frame)
    "transition_max_jump": 8,        # transition: outer-line luma jumps at most this at onset
    "steady_max_range_px": 2,        # steady: depth range during the hit at most this
    "cut_min_diff": 12.0,            # cut: frame difference (mean |dY|, 8-bit) at least
    "cut_ratio": 4.0,                # cut: and this many times the window's median difference
    "dissolve_min_diff": 2.0,        # dissolve: mean |Y(t+4) - Y(t-4)| at least (picture changes)
    "dissolve_max_linearity": 0.35,  # dissolve: mean |Y(t+4)-2Y(t)+Y(t-4)| / mean |Y(t+4)-Y(t-4)|
    "dissolve_min_frames": 3,        # dissolve: run of linear frames at least this long
}
SEVERITY_CHOICES = ("unchanged", "pass", "info", "warn", "fail")
LOG = log.get("check.video.blanking")


def params_with_defaults(params: dict | None) -> dict:
    p = {k: v for k, v in DEFAULTS.items()}
    p.update({k: v for k, v in (params or {}).items() if k in DEFAULTS})
    p["context_severity"] = {**DEFAULTS["context_severity"],
                             **((params or {}).get("context_severity") or {})}
    return p


def params_schema() -> dict:
    num = {"type": "number", "minimum": 0}
    return {
        "context_analysis": {"type": "boolean"},
        "context_frames": {"type": "integer", "minimum": 1, "maximum": 250},
        "context_severity": {"type": "object", "additionalProperties": False,
                             "properties": {c: {"enum": list(SEVERITY_CHOICES)} for c in CLASSES}},
        "max_context_candidates": {"type": "integer", "minimum": 0},
        "context_max_seconds": num,
        "context_max_span_frames": {"type": "integer", "minimum": 1},
        "context_depth_fraction": {"type": "number", "exclusiveMinimum": 0, "maximum": 0.5},
        "context_min_band_frames": {"type": "integer", "minimum": 1},
        "context_min_motion_px": num, "context_monotone": {"type": "number", "minimum": 0,
                                                           "maximum": 1},
        "context_min_r2": {"type": "number", "minimum": 0, "maximum": 1},
        "context_cut_onset_frames": {"type": "integer", "minimum": 0},
        "context_cut_end_frames": {"type": "integer", "minimum": 0},
        "context_max_velocity_px": num,
        "context_transition_gap": {"type": "integer", "minimum": 0},
        "cut_max_hit_frames": {"type": "integer", "minimum": 0},
        "transition_max_jump": num, "steady_max_range_px": num,
        "cut_min_diff": num, "cut_ratio": num, "dissolve_min_diff": num,
        "dissolve_max_linearity": num, "dissolve_min_frames": {"type": "integer", "minimum": 1},
    }


# --- per-frame features ---------------------------------------------------------------
@dataclass
class EdgeFeat:
    cov: float       # share of edge lines with a band (depth >= 1)
    shallow: float   # share with 1 <= depth <= max_depth (what stage 1 calls an edge band)
    deep: float      # share with depth > max_depth
    depth: float     # band depth px (median over banded lines; L/R: mean of field medians)
    outer: float = 0.0  # mean luma of the outermost line (how abruptly a band appears)


@dataclass
class FrameFeat:
    edges: dict[str, EdgeFeat]
    sample: bytes = field(repr=False)


def _summary(depths: list[int], max_depth: int, parity: bool, outer: bytes) -> EdgeFeat:
    n = len(depths) or 1
    band = [d for d in depths if d >= 1]
    shallow = sum(1 for d in band if d <= max_depth)
    mean_outer = sum(outer) / (len(outer) or 1)
    if not band:
        return EdgeFeat(0.0, 0.0, 0.0, 0.0, mean_outer)
    if parity:
        meds = [statistics.median(x) for x in (
            [d for d in depths[0::2] if d >= 1], [d for d in depths[1::2] if d >= 1]) if x]
        depth = sum(meds) / len(meds)
    else:
        depth = float(statistics.median(band))
    return EdgeFeat(len(band) / n, shallow / n, (len(band) - shallow) / n, depth, mean_outer)


def black_table(black_luma: float) -> bytes:
    """bytes.translate table: luma <= black_luma -> 0, else 1."""
    return bytes(0 if v <= black_luma else 1 for v in range(256))


def frame_features(y: bytes, w: int, h: int, table: bytes, max_depth: int,
                   depth_fraction: float) -> FrameFeat:
    """Band depth per edge of one 8-bit luma frame (w*h bytes). Pure Python, C-speed
    slicing: translate once, then find the first non-black pixel per line."""
    t = y.translate(table)
    dw = max(max_depth + 1, min(w, int(w * depth_fraction)))
    dh = max(max_depth + 1, min(h, int(h * depth_fraction)))
    left, right = [], []
    for r in range(0, h * w, w):
        i = t.find(1, r, r + dw)
        left.append(dw if i < 0 else i - r)
        j = t.rfind(1, r + w - dw, r + w)
        right.append(dw if j < 0 else r + w - 1 - j)
    topb, botb = t[:dh * w], t[(h - dh) * w:]
    top, bottom = [], []
    for c in range(w):
        i = topb[c::w].find(1)
        top.append(dh if i < 0 else i)
        j = botb[c::w].rfind(1)
        bottom.append(dh if j < 0 else dh - 1 - j)
    return FrameFeat({"left": _summary(left, max_depth, True, y[0::w]),
                      "right": _summary(right, max_depth, True, y[w - 1::w]),
                      "top": _summary(top, max_depth, False, y[:w]),
                      "bottom": _summary(bottom, max_depth, False, y[(h - 1) * w:])},
                     y[::SAMPLE_STRIDE])


def mad(a: bytes, b: bytes) -> float:
    """Mean absolute difference of two equal-length byte samples."""
    return sum(map(abs, map(operator.sub, a, b))) / (len(a) or 1)


def mad2(a: bytes, b: bytes, c: bytes) -> float:
    """Mean |a - 2b + c| (second difference in time)."""
    return sum(abs(x - 2 * y + z) for x, y, z in zip(a, b, c)) / (len(a) or 1)


# --- decoding ---------------------------------------------------------------------------
class DecodeError(RuntimeError):
    pass


def decode_window(path, ffmpeg: str, stream: int, fps: Fraction, w: int, h: int, f0: int,
                  f1: int, time_bases: list[Fraction], deadline: float, feat) -> dict[int, object]:
    """Seek-decode file frames f0..f1 (inclusive) as 8-bit luma and return
    {frame: feat(bytes)}. Frames come from the exact pts (as the engine's MetaRecord.frame):
    filter time t = pts x time base is relative to the seek point X, so frame =
    round((t + X) x fps). Raises DecodeError (timeout, ffmpeg failure, no frames)."""
    from cmr.engine import parse_metadata_file, pick_time_base   # engine imports checks lazily
    n = f1 - f0 + 1
    x = Fraction(f"{float(max(Fraction(0), (f0 - Fraction(1, 2)) / fps)):.6f}")
    with tempfile.TemporaryDirectory(prefix="cmr_ctx_") as tmp:
        meta = Path(tmp) / "pts.txt"
        if not all(ch.isalnum() or ch in "_./-" for ch in str(meta)):
            raise DecodeError(f"temp path {meta} unsafe for a filtergraph")
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{float(x):.6f}",
               "-i", str(path), "-map", f"0:{stream}", "-frames:v", str(n),
               "-vf", f"scale=out_range=tv,format=yuv420p,extractplanes=y,"
                      f"metadata=mode=add:key=cmr.ctx:value=1,"
                      f"metadata=mode=print:file={meta}",
               "-f", "rawvideo", "-"]
        if LOG.isEnabledFor(logging.DEBUG):
            LOG.debug("exec context decode: %s", log.redact_cmd(cmd))
        t0 = time.monotonic()
        size, out = w * h, []
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            while True:
                if time.monotonic() > deadline:
                    raise DecodeError("time budget exhausted")
                buf = p.stdout.read(size)
                if len(buf) < size:
                    break
                out.append(feat(buf))
            err = p.stderr.read().decode(errors="replace")
            rc = p.wait(timeout=max(1.0, deadline - time.monotonic()))
        except (DecodeError, subprocess.TimeoutExpired):
            p.kill()
            p.wait()
            raise DecodeError("time budget exhausted") from None
        finally:
            p.stdout.close()
            p.stderr.close()
        LOG.debug("context decode %d-%d: exit %s, %d frame(s) in %.3f s%s", f0, f1, rc, len(out),
                  time.monotonic() - t0, log.tail(err.splitlines()) if err.strip() else "")
        if rc != 0 or not out:
            raise DecodeError(f"ffmpeg exit {rc}, {len(out)} frame(s): {err.strip()[:200]}")
        raw = parse_metadata_file(meta.read_text()) if meta.exists() else []
    frames = list(range(f0, f0 + len(out)))
    tb = pick_time_base(raw, time_bases)
    if tb is not None and len(raw) == len(out):
        frames = [round((int(pts) * tb + x) * fps) for pts, _, _ in raw]
        if frames != list(range(f0, f0 + len(out))):
            LOG.debug("context decode %d-%d: frames from pts %s..%s differ from the seek "
                      "count (used pts)", f0, f1, frames[0], frames[-1])
    else:
        LOG.debug("context decode %d-%d: no exact pts (%d records), frames counted from the "
                  "seek point", f0, f1, len(raw))
    return dict(zip(frames, out))


# --- transitions ------------------------------------------------------------------------
def transitions(frames: dict[int, FrameFeat], p: dict) -> tuple[list[int], list[int]]:
    """-> (cut frames, dissolve frames) among `frames` (sorted lists).
    Cut at f: |Y(f) - Y(f-1)| >= cut_min_diff and >= cut_ratio x the median difference of
    the other frames within +-CUT_LOCAL (a step-printed / held-frame sequence makes every
    change a cut).
    Dissolve at f: the picture changes over +-k frames (k = DISSOLVE_SPAN) by >=
    dissolve_min_diff and the change is linear in time: mean |Y(f+k) - 2Y(f) + Y(f-k)| /
    mean |Y(f+k) - Y(f-k)| <= dissolve_max_linearity; runs >= dissolve_min_frames.
    k = 4, not 1: with 3:2 pulldown every 5th frame repeats (k = 1 breaks every run), and
    a slow Ken Burns move is linear over 1 frame but not over 8 (real material, see
    CLAUDE.md)."""
    fs = sorted(frames)
    d1 = {f: mad(frames[f].sample, frames[f - 1].sample) for f in fs if f - 1 in frames}
    cuts = []
    for f, v in d1.items():
        if v < p["cut_min_diff"]:
            continue
        rest = [u for g, u in d1.items() if 1 < abs(g - f) <= CUT_LOCAL]
        med = statistics.median(rest) if rest else 0.0
        if v >= p["cut_ratio"] * max(med, 0.5):
            cuts.append(f)
    k, lin = DISSOLVE_SPAN, []
    for f in fs:
        if f - k in frames and f + k in frames and not any(f - k < c <= f + k for c in cuts):
            a = mad(frames[f + k].sample, frames[f - k].sample)
            if a >= p["dissolve_min_diff"]:
                b = mad2(frames[f + k].sample, frames[f].sample, frames[f - k].sample)
                if b / a <= p["dissolve_max_linearity"]:
                    lin.append(f)
    dissolve, run = [], []
    for f in lin + [None]:
        if run and (f is None or f != run[-1] + 1):
            if len(run) >= p["dissolve_min_frames"]:
                dissolve += run
            run = []
        if f is not None:
            run.append(f)
    return cuts, dissolve


# --- per-candidate classification -------------------------------------------------------
def track_features(track: dict[int, float], a: int, b: int, dmax: int,
                   stops: frozenset | set = frozenset()) -> dict:
    """Depth trajectory around a hit (frames a..b) in `track` {frame: depth px, 0 = no band}.
    The band run is extended outward from the hit while the band persists, but not across
    a frame in `stops` (cut frames: first frame of a shot, and dissolve frames), so the
    trend is that of the hit's own shot. Steps smaller than 1 px count as no step (3:2
    pulldown repeats a frame every 5th, so zero steps are ignored for the direction)."""
    hit = [track[f] for f in range(a, b + 1) if f in track and track[f] > 0]
    s, e = a, b
    while s - 1 in track and track[s - 1] > 0 and s not in stops and s - 1 not in stops:
        s -= 1
    while e + 1 in track and track[e + 1] > 0 and e + 1 not in stops:
        e += 1
    run = [f for f in range(s, e + 1) if f in track]
    unsat = [track[f] for f in run if 0 < track[f] < dmax]
    steps = [track[g] - track[f] for f, g in zip(run, run[1:])
             if 0 < track[f] < dmax and 0 < track[g] < dmax]
    nz = [x for x in steps if abs(x) >= 1]
    pos = sum(1 for x in nz if x > 0)
    return {
        "band_run": [s, e], "context_band_frames": (a - s) + (e - b),
        "hit_depth_px": round(statistics.median(hit), 1) if hit else None,
        "hit_depth_range_px": round(max(hit) - min(hit), 1) if hit else None,
        "depth_min": round(min(unsat), 1) if unsat else None,
        "depth_max": round(max(unsat), 1) if unsat else None,
        "depth_motion_px": round(max(unsat) - min(unsat), 1) if unsat else 0.0,
        "moving_steps": len(nz),
        "monotone": round(max(pos, len(nz) - pos) / len(nz), 2) if nz else 0.0,
        "velocity_px": round(statistics.median([abs(x) for x in nz]), 2) if nz else 0.0,
        "trend_r2": _r2([(f, track[f]) for f in run if 0 < track[f] < dmax]),
    }


def _r2(pts: list[tuple[int, float]]) -> float:
    """Coefficient of determination of a straight-line fit depth ~ frame (0 if < 3 points
    or no variance): how much of the depth change a steady move explains."""
    if len(pts) < 3:
        return 0.0
    n = len(pts)
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    syy = sum((y - my) ** 2 for _, y in pts)
    sxy = sum((x - mx) * (y - my) for x, y in pts)
    if sxx == 0 or syy == 0:
        return 0.0
    return round(sxy * sxy / (sxx * syy), 3)


def classify(frames: dict[int, FrameFeat], edge: str, a: int, b: int, cuts: list[int],
             dissolves: list[int], dmax: int, p: dict, dp: dict,
             possible: tuple[int, int] | None = None) -> dict:
    """Context class + features of one hit (edge, frames a..b inclusive). `dp` = the
    detector params (coverage, coverage_tolerance); `dmax` = depth search limit (px).
    `possible` (quality fast): the possible true extent of a sampled hit; the hit is
    widened from the sampled a..b to the adjacent frames that carry the band, within it."""
    k = p["context_frames"]
    cov = dp["coverage"] - dp["coverage_tolerance"]

    def band(f, e=edge):
        x = frames[f].edges[e]
        return x.depth if x.cov >= cov else 0.0

    if possible:
        while a - 1 >= possible[0] and a - 1 in frames and band(a - 1) > 0:
            a -= 1
        while b + 1 <= possible[1] and b + 1 in frames and band(b + 1) > 0:
            b += 1
    win = [f for f in range(a - k, b + k + 1) if f in frames]
    hit = [f for f in range(a, b + 1) if f in frames]
    ev: dict = {"context_version": VERSION, "window": [win[0], win[-1]] if win else None,
                "hit": [a, b]}
    if not hit:
        return {"context": "unknown", "reason": "hit frames not decoded", **ev}
    track = {f: band(f) for f in win}
    if len(win) <= MAX_TRACK:
        ev["depth_track"] = [round(track[f], 1) for f in win]
    ok = [f for f in hit if track[f] > 0]
    tf = track_features(track, a, b, dmax, set(cuts) | set(dissolves))
    ev.update(tf)
    # symmetry: opposite edge has a band of similar depth on the hit frames
    opp = OPPOSITE[edge]
    sym = [f for f in ok if band(f, opp) > 0
           and abs(band(f, opp) - track[f]) <= max(2.0, 0.25 * track[f])]
    ev["symmetry"] = round(len(sym) / len(ok), 2) if ok else 0.0
    ev["opposite_band"] = round(sum(1 for f in ok if band(f, opp) > 0) / len(ok), 2) if ok else 0.0
    g = p["context_transition_gap"]
    cnear = [c - b - 1 if c > b + 1 else (a - c if c < a else 0) for c in cuts]
    ev["cut_distance"] = min(cnear) if cnear else None
    dset = set(dissolves)
    dnear = [abs(f - min(max(f, a), b)) for f in dissolves]
    ev["dissolve_distance"] = min(dnear) if dnear else None
    in_dis = sum(1 for f in hit if any(x in dset for x in range(f - g, f + g + 1)))
    ev["dissolve_share"] = round(in_dis / len(hit), 2)
    # onset: how much the outer line's mean luma jumps into the first hit frame when the
    # band appears there (a transition darkens the edge gradually; a mask switches on)
    jump = 0.0
    if a - 1 in frames and band(a - 1) == 0:
        jump = abs(frames[a].edges[edge].outer - frames[a - 1].edges[edge].outer)
    ev["onset_jump"] = round(jump, 1)
    # guards (user labels on a real long-form master): a band that STARTS AT A CUT (an image
    # cut in with its first move keyframe too loose) or whose depth moves FAST is a framing
    # error even when the move is linear: never excused as animation
    s0, onset = tf["band_run"][0], None
    for c in cuts:
        if 0 <= s0 - c <= p["context_cut_onset_frames"] and c - 1 in track:
            before, first = track[c - 1], track[s0]
            if before == 0 or abs(before - first) > max(2.0, 0.5 * max(before, first)):
                onset = s0 - c
    ev["cut_onset"] = onset
    # mirror: the band run ENDS <= context_cut_end_frames before a cut and is gone (or of a
    # clearly different depth) on the cut frame
    e0, end = tf["band_run"][1], None
    for c in cuts:
        if 0 <= c - e0 - 1 <= p["context_cut_end_frames"] and c in track:
            after, last = track[c], track[e0]
            if after == 0 or abs(after - last) > max(2.0, 0.5 * max(after, last)):
                end = c - e0 - 1
                break
    ev["cut_end"] = end
    guard = [g for g, hit_ in (("cut_onset", onset is not None), ("cut_end", end is not None),
                               ("fast_move", tf["velocity_px"] > p["context_max_velocity_px"]))
             if hit_]
    if guard:
        ev["animation_guard"] = guard
    if not ok:
        return {"context": "unknown", "reason": "hit not reproduced by the context decode", **ev}
    # 1. animation: the band continues outside the hit and moves steadily
    moving = (tf["context_band_frames"] >= p["context_min_band_frames"]
              and tf["depth_motion_px"] >= p["context_min_motion_px"]
              and tf["moving_steps"] >= 2 and tf["monotone"] >= p["context_monotone"]
              and tf["trend_r2"] >= p["context_min_r2"])
    if moving and not guard:
        return {"context": "animation", **ev}
    # 2. transition: a short hit at a cut (field-mixed cut frame), or a hit mostly inside a
    #    dissolve / fade. A moving band tied to a cut (guard cut_onset / cut_end) is not a
    #    cut-frame artefact: the cut branch does not apply to it
    cut_tied = moving and ("cut_onset" in guard or "cut_end" in guard)
    if (jump <= p["transition_max_jump"]
            and ((not cut_tied and cnear and min(cnear) <= g
                  and b - a + 1 <= p["cut_max_hit_frames"])
                 or in_dis / len(hit) >= 0.5)):
        return {"context": "transition", **ev}
    # 3. steady: constant depth during the hit (the masking-error signature)
    if tf["hit_depth_range_px"] is not None and tf["hit_depth_range_px"] <= max(
            p["steady_max_range_px"], 0.2 * (tf["hit_depth_px"] or 0)):
        return {"context": "steady", **ev}
    return {"context": "unknown", "reason": "band depth changes erratically (dark picture?)", **ev}


# --- driver -----------------------------------------------------------------------------
def _target(cls: str, p: dict) -> Severity | None:
    v = p["context_severity"].get(cls, "unchanged")
    return None if v == "unchanged" else Severity.parse(v)


def analyse(findings: list, ctx, dp: dict) -> None:
    """Stage 2 over the findings of one file (in place). `dp` = detector params with
    defaults. Adds evidence[0] `context` (+ features) to every finding with a candidate,
    lowers severity per `context_severity` (never raises), sets `finding.verifier`."""
    p = params_with_defaults(ctx.params)
    todo = [f for f in findings if f.candidate is not None and f.evidence]
    if not p["context_analysis"] or not todo:
        return
    t_start = time.monotonic()
    deadline = t_start + float(p["context_max_seconds"])
    v = ctx.media.video[0]
    w, h, stream = int(v["width"]), int(v["height"]), int(v["index"])
    fps = ctx.timeline.fps
    nframes = ctx.timeline.file_frames
    ffmpeg = (ctx.passinfo.command[0] if ctx.passinfo and ctx.passinfo.command else "ffmpeg")
    tbs = []
    for s in ctx.media.raw.get("streams", []):
        if s.get("index") == stream and s.get("time_base"):
            tbs.append(Fraction(s["time_base"]))
    k = int(p["context_frames"])
    dpth = {"left": max(dp["max_depth"] + 1, int(w * p["context_depth_fraction"])),
            "right": max(dp["max_depth"] + 1, int(w * p["context_depth_fraction"])),
            "top": max(dp["max_depth"] + 1, int(h * p["context_depth_fraction"])),
            "bottom": max(dp["max_depth"] + 1, int(h * p["context_depth_fraction"]))}
    table = black_table(dp["black_luma"])

    def feat(buf):
        return frame_features(buf, w, h, table, dp["max_depth"], p["context_depth_fraction"])

    def mark(f, res):
        ev = f.evidence[0]
        ev.update(res)
        tgt = _target(res["context"], p)
        before = f.severity
        if tgt is not None and Severity.INFO <= f.severity:
            f.lower_to(tgt)
        f.verifier = {"name": NAME, "version": VERSION, "decision": res["context"], "p": None,
                      "model_hash": None, "severity": f.severity.name,
                      "lowered": f.severity < before}

    # which findings are analysed: not persistent, bounded span; worst first, then time
    cands = []
    for f in todo:
        ev = f.evidence[0]
        if ev.get("persistent"):
            mark(f, {"context": "unknown", "reason": "persistent (whole clip): not analysed",
                     "context_version": VERSION})
            continue
        a = ev.get("possible_frame_start", f.frame_start)   # quality fast: sampled range
        b = ev.get("possible_frame_end", f.frame_end)       # widened by step-1
        if b - a + 1 > p["context_max_span_frames"]:
            mark(f, {"context": "unknown", "context_version": VERSION,
                     "reason": f"span {b - a + 1} frames > context_max_span_frames"})
            continue
        cands.append((f, a, b))
    cands.sort(key=lambda x: (-x[0].severity, x[1]))
    cap = int(p["max_context_candidates"])
    if len(cands) > cap:
        LOG.info("context: %d candidate(s), analysing the %d worst (max_context_candidates); "
                 "rest context unknown", len(cands), cap)
        for f, _, _ in cands[cap:]:
            mark(f, {"context": "unknown", "context_version": VERSION,
                     "reason": "not analysed (max_context_candidates)"})
        cands = cands[:cap]
    # group overlapping windows into one decode
    wins = sorted(((max(0, a - k - 1), min(b + k + 1, (nframes or b + k + 2) - 1), f, a, b)
                   for f, a, b in cands), key=lambda x: x[0])
    groups: list[list] = []
    for x in wins:
        if groups and x[0] <= groups[-1][0][1] + 1 and x[1] - groups[-1][0][0] <= 4 * (
                p["context_max_span_frames"] + 2 * k):
            g = groups[-1]
            g[0][1] = max(g[0][1], x[1])
            g[1].append(x)
        else:
            groups.append([[x[0], x[1]], [x]])
    timed_out = done = 0
    decoded = 0
    for (g0, g1), members in groups:
        if time.monotonic() > deadline:
            timed_out += len(members)
            for _, _, f, _, _ in members:
                mark(f, {"context": "unknown", "context_version": VERSION,
                         "reason": "not analysed (context_max_seconds)"})
            continue
        try:
            frames = decode_window(ctx.media.path, ffmpeg, stream, fps, w, h, g0, g1, tbs,
                                   deadline, feat)
        except (DecodeError, OSError) as e:
            LOG.warning("context decode %d-%d failed: %s", g0, g1, e)
            for _, _, f, _, _ in members:
                mark(f, {"context": "unknown", "context_version": VERSION,
                         "reason": f"context decode failed: {e}"[:200]})
            continue
        decoded += len(frames)
        cuts, dissolves = transitions(frames, p)
        for _, _, f, a, b in members:
            ev = f.evidence[0]
            res = classify(frames, ev["edge"], f.frame_start, f.frame_end, cuts, dissolves,
                           dpth[ev["edge"]], p, dp,
                           (a, b) if "possible_frame_start" in ev else None)
            mark(f, res)
            done += 1
    if timed_out:
        LOG.warning("context: time budget %.0f s exhausted, %d candidate(s) context unknown",
                    p["context_max_seconds"], timed_out)
    LOG.info("context: %d analysed in %d decode(s) (%d frames) in %.2f s; classes %s; lowered %d",
             done, len(groups), decoded, time.monotonic() - t_start,
             dict(Counter(f.evidence[0].get("context") for f in todo)),
             sum(1 for f in todo if f.verifier and f.verifier.get("lowered")))
