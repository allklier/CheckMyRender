"""Waivers v1 (D17): reviewed findings that are not re-flagged unless the evidence changed.

A waiver = check id + stream (+ edge/kind/channel from the finding evidence) + frame range +
a PERCEPTUAL fingerprint of the evidence, computed after the main pass by seeking to a
bounded number of evenly spaced frames of the range (first and last included):
- video: dHash, 64 bits per frame (9x8 cell means of the luma plane, bit = left > right
  neighbour). Distance = max Hamming distance over the sampled frames.
  v2 (current): ffmpeg only decodes and extracts the luma plane in native bit depth
  (`extractplanes=y`; RGB sources: R, G, B planes stacked, BT.709 luma from the cell sums;
  no scale, no format conversion); the 9x8 box means and the hash are computed here
  (`luma_dhash`), so the hash does not depend on the ffmpeg build (measured identical on
  6.1.1 / 7.1.1 / 9.0.2). v1 (`scale=9:8:flags=area+accurate_rnd,format=gray`) depends on the
  swscale build (dim content differs between ffmpeg 6.1 and 7.1+); kept only to match
  waivers written with it (re-creating such a waiver upgrades it to v2).
- audio: per sampled frame and channel, sample peak and RMS in dBFS (0.1 dB, floor -90).
  Distance = max absolute dB difference. Why: the audio checks judge levels (clipping,
  silence, dead channel, true peak), so a level envelope is what must stay the same;
  it survives re-encodes and resampling-free re-renders, a remix or gain change shows.
  A change of timbre at the same level is not seen. PCM envelopes are identical across
  ffmpeg 6.1.1 / 7.1.1 / 9.0.2; AAC is not (seek + decoder pre-roll differ, up to 1.6 dB
  measured, inside the 2 dB tolerance; documented, not fixed).
Matching (same location only; moved content = roadmap): a finding of the same check, stream
and locator whose range lies inside the waiver range +- margin is fingerprinted at the
WAIVER's frames; distance <= tolerance -> WAIVED (severity lowered to PASS, raw_severity
kept, waiver_ref set); otherwise it stays flagged with a "content changed" note.
Category waivers (`type: category`, waiver file version 2): a blanket decision for a check
class, NO fingerprint: check id [+ evidence kind] [+ edge | channel] [+ frame range], waives
findings whose RAW severity is <= the required `max_severity` ceiling (info|warn|fail).
Applied after the per-finding waivers, only to findings those did not waive.
Job scope (D31): an entry with `job: <id>` applies only to runs of that job (job.yaml
`job.id`); `job: "*"` applies to every job; an entry without `job` applies everywhere (files
written before D31). Entries of another job are skipped and listed as `foreign`.
Contract and verified facts: src/cmr/CLAUDE.md.
"""
from __future__ import annotations

import datetime
import logging
import math
import sys
from array import array
from fractions import Fraction
from pathlib import Path
from typing import Callable

import yaml

from . import log
from .config import ConfigError
from .textnorm import nfc
from .media import probe
from .model import Finding, Severity

WAIVER_VERSION = 2          # 2 adds category waivers; files without them are still written as 1
READ_VERSIONS = (1, 2)
CEILINGS = ("info", "warn", "fail")    # category waiver max_severity
EDGES = ("top", "bottom", "left", "right")
DEFAULTS = {"video_max_bits": 10, "audio_max_db": 2.0, "margin_frames": 5}
VIDEO_SAMPLES, AUDIO_SAMPLES = 8, 16
FLOOR_DB = -90.0
ANY_JOB = "*"                # `job: "*"`: a deliberate cross-job waiver (D31)
LOCATOR_KEYS = ("edge", "kind", "channel")     # evidence keys that are part of the location
ALGO = {"video": ("video_dhash", "dhash64-luma9x8-box", 2),
        "audio": ("audio_envelope", "peak-rms-dbfs-0.1db", 1),
        "slate": ("slate_text", "field-exact-text", 1)}
VIDEO_V1 = ("video_dhash", "dhash64-gray9x8-area", 1)   # build-dependent, matching old waivers only
V1_NOTE = ("fingerprint v1 (depends on the ffmpeg build); re-create the waiver to upgrade it "
           "to v2")
TEXT_TYPE = ALGO["slate"][0]
_RGB_PREFIXES = ("rgb", "bgr", "gbr", "argb", "abgr", "0rgb", "0bgr", "x2rgb", "x2bgr")
LOG = log.get("waivers")


class FingerprintError(RuntimeError):
    pass


def media_kind(check_id: str) -> str | None:
    """video / audio (perceptual fingerprints) or slate (exact OCR text, D27), else None."""
    for kind in ("video", "audio", "slate"):
        if check_id.startswith(kind + "."):
            return kind
    return None


def waivable(fd: dict) -> bool:
    """Findings with a frame range from a video/audio content check, INFO..FAIL, not waived."""
    return (media_kind(fd["check_id"]) is not None and fd.get("frame_start") is not None
            and fd.get("stream") is not None and not fd.get("waiver_ref")
            and fd["severity"] in ("INFO", "WARN", "FAIL"))


def locator(evidence: list | None) -> dict:
    ev = (evidence or [{}])[0] if evidence else {}
    return {k: ev[k] for k in LOCATOR_KEYS if isinstance(ev, dict) and k in ev}


def sample_frames(a: int, b: int, n: int) -> list[int]:
    if b - a + 1 <= n:
        return list(range(a, b + 1))
    return sorted({a + round(i * (b - a) / (n - 1)) for i in range(n)})


# --- fingerprints ---------------------------------------------------------------------
def dhash(px: bytes, w: int = 9, h: int = 8) -> str:
    v = 0
    for r in range(h):
        for c in range(w - 1):
            v = (v << 1) | (px[r * w + c] > px[r * w + c + 1])
    return f"{v:016x}"


def cell_edges(n: int, cells: int) -> list[int]:
    """Cell boundaries 0 = e[0] < ... < e[cells] = n, e[i] = i * n // cells (integer floor);
    cell i covers samples e[i] .. e[i+1]-1. Needs n >= cells (every cell non-empty)."""
    return [i * n // cells for i in range(cells + 1)]


RGB_WEIGHTS = (2126, 7152, 722)    # BT.709 luma x 10000, for RGB sources (R, G, B planes)


def luma_cells(raw: bytes, w: int, h: int, depth: int, big_endian: bool = False,
               rgb: bool = False) -> list[int]:
    """9x8 box means (row-major, 72 values) of the luma, normalised to 8-bit codes, in integer
    arithmetic: mean8 = round_half_up(sum / (count * 2**(depth-8))), so an 8-bit and a 10-bit
    encode of the same picture give the same codes (the 8-bit step is the dead zone that keeps
    grain on flat areas from flipping bits, as in v1). `raw` = one plane (Y) or, with `rgb`,
    the R, G and B planes stacked (3*h rows); RGB luma = BT.709 weights applied to the cell
    SUMS (a box mean is linear, so no per-pixel work). Samples are 1 byte (depth 8) or 2 bytes
    (9..16 bit, little-endian unless `big_endian`). Cell edges: cell_edges()."""
    if w < 9 or h < 8 or not 8 <= depth <= 16:
        raise FingerprintError(f"plane {w}x{h} at {depth} bit not supported")
    bps, planes = 1 if depth == 8 else 2, 3 if rgb else 1
    if len(raw) != w * h * bps * planes:
        raise FingerprintError(f"got {len(raw)} bytes, expected {w * h * bps * planes} "
                               f"({w}x{h}, {depth} bit, {planes} plane(s))")
    if bps == 1:
        px = memoryview(raw)
    elif big_endian or sys.byteorder != "little":
        a = array("H")
        a.frombytes(raw)
        if big_endian != (sys.byteorder != "little"):
            a.byteswap()
        px = memoryview(a)
    else:
        px = memoryview(raw).cast("H")
    xs, ys = cell_edges(w, 9), cell_edges(h, 8)
    weights, scale = (RGB_WEIGHTS, 10000) if rgb else ((1,), 1)
    out = []
    for r in range(8):
        sums = [0] * 9
        for p, wt in enumerate(weights):
            for y in range(ys[r], ys[r + 1]):
                base = (p * h + y) * w
                for c in range(9):
                    sums[c] += wt * sum(px[base + xs[c]:base + xs[c + 1]])
        for c in range(9):
            q = scale * (xs[c + 1] - xs[c]) * (ys[r + 1] - ys[r]) << (depth - 8)
            out.append((2 * sums[c] + q) // (2 * q))
    return out


def luma_dhash(raw: bytes, w: int, h: int, depth: int, big_endian: bool = False,
               rgb: bool = False) -> str:
    return dhash(luma_cells(raw, w, h, depth, big_endian, rgb))


_DEPTHS: dict[str, dict[str, int]] = {}


def pix_fmt_depths(ffmpeg: str) -> dict[str, int]:
    """pix_fmt -> bit depth of its first component, from `ffmpeg -pix_fmts` (columns FLAGS NAME
    NB_COMPONENTS BITS_PER_PIXEL BIT_DEPTHS, e.g. `IO... yuv420p10le 3 15 10-10-10`)."""
    if ffmpeg not in _DEPTHS:
        out = _ffmpeg(ffmpeg, ["-pix_fmts"]).decode(errors="replace")
        d = {}
        for line in out.split("-----", 1)[-1].splitlines():
            p = line.split()
            if len(p) == 5 and p[2].isdigit() and p[4].split("-")[0].isdigit():
                d[p[1]] = int(p[4].split("-")[0])
        _DEPTHS[ffmpeg] = d
    return _DEPTHS[ffmpeg]


def plane_for(pix_fmt: str) -> str:
    """`y` (the luma plane) for Y'CbCr / gray formats; `rgb` for RGB formats (no Y plane: the
    R, G, B planes are extracted and stacked, luma = BT.709 weights on the cell sums). No
    scale or format conversion either way, so no build dependency."""
    return "rgb" if pix_fmt.startswith(_RGB_PREFIXES) else "y"


def _db(x: float) -> float:
    return FLOOR_DB if x <= 0 else max(FLOOR_DB, round(20 * math.log10(x), 1))


def _ffmpeg(ffmpeg: str, args: list[str]) -> bytes:
    p = log.run(LOG, [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", *args],
                "ffmpeg fingerprint seek", logging.DEBUG, capture_output=True, timeout=120)
    if p.returncode != 0:
        raise FingerprintError(p.stderr.decode(errors="replace").strip() or "ffmpeg failed")
    return p.stdout


class Fingerprinter:
    """Fingerprints one media file at given frames (one seek + short decode per frame)."""

    def __init__(self, path, fps: Fraction | str, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe"):
        self.path, self.fps = Path(path), Fraction(fps)
        self.ffmpeg, self.ffprobe, self._media = ffmpeg, ffprobe, None
        self._plane: dict[int, tuple | None] = {}
        self._version: str | None = None

    def _channels(self, stream: int) -> int:
        for s in self._probe().audio:
            if s["index"] == stream and s.get("channels"):
                return int(s["channels"])
        raise FingerprintError(f"stream {stream} is not an audio stream with channels")

    def _probe(self):
        if self._media is None:
            self._media = probe(self.path, self.ffprobe)
        return self._media

    def video_plane(self, stream: int) -> tuple[str, int, bool, int, int] | None:
        """(plane, depth, big-endian, width, height) for the v2 hash, or None when the stream's
        pixel format has no 8..16-bit integer plane to hash (float, bayer, unknown)."""
        if stream not in self._plane:
            s = next((v for v in self._probe().video if v["index"] == stream), None)
            if s is None:
                raise FingerprintError(f"stream {stream} is not a video stream")
            pf = s.get("pixel_format_raw") or "unknown"
            depth = pix_fmt_depths(self.ffmpeg).get(pf)
            ok = depth is not None and 8 <= depth <= 16 and s.get("width") and s.get("height")
            self._plane[stream] = (plane_for(pf), depth, pf.endswith("be"), int(s["width"]),
                                   int(s["height"])) if ok else None
            if not ok:
                LOG.warning("stream %s: pixel format %s (%s bit) not supported by the v2 video "
                            "fingerprint; using v1 (depends on the ffmpeg build)", stream, pf, depth)
        return self._plane[stream]

    def _seek(self, frame: int) -> list[str]:
        # seek half a frame early: accurate seek drops frames before -ss, so frame `frame` is
        # the first one out (verified against a full decode, src/cmr/CLAUDE.md)
        t = max(Fraction(0), (frame - Fraction(1, 2)) / self.fps)
        return ["-ss", f"{float(t):.6f}", "-i", str(self.path)]

    def video_frame(self, stream: int, frame: int) -> str:
        """v2: the decoded plane in native size and depth (`extractplanes`, `ffmpeg -h
        filter=extractplanes`: planes y/u/v/r/g/b/a; rawvideo keeps the plane's own pixel
        format gray/gray10le/..., verified by byte count and values), hashed in luma_dhash."""
        plane, depth, be, w, h = self.video_plane(stream)
        # RGB: three plane outputs stacked into one frame (`ffmpeg -h filter=vstack`: inputs)
        vf = ("extractplanes=r+g+b[r][g][b];[r][g][b]vstack=inputs=3" if plane == "rgb"
              else "extractplanes=y")
        raw = _ffmpeg(self.ffmpeg, [*self._seek(frame), "-map", f"0:{stream}", "-frames:v", "1",
                                    "-vf", vf, "-f", "rawvideo", "-"])
        try:
            return luma_dhash(raw, w, h, depth, big_endian=be, rgb=plane == "rgb")
        except FingerprintError as e:
            raise FingerprintError(f"frame {frame}: {e}") from None

    def video_frame_v1(self, stream: int, frame: int) -> str:
        """v1 (build-dependent swscale downscale): only to match waivers written with it.
        The chain is exactly the one v1 waivers were written with (commit 6212131). A variant
        via `format=yuv444p` reproduced 6.1.1 on 10-bit dim stripes under 7.1/9.0 but read
        8-bit dim stripes as black on every build (6.1.1 included) and moved dark pictures by
        3-4 bits, so it was not kept (measured, src/cmr/CLAUDE.md Waivers)."""
        px = _ffmpeg(self.ffmpeg, [*self._seek(frame), "-map", f"0:{stream}", "-frames:v", "1",
                                   "-vf", "scale=9:8:flags=area+accurate_rnd,format=gray",
                                   "-f", "rawvideo", "-"])
        if len(px) != 72:
            raise FingerprintError(f"frame {frame}: got {len(px)} bytes, expected 72")
        return dhash(px)

    def audio_frame(self, stream: int, frame: int) -> list[list[float]]:
        ch = self._channels(stream)
        raw = _ffmpeg(self.ffmpeg, ["-ss", f"{float(frame / self.fps):.6f}", "-i", str(self.path),
                                    "-map", f"0:{stream}", "-t", f"{float(1 / self.fps):.6f}",
                                    "-f", "f32le", "-c:a", "pcm_f32le", "-"])
        s = array("f")
        s.frombytes(raw[: len(raw) - len(raw) % (4 * ch)])
        out = []
        for c in range(ch):
            x = s[c::ch]
            if not x:
                raise FingerprintError(f"frame {frame}: no audio decoded")
            out.append([_db(max(abs(v) for v in x)), _db(math.sqrt(sum(v * v for v in x) / len(x)))])
        return out

    def fingerprint(self, check_id: str, stream: int, frames: list[int],
                    version: int | None = None) -> dict:
        """`version` = the waiver's fingerprint version when matching (video 1 recomputes with
        the v1 chain); None = current algorithm (v2, or v1 when the pixel format has no
        integer plane). Video fingerprints also record `ffmpeg` (version line, informational:
        not compared)."""
        kind = media_kind(check_id)
        typ, algo, ver = ALGO[kind]
        fn, extra = self.audio_frame, {}
        if kind == "video":
            plane = None if version == 1 else self.video_plane(stream)
            if plane is None and version not in (None, 1):
                raise FingerprintError(f"stream {stream}: pixel format not supported by the "
                                       f"v{version} video fingerprint")
            if plane is None:
                (typ, algo, ver), fn = VIDEO_V1, self.video_frame_v1
            else:
                fn, extra = self.video_frame, {"plane": plane[0]}
            extra["ffmpeg"] = self.ffmpeg_version()
        return {"type": typ, "algorithm": algo, "version": ver, "frames": list(frames),
                "values": [fn(stream, f) for f in frames], **extra}

    def ffmpeg_version(self) -> str:
        if self._version is None:
            out = _ffmpeg(self.ffmpeg, ["-version"]).decode(errors="replace").splitlines()
            self._version = out[0].strip() if out else "unknown"
        return self._version

    def for_range(self, check_id: str, stream: int, a: int, b: int) -> dict:
        n = VIDEO_SAMPLES if media_kind(check_id) == "video" else AUDIO_SAMPLES
        return self.fingerprint(check_id, stream, sample_frames(a, b, n))


def text_fingerprint(evidence: list | None) -> dict:
    """Slate findings (D27): the field name (evidence `kind`) + the exact OCR'd text (evidence
    `text`). Not tied to frames: a waiver stays valid while the slate reads the same, and a
    changed text (e.g. another AD-ID) is 'content changed'. No media access needed."""
    ev = (evidence or [{}])[0] if evidence else {}
    typ, algo, ver = ALGO["slate"]
    return {"type": typ, "algorithm": algo, "version": ver, "frames": [],
            "values": [ev.get("kind"), ev.get("text")]}


def distance(a: dict, b: dict) -> float | None:
    """None = not comparable (other type/algorithm/version or frames)."""
    # informational keys are not compared: `ffmpeg` (build that made it) and `plane` (v2
    # video: y, or rgb = BT.709 luma from R G B; both are luma, so they stay comparable)
    if any(a.get(k) != b.get(k) for k in ("type", "algorithm", "version", "frames")):
        return None
    if a["type"] == TEXT_TYPE:
        return 0 if nfc(a["values"]) == nfc(b["values"]) else 1     # D32: NFC (waiver YAML edited on a Mac)
    if a["type"] == "video_dhash":
        return max((bin(int(x, 16) ^ int(y, 16)).count("1") for x, y in zip(a["values"], b["values"])),
                   default=0)
    d = 0.0
    for fa, fb in zip(a["values"], b["values"]):
        if len(fa) != len(fb):
            return None
        for ca, cb in zip(fa, fb):
            d = max(d, *(abs(p - q) for p, q in zip(ca, cb)))
    return round(d, 2)


# --- waiver file ----------------------------------------------------------------------
def parse_date(v) -> datetime.date | None:
    if v in (None, ""):
        return None
    try:
        return v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v))
    except ValueError:
        raise ConfigError(f"bad date {v!r} (use YYYY-MM-DD)") from None


_REQUIRED = ("id", "check_id", "stream", "frame_start", "frame_end", "fingerprint", "reason")
_REQUIRED_CATEGORY = ("id", "check_id", "max_severity", "reason")


def is_category(w: dict) -> bool:
    return w.get("type") == "category"


def _check_category(w: dict) -> str | None:
    """Problem with a category waiver entry, else None."""
    if str(w["max_severity"]).lower() not in CEILINGS:
        return f"max_severity {w['max_severity']!r} (allowed: {', '.join(CEILINGS)})"
    if media_kind(str(w["check_id"])) is None:
        return f"check_id {w['check_id']!r}: only video.*/audio.*/slate.* checks can be waived"
    if "edge" in w and "channel" in w:
        return "edge and channel are exclusive"
    if "edge" in w and w["edge"] not in EDGES:
        return f"edge {w['edge']!r} (allowed: {', '.join(EDGES)})"
    a, b = w.get("frame_start"), w.get("frame_end")
    if (a is None) != (b is None) or (a is not None and not (
            isinstance(a, int) and isinstance(b, int) and 0 <= a <= b)):
        return "frame_start/frame_end must both be set, integers, 0 <= start <= end"
    return None


def load(path) -> list[dict]:
    try:
        data = yaml.safe_load(Path(path).read_text()) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"waiver file {path}: {e}") from e
    ws = data.get("waivers") if isinstance(data, dict) else None
    if not isinstance(data, dict) or (data and data.get("waiver_version") not in READ_VERSIONS) \
            or not isinstance(ws or [], list):
        raise ConfigError(f"waiver file {path}: needs waiver_version: "
                          f"{' or '.join(map(str, READ_VERSIONS))} and a `waivers` list")
    for i, w in enumerate(ws or []):
        if not isinstance(w, dict) or w.get("type", "finding") not in ("finding", "category"):
            raise ConfigError(f"waiver file {path}: entry {i + 1} is not a finding/category waiver")
        if is_category(w) and data["waiver_version"] < 2:
            raise ConfigError(f"waiver file {path}: entry {i + 1}: category waivers need "
                              f"waiver_version: 2")
        miss = [k for k in (_REQUIRED_CATEGORY if is_category(w) else _REQUIRED) if k not in w]
        if miss:
            raise ConfigError(f"waiver file {path}: entry {i + 1} lacks {', '.join(miss)}")
        bad = _check_category(w) if is_category(w) else None
        if "job" in w and (not isinstance(w["job"], str) or not w["job"]):
            bad = f"job {w['job']!r} must be a job id or \"*\""
        if bad:
            raise ConfigError(f"waiver file {path}: entry {i + 1}: {bad}")
        parse_date(w.get("expires"))
    return ws or []


def save(path, waivers: list[dict]) -> None:
    # version 1 while the file has only per-finding waivers, so older cmr builds still read it
    ver = WAIVER_VERSION if any(is_category(w) for w in waivers) else 1
    Path(path).write_text(yaml.safe_dump({"waiver_version": ver, "waivers": waivers},
                                         sort_keys=False, allow_unicode=True,
                                         default_flow_style=None))


def next_id(waivers: list[dict]) -> str:
    n = max((int(w["id"][1:]) for w in waivers
             if str(w["id"])[:1] == "W" and str(w["id"])[1:].isdigit()), default=0)
    return f"W{n + 1:04d}"


def make_waiver(wid: str, fd: dict, fp: dict, reason: str, today: datetime.date,
                expires: datetime.date | None = None, job: str | None = None) -> dict:
    w = {"id": wid, **({"job": job} if job else {}), "check_id": fd["check_id"], "stream": fd["stream"],
         **locator(fd.get("evidence") or (fd.get("candidate") or {}).get("evidence")),
         "frame_start": fd["frame_start"], "frame_end": fd["frame_end"],
         "timecode_start": fd.get("timecode_start"), "timecode_end": fd.get("timecode_end"),
         "message": fd.get("message"), "fingerprint": fp, "reason": reason,
         "created": today.isoformat()}
    if expires:
        w["expires"] = expires.isoformat()
    return w


def make_category(wid: str, check_id: str, max_severity: str, reason: str, today: datetime.date,
                  kind: str | None = None, edge: str | None = None, channel: int | None = None,
                  frames: tuple[int, int] | None = None,
                  expires: datetime.date | None = None, job: str | None = None) -> dict:
    """Category waiver (no fingerprint: a blanket decision). Raises ValueError if invalid."""
    w = {"id": wid, **({"job": job} if job else {}), "type": "category", "check_id": check_id}
    for k, v in (("kind", kind), ("edge", edge), ("channel", channel)):
        if v is not None:
            w[k] = v
    if frames:
        w["frame_start"], w["frame_end"] = frames
    w.update(max_severity=str(max_severity).lower(), reason=reason, created=today.isoformat())
    if expires:
        w["expires"] = expires.isoformat()
    bad = _check_category(w)
    if bad:
        raise ValueError(bad)
    return w


# --- matching -------------------------------------------------------------------------
def split_job(waivers: list[dict], job_id: str | None) -> tuple[list[dict], list[dict]]:
    """(entries this run applies, foreign entries) for a run of job `job_id` (None = the run
    has no job id): an entry applies when it has no `job` (written before D31), `job: "*"`,
    or the run's job id; any other `job` is foreign (skipped, listed in the report)."""
    mine, foreign = [], []
    for w in waivers:
        (mine if "job" not in w or w["job"] in (ANY_JOB, job_id) else foreign).append(w)
    return mine, foreign


def settings(profile: dict) -> dict:
    return {**DEFAULTS, **(profile.get("waivers") or {})}


def _tolerance(w: dict, s: dict) -> float:
    kind = media_kind(w["check_id"])
    return s["video_max_bits"] if kind == "video" else 0 if kind == "slate" else s["audio_max_db"]


def location_match(f: Finding, w: dict, margin: int) -> bool:
    if f.check_id != w["check_id"] or f.stream != w["stream"] or f.frame_start is None:
        return False
    ev = f.evidence or (f.candidate.evidence if f.candidate else [])
    loc = locator(ev)
    if any(loc.get(k) != w.get(k) for k in LOCATOR_KEYS if k in loc or k in w):
        return False
    return _in_range(f, ev, w, margin)


def _in_range(f: Finding, ev: list, w: dict, margin: int) -> bool:
    m = max(margin, int((ev[0] if ev else {}).get("boundary_uncertainty_frames", 0) or 0))
    end = f.frame_end if f.frame_end is not None else f.frame_start
    return w["frame_start"] - m <= f.frame_start and end <= w["frame_end"] + m


def category_match(f: Finding, w: dict, margin: int) -> bool:
    """Same check; a frame range (content finding, as D17: metadata findings are never
    waived); RAW severity INFO..max_severity; evidence kind / edge equal when the waiver
    sets them; channel equal to evidence `channel` or one of `channels`; with a frame range,
    the finding range inside it (+- margin, as per-finding waivers)."""
    if f.check_id != w["check_id"] or f.frame_start is None or not (
            Severity.INFO <= f.raw_severity <= Severity.parse(w["max_severity"])):
        return False
    ev = f.evidence or (f.candidate.evidence if f.candidate else [])
    e0 = ev[0] if ev and isinstance(ev[0], dict) else {}
    if any(k in w and e0.get(k) != w[k] for k in ("kind", "edge")):
        return False
    if "channel" in w and not (e0.get("channel") == w["channel"]
                               or w["channel"] in (e0.get("channels") or [])):
        return False
    return "frame_start" not in w or _in_range(f, ev, w, margin)


def _count(state: dict, wid: str) -> None:
    c = state.setdefault("__counts__", {})
    c[wid] = c.get(wid, 0) + 1


def _legacy(w: dict) -> dict:
    """Report keys for a waiver with a v1 video fingerprint (build-dependent): the report says
    so, `fingerprint_note` tells how to upgrade."""
    fp = w.get("fingerprint") or {}
    if fp.get("type") == VIDEO_V1[0] and fp.get("version") == VIDEO_V1[2]:
        return {"fingerprint_version": 1, "fingerprint_note": V1_NOTE}
    return {}


def apply(findings: list[Finding], waivers: list[dict], current: Callable[[dict], dict],
          s: dict, today: datetime.date, state: dict) -> None:
    """Mark findings WAIVED / content-changed. `current(w)` fingerprints the checked file at
    w's frames (only called on a location match). `state[id]` collects 'applied'/'changed'
    across files for summary(), `state["__counts__"][id]` the number of findings waived.
    Category waivers run after the per-finding ones, on findings still not waived (also
    ones a per-finding waiver marked content_changed: the blanket decision covers them)."""
    active = [w for w in waivers if not (parse_date(w.get("expires")) and parse_date(w["expires"]) < today)]
    cats = [w for w in active if is_category(w)]
    active = [w for w in active if not is_category(w)]
    cache: dict[str, float | None] = {}
    for f in findings:
        if f.severity not in (Severity.INFO, Severity.WARN, Severity.FAIL):
            continue
        hits = [w for w in active if location_match(f, w, s["margin_frames"])]
        changed = []
        for w in hits:
            if (w.get("fingerprint") or {}).get("type") == TEXT_TYPE:
                # slate: compare with THIS finding's text (not cached per waiver)
                cache[w["id"]] = distance(w["fingerprint"], text_fingerprint(
                    f.evidence or (f.candidate.evidence if f.candidate else [])))
            elif w["id"] not in cache:
                try:
                    cache[w["id"]] = distance(w["fingerprint"], current(w))
                except FingerprintError as e:
                    LOG.warning("%s: fingerprint failed: %s", w["id"], e)
                    cache[w["id"]] = None
            d, tol = cache[w["id"]], _tolerance(w, s)
            if d is not None and d <= tol:
                state.setdefault(w["id"], set()).add("applied")
                f.waiver_ref = w["id"]
                f.waiver = {"id": w["id"], "status": "waived", "reason": w["reason"],
                            "distance": d, "tolerance": tol, **_legacy(w)}
                f.lower_to(Severity.PASS)
                _count(state, w["id"])
                LOG.debug("%s waived %s frames %s-%s (distance %s <= %s)", w["id"], f.check_id,
                          f.frame_start, f.frame_end, d, tol)
                break
            LOG.info("%s content changed for %s frames %s-%s (distance %s, tolerance %s)", w["id"],
                     f.check_id, f.frame_start, f.frame_end, d, tol)
            state.setdefault(w["id"], set()).add("changed")
            changed.append((w, d, tol))
        else:
            if changed:
                w, d, tol = changed[0]
                f.waiver = {"id": w["id"], "status": "content_changed", "reason": w["reason"],
                            "distance": d, "tolerance": tol,
                            "note": f"previously waived ({w['id']}), content changed - re-review",
                            **_legacy(w)}
    for f in findings:
        if f.waiver_ref or f.severity not in (Severity.INFO, Severity.WARN, Severity.FAIL):
            continue
        for w in cats:
            if category_match(f, w, s["margin_frames"]):
                state.setdefault(w["id"], set()).add("applied")
                _count(state, w["id"])
                f.waiver_ref = w["id"]
                f.waiver = {"id": w["id"], "status": "waived", "type": "category",
                            "reason": w["reason"], "max_severity": w["max_severity"]}
                f.lower_to(Severity.PASS)
                LOG.debug("%s (category) waived %s %s frames %s-%s", w["id"], f.check_id,
                          f.raw_severity.name, f.frame_start, f.frame_end)
                break


def summary(path, waivers: list[dict], state: dict, today: datetime.date,
            foreign: list[dict] = ()) -> dict:
    """Waivers by outcome. Every entry has `type` (finding|category) and `count` (findings
    waived in this run); category entries also their scope (kind/edge/channel/max_severity).
    `foreign` (1.4): entries of another job (split_job), not applied; entries carry `job`."""
    out = {"file": str(path), "applied": [], "content_changed": [], "stale": [], "expired": [],
           "foreign": []}
    counts = state.get("__counts__", {})
    for w, is_foreign in [(w, False) for w in waivers] + [(w, True) for w in foreign]:
        st = state.get(w["id"], set())
        if is_foreign:
            key = "foreign"
        elif parse_date(w.get("expires")) and parse_date(w["expires"]) < today:
            key = "expired"
        elif "applied" in st:
            key = "applied"
        elif "changed" in st:
            key = "content_changed"
        else:
            key = "stale"
        e = {k: w.get(k) for k in ("id", "check_id", "stream", "frame_start",
                                   "frame_end", "timecode_start", "timecode_end", "reason")}
        e.update(type=w.get("type", "finding"), count=counts.get(w["id"], 0))
        if "job" in w:
            e["job"] = w["job"]
        if is_category(w):
            e.update({k: w[k] for k in ("kind", "edge", "channel") if k in w},
                     max_severity=w["max_severity"])
        out[key].append(e)
    return out
