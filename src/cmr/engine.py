"""Engine: check registry, timeline, single decode pass, detect -> verify -> evaluate.

Check interface (see docs/check-interface.md for the contract other modules build on):

    @register
    class MyCheck(Check):
        id = "video.something"
        needs = frozenset({"probe"})          # add "decode" to force the single pass
        requires_filters = ("signalstats",)   # verified against `ffmpeg -filters` per run
        params_schema = {...}                 # optional jsonschema for profile params

        def taps(self, ctx) -> list[Tap]          # (a) filter chains for the single pass
        def detect(self, ctx, records) -> list[Candidate]   # (b) parse per-frame output (stage 1)
        def evaluate(self, ctx, candidates) -> list[Finding] # (c) verdict

The engine merges every tap of every enabled check into ONE ffmpeg process
(`-filter_complex`, split/asplit per input stream). Each tap chain gets
`,metadata=mode=print:file=...` (audio: ametadata) appended; per-frame records are
parsed and handed to detect() keyed by tap name.

Verified on ffmpeg 6.1.1 (dev machine):
- a filtergraph whose branches all end in nullsink is rejected ("A filtergraph has
  zero outputs, this is not supported"), so every branch gets an output label and is
  mapped to `-f null -` (wrapped_avframe, no encode cost).
- `metadata=mode=print:file=F` writes "frame:N    pts:P    pts_time:T" then key=value
  lines, only for frames that carry metadata; signalstats values are in native
  bit depth (10-bit: 0..1023).
- `metadata=mode=add:key=K:value=V` tags every frame (used as the frame counter).
- `metadata`/`ametadata` `key=K` prints only K, and still prints the "frame/pts/pts_time"
  line (one print filter per key, so Tap.keys costs one tiny filter per key).
- pts_time is printed with only 6 significant digits (3600.0417 s -> "3600.04"), so
  frames are computed from the exact integer `pts` x time base. Input timestamps
  are offset to 0 by ffmpeg (a MOV with start_time 3600 prints pts 0 first).
- decode errors appear on stderr at `-v error` (e.g. "[prores @ ..] invalid plane
  data size") while the exit code stays 0; the stderr lines carry no frame number.
"""
from __future__ import annotations

import hashlib
import importlib
import math
import os
import pkgutil
import re
import shutil
import subprocess
import tempfile
import traceback
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Callable

import jsonschema

from .config import ConfigError   # raise from a check for unusable params -> "configuration error"
from .media import MediaInfo, ProbeError, probe
from .model import Candidate, Finding, Severity
from .timecode import TimecodeError, frames_to_tc, parse_fps, tc_is_drop, tc_to_frames


# --- registry ---------------------------------------------------------------------
class Skip(Exception):
    """Raise from a check to report SKIPPED with the given reason."""


@dataclass
class Tap:
    name: str            # unique within the check
    stream: int          # absolute ffprobe stream index
    chain: str           # ffmpeg filter chain, e.g. "crop=iw:1:0:0:exact=1,signalstats"
    keys: tuple[str, ...] = ()   # print only these metadata keys; () = all keys


@dataclass
class MetaRecord:
    frame: int           # video: round(t*fps); audio: frame containing the chunk start
    pts_time: float      # t = exact pts * time base (not the 6-digit printed pts_time)
    values: dict[str, str]


@dataclass
class PassInfo:
    ran: bool
    returncode: int | None = None
    stderr: list[str] = field(default_factory=list)
    decoded_frames: int | None = None    # frames seen on the first video stream
    command: list[str] = field(default_factory=list)


class Check:
    id: str = ""
    needs: frozenset = frozenset({"probe"})
    requires_filters: tuple[str, ...] = ()
    params_schema: dict | None = None
    qualities: tuple[str, ...] = ("high",)   # profile `quality` values this check implements

    def taps(self, ctx: "CheckContext") -> list[Tap]:
        return []

    def detect(self, ctx: "CheckContext", records: dict[str, list[MetaRecord]]) -> list[Candidate]:
        return []

    def evaluate(self, ctx: "CheckContext", candidates: list[Candidate]) -> list[Finding]:
        raise NotImplementedError


REGISTRY: dict[str, Check] = {}
IMPORT_ERRORS: dict[str, str] = {}
# check_id -> fn(candidate, ctx) -> {"decision", "p", "model_hash", "severity"}.
# Empty in v1 (ML = roadmap). The engine only ever LOWERS severity from a verifier.
VERIFIERS: dict[str, Callable] = {}


def register(cls):
    if not cls.id:
        raise ValueError(f"{cls.__name__} has no id")
    if cls.id in REGISTRY:
        raise ValueError(f"duplicate check id {cls.id}")
    REGISTRY[cls.id] = cls()
    return cls


_loaded = False


def load_checks() -> None:
    """Import every module/package in cmr.checks: adding a check = one new file."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    from . import checks
    for m in pkgutil.iter_modules(checks.__path__):
        name = f"{checks.__name__}.{m.name}"
        try:
            importlib.import_module(name)
        except Exception as e:  # a broken plugin must not take the tool down
            IMPORT_ERRORS[name] = f"{type(e).__name__}: {e}"


# --- timeline -----------------------------------------------------------------------
@dataclass
class Timeline:
    fps: Fraction
    drop: bool
    file_frames: int | None
    start_frame: int                 # timecode of file frame 0, as a frame count
    tc_source: str                   # 'file' | 'spec' | 'none'
    head_frames: int
    tail_frames: int
    program_start: int | None        # file frame index of first program frame
    program_end: int | None          # inclusive
    problems: list[str] = field(default_factory=list)

    def tc(self, frame: int) -> str:
        return frames_to_tc(self.start_frame + frame, self.fps, self.drop)

    def to_dict(self) -> dict:
        return {"fps": f"{self.fps.numerator}/{self.fps.denominator}", "drop_frame": self.drop,
                "file_frames": self.file_frames, "start_timecode": self.tc(0),
                "timecode_source": self.tc_source, "head_frames": self.head_frames,
                "tail_frames": self.tail_frames, "program_start": self.program_start,
                "program_end": self.program_end, "problems": self.problems}


def build_timeline(media: MediaInfo, spec: dict) -> Timeline:
    timing = spec.get("timing") or {}
    v = media.video[0] if media.video else {}
    problems = []
    fps = v.get("fps")
    if fps is None:
        fps = parse_fps((spec.get("video") or {}).get("fps", 25))
        problems.append(f"no video fps in file; using {fps} from spec")
    spec_drop = timing.get("timecode_mode") == "drop"
    drop = tc_is_drop(media.timecode) if media.timecode else spec_drop
    try:
        frames_to_tc(0, fps, drop)
    except TimecodeError as e:
        problems.append(str(e) + "; using non-drop")
        drop = False

    file_frames = v.get("nb_frames")
    if file_frames is None and v.get("duration"):
        file_frames = round(Fraction(v["duration"]) * fps)
        problems.append("nb_frames missing; frame count derived from duration")

    head = sum(s.get("frames", 0) for s in timing.get("head_layout") or [] if s["type"] != "program")
    tail = sum(s.get("frames", 0) for s in timing.get("tail_layout") or [])

    start, source = 0, "none"
    try:
        if media.timecode:
            start, source = tc_to_frames(media.timecode, fps, drop), "file"
        elif timing.get("file_start_timecode"):
            start, source = tc_to_frames(timing["file_start_timecode"], fps, drop), "spec"
        elif timing.get("program_start_timecode"):
            start, source = tc_to_frames(timing["program_start_timecode"], fps, drop) - head, "spec"
    except TimecodeError as e:
        problems.append(f"timecode: {e}")

    p_start = p_end = None
    if file_frames is not None:
        if head + tail >= file_frames:
            problems.append(f"head ({head}) + tail ({tail}) frames fall outside the file ({file_frames} frames)")
        else:
            p_start, p_end = head, file_frames - tail - 1
    return Timeline(fps, drop, file_frames, start, source, head, tail, p_start, p_end, problems)


# --- context ------------------------------------------------------------------------
@dataclass
class CheckContext:
    check_id: str
    media: MediaInfo
    spec: dict
    params: dict
    severity: Severity           # profile severity for a problem found by this check
    timeline: Timeline
    passinfo: PassInfo | None = None
    quality: str = "high"        # profile `quality` (fast|high), validated per check

    def finding(self, message: str, severity=None, **kw) -> Finding:
        return Finding(check_id=self.check_id,
                       severity=self.severity if severity is None else severity,
                       message=message, **kw)


# --- single pass --------------------------------------------------------------------
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./-]+$")
_FILTER_LINE = re.compile(r"^ [T.][S.][C.] (\S+)\s+\S+->\S+")
_COUNTER = "metadata=mode=add:key=cmr.frame:value=1"


def available_filters(ffmpeg_bin: str = "ffmpeg") -> set[str]:
    p = subprocess.run([ffmpeg_bin, "-hide_banner", "-filters"], capture_output=True, text=True)
    return {m.group(1) for line in p.stdout.splitlines() if (m := _FILTER_LINE.match(line))}


def parse_metadata_file(text: str) -> list[tuple[str | None, str, dict]]:
    """-> [(pts integer string or None, pts_time string, {key: value})] in file order."""
    out, cur = [], None
    for line in text.splitlines():
        if line.startswith("frame:"):
            m = re.search(r"pts_time:(\S+)", line)
            pts = re.search(r"\bpts:(-?\d+)\b", line)
            cur = {}
            out.append((pts.group(1) if pts else None, m.group(1) if m else "nan", cur))
        elif cur is not None and "=" in line:
            k, _, val = line.partition("=")
            cur[k] = val
    return out


def build_graph(taps: list[tuple[str, Tap, str]], kinds: dict[int, str], tmp: Path):
    """taps: (key, Tap, kind). Returns (graph string, output labels, {key: [metadata files]}).
    A tap with `keys` gets one `metadata=mode=print:key=K` per key (own file each)."""
    by_stream: dict[int, list[tuple[str, Tap]]] = {}
    for key, tap, _ in taps:
        by_stream.setdefault(tap.stream, []).append((key, tap))
    parts, labels, files = [], [], {}
    n = 0
    for idx, items in by_stream.items():
        audio = kinds[idx] == "audio"
        meta = "ametadata" if audio else "metadata"
        if len(items) == 1:
            ins = [f"[0:{idx}]"]
        else:
            ins = [f"[s{idx}_{i}]" for i in range(len(items))]
            parts.append(f"[0:{idx}]{'asplit' if audio else 'split'}={len(items)}{''.join(ins)}")
        for (key, tap), src in zip(items, ins):
            fs = [tmp / f"tap{n}_{i}.txt" for i in range(max(1, len(tap.keys)))]
            for f in fs:
                if not _SAFE_PATH.match(str(f)):
                    raise RuntimeError(f"temp path {f} has characters unsafe for a filtergraph")
            files[key] = fs
            prints = ([f"{meta}=mode=print:key={k}:file={f}" for k, f in zip(tap.keys, fs)]
                      if tap.keys else [f"{meta}=mode=print:file={fs[0]}"])
            parts.append(f"{src}{tap.chain},{','.join(prints)}[o{n}]")
            labels.append(f"[o{n}]")
            n += 1
    return ";".join(parts), labels, files


def default_filter_threads() -> int:
    """-filter_complex_threads default: half the CPUs, max 8. Measured (16-CPU box,
    1080p ProRes, blanking+black taps): 1 thread and 16 (= ffmpeg default, all CPUs)
    were slowest; 4-8 fastest (see docs/check-interface.md). Override: run_file(filter_threads=N),
    CLI --filter-threads N."""
    return max(1, min(8, (os.cpu_count() or 2) // 2))


def _merge_prints(parsed: list[list[tuple]]) -> list[tuple]:
    """Merge several key-filtered prints of one tap by pts (file order kept)."""
    if len(parsed) == 1:
        return parsed[0]
    merged: dict = {}
    for recs in parsed:
        for pts, t, vals in recs:
            merged.setdefault((pts, t), (pts, t, {}))[2].update(vals)
    return list(merged.values())


def run_pass(media: MediaInfo, taps: list[tuple[str, Tap, str]], ffmpeg_bin: str, tmp: Path,
             filter_threads: int | None = None):
    kinds = {s["index"]: "video" for s in media.video} | {s["index"]: "audio" for s in media.audio}
    for _, tap, _ in taps:
        if tap.stream not in kinds:
            raise RuntimeError(f"tap {tap.name}: stream {tap.stream} is not a video/audio stream")
    graph, labels, files = build_graph(taps, kinds, tmp)
    cmd = [ffmpeg_bin, "-hide_banner", "-nostdin", "-nostats", "-v", "error",
           "-filter_complex_threads", str(filter_threads or default_filter_threads()),
           "-i", str(media.path), "-filter_complex", graph]
    for lab in labels:
        cmd += ["-map", lab]
    cmd += ["-f", "null", "-"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    records = {}
    for key, fs in files.items():
        records[key] = _merge_prints([parse_metadata_file(f.read_text()) if f.exists() else []
                                      for f in fs])
    return PassInfo(True, p.returncode, [l for l in p.stderr.splitlines() if l.strip()],
                    command=cmd), records


def _printed_tol(t: str) -> Fraction:
    """Max error of a pts_time printed with 6 significant digits (%.6g), plus margin."""
    v = abs(float(t))
    return Fraction(1, 10**6) + (Fraction(10) ** (math.floor(math.log10(v)) - 5) if v else 0)


def pick_time_base(raw: list[tuple], candidates: list[Fraction]) -> Fraction | None:
    """First candidate time base consistent with every printed (pts, pts_time).
    None if nothing fits (inferring a time base from the 6-digit pts_time is not
    exact enough at long timestamps, measured, so a mismatch is an ERROR)."""
    pairs = [(int(p), t) for p, t, _ in raw if p is not None and t not in ("nan", "NOPTS")]
    if not pairs:
        return None
    for tb in candidates:
        if all(abs(p * tb - Fraction(t)) <= _printed_tol(t) for p, t in pairs):
            return tb
    return None


def _to_records(raw: list[tuple], fps: Fraction, audio: bool,
                time_bases: list[Fraction] = ()) -> list[MetaRecord]:
    """Exact time t = pts * time base (falls back to the printed pts_time only when no
    pts is printed); explicit rounding t * fps -> integer frame."""
    tb = pick_time_base(raw, list(time_bases))
    if tb is None and any(p is not None for p, _, _ in raw):
        raise RuntimeError("cannot determine the filter time base from the metadata print")
    out = []
    for pts, t, vals in raw:
        # Fraction('nan'/'NOPTS') raises -> check ERROR, not silent
        tt = int(pts) * tb if pts is not None else Fraction(t)
        x = tt * fps
        frame = math.floor(x + Fraction(1, 10**6)) if audio else round(x)
        out.append(MetaRecord(frame, float(tt), vals))
    return out


# --- per-file run -------------------------------------------------------------------
def file_identity(path: Path) -> dict:
    """size + sha256 of the first and last MiB (full hash of multi-GB renders is too slow)."""
    size = path.stat().st_size
    h = hashlib.sha256()
    with path.open("rb") as f:
        h.update(f.read(1 << 20))
        if size > 2 << 20:
            f.seek(-(1 << 20), 2)
            h.update(f.read())
    return {"size": size, "sha256_head_tail_1MiB": h.hexdigest()}


def _check_order(profile: dict) -> list[tuple[str, dict]]:
    return sorted(((cid, cfg or {}) for cid, cfg in profile.get("checks", {}).items()
                   if (cfg or {}).get("enabled", True)), key=lambda x: x[0])


def run_file(path: Path, spec: dict, profile: dict, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe",
             filters: set[str] | None = None, filter_threads: int | None = None) -> dict:
    load_checks()
    path = Path(path)
    result = {"path": str(path), "identity": None, "timeline": None, "findings": []}
    findings: list[Finding] = result["findings"]
    try:
        result["identity"] = file_identity(path)
        media = probe(path, ffprobe_bin)
        if not media.video:
            raise ProbeError("no video stream")
        timeline = build_timeline(media, spec)
    except (OSError, ProbeError) as e:
        findings.append(Finding("probe", Severity.ERROR, str(e)))
        result["worst"] = "ERROR"
        return result
    result["timeline"] = timeline.to_dict()
    if filters is None:
        filters = available_filters(ffmpeg_bin)

    contexts: list[tuple[Check, CheckContext]] = []
    for cid, cfg in _check_order(profile):
        check = REGISTRY.get(cid)
        if check is None:
            hint = f" (plugin import errors: {IMPORT_ERRORS})" if IMPORT_ERRORS else ""
            findings.append(Finding(cid, Severity.ERROR, f"unknown check id{hint}"))
            continue
        params = {k: v for k, v in cfg.items() if k not in ("enabled", "severity", "quality")}
        quality = cfg.get("quality", "high")
        try:
            sev = Severity.parse(cfg.get("severity", "fail"))
            if quality not in check.qualities:   # value itself checked by PROFILE_SCHEMA
                raise ValueError(f"quality {quality!r} not implemented "
                                 f"(supports: {', '.join(check.qualities)})")
            if check.params_schema:
                jsonschema.validate(params, check.params_schema)
        except (ValueError, jsonschema.ValidationError) as e:
            findings.append(Finding(cid, Severity.ERROR, f"bad profile params: {getattr(e, 'message', e)}"))
            continue
        missing = [f for f in check.requires_filters if f not in filters]
        if missing:
            findings.append(Finding(cid, Severity.ERROR, f"ffmpeg lacks filter(s): {', '.join(missing)}"))
            continue
        contexts.append((check, CheckContext(cid, media, spec, params, sev, timeline,
                                             quality=quality)))

    # collect taps, run the single pass if anything needs it
    all_taps, tap_owner, tap_errors = [], {}, {}
    for check, ctx in contexts:
        try:
            for tap in check.taps(ctx):
                key = f"{ctx.check_id}/{tap.name}"
                all_taps.append((key, tap, ctx.check_id))
                tap_owner[key] = (ctx.check_id, tap)
        except Skip:
            pass   # reported again by evaluate()
        except Exception as e:
            tap_errors[ctx.check_id] = f"taps() failed: {type(e).__name__}: {e}"
    passinfo, raw_records = PassInfo(False), {}
    if all_taps or any("decode" in c.needs for c, _ in contexts):
        counter = ("__engine__/frames", Tap("frames", media.video[0]["index"], _COUNTER), "__engine__")
        with tempfile.TemporaryDirectory(prefix="cmr_") as tmp:
            try:
                passinfo, raw_records = run_pass(media, all_taps + [counter], ffmpeg_bin, Path(tmp),
                                                 filter_threads)
            except (OSError, RuntimeError) as e:
                passinfo = PassInfo(False, None, [f"single pass failed to start: {e}"])
        passinfo.decoded_frames = len(raw_records.get("__engine__/frames", [])) if passinfo.ran else None

    audio_idx = {s["index"] for s in media.audio}
    # time base candidates per stream: container stream time_base, then 1/sample_rate
    tbs: dict[int, list[Fraction]] = {}
    for s in media.raw.get("streams", []):
        c = [Fraction(s["time_base"])] if s.get("time_base") else []
        if s.get("sample_rate"):
            c.append(Fraction(1, int(s["sample_rate"])))
        tbs[s["index"]] = c
    for check, ctx in contexts:
        ctx.passinfo = passinfo
        if ctx.check_id in tap_errors:
            findings.append(ctx.finding(tap_errors[ctx.check_id], Severity.ERROR))
            continue
        try:
            records = {}
            for key, (cid, tap) in tap_owner.items():
                if cid == ctx.check_id:
                    records[tap.name] = _to_records(raw_records.get(key, []), timeline.fps,
                                                    tap.stream in audio_idx, tbs.get(tap.stream, []))
            if records and not passinfo.ran:
                raise RuntimeError("; ".join(passinfo.stderr) or "single pass did not run")
            cands = check.detect(ctx, records)
            out = check.evaluate(ctx, cands)
            verify = VERIFIERS.get(ctx.check_id)
            for f in out:
                if verify and f.candidate is not None:
                    f.verifier = verify(f.candidate, ctx)
                    f.lower_to(f.verifier["severity"])
            if not out:
                out = [ctx.finding("ok", Severity.PASS)]
        except Skip as e:
            out = [ctx.finding(str(e) or "skipped", Severity.SKIPPED)]
        except ConfigError as e:
            out = [ctx.finding(f"configuration error: {e}", Severity.ERROR)]
        except Exception as e:
            tb = traceback.format_exception_only(type(e), e)[-1].strip()
            out = [ctx.finding(f"check crashed: {tb}", Severity.ERROR)]
        for f in out:
            f.quality = ctx.quality
        findings.extend(out)

    for f in findings:
        if f.frame_start is not None:
            f.timecode_start = timeline.tc(f.frame_start)
        if f.frame_end is not None:
            f.timecode_end = timeline.tc(f.frame_end)
    findings.sort(key=lambda f: (f.check_id, -1 if f.frame_start is None else f.frame_start,
                                 f.stream if f.stream is not None else -1))
    result["worst"] = max((f.severity for f in findings), default=Severity.PASS).name
    return result


def ffmpeg_version(ffmpeg_bin: str = "ffmpeg") -> str:
    if not shutil.which(ffmpeg_bin):
        return "not found"
    p = subprocess.run([ffmpeg_bin, "-version"], capture_output=True, text=True)
    return p.stdout.splitlines()[0] if p.stdout else "unknown"
