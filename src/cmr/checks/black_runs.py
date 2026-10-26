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
A frame is dark when YHIGH (90th percentile luma) <= black_luma,
so a dark-but-not-black scene (YHIGH well above 16) is not flagged.

Content on black (param `content_on_black`, default on): a dark frame that carries a logo,
end card or credits is NOT black. Second tap, same in both quality modes (full-resolution
threshold, so 1-2 px text strokes count):
  lutyuv=y='if(gt(val,minval+(T-16)*(maxval-minval)/219),maxval,minval)',
  scale=iw/4:ih/4:flags=area+accurate_rnd,format=yuv420p,signalstats   -> YAVG
The mask is maxval/minval of the input's own range (lutyuv: 940/64 for 10-bit tv, 255/0 for
yuvj/gray; measured), format=yuv420p maps both to 16/235, and area scaling keeps the mean, so
bright fraction = (YAVG - 16) / 219 for any input format (measured exact on yuvj420p, yuv420p,
yuv422p10le, yuv444p12le: 1/32 box -> YAVG 22.8438). T = `content_luma` (8-bit tv code).
A dark frame whose bright fraction >= `content_min_fraction` is content, not black. Raw dark
runs longer than the limit that contained content frames give one INFO finding (kind
`content_on_black`) so the excusal stays visible; the remaining pure-black pieces are judged
as usual. Thresholds measured on real material (see src/cmr/checks/CLAUDE.md).

Runs are found on integer frames, clipped to timeline.program_start..program_end (head/tail
layout black is intentional and ignored). A run longer than `max_black_run_frames` is a
candidate. Frames absent from the records break a run. Edge-of-frame black borders are the
blanking check's job, not this one.
"""
from __future__ import annotations

from cmr import log
from cmr.engine import Check, Skip, Tap, register
from cmr.model import Candidate, Severity

LOG = log.get("video.black_runs")
KEY = "lavfi.signalstats.YHIGH"
AVG = "lavfi.signalstats.YAVG"
CHAINS = {"high": "format=yuv420p,signalstats",
          "fast": "scale=iw/4:ih/4:flags=area+accurate_rnd,format=yuv420p,signalstats"}
BLACK_LUMA = 24            # 8-bit code value; tv black = 16
CONTENT_LUMA = 100         # 8-bit tv code: a pixel above this is picture content (logo/text)
CONTENT_MIN_FRACTION = 0.0001   # 0.01% of the frame (207 px at 1080p); one hot pixel is 0.00005%
CONTENT_KIND = "content_on_black"


def bright_chain(content_luma: float) -> str:
    t = f"minval+({content_luma}-16)*(maxval-minval)/219"
    return (f"lutyuv=y='if(gt(val,{t}),maxval,minval)',"
            "scale=iw/4:ih/4:flags=area+accurate_rnd,format=yuv420p,signalstats")


def runs(frames) -> list[tuple[int, int]]:
    """Sorted ints -> [(first, last)] of consecutive runs."""
    out = []
    for f in frames:
        if out and f == out[-1][1] + 1:
            out[-1][1] = f
        else:
            out.append([f, f])
    return [tuple(r) for r in out]


def classify(dark: list[int], share: dict[int, float], limit: int, min_fraction: float | None):
    """dark: sorted dark frames; share: frame -> bright fraction (missing = 0).
    -> (black runs > limit, excused [(raw run, content frames)] for raw runs > limit with content).
    min_fraction None = detector off (every dark frame is black)."""
    content = set() if min_fraction is None else {f for f in dark if share.get(f, 0.0) >= min_fraction}
    black = [(a, b) for a, b in runs([f for f in dark if f not in content]) if b - a + 1 > limit]
    excused = []
    for a, b in runs(dark):
        c = sorted(f for f in content if a <= f <= b)
        if b - a + 1 > limit and c:
            excused.append(((a, b), c))
    return black, excused


@register
class BlackRuns(Check):
    id = "video.black_runs"
    needs = frozenset({"probe"})
    requires_filters = ("format", "scale", "signalstats", "lutyuv")
    qualities = ("fast", "high")
    params_schema = {
        "type": "object", "additionalProperties": False,
        "required": ["max_black_run_frames"],
        "properties": {"max_black_run_frames": {"type": "integer", "minimum": 0},
                       "black_luma": {"type": "number", "minimum": 0},
                       "content_on_black": {"type": "boolean"},
                       "content_luma": {"type": "number", "minimum": 16, "maximum": 255},
                       "content_min_fraction": {"type": "number", "exclusiveMinimum": 0,
                                                "maximum": 1}},
    }

    def taps(self, ctx):
        v = ctx.media.video[0]["index"]
        taps = [Tap("black", v, CHAINS[ctx.quality], (KEY,))]
        if ctx.params.get("content_on_black", True):
            taps.append(Tap("bright", v, bright_chain(ctx.params.get("content_luma", CONTENT_LUMA)),
                            (AVG,)))
        return taps

    def detect(self, ctx, records):
        tl = ctx.timeline
        if tl.program_start is None or tl.program_end is None:
            raise Skip("program range unknown (head/tail layout does not fit)")
        limit = ctx.params["max_black_run_frames"]
        luma = ctx.params.get("black_luma", BLACK_LUMA)
        on = ctx.params.get("content_on_black", True)
        minf = ctx.params.get("content_min_fraction", CONTENT_MIN_FRACTION) if on else None
        dark = sorted(r.frame for r in records["black"]
                      if tl.program_start <= r.frame <= tl.program_end
                      and float(r.values[KEY]) <= luma)
        share = {r.frame: max(0.0, (float(r.values[AVG]) - 16) / 219)
                 for r in records.get("bright", [])} if on else {}
        black, excused = classify(dark, share, limit, minf)
        v = ctx.media.video[0]["index"]
        cands = [Candidate(self.id, v, a, b, float(b - a + 1), [{"run_frames": b - a + 1}])
                 for a, b in black]
        for (a, b), c in excused:
            left = [[x, y] for x, y in black if a <= x <= b]
            ev = {"kind": CONTENT_KIND, "dark_run_frames": b - a + 1, "content_frames": len(c),
                  "content_ranges": [list(r) for r in runs(c)],
                  "max_content_fraction": round(max(share.get(f, 0.0) for f in c), 6),
                  "black_runs_left": left,
                  "content_luma": ctx.params.get("content_luma", CONTENT_LUMA),
                  "content_min_fraction": minf}
            LOG.info("dark run %d-%d (%d frames): %d frame(s) with content on black excused, "
                     "black runs left %s", a, b, b - a + 1, len(c), left)
            cands.append(Candidate(self.id, v, a, b, float(len(c)), [ev]))
        return cands

    def evaluate(self, ctx, candidates):
        limit = ctx.params["max_black_run_frames"]
        out = []
        for c in candidates:
            n = c.frame_end - c.frame_start + 1
            ev = c.evidence[0]
            if ev.get("kind") == CONTENT_KIND:
                left = ev["black_runs_left"]
                rest = (f"; black run(s) still reported: {', '.join(f'{x}-{y}' for x, y in left)}"
                        if left else "")
                out.append(ctx.finding(
                    f"{n} dark frames in program not counted as a black run: {ev['content_frames']} "
                    f"frame(s) carry content on black (logo/text/credits, up to "
                    f"{ev['max_content_fraction'] * 100:.2f}% of the picture above luma "
                    f"{ev['content_luma']}){rest}",
                    Severity.INFO, expected=f"<= {limit}", actual=n, stream=c.stream,
                    frame_start=c.frame_start, frame_end=c.frame_end, candidate=c, evidence=[ev]))
            else:
                out.append(ctx.finding(f"{n} consecutive black frames in program",
                                       expected=f"<= {limit}", actual=n, stream=c.stream,
                                       frame_start=c.frame_start, frame_end=c.frame_end,
                                       candidate=c))
        return out
