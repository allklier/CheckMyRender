"""video.black_runs: runs of black frames inside the PROGRAM range.

Stage 1: signalstats per frame (verified: `ffmpeg -h filter=signalstats` lists no
per-frame options; it prints lavfi.signalstats.YHIGH etc. for every frame), on 8-bit
tv-range luma (black = 16):
  quality high: `format=yuv420p,signalstats` - YHIGH identical per frame to the former
    `format=yuv444p,signalstats` (0 differences over 1,980 test frames, see
    src/cmr/checks/CLAUDE.md) at ~45% of its cost (only the chroma planes differ).
  quality fast: `scale=iw/4:ih/4:flags=area+accurate_rnd,format=yuv420p,signalstats`
    (1/16 of the pixels, every frame). Area averaging lowers YHIGH on grainy near-black
    (more frames called black = more candidates, not fewer) and raises it where thin
    bright lines cover <10% of a black frame; flat levels match (accurate_rnd removes a
    +1 bias). Every frame is still measured, so run lengths stay frame-exact.
A frame is black when YHIGH (90th percentile luma) <= black_luma,
so a dark-but-not-black scene (YHIGH well above 16) is not flagged. Runs are found on
integer frames, clipped to timeline.program_start..program_end (head/tail layout black
is intentional and ignored). A run longer than `max_black_run_frames` is a candidate.
Frames absent from the records break a run. Edge-of-frame black borders are the blanking
check's job, not this one.
"""
from __future__ import annotations

from cmr.engine import Check, Skip, Tap, register
from cmr.model import Candidate

KEY = "lavfi.signalstats.YHIGH"
CHAINS = {"high": "format=yuv420p,signalstats",
          "fast": "scale=iw/4:ih/4:flags=area+accurate_rnd,format=yuv420p,signalstats"}
BLACK_LUMA = 24            # 8-bit code value; tv black = 16


@register
class BlackRuns(Check):
    id = "video.black_runs"
    needs = frozenset({"probe"})
    requires_filters = ("format", "scale", "signalstats")
    qualities = ("fast", "high")
    params_schema = {
        "type": "object", "additionalProperties": False,
        "required": ["max_black_run_frames"],
        "properties": {"max_black_run_frames": {"type": "integer", "minimum": 0},
                       "black_luma": {"type": "number", "minimum": 0}},
    }

    def taps(self, ctx):
        return [Tap("black", ctx.media.video[0]["index"], CHAINS[ctx.quality], (KEY,))]

    def detect(self, ctx, records):
        tl = ctx.timeline
        if tl.program_start is None or tl.program_end is None:
            raise Skip("program range unknown (head/tail layout does not fit)")
        limit = ctx.params["max_black_run_frames"]
        luma = ctx.params.get("black_luma", BLACK_LUMA)
        black = sorted(r.frame for r in records["black"]
                       if tl.program_start <= r.frame <= tl.program_end
                       and float(r.values[KEY]) <= luma)
        runs, start, prev = [], None, None
        for f in black + [None]:
            if start is not None and (f is None or f != prev + 1):
                runs.append((start, prev))
                start = None
            if f is not None and start is None:
                start = f
            prev = f
        v = ctx.media.video[0]["index"]
        return [Candidate(self.id, v, a, b, float(b - a + 1), [{"run_frames": b - a + 1}])
                for a, b in runs if b - a + 1 > limit]

    def evaluate(self, ctx, candidates):
        limit = ctx.params["max_black_run_frames"]
        return [ctx.finding(f"{c.frame_end - c.frame_start + 1} consecutive black frames in program",
                            expected=f"<= {limit}", actual=c.frame_end - c.frame_start + 1,
                            stream=c.stream, frame_start=c.frame_start, frame_end=c.frame_end,
                            candidate=c) for c in candidates]
