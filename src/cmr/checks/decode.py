"""'Does it decode': stderr of the single pass (-v error) + decoded vs. probed frame count.
ffmpeg's decoder messages carry no frame number, so findings are file-level
(no timecode) unless the frame count is short, which is located at the first
missing frame."""
from __future__ import annotations

from ..engine import Check, register


@register
class DecodeErrors(Check):
    id = "decode.errors"
    needs = frozenset({"probe", "decode"})

    def evaluate(self, ctx, candidates):
        pi, tl, out = ctx.passinfo, ctx.timeline, []
        if pi is None or not pi.ran:
            msg = "; ".join(pi.stderr) if pi else "single pass did not run"
            return [ctx.finding(f"file was not decoded: {msg}")]
        max_lines = int(ctx.params.get("max_lines", 20))
        if pi.returncode != 0 or pi.stderr:
            out.append(ctx.finding(
                f"{len(pi.stderr)} decoder error line(s), ffmpeg exit {pi.returncode} "
                "(ffmpeg does not report the frame)", evidence=[{"stderr": pi.stderr[:max_lines]}]))
        if tl.file_frames is not None and pi.decoded_frames != tl.file_frames:
            n = pi.decoded_frames or 0
            first = min(n, tl.file_frames - 1)
            out.append(ctx.finding(f"decoded {n} frames, container says {tl.file_frames}",
                                   expected=tl.file_frames, actual=n,
                                   frame_start=first, frame_end=tl.file_frames - 1))
        return out
