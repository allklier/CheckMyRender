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
Sequences (EXR/DPX) are post-v1: only kind='file' exists.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from .timecode import parse_fps, TimecodeError


class ProbeError(RuntimeError):
    pass


# --- value mapping (ffprobe -> spec vocabulary) ------------------------------------
PRORES_PROFILES = {"Proxy": "422 Proxy", "LT": "422 LT", "Standard": "422", "HQ": "422 HQ",
                   "4444": "4444", "XQ": "4444 XQ"}
SCAN = {"progressive": "progressive", "tt": "interlaced", "bb": "interlaced",
        "tb": "interlaced", "bt": "interlaced"}
RANGE = {"tv": "tv", "limited": "tv", "mpeg": "tv", "pc": "pc", "full": "pc", "jpeg": "pc"}
LAYOUT_CHANNELS = {"mono": 1, "stereo": 2, "5.1": 6, "7.1": 8}


def map_codec_profile(codec: str | None, profile: str | None) -> str:
    if profile is None:
        return "unknown"
    if codec == "prores":
        return PRORES_PROFILES.get(profile, profile)
    return profile


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
    timecode: str | None = None     # first embedded start timecode found

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
                     "timecode": self.timecode, "video": self.video, "audio": self.audio,
                     "subtitles": self.subtitles})


def ffprobe(path: Path, ffprobe_bin: str = "ffprobe") -> dict:
    cmd = [ffprobe_bin, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
           "-show_frames", "-read_intervals", "%+#10", str(path)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise ProbeError(f"ffprobe failed on {path}: {p.stderr.strip()}")
    return json.loads(p.stdout)


def from_probe(path: Path, raw: dict) -> MediaInfo:
    mi = MediaInfo(path=Path(path), kind="file", raw=raw, container=_container(raw.get("format", {})))
    first_frame = {}
    for fr in raw.get("frames", []):
        first_frame.setdefault(fr.get("stream_index"), fr)
    for s in raw.get("streams", []):
        tags = s.get("tags") or {}
        if mi.timecode is None and tags.get("timecode"):
            mi.timecode = tags["timecode"]
        t = s.get("codec_type")
        if t == "video":
            fr, color_source = first_frame.get(s["index"], {}), {}
            for key in ("color_primaries", "color_transfer", "color_space", "color_range"):
                if s.get(key) in (None, "", "unknown") and fr.get(key) not in (None, "", "unknown"):
                    s = {**s, key: fr[key]}
                    color_source[key] = "first_frame"
            mi.video.append({
                "index": s["index"],
                "codec": s.get("codec_name", "unknown"),
                "codec_profile": map_codec_profile(s.get("codec_name"), s.get("profile")),
                "width": s.get("width"), "height": s.get("height"),
                "pixel_format": s.get("pix_fmt", "unknown"),
                "sample_aspect_ratio": s.get("sample_aspect_ratio", "unknown"),
                "display_aspect_ratio": s.get("display_aspect_ratio", "unknown"),
                "fps": _fps(s.get("r_frame_rate")),
                "avg_fps": _fps(s.get("avg_frame_rate")),
                "scan": SCAN.get(s.get("field_order", ""), "unknown"),
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
            mi.audio.append({
                "index": s["index"],
                "codec": s.get("codec_name", "unknown"),
                "sample_rate": _int(s.get("sample_rate")),
                "bit_depth": _int(s.get("bits_per_raw_sample")) or _int(s.get("bits_per_sample")) or None,
                "channels": s.get("channels"),
                "channel_layout": s.get("channel_layout") or "unknown",
            })
        elif t == "subtitle":
            mi.subtitles.append({"index": s["index"], "codec": s.get("codec_name", "unknown")})
    return mi


def probe(path: Path, ffprobe_bin: str = "ffprobe") -> MediaInfo:
    """Exactly one ffprobe call per file."""
    return from_probe(path, ffprobe(path, ffprobe_bin))
