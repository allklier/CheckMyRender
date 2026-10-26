"""Timing checks: program frame count, head/tail layout positions, start timecode.

Frame positions come from engine.Timeline (head/tail from spec timing.head_layout /
tail_layout, file frames from ffprobe nb_frames). Segment *content* (is the black
segment black, is the slate a non-black frame) needs the content pass and is not
checked here.
"""
from __future__ import annotations

from ..engine import Check, Skip, register
from ..timecode import TimecodeError, frames_to_tc, tc_to_frames
from ..schemas import OPT_OBJ, check_params


@register
class ProgramFrameCount(Check):
    id = "timing.frame_count"
    params_schema = check_params(timing=OPT_OBJ, tolerance_frames={"type": "integer", "minimum": 0})

    def evaluate(self, ctx, candidates):
        tl, timing = ctx.timeline, ctx.params.get("timing") or {}
        exp = timing.get("program_duration_frames")
        if exp is None:
            raise Skip("spec has no timing.program_duration_frames")
        if tl.file_frames is None:
            raise Skip("file frame count unknown")
        tol = int(ctx.params.get("tolerance_frames", 0))
        act = tl.file_frames - tl.head_frames - tl.tail_frames
        if abs(act - exp) > tol:
            msg = (f"program frames: expected {exp} (+-{tol}), got {act} "
                   f"(file {tl.file_frames} - head {tl.head_frames} - tail {tl.tail_frames})")
            if tl.program_start is None:   # layout does not fit: no program range to locate
                return [ctx.finding(msg, expected=exp, actual=act)]
            return [ctx.finding(msg + "; program range shown", expected=exp, actual=act,
                                frame_start=tl.program_start, frame_end=tl.program_end)]
        return []


@register
class Layout(Check):
    """Head/tail layout positions: layout must fit in the file, and the first program
    frame must land on program_start_timecode."""
    id = "timing.layout"
    params_schema = check_params(timing=OPT_OBJ)

    def evaluate(self, ctx, candidates):
        tl, timing = ctx.timeline, ctx.params.get("timing") or {}
        out = [ctx.finding(p) for p in tl.problems if "fall outside the file" in p]
        if out:
            return out
        pst = timing.get("program_start_timecode")
        if pst is None:
            raise Skip("spec has no timing.program_start_timecode")
        if tl.tc_source == "none":
            raise Skip("no file timecode and no spec start timecode")
        try:
            exp = tc_to_frames(pst, tl.fps, tl.drop)
        except TimecodeError as e:
            return [ctx.finding(f"bad program_start_timecode: {e}")]
        act = tl.start_frame + tl.head_frames
        if act != exp:
            # locate the frame that actually carries program_start_timecode, if inside the file
            at = exp - tl.start_frame
            where = (f"; {pst} is file frame {at}" if tl.file_frames and 0 <= at < tl.file_frames
                     else "")
            out.append(ctx.finding(
                f"first program frame (file frame {tl.head_frames}, after {tl.head_frames} head frames) "
                f"has timecode {tl.tc(tl.head_frames)}, expected {pst}{where}",
                expected=pst, actual=tl.tc(tl.head_frames),
                frame_start=tl.head_frames, frame_end=tl.head_frames))
        return out


@register
class TimecodeStart(Check):
    """Embedded start timecode vs spec file_start_timecode (or, if absent, derived as
    program_start_timecode - head frames). Also checks the DF/NDF mode (';' = drop).
    Source (media.timecode_source): a stream `timecode` tag (MOV tmcd/video), else the
    format tag (MXF material package)."""
    id = "timing.timecode_start"
    params_schema = check_params(timing=OPT_OBJ)

    def evaluate(self, ctx, candidates):
        tl, timing, media = ctx.timeline, ctx.params.get("timing") or {}, ctx.media
        if media.timecode is None:
            return [ctx.finding("no embedded timecode (no tmcd track, stream or MXF format timecode tag)")]
        out = []
        want_drop = timing.get("timecode_mode", "non-drop") == "drop"
        if tl.drop != want_drop:
            out.append(ctx.finding(f"timecode mode: expected {'drop' if want_drop else 'non-drop'}, "
                                   f"file timecode {media.timecode} is {'drop' if tl.drop else 'non-drop'}",
                                   expected=timing.get("timecode_mode"), actual=media.timecode))
        try:
            if timing.get("file_start_timecode"):
                exp = tc_to_frames(timing["file_start_timecode"], tl.fps, tl.drop)
                how = "spec file_start_timecode"
            elif timing.get("program_start_timecode"):
                exp = tc_to_frames(timing["program_start_timecode"], tl.fps, tl.drop) - tl.head_frames
                how = f"program_start_timecode - {tl.head_frames} head frames"
            else:
                raise Skip("spec has no file_start_timecode or program_start_timecode")
        except TimecodeError as e:
            return out + [ctx.finding(f"bad spec timecode: {e}")]
        if exp != tl.start_frame:
            exp_tc = frames_to_tc(exp, tl.fps, tl.drop)
            src = f" from {media.timecode_source}" if media.timecode_source else ""
            out.append(ctx.finding(f"start timecode: expected {exp_tc} ({how}), got {media.timecode}{src} "
                                   f"(off by {tl.start_frame - exp:+d} frames)",
                                   expected=exp_tc, actual=media.timecode, frame_start=0, frame_end=0))
        return out
