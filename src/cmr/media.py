"""Media abstraction: one ffprobe per file -> MediaInfo, plus the value-mapping layer
that translates ffprobe vocabulary into spec vocabulary.

Verified with ffprobe 6.1.1 (`ffprobe -v error -print_format json
-show_format -show_streams`, ProRes/PCM/tmcd/c608 MOV written by ffmpeg):
- prores `profile` strings: Proxy, LT, Standard, HQ, 4444, XQ
- `field_order`: 'progressive' for prores_ks output; interlaced = tt/bb/tb/bt
- `color_range` is ABSENT at stream level for ProRes-in-MOV even when encoded with
  -color_range tv, but the decoded frame reports 'tv'. The same single ffprobe call
  therefore also reads the first packets (`-show_frames -read_intervals %+#10`) and
  fills missing stream colour fields from the first decoded frame of that stream
  (recorded in `color_source`). UNVERIFIED: whether the ProRes decoder derives the
  frame value from the bitstream or sets it by default.
- timecode: stream tag `timecode` on the video stream and on the tmcd data stream
- embedded 608 in MOV: subtitle stream codec_name 'eia_608' (tag c608);
  H.264 A53 captions: video stream field `closed_captions` = 1
- `format_name` is 'mov,mp4,m4a,3gp,3g2,mj2' for both MOV and MP4; MOV is told
  apart by format tag major_brand 'qt  '.
MXF (ffmpeg 6.1.1 mxf muxer output and two real masters, Avid and Premiere):
- timecode: ONLY the format tag `timecode` (material package), no stream tag ->
  `timecode` falls back to it (`timecode_source` 'format'); DF/NDF from ';' vs ':'.
- `operational_pattern_ul` format tag, e.g. 060e2b34.04010101.0d010201.01010900 = OP1a
  (byte 13 item complexity 1-3, byte 14 package complexity 1-3 -> a/b/c; byte 13 0x10 =
  OP-Atom) -> `mxf_op`.
- video `nb_frames` and DNxHD `bit_rate` absent; audio `channel_layout` absent (2 ch ->
  'stereo', `channel_layout_source` 'inferred').
- DNxHD `profile` is 'DNXHD' (no bitrate class); DNxHR 'DNXHR LB|SQ|HQ|HQX|444'. The class
  comes from the first frame's `pkt_size` (fixed frame size per CID) x fps -> Mbps, nearest
  Avid name (175, 115, 36, ...), 'x' for 10-bit -> e.g. 'DNxHD 175x'.
- 436M VANC: data stream tag `data_type` 'vbi_vanc_smpte_436M'; caption counts read by
  `vanc.read_436m` (first packets only) into raw['vanc_436m'].
- `yuvj*` pixel formats (H.264/MJPEG full range) map to the base format + range 'pc'
  (`pixel_format_raw` keeps the ffprobe name), so the problem is reported once, as range.
Sequences (EXR/DPX) are post-v1: only kind='file' exists.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from . import log
from . import vanc as vanc_mod
from .timecode import parse_fps, TimecodeError

LOG = log.get("media")


class ProbeError(RuntimeError):
    pass


# --- value mapping (ffprobe -> spec vocabulary) ------------------------------------
PRORES_PROFILES = {"Proxy": "422 Proxy", "LT": "422 LT", "Standard": "422", "HQ": "422 HQ",
                   "4444": "4444", "XQ": "4444 XQ"}
SCAN = {"progressive": "progressive", "tt": "interlaced", "bb": "interlaced",
        "tb": "interlaced", "bt": "interlaced"}
RANGE = {"tv": "tv", "limited": "tv", "mpeg": "tv", "pc": "pc", "full": "pc", "jpeg": "pc"}
LAYOUT_CHANNELS = {"mono": 1, "stereo": 2, "5.1": 6, "7.1": 8}
FIELD_ORDERS = ("progressive", "tt", "bb", "tb", "bt")
DNXHR = {"DNXHR LB": "DNxHR LB", "DNXHR SQ": "DNxHR SQ", "DNXHR HQ": "DNxHR HQ",
         "DNXHR HQX": "DNxHR HQX", "DNXHR 444": "DNxHR 444"}
# Avid DNxHD names (Mbps at the stream's own rate, e.g. CID 1238 = 175 @23.976, 185 @25, 220 @29.97)
DNXHD_CLASSES = (36, 45, 60, 75, 80, 90, 100, 110, 115, 120, 145, 175, 185, 220, 240, 290, 350,
                 365, 440)


def map_codec_profile(codec: str | None, profile: str | None) -> str:
    if profile is None:
        return "unknown"
    if codec == "prores":
        return PRORES_PROFILES.get(profile, profile)
    if codec == "dnxhd":
        return DNXHR.get(profile, profile)
    return profile


def dnxhd_class(frame_bytes: int | None, fps: Fraction | None, pix_fmt: str | None,
                bit_rate: int | None = None) -> str | None:
    """DNxHD bitrate class from the frame size (fixed per CID) x fps, or the stream bit_rate.
    Nearest Avid name within 5 %, else the rounded Mbps; 'x' suffix for 10-bit."""
    if frame_bytes and fps:
        mbps = float(frame_bytes * 8 * fps) / 1e6
    elif bit_rate:
        mbps = bit_rate / 1e6
    else:
        return None
    near = min(DNXHD_CLASSES, key=lambda c: abs(c - mbps))
    name = near if abs(near - mbps) <= 0.05 * near else round(mbps)
    return f"DNxHD {name}" + ("x" if pix_fmt and "10" in pix_fmt else "")


def base_pix_fmt(pix_fmt: str | None) -> tuple[str, bool]:
    """'yuvj420p' -> ('yuv420p', True): the j formats are the base format at full range."""
    if pix_fmt and pix_fmt.startswith("yuvj"):
        return "yuv" + pix_fmt[4:], True
    return pix_fmt or "unknown", False


def mxf_op(ul: str | None) -> str | None:
    """MXF operational pattern UL (ffprobe format tag) -> 'OP1a'..'OP3c' / 'OPAtom' / None."""
    try:
        b = bytes.fromhex((ul or "").replace(".", ""))
    except ValueError:
        return None
    if len(b) != 16 or b[8:12] != bytes.fromhex("0d010201"):
        return None
    if b[12] == 0x10:
        return "OPAtom"
    if 1 <= b[12] <= 3 and 1 <= b[13] <= 3:
        return f"OP{b[12]}{'abc'[b[13] - 1]}"
    return None


def norm_range(v) -> str:
    return RANGE.get(str(v).lower(), "unknown") if v not in (None, "") else "unknown"


def norm_tag(v) -> str:
    """Colour primaries/transfer/matrix: ffprobe omits or says 'unknown'/'unspecified'."""
    if v in (None, "", "unknown", "unspecified", "reserved"):
        return "unknown"
    return str(v)


def _fps(v) -> Fraction | None:
    try:
        f = parse_fps(v)
    except TimecodeError:
        return None
    return f if f.numerator else None


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _container(fmt: dict) -> str:
    names = (fmt.get("format_name") or "").split(",")
    if "mov" in names:
        brand = (fmt.get("tags") or {}).get("major_brand", "").strip()
        return "mov" if brand == "qt" else "mp4"
    return names[0] if names else "unknown"


@dataclass
class MediaInfo:
    path: Path
    kind: str                       # 'file' (sequences: post-v1)
    raw: dict                       # ffprobe JSON, untouched
    container: str
    video: list[dict] = field(default_factory=list)   # mapped, one per video stream
    audio: list[dict] = field(default_factory=list)   # mapped, one per audio stream (file order)
    subtitles: list[dict] = field(default_factory=list)
    data: list[dict] = field(default_factory=list)    # data streams (436M caption counts)
    timecode: str | None = None     # first embedded start timecode found
    timecode_source: str | None = None   # 'stream <index>' (tag on a stream) | 'format' (MXF)
    mxf_op: str | None = None       # MXF operational pattern ('OP1a'...), None if not MXF/unknown

    def to_dict(self) -> dict:
        def conv(o):
            if isinstance(o, Fraction):
                return f"{o.numerator}/{o.denominator}"
            if isinstance(o, dict):
                return {k: conv(v) for k, v in o.items()}
            if isinstance(o, list):
                return [conv(v) for v in o]
            return o
        return conv({"path": str(self.path), "kind": self.kind, "container": self.container,
                     "timecode": self.timecode, "timecode_source": self.timecode_source,
                     "mxf_op": self.mxf_op, "video": self.video, "audio": self.audio,
                     "subtitles": self.subtitles, "data": self.data})


PROBE_TIMEOUT = 60


def ffprobe(path: Path, ffprobe_bin: str = "ffprobe") -> dict:
    """Streams/format in one call; the first decoded video frames (colour fields) in a second.

    `-show_frames -read_intervals %+#10` WITHOUT `-select_streams v` did not stop after 10 frames
    on ffmpeg 5.1.10 (Rocky 9): ffprobe ran for 10+ minutes and printed 1.7 GB of JSON for an
    18 MB test file. With `-select_streams v` it returns in 0.07 s. Observed, root cause not
    investigated. Every call has a timeout."""
    cmd = [ffprobe_bin, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
           str(path)]
    try:
        p = log.run(LOG, cmd, "ffprobe streams", capture_output=True, text=True, timeout=PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ProbeError(f"ffprobe timed out after {PROBE_TIMEOUT}s on {path}")
    if p.returncode != 0:
        raise ProbeError(f"ffprobe failed on {path}: {p.stderr.strip()}")
    raw = json.loads(p.stdout)
    fcmd = [ffprobe_bin, "-v", "error", "-print_format", "json", "-select_streams", "v",
            "-show_frames", "-read_intervals", "%+#10",
            "-show_entries",
            "frame=stream_index,pkt_size,color_primaries,color_transfer,color_space,color_range",
            str(path)]
    try:
        fp = log.run(LOG, fcmd, "ffprobe first frames", capture_output=True, text=True,
                     timeout=PROBE_TIMEOUT)
        if fp.returncode == 0:
            raw["frames"] = json.loads(fp.stdout).get("frames", [])
    except subprocess.TimeoutExpired:
        pass    # first-frame colour fill is best effort; tags from the stream are still used
    vanc = [s["index"] for s in raw.get("streams", []) if s.get("codec_type") == "data"
            and (s.get("tags") or {}).get("data_type") == vanc_mod.DATA_TYPE_436M]
    if vanc:
        raw["vanc_436m"] = [vanc_mod.read_436m(path, i, ffprobe_bin) for i in vanc]
    return raw


def from_probe(path: Path, raw: dict) -> MediaInfo:
    mi = MediaInfo(path=Path(path), kind="file", raw=raw, container=_container(raw.get("format", {})))
    first_frame = {}
    for fr in raw.get("frames", []):
        first_frame.setdefault(fr.get("stream_index"), fr)
    fmt_tags = (raw.get("format") or {}).get("tags") or {}
    mi.mxf_op = mxf_op(fmt_tags.get("operational_pattern_ul")) if mi.container == "mxf" else None
    vanc = {v["stream"]: v for v in raw.get("vanc_436m", [])}
    for s in raw.get("streams", []):
        tags = s.get("tags") or {}
        if mi.timecode is None and tags.get("timecode"):
            mi.timecode, mi.timecode_source = tags["timecode"], f"stream {s['index']}"
        t = s.get("codec_type")
        if t == "video":
            fr, color_source = first_frame.get(s["index"], {}), {}
            for key in ("color_primaries", "color_transfer", "color_space", "color_range"):
                if s.get(key) in (None, "", "unknown") and fr.get(key) not in (None, "", "unknown"):
                    s = {**s, key: fr[key]}
                    color_source[key] = "first_frame"
            pix_fmt, j_full = base_pix_fmt(s.get("pix_fmt"))
            if j_full and s.get("color_range") in (None, "", "unknown"):
                s = {**s, "color_range": "pc"}
                color_source["color_range"] = "pixel_format"
            fps = _fps(s.get("r_frame_rate"))
            profile = map_codec_profile(s.get("codec_name"), s.get("profile"))
            if s.get("codec_name") == "dnxhd" and profile == "DNXHD":
                profile = dnxhd_class(_int(fr.get("pkt_size")), fps, s.get("pix_fmt"),
                                      _int(s.get("bit_rate"))) or profile
            mi.video.append({
                "index": s["index"],
                "codec": s.get("codec_name", "unknown"),
                "codec_profile": profile,
                "width": s.get("width"), "height": s.get("height"),
                "pixel_format": pix_fmt,
                "pixel_format_raw": s.get("pix_fmt", "unknown"),
                "sample_aspect_ratio": s.get("sample_aspect_ratio", "unknown"),
                "display_aspect_ratio": s.get("display_aspect_ratio", "unknown"),
                "fps": fps,
                "avg_fps": _fps(s.get("avg_frame_rate")),
                "scan": SCAN.get(s.get("field_order", ""), "unknown"),
                "field_order": s.get("field_order") if s.get("field_order") in FIELD_ORDERS else "unknown",
                "color": {"primaries": norm_tag(s.get("color_primaries")),
                          "transfer": norm_tag(s.get("color_transfer")),
                          "matrix": norm_tag(s.get("color_space")),
                          "range": norm_range(s.get("color_range"))},
                "color_source": color_source,   # fields taken from the first decoded frame
                "nb_frames": _int(s.get("nb_frames")),
                "duration": s.get("duration"),
                "closed_captions": bool(s.get("closed_captions")),
            })
        elif t == "audio":
            layout, layout_source = s.get("channel_layout"), "probe"
            if not layout and s.get("channels") == 2:     # MXF carries no layout for PCM pairs
                layout, layout_source = "stereo", "inferred"
            mi.audio.append({
                "index": s["index"],
                "codec": s.get("codec_name", "unknown"),
                "sample_rate": _int(s.get("sample_rate")),
                "bit_depth": _int(s.get("bits_per_raw_sample")) or _int(s.get("bits_per_sample")) or None,
                "channels": s.get("channels"),
                "channel_layout": layout or "unknown",
                "channel_layout_source": layout_source,
            })
        elif t == "subtitle":
            mi.subtitles.append({"index": s["index"], "codec": s.get("codec_name", "unknown")})
        elif t == "data":
            mi.data.append({"index": s["index"], "data_type": tags.get("data_type", "unknown"),
                            "captions_436m": vanc.get(s["index"])})
    if mi.timecode is None and fmt_tags.get("timecode"):
        mi.timecode, mi.timecode_source = fmt_tags["timecode"], "format"
    return mi


def probe(path: Path, ffprobe_bin: str = "ffprobe") -> MediaInfo:
    """Exactly one ffprobe call per file."""
    return from_probe(path, ffprobe(path, ffprobe_bin))
