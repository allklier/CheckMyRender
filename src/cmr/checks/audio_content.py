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

Verified on ffmpeg 6.1.1 (dev machine): `ffmpeg -h filter=ebur128` (metadata, peak=true, keys
seen in output), `-h filter=astats` (metadata, reset, measure_perchannel Peak_level,
measure_overall none; key lavfi.astats.<ch 1-based>.Peak_level, digital silence = -inf),
`-h filter=asetnsamples` (n, p), `-h filter=atrim` (start_sample, end_sample).
`silencedetect` has `mono=1` (per-channel keys lavfi.silence_start.<ch>) but reports seconds
only at run edges, so it is not used: runs are found in Python on integer frames.
"""
from __future__ import annotations

import math
from fractions import Fraction

import jsonschema

from .. import log
from ..engine import Check, ConfigError, Skip, Tap, register
from ..model import Candidate, Severity
from ..schemas import check_params
from ..timecode import TimecodeError, tc_to_frames

LOG = log.get("audio")
DECLARED = "declared_silence"      # evidence kind of findings about declared (intended) silence


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


def allowed_silence(ctx) -> dict[tuple[int, int], list[tuple[int, int]]]:
    """Declared intended silence -> {(absolute stream index, channel 0-based): [(a, b) file frames]}.
    Entries come from the spec (`audio.allowed_silence`, passed as param `audio`) and/or the
    profile param `allowed_silence`: {stream: position among audio streams (0-based, as
    audio.tracks / loudness program_track), channels: [1-based] (omitted = all), start/end:
    timecode (inclusive; omitted = program start/end)}. A stream not in the file is ignored."""
    entries = list((ctx.params.get("audio") or {}).get("allowed_silence") or [])
    entries += list(ctx.params.get("allowed_silence") or [])
    tl, out = ctx.timeline, {}
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("stream"), int):
            raise ConfigError(f"allowed_silence entry needs an integer 'stream': {e!r}")
        if e["stream"] >= len(ctx.media.audio):
            LOG.warning("allowed_silence: stream %d not in file (%d audio streams), ignored",
                        e["stream"], len(ctx.media.audio))
            continue
        s = ctx.media.audio[e["stream"]]
        chans = e.get("channels") or list(range(1, s["channels"] + 1))
        if any(not isinstance(c, int) or not 1 <= c <= s["channels"] for c in chans):
            raise ConfigError(f"allowed_silence stream {e['stream']}: channels {chans} not in 1..{s['channels']}")
        try:
            a = (tc_to_frames(e["start"], tl.fps, tl.drop) - tl.start_frame if e.get("start")
                 else tl.program_start)
            b = (tc_to_frames(e["end"], tl.fps, tl.drop) - tl.start_frame if e.get("end")
                 else tl.program_end)
        except TimecodeError as err:
            raise ConfigError(f"allowed_silence: {err}") from err
        for c in chans:
            out.setdefault((s["index"], c - 1), []).append((a, b))
    if out:
        LOG.info("declared silence: %s", {f"{k[0]}:{k[1] + 1}": v for k, v in out.items()})
    return out


def _allowed_at(ranges, f) -> bool:
    return any(a <= f <= b for a, b in ranges or ())


_ALLOWED = {"type": "array", "items": {"type": "object", "required": ["stream"], "properties": {
    "stream": {"type": "integer", "minimum": 0},
    "channels": {"type": "array", "items": {"type": "integer", "minimum": 1}},
    "start": {"type": "string"}, "end": {"type": "string"}}}}


# Spec channel names -> ffmpeg channel names. LFE (and "-" = unused) are left out of the
# measured layout: ebur128 gives LFE weight 0 for loudness but still counts it in the true
# peak (measured, ffmpeg 6.1.1), so the channel is dropped before ebur128 (BS.1770 excludes LFE).
# Surrounds map to SL/SR (ebur128 weight 1.41, measured: +1.5 LU, same as BL/BR).
FF_CHANNEL = {"L": "FL", "R": "FR", "C": "FC", "M": "FC", "Ls": "SL", "Rs": "SR"}
DROPPED = ("LFE", "-")
FF_ORDER = ["FL", "FR", "FC", "SL", "SR"]          # ffmpeg channel-mask order
DEFAULT_CHANNELS = {2: ["L", "R"], 6: ["L", "R", "C", "LFE", "Ls", "Rs"]}
PROGRAM_SCHEMA = {"type": "object", "required": ["streams"], "additionalProperties": False,
                  "properties": {"name": {"type": "string"},
                                 "streams": {"type": "array", "minItems": 1,
                                             "items": {"type": "integer", "minimum": 0}},
                                 "channels": {"type": "array", "items": {
                                     "enum": [*FF_CHANNEL, *DROPPED]}}}}
_PROGRAMS = {"type": "array", "minItems": 1, "items": PROGRAM_SCHEMA}


def declared_programs(ctx) -> list[dict] | None:
    """The spec's program declaration, or None. `audio.programs` (param `audio` = spec audio
    section) is the place; `audio.loudness.programs` (param `loudness`, or inside `audio`) is
    the older spelling and still works. Both present and different = ConfigError."""
    audio = ctx.params.get("audio") or {}
    ld = ctx.params.get("loudness") or audio.get("loudness") or {}
    a, b = audio.get("programs"), ld.get("programs")
    if a is not None and b is not None and a != b:
        raise ConfigError("audio.programs and audio.loudness.programs (older spelling) declare "
                          "different programs; declare them once, under audio.programs")
    progs = a if a is not None else b
    if progs is None:
        return None
    err = next(jsonschema.Draft202012Validator(_PROGRAMS).iter_errors(progs), None)
    if err is not None:
        raise ConfigError(f"audio.programs: {err.message}")
    return progs


def resolve_programs(ctx, progs: list[dict]) -> list[dict]:
    """-> [{name, streams: [stream dicts], channels: [names] | None, measured: [names] | None,
    roles: [(absolute stream index, channel 0-based, role name)]}] for every merged channel.
    `channels` names the merged channels in order (default 2 ch L R, 6 ch L R C LFE Ls Rs; a
    single mono stream without channels = its own layout, roles ch1..)."""
    audio, out = ctx.media.audio, []
    for i, p in enumerate(progs):
        name = p.get("name") or f"program {i + 1}"
        missing = [t for t in p["streams"] if t >= len(audio)]
        if missing:
            raise Skip(f"{name}: audio stream(s) {missing} not in file ({len(audio)} audio streams)")
        streams = [audio[t] for t in p["streams"]]
        if len({s.get("sample_rate") for s in streams}) > 1:
            raise Skip(f"{name}: streams have different sample rates, cannot merge")
        total = sum(s["channels"] for s in streams)
        chans = p.get("channels") or DEFAULT_CHANNELS.get(total)
        if total == 1 and not p.get("channels"):
            chans = None                  # single mono stream: as program_track
        elif chans is None:
            raise ConfigError(f"audio program {name}: {total} merged channels, give 'channels' "
                              f"(names {', '.join([*FF_CHANNEL, *DROPPED])})")
        elif len(chans) != total:
            raise ConfigError(f"audio program {name}: 'channels' names {len(chans)} channel(s), "
                              f"the streams carry {total}")
        if chans is not None:
            ff = [FF_CHANNEL[c] for c in chans if c not in DROPPED]
            if not ff or len(set(ff)) != len(ff):
                raise ConfigError(f"audio program {name}: channels {chans} measure no channel or "
                                  "repeat one (M and C are the same position)")
        names = chans or [f"ch{k + 1}" for k in range(total)]
        roles = [(s["index"], c) for s in streams for c in range(s["channels"])]
        out.append({"name": name, "streams": streams, "channels": chans,
                    "measured": None if chans is None else [c for c in chans if c not in DROPPED],
                    "roles": [(s, c, n) for (s, c), n in zip(roles, names)]})
    return out


def _label(chans) -> str:
    """[(stream, channel 0-based, role)] -> 'L(1) R(2) Ls(5)' (stream index; '.channel' when
    the stream has several channels)."""
    multi = {s for s, c, _ in chans if c > 0}
    return " ".join(f"{r}({s}.{c + 1})" if s in multi else f"{r}({s})" for s, c, r in chans)


def _groups(ctx, levels, measured):
    """Units the silence checks judge: one per declared program (its channels whose role passes
    `measured(role)`), then one per audio stream not in any program (all its channels, role
    None) - files without programs are judged per stream as before.
    -> [(finding stream, [(stream, channel, role)], program dict | None)]."""
    progs = declared_programs(ctx)
    progs = resolve_programs(ctx, progs) if progs else []
    out, covered = [], set()
    for p in progs:
        out.append((p["streams"][0]["index"], [x for x in p["roles"] if measured(x[2])], p))
        covered |= {s for s, _, _ in p["roles"]}
    for idx, per in levels.items():
        if idx not in covered:
            out.append((idx, [(idx, c, None) for c in range(len(next(iter(per.values()))))], None))
    return out


def _prog_ev(p, chans) -> dict:
    return {} if p is None else {"program": p["name"], "streams": [s["index"] for s in p["streams"]],
                                 "roles": [r for _, _, r in chans], "label": _label(chans)}


def _who(stream, ev) -> str:
    """Message prefix: 'program 5.1 (L(1) R(2) ...)' or 'stream N'."""
    return f"program {ev['program']} ({ev['label']})" if ev.get("program") else f"stream {stream}"


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
    """Dead zones. With declared programs (spec audio.programs, or the older
    audio.loudness.programs) a PROGRAM is silent in a frame when ALL its measured channels (LFE
    and '-' excluded) peak below threshold_dbfs at the same time; each run longer than
    max_run_frames is reported once per program (finding stream = the program's first stream).
    Streams in no program, and every stream of a file without programs, are judged per stream
    as before (all channels of the stream). Channels declared silent (allowed_silence) are left
    out of the per-frame test; a frame where every channel is declared is not judged. A run that
    would have been reported without the declaration gives one INFO (kind declared_silence).
    Optional `warn_run_frames` (< max_run_frames) adds a lower tier: a run longer than it and up
    to max_run_frames is a WARN ("please listen", evidence tier warn), a longer one keeps the
    check's severity (tier limit). Absent or >= max_run_frames = the single tier."""
    id = "audio.silence"
    params_schema = check_params(["threshold_dbfs", "max_run_frames"], threshold_dbfs=_threshold,
                                 max_run_frames={"type": "integer", "minimum": 0},
                                 warn_run_frames={"type": "integer", "minimum": 0},
                                 audio={"type": "object"}, allowed_silence=_ALLOWED)

    @staticmethod
    def _limits(ctx) -> tuple[int, int | None]:
        """-> (max_run_frames, warn_run_frames or None when single tier)."""
        mx, w = ctx.params["max_run_frames"], ctx.params.get("warn_run_frames")
        return mx, (w if w is not None and w < mx else None)

    def detect(self, ctx, records):
        thr, tl = ctx.params["threshold_dbfs"], ctx.timeline
        mx, w = self._limits(ctx)
        lo = mx if w is None else w
        allowed = allowed_silence(ctx)
        levels = self.levels(ctx, records)
        cands = []
        for idx, chans, p in _groups(ctx, levels, lambda r: r not in DROPPED):
            if not chans:
                continue
            extra = _prog_ev(p, chans)
            raw, silent = [], []
            for f in range(tl.program_start, tl.program_end + 1):
                if max(levels[s][f][c] for s, c, _ in chans) < thr:
                    raw.append(f)
                judged = [levels[s][f][c] for s, c, _ in chans if not _allowed_at(allowed.get((s, c)), f)]
                if judged and max(judged) < thr:
                    silent.append(f)
            for a, b in _runs(silent):
                cands.append(Candidate(self.id, idx, a, b, float(b - a + 1),
                                       [{"frames": b - a + 1, "threshold_dbfs": thr, **extra}]))
            kept = set(silent)
            for a, b in _runs(raw):
                excused = [f for f in range(a, b + 1) if f not in kept]
                if b - a + 1 > lo and excused:
                    cands.append(Candidate(self.id, idx, a, b, float(len(excused)),
                                           [{"kind": DECLARED, "frames": b - a + 1,
                                             "declared_frames": len(excused),
                                             "threshold_dbfs": thr, **extra}]))
        return cands

    def evaluate(self, ctx, candidates):
        mx, w = self._limits(ctx)
        out = []
        for c in candidates:
            n = c.frame_end - c.frame_start + 1
            ev = c.evidence[0]
            if ev.get("kind") == DECLARED:
                out.append(ctx.finding(
                    f"{_who(c.stream, ev)}: {n} silent frames, {ev['declared_frames']} of them on "
                    f"channels declared silent (allowed_silence) - not counted", Severity.INFO,
                    stream=c.stream, frame_start=c.frame_start, frame_end=c.frame_end,
                    actual=n, candidate=c, evidence=[ev]))
            elif n > mx or (w is not None and n > w):
                tier = "limit" if n > mx else "warn"
                lim = f"max {mx}" if w is None else (
                    f"over the {mx} frame limit (warn from {w})" if tier == "limit"
                    else f"warn tier: over {w}, up to {mx} frames, please listen")
                out.append(ctx.finding(
                    f"{_who(c.stream, ev)}: silence {n} frames "
                    f"(all {'measured ' if ev.get('program') else ''}channels < "
                    f"{ctx.params['threshold_dbfs']} dBFS; {lim})",
                    None if tier == "limit" else min(Severity.WARN, ctx.severity),
                    stream=c.stream, frame_start=c.frame_start, frame_end=c.frame_end,
                    expected=f"<= {w if tier == 'warn' else mx} frames", actual=n, candidate=c,
                    **({} if w is None else {"evidence": [{**ev, "tier": tier}]})))
        return out


@register
class SilentChannel(_PerFrameLevels):
    """A channel whose sample peak stays below threshold_dbfs for the whole program. With
    declared programs every channel of a program is judged by its role (L R C Ls Rs ..; '-' never,
    LFE only with include_lfe: true) and named in the message; partial silence is never
    reported here (audio.silence judges the program as a whole). Streams in no program are
    judged per channel as before. Frames inside a declared allowed_silence range of that channel
    are not looked at; a channel declared silent for the whole program that is silent gives
    INFO (kind declared_silence)."""
    id = "audio.silent_channel"
    params_schema = check_params(["threshold_dbfs"], threshold_dbfs=_threshold,
                                 audio={"type": "object"}, allowed_silence=_ALLOWED,
                                 include_lfe={"type": "boolean"})

    def detect(self, ctx, records):
        thr, tl = ctx.params["threshold_dbfs"], ctx.timeline
        lfe = ctx.params.get("include_lfe", False)
        allowed = allowed_silence(ctx)
        levels = self.levels(ctx, records)
        cands, seen, declared = [], set(), {}
        for _, chans, p in _groups(ctx, levels, lambda r: r != "-" and (lfe or r != "LFE")):
            for s, c, role in chans:
                if (s, c) in seen:                   # a stream in two programs: judged once
                    continue
                seen.add((s, c))
                per, rng = levels[s], allowed.get((s, c))
                who = {} if p is None else {"role": role, "program": p["name"]}
                judged = [v[c] for f, v in per.items() if not _allowed_at(rng, f)]
                if not judged:                       # declared silent over the whole program
                    if max(v[c] for v in per.values()) < thr:
                        d = declared.setdefault(s, {"kind": DECLARED, "channels": []})
                        d["channels"].append(c + 1)
                        if p is not None:
                            d.setdefault("roles", []).append(role)
                            d["program"] = p["name"]
                    continue
                peak = max(judged)
                if peak < thr:
                    cands.append(Candidate(self.id, s, tl.program_start, tl.program_end, 1.0,
                                           [{"channel": c + 1, "peak_dbfs": _num(peak), **who}]))
        for s, ev in declared.items():
            cands.append(Candidate(self.id, s, tl.program_start, tl.program_end, 0.0, [ev]))
        return cands

    def evaluate(self, ctx, candidates):
        out = []
        for c in candidates:
            ev = c.evidence[0]
            if ev.get("kind") == DECLARED:
                roles = f" ({ev['program']}: {' '.join(ev['roles'])})" if ev.get("roles") else ""
                out.append(ctx.finding(
                    f"stream {c.stream} channel(s) {','.join(map(str, ev['channels']))}{roles}: "
                    "silent for the whole program as declared (allowed_silence)", Severity.INFO,
                    stream=c.stream, frame_start=c.frame_start, frame_end=c.frame_end,
                    candidate=c, evidence=[ev]))
                continue
            peak = "-inf" if ev["peak_dbfs"] is None else f"{ev['peak_dbfs']:.1f}"
            who = (f"program {ev['program']} channel {ev['role']} (stream {c.stream})"
                   if ev.get("role") else f"stream {c.stream} channel {ev['channel']}")
            out.append(ctx.finding(
                f"{who}: silent for the whole program "
                f"(peak {peak} dBFS < {ctx.params['threshold_dbfs']} dBFS)",
                stream=c.stream, frame_start=c.frame_start, frame_end=c.frame_end,
                actual=ev["peak_dbfs"], candidate=c))
        return out


@register
class Clipping(_PerFrameLevels):
    """Runs of frames where any channel's sample peak reaches clip_dbfs (at/near full scale).
    Judged per stream; with declared programs the message also names program and channel role."""
    id = "audio.clipping"
    params_schema = check_params(["clip_dbfs"], clip_dbfs=_threshold, audio={"type": "object"})

    def _roles(self, ctx) -> dict[tuple[int, int], list[str]]:
        """{(stream, channel 0-based): ['5.1 C', ...]}; programs that do not resolve are only
        logged (clipping is still judged per stream)."""
        try:
            progs = declared_programs(ctx)
            progs = resolve_programs(ctx, progs) if progs else []
        except (Skip, ConfigError) as e:
            LOG.warning("clipping: programs not used for naming: %s", e)
            return {}
        out = {}
        for p in progs:
            for s, c, r in p["roles"]:
                out.setdefault((s, c), []).append(f"{p['name']} {r}")
        return out

    def detect(self, ctx, records):
        thr = ctx.params["clip_dbfs"]
        roles = self._roles(ctx)
        cands = []
        for idx, per in self.levels(ctx, records).items():
            hot = [f for f, peaks in sorted(per.items()) if max(peaks) >= thr]
            for a, b in _runs(hot):
                chans = sorted({c + 1 for f in range(a, b + 1) for c, p in enumerate(per[f]) if p >= thr})
                peak = max(max(per[f]) for f in range(a, b + 1))
                ev = {"channels": chans, "peak_dbfs": peak}
                named = [r for c in chans for r in roles.get((idx, c - 1), [])]
                if named:
                    ev["roles"] = named
                cands.append(Candidate(self.id, idx, a, b, peak, [ev]))
        return cands

    def evaluate(self, ctx, candidates):
        out = []
        for c in candidates:
            ev = c.evidence[0]
            prog = f" (program {', '.join(ev['roles'])})" if ev.get("roles") else ""
            out.append(ctx.finding(f"stream {c.stream}{prog}: sample peak {ev['peak_dbfs']:.2f} dBFS "
                                   f">= {ctx.params['clip_dbfs']} dBFS on channel(s) "
                                   f"{','.join(map(str, ev['channels']))}",
                                   stream=c.stream, frame_start=c.frame_start, frame_end=c.frame_end,
                                   actual=ev["peak_dbfs"], candidate=c))
        return out


GATING = "BS.1770 relative-gated (-70 LUFS absolute, -10 LU relative); not dialogue-gated"
UNDECLARED = "undeclared_programs"   # evidence kind: several streams, no programs, one measured


def _tap_name(i: int) -> str:
    return "r128" if i == 0 else f"r128_{i}"


def program_chain(n_inputs: int, start: int, end: int, channels: list[str] | None, tag: str) -> str:
    """ebur128 chain for one loudness program. Input 0 is the tap's main stream, inputs 1..n-1
    the tap's extra streams ([in1]..). Each input is cut to the program range (atrim), merged in
    order (amerge), then `pan` keeps the measured channels with their ffmpeg names (LFE dropped).
    channels None = the stream's own layout, no pan (single stream, program_track)."""
    trim = f"atrim=start_sample={start}:end_sample={end}"
    if n_inputs == 1:
        head = trim
    else:
        segs = [f"{trim}[{tag}0]"] + [f"[in{k}]{trim}[{tag}{k}]" for k in range(1, n_inputs)]
        head = ";".join(segs) + ";" + "".join(f"[{tag}{k}]" for k in range(n_inputs)) + \
            f"amerge=inputs={n_inputs}"
    if channels is not None:
        keep = sorted(((FF_CHANNEL[c], i) for i, c in enumerate(channels) if c not in DROPPED),
                      key=lambda x: FF_ORDER.index(x[0]))
        head += f",pan={'+'.join(n for n, _ in keep)}|{'|'.join(f'{n}=c{i}' for n, i in keep)}"
    return f"{head},ebur128=metadata=1:peak=true"


@register
class Loudness(Check):
    """Integrated loudness / LRA / true peak of the spec's program(s) over the program range.
    The spec names the standard (`loudness.standard`) and what to measure: `program_track`
    (position among audio streams, measured with the stream's own layout; default 0), or the
    declared programs: spec `audio.programs` (param `audio`; older spelling
    `audio.loudness.programs`) `[{name, streams: [positions], channels: [L R C LFE Ls Rs M -]}]` -
    the listed streams are merged in order and `channels` names every merged channel (default
    for 2 channels L R, for 6 L R C LFE Ls Rs); LFE and "-" are not measured; one finding set
    per program. Without programs, without an explicit `program_track` (that declares the
    program) and with more than one audio stream, one extra finding
    (kind undeclared_programs, severity `undeclared_programs_severity`, default warn) says that
    only one stream was measured. Target, tolerance and true-peak ceiling come from the
    profile's `standards` presets. Optional `lra_max_lu`."""
    id = "audio.loudness"
    requires_filters = ("atrim", "ebur128", "amerge", "pan")
    _preset = {"type": "object", "required": ["target_lufs", "tolerance_lu", "true_peak_max_dbtp"],
               "properties": {"target_lufs": {"type": "number"},
                              "tolerance_lu": {"type": "number", "minimum": 0},
                              "true_peak_max_dbtp": {"type": "number"}}}
    params_schema = check_params(["loudness", "standards"],
                                 loudness={"type": "object", "required": ["standard"],
                                           "properties": {
                                               "program_track": {"type": "integer", "minimum": 0},
                                               "programs": _PROGRAMS}},
                                 standards={"type": "object", "additionalProperties": _preset},
                                 lra_max_lu={"type": "number", "minimum": 0},
                                 audio={"type": "object"},
                                 undeclared_programs_severity={"enum": ["info", "warn"]})

    def _programs(self, ctx) -> list[dict]:
        """-> [{name, streams: [stream dicts], channels: [names] | None, measured: [names]}]."""
        ld, audio = ctx.params["loudness"], ctx.media.audio
        progs = declared_programs(ctx)
        if progs is not None and "program_track" in ld:
            raise ConfigError("loudness: give either program_track or programs, not both")
        if progs is None:
            t = ld.get("program_track", 0)
            if t >= len(audio):
                raise Skip(f"program_track {t} not in file ({len(audio)} audio streams)")
            return [{"name": None, "streams": [audio[t]], "channels": None, "measured": None,
                     "track": t, "explicit": "program_track" in ld}]
        return resolve_programs(ctx, progs)

    def taps(self, ctx):
        taps = []
        for i, p in enumerate(self._programs(ctx)):
            s0 = p["streams"][0]
            start, end, _ = _program(ctx, s0)
            chain = program_chain(len(p["streams"]), start, end, p["channels"], f"ld{i}_")
            taps.append(Tap(name=_tap_name(i), stream=s0["index"], chain=chain,
                            inputs=tuple(s["index"] for s in p["streams"][1:])))
            LOG.info("loudness %s: streams %s channels %s measured %s", p["name"] or "program_track",
                     [s["index"] for s in p["streams"]], p["channels"], p["measured"])
        return taps

    def detect(self, ctx, records):
        std = ctx.params["loudness"]["standard"]
        preset = ctx.params["standards"].get(std)
        if preset is None:
            raise ConfigError(f"standard {std!r} has no preset in the profile "
                             f"(known: {', '.join(ctx.params['standards'])})")
        cands = []
        progs = self._programs(ctx)
        for i, p in enumerate(progs):
            recs = records.get(_tap_name(i), [])
            if not recs:
                raise RuntimeError("ebur128 produced no measurement")
            s = p["streams"][0]
            start, end, sr = _program(ctx, s)
            last = recs[-1].values
            lin = float(last["lavfi.r128.true_peak"])
            m = {"standard": std, **preset, "gating": GATING, "stream": s["index"],
                 "integrated_lufs": float(last["lavfi.r128.I"]), "lra_lu": float(last["lavfi.r128.LRA"]),
                 "true_peak_dbtp": _num(20 * math.log10(lin)) if lin > 0 else None, "tp_block": None}
            if p["name"] is not None:
                m.update(program=p["name"], streams=[x["index"] for x in p["streams"]],
                         channels=p["channels"], measured_channels=p["measured"])
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
            cands.append(Candidate(self.id, s["index"], tl.program_start, tl.program_end,
                                   m["integrated_lufs"], [m]))
        n = len(ctx.media.audio)
        if progs[0]["name"] is None and not progs[0]["explicit"] and n > 1:   # several streams, nothing declared
            s = progs[0]["streams"][0]
            cands.append(Candidate(self.id, s["index"], ctx.timeline.program_start,
                                   ctx.timeline.program_end, 0.0,
                                   [{"kind": UNDECLARED, "stream": s["index"], "audio_streams": n,
                                     "program_track": progs[0]["track"]}]))
        return cands

    def evaluate(self, ctx, candidates):
        out = []
        for c in candidates:
            ev = c.evidence[0]
            if ev.get("kind") == UNDECLARED:
                out.append(ctx.finding(
                    f"{ev['audio_streams']} audio streams but no programs declared: loudness "
                    f"measured only audio stream {ev['program_track']} (file stream "
                    f"{ev['stream']}, its own layout) as the program; declare audio.programs in the spec (e.g. "
                    "5.1 on streams 0-5 with channels L R C LFE Ls Rs, stereo on 6-7) to measure "
                    "each program", Severity.parse(ctx.params.get("undeclared_programs_severity", "warn")),
                    stream=c.stream, frame_start=c.frame_start, frame_end=c.frame_end,
                    candidate=c, evidence=[ev]))
            else:
                out += self._evaluate_one(ctx, c)
        return out

    def _evaluate_one(self, ctx, c):
        m = c.evidence[0]
        tp = "-inf" if m["true_peak_dbtp"] is None else f"{m['true_peak_dbtp']:.1f}"
        prog = (f"{m['program']} (streams {','.join(map(str, m['streams']))}, measured "
                f"{' '.join(m['measured_channels'] or ['as stream layout'])}): "
                if m.get("program") else "")
        summary = (f"{prog}{m['standard']}: I {m['integrated_lufs']:.1f} LUFS, LRA {m['lra_lu']:.1f} LU, "
                   f"true peak {tp} dBTP [{GATING}]")
        loc = dict(stream=c.stream, frame_start=c.frame_start, frame_end=c.frame_end, candidate=c,
                   evidence=[m])
        out = []
        if abs(m["integrated_lufs"] - m["target_lufs"]) > m["tolerance_lu"]:
            out.append(ctx.finding(f"integrated loudness outside {m['target_lufs']} +-{m['tolerance_lu']} LU; "
                                   + summary, expected=f"{m['target_lufs']} +-{m['tolerance_lu']} LUFS",
                                   actual=round(m["integrated_lufs"], 1),
                                   **{**loc, "evidence": [{**m, "kind": "integrated"}]}))
        if m["tp_block"]:
            out.append(ctx.finding(f"true peak above {m['true_peak_max_dbtp']} dBTP (first in this 100 ms "
                                   f"block); " + summary, expected=f"<= {m['true_peak_max_dbtp']} dBTP",
                                   actual=m["true_peak_dbtp"],
                                   **{**loc, "frame_start": m["tp_block"][0], "frame_end": m["tp_block"][1],
                                      "evidence": [{**m, "kind": "true_peak"}]}))
        lra_max = ctx.params.get("lra_max_lu")
        if lra_max is not None and m["lra_lu"] > lra_max:
            out.append(ctx.finding(f"loudness range above {lra_max} LU; " + summary,
                                   expected=f"<= {lra_max} LU", actual=round(m["lra_lu"], 1),
                                   **{**loc, "evidence": [{**m, "kind": "lra"}]}))
        return out or [ctx.finding(summary, Severity.PASS, actual=round(m["integrated_lufs"], 1), **loc)]
