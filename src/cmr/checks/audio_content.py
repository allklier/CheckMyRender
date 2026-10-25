"""Audio content checks over the PROGRAM RANGE (head/tail excluded via engine.Timeline):
audio.loudness, audio.silence, audio.silent_channel, audio.clipping.

Program range in samples: start = program_start * sr / fps, end = (program_end + 1) * sr / fps,
each rounded half-up to an integer sample (`_samples`), cut with `atrim=start_sample:end_sample`.
atrim keeps the original timestamps (verified: first printed pts = start_sample).

Per-frame levels (silence / silent channel / clipping):
    atrim,asetnsamples=n=N:p=0,astats=metadata=1:reset=1:measure_perchannel=Peak_level:measure_overall=none
N = floor(sr / fps) samples. Chunk k starts at sample start + k*N and belongs to the file frame
floor((start + k*N) * fps / sr) (exact Fractions). The frame is taken from the chunk INDEX, not
from the printed pts_time: ametadata prints pts_time with 6 significant digits ("3600.04" for
3600.0417 s), too coarse for frame accuracy in long files. When sr/fps is an integer
(48 kHz at 23.976/24/25/30) chunks are exactly one frame; otherwise (e.g. 29.97: 1601.6) a frame
gets one or two chunks and a chunk may spill < 1 frame into the next frame. Frames of the
program range without any chunk (audio shorter than the program) count as silent.

Loudness: `atrim,ebur128=metadata=1:peak=true` -> per-100 ms records lavfi.r128.I / LRA
(running values, LUFS/LU, 3 decimals) and lavfi.r128.true_peak (running max, LINEAR, 3 decimals).
The last record gives I and LRA; true peak = 20*log10(last true_peak). The first record whose
running true peak exceeds the ceiling locates the overshoot to a 100 ms block (N = sr // 10).
ebur128 is BS.1770 gating (absolute -70 LUFS + relative -10 LU), NOT dialogue-gated.

Verified on ffmpeg 6.1.1 (this box): `ffmpeg -h filter=ebur128` (metadata, peak=true, keys
seen in output), `-h filter=astats` (metadata, reset, measure_perchannel Peak_level,
measure_overall none; key lavfi.astats.<ch 1-based>.Peak_level, digital silence = -inf),
`-h filter=asetnsamples` (n, p), `-h filter=atrim` (start_sample, end_sample).
`silencedetect` has `mono=1` (per-channel keys lavfi.silence_start.<ch>) but reports seconds
only at run edges, so it is not used: runs are found in Python on integer frames.
"""
from __future__ import annotations

import math
from fractions import Fraction

from ..engine import Check, Skip, Tap, register
from ..model import Candidate, Severity


def _samples(frame: int, sr: int, fps: Fraction) -> int:
    """File frame boundary -> sample index, rounded half-up."""
    return math.floor(Fraction(frame) * sr / fps + Fraction(1, 2))


def _program(ctx, stream: dict):
    """-> (start_sample, end_sample, sample_rate) of the program range on this stream."""
    tl = ctx.timeline
    if tl.program_start is None:
        raise Skip("head/tail layout does not fit the file: no program range")
    sr = stream.get("sample_rate")
    if not sr:
        raise Skip(f"stream {stream['index']}: unknown sample rate")
    return _samples(tl.program_start, sr, tl.fps), _samples(tl.program_end + 1, sr, tl.fps), sr


def _db(x: str) -> float:
    return float(x)          # astats prints "-inf" for digital silence; float() accepts it


def _num(x: float):
    """JSON-safe level: -inf (digital silence) -> None."""
    return None if math.isinf(x) else round(x, 3)


def _runs(frames):
    """Sorted ints -> [(first, last)] of consecutive runs."""
    out = []
    for f in frames:
        if out and f == out[-1][1] + 1:
            out[-1][1] = f
        else:
            out.append([f, f])
    return [tuple(r) for r in out]


class _PerFrameLevels(Check):
    """Base: per-frame per-channel sample peak (dBFS) of every audio stream in the program."""
    requires_filters = ("atrim", "asetnsamples", "astats")

    def taps(self, ctx):
        taps = []
        for s in ctx.media.audio:
            start, end, sr = _program(ctx, s)
            n = math.floor(sr / ctx.timeline.fps)
            taps.append(Tap(name=f"a{s['index']}", stream=s["index"], chain=(
                f"atrim=start_sample={start}:end_sample={end},asetnsamples=n={n}:p=0,"
                "astats=metadata=1:reset=1:measure_perchannel=Peak_level:measure_overall=none")))
        return taps

    def levels(self, ctx, records):
        """-> {stream index: {frame: [peak dBFS per channel]}} over every program frame."""
        tl, out = ctx.timeline, {}
        for s in ctx.media.audio:
            start, _, sr = _program(ctx, s)
            n = math.floor(sr / tl.fps)
            ch = s["channels"]
            per = {f: [-math.inf] * ch for f in range(tl.program_start, tl.program_end + 1)}
            for k, rec in enumerate(records.get(f"a{s['index']}", [])):
                f = math.floor(Fraction(start + k * n) * tl.fps / sr)
                if f not in per:
                    continue
                for c in range(ch):
                    v = rec.values.get(f"lavfi.astats.{c + 1}.Peak_level")
                    if v is None:
                        raise RuntimeError(f"astats gave no Peak_level for channel {c + 1}")
                    per[f][c] = max(per[f][c], _db(v))
            out[s["index"]] = per
        return out


_threshold = {"type": "number", "maximum": 0}


@register
class Silence(_PerFrameLevels):
    """Dead zones: runs of frames where every channel of a stream peaks below threshold_dbfs.
    A run longer than max_run_frames is a problem."""
    id = "audio.silence"
    params_schema = {"type": "object", "required": ["threshold_dbfs", "max_run_frames"],
                     "properties": {"threshold_dbfs": _threshold,
                                    "max_run_frames": {"type": "integer", "minimum": 0}}}

    def detect(self, ctx, records):
        thr = ctx.params["threshold_dbfs"]
        cands = []
        for idx, per in self.levels(ctx, records).items():
            silent = [f for f, peaks in sorted(per.items()) if max(peaks) < thr]
            for a, b in _runs(silent):
                cands.append(Candidate(self.id, idx, a, b, float(b - a + 1),
                                       [{"frames": b - a + 1, "threshold_dbfs": thr}]))
        return cands

    def evaluate(self, ctx, candidates):
        mx = ctx.params["max_run_frames"]
        return [ctx.finding(f"stream {c.stream}: silence {c.frame_end - c.frame_start + 1} frames "
                            f"(all channels < {ctx.params['threshold_dbfs']} dBFS; max {mx})",
                            stream=c.stream, frame_start=c.frame_start, frame_end=c.frame_end,
                            expected=f"<= {mx} frames", actual=c.frame_end - c.frame_start + 1,
                            candidate=c)
                for c in candidates if c.frame_end - c.frame_start + 1 > mx]


@register
class SilentChannel(_PerFrameLevels):
    """A channel whose sample peak stays below threshold_dbfs for the whole program."""
    id = "audio.silent_channel"
    params_schema = {"type": "object", "required": ["threshold_dbfs"],
                     "properties": {"threshold_dbfs": _threshold}}

    def detect(self, ctx, records):
        thr, tl = ctx.params["threshold_dbfs"], ctx.timeline
        cands = []
        for idx, per in self.levels(ctx, records).items():
            for c in range(len(next(iter(per.values())))):
                peak = max(p[c] for p in per.values())
                if peak < thr:
                    cands.append(Candidate(self.id, idx, tl.program_start, tl.program_end, 1.0,
                                           [{"channel": c + 1, "peak_dbfs": _num(peak)}]))
        return cands

    def evaluate(self, ctx, candidates):
        out = []
        for c in candidates:
            ev = c.evidence[0]
            peak = "-inf" if ev["peak_dbfs"] is None else f"{ev['peak_dbfs']:.1f}"
            out.append(ctx.finding(
                f"stream {c.stream} channel {ev['channel']}: silent for the whole program "
                f"(peak {peak} dBFS < {ctx.params['threshold_dbfs']} dBFS)",
                stream=c.stream, frame_start=c.frame_start, frame_end=c.frame_end,
                actual=ev["peak_dbfs"], candidate=c))
        return out


@register
class Clipping(_PerFrameLevels):
    """Runs of frames where any channel's sample peak reaches clip_dbfs (at/near full scale)."""
    id = "audio.clipping"
    params_schema = {"type": "object", "required": ["clip_dbfs"],
                     "properties": {"clip_dbfs": _threshold}}

    def detect(self, ctx, records):
        thr = ctx.params["clip_dbfs"]
        cands = []
        for idx, per in self.levels(ctx, records).items():
            hot = [f for f, peaks in sorted(per.items()) if max(peaks) >= thr]
            for a, b in _runs(hot):
                chans = sorted({c + 1 for f in range(a, b + 1) for c, p in enumerate(per[f]) if p >= thr})
                peak = max(max(per[f]) for f in range(a, b + 1))
                cands.append(Candidate(self.id, idx, a, b, peak,
                                       [{"channels": chans, "peak_dbfs": peak}]))
        return cands

    def evaluate(self, ctx, candidates):
        return [ctx.finding(f"stream {c.stream}: sample peak {c.evidence[0]['peak_dbfs']:.2f} dBFS "
                            f">= {ctx.params['clip_dbfs']} dBFS on channel(s) "
                            f"{','.join(map(str, c.evidence[0]['channels']))}",
                            stream=c.stream, frame_start=c.frame_start, frame_end=c.frame_end,
                            actual=c.evidence[0]["peak_dbfs"], candidate=c)
                for c in candidates]


GATING = "BS.1770 relative-gated (-70 LUFS absolute, -10 LU relative); not dialogue-gated"


@register
class Loudness(Check):
    """Integrated loudness / LRA / true peak of the spec's program track over the program range.
    The spec names the standard (`loudness.standard`) and track (`loudness.program_track`,
    position among audio streams); target, tolerance and true-peak ceiling come from the
    profile's `standards` presets. Optional `lra_max_lu`."""
    id = "audio.loudness"
    requires_filters = ("atrim", "ebur128")
    _preset = {"type": "object", "required": ["target_lufs", "tolerance_lu", "true_peak_max_dbtp"],
               "properties": {"target_lufs": {"type": "number"},
                              "tolerance_lu": {"type": "number", "minimum": 0},
                              "true_peak_max_dbtp": {"type": "number"}}}
    params_schema = {"type": "object", "required": ["loudness", "standards"],
                     "properties": {"loudness": {"type": "object", "required": ["standard"]},
                                    "standards": {"type": "object", "additionalProperties": _preset},
                                    "lra_max_lu": {"type": "number", "minimum": 0}}}

    def _stream(self, ctx):
        t = ctx.params["loudness"].get("program_track", 0)
        if t >= len(ctx.media.audio):
            raise Skip(f"program_track {t} not in file ({len(ctx.media.audio)} audio streams)")
        return ctx.media.audio[t]

    def taps(self, ctx):
        s = self._stream(ctx)
        start, end, _ = _program(ctx, s)
        return [Tap(name="r128", stream=s["index"],
                    chain=f"atrim=start_sample={start}:end_sample={end},ebur128=metadata=1:peak=true")]

    def detect(self, ctx, records):
        recs = records.get("r128", [])
        if not recs:
            raise RuntimeError("ebur128 produced no measurement")
        s = self._stream(ctx)
        std = ctx.params["loudness"]["standard"]
        preset = ctx.params["standards"].get(std)
        if preset is None:
            raise ValueError(f"standard {std!r} has no preset in the profile "
                             f"(known: {', '.join(ctx.params['standards'])})")
        start, end, sr = _program(ctx, s)
        last = recs[-1].values
        lin = float(last["lavfi.r128.true_peak"])
        m = {"standard": std, **preset, "gating": GATING, "stream": s["index"],
             "integrated_lufs": float(last["lavfi.r128.I"]), "lra_lu": float(last["lavfi.r128.LRA"]),
             "true_peak_dbtp": _num(20 * math.log10(lin)) if lin > 0 else None, "tp_block": None}
        ceiling = 10 ** (preset["true_peak_max_dbtp"] / 20)
        for k, r in enumerate(recs):            # running max: first block over the ceiling
            if float(r.values["lavfi.r128.true_peak"]) > ceiling:
                n = sr // 10
                fps = ctx.timeline.fps
                a = math.floor(Fraction(start + k * n) * fps / sr)
                b = math.floor(Fraction(min(start + (k + 1) * n, end) - 1) * fps / sr)
                m["tp_block"] = [a, b]
                break
        tl = ctx.timeline
        return [Candidate(self.id, s["index"], tl.program_start, tl.program_end, m["integrated_lufs"], [m])]

    def evaluate(self, ctx, candidates):
        (c,) = candidates
        m = c.evidence[0]
        tp = "-inf" if m["true_peak_dbtp"] is None else f"{m['true_peak_dbtp']:.1f}"
        summary = (f"{m['standard']}: I {m['integrated_lufs']:.1f} LUFS, LRA {m['lra_lu']:.1f} LU, "
                   f"true peak {tp} dBTP [{GATING}]")
        loc = dict(stream=c.stream, frame_start=c.frame_start, frame_end=c.frame_end, candidate=c,
                   evidence=[m])
        out = []
        if abs(m["integrated_lufs"] - m["target_lufs"]) > m["tolerance_lu"]:
            out.append(ctx.finding(f"integrated loudness outside {m['target_lufs']} +-{m['tolerance_lu']} LU; "
                                   + summary, expected=f"{m['target_lufs']} +-{m['tolerance_lu']} LUFS",
                                   actual=round(m["integrated_lufs"], 1), **loc))
        if m["tp_block"]:
            out.append(ctx.finding(f"true peak above {m['true_peak_max_dbtp']} dBTP (first in this 100 ms "
                                   f"block); " + summary, expected=f"<= {m['true_peak_max_dbtp']} dBTP",
                                   actual=m["true_peak_dbtp"],
                                   **{**loc, "frame_start": m["tp_block"][0], "frame_end": m["tp_block"][1]}))
        lra_max = ctx.params.get("lra_max_lu")
        if lra_max is not None and m["lra_lu"] > lra_max:
            out.append(ctx.finding(f"loudness range above {lra_max} LU; " + summary,
                                   expected=f"<= {lra_max} LU", actual=round(m["lra_lu"], 1), **loc))
        return out or [ctx.finding(summary, Severity.PASS, actual=round(m["integrated_lufs"], 1), **loc)]
