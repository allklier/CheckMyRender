"""Consistency checks: probe metadata vs. spec (values arrive via profile ${spec.*} refs).
All compare MediaInfo values that went through the media.py value-mapping layer."""
from __future__ import annotations

from fractions import Fraction

from ..engine import Check, Skip, register
from ..media import LAYOUT_CHANNELS, norm_range
from ..model import Severity
from ..timecode import parse_fps


def _v0(ctx) -> dict:
    return ctx.media.video[0]


def _compare(ctx, items, stream=None):
    """items: [(label, expected, actual)]; expected None = not specified -> not checked."""
    out = []
    for label, exp, act in items:
        if exp is not None and exp != act:
            out.append(ctx.finding(f"{label}: expected {exp!r}, got {act!r}", expected=exp,
                                   actual=act, stream=stream))
    return out


def _ratio(v) -> Fraction | None:
    if v in (None, "unknown", "0:1", "N/A"):
        return None
    return Fraction(str(v).replace(":", "/"))


def effective_aspects(v: dict) -> tuple[Fraction, Fraction]:
    """(pixel aspect, display aspect) of a video stream. An untagged sample aspect ratio means
    square pixels (ffmpeg's convention; H.264 from Adobe Media Encoder carries none), and an
    untagged display aspect is width/height x pixel aspect."""
    par = _ratio(v["sample_aspect_ratio"]) or Fraction(1)
    dar = _ratio(v["display_aspect_ratio"]) or Fraction(v["width"], v["height"]) * par
    return par, dar


def bit_depth_comparable(a: dict) -> bool:
    """Only PCM has a bit depth; lossy audio (AAC, AC-3, ...) reports none."""
    return a["codec"].startswith("pcm_")


@register
class ContainerFormat(Check):
    id = "container.format"

    def evaluate(self, ctx, candidates):
        return _compare(ctx, [("container", ctx.params.get("expect"), ctx.media.container)])


@register
class VideoCodec(Check):
    id = "video.codec"

    def evaluate(self, ctx, candidates):
        v = _v0(ctx)
        return _compare(ctx, [("codec", ctx.params.get("codec"), v["codec"]),
                              ("codec profile", ctx.params.get("profile"), v["codec_profile"])],
                        v["index"])


@register
class VideoResolution(Check):
    id = "video.resolution"

    def evaluate(self, ctx, candidates):
        v, p = _v0(ctx), ctx.params
        par = p.get("pixel_aspect")
        dar = p.get("aspect_ratio")
        act_par, act_dar = effective_aspects(v)
        return _compare(ctx, [
            ("width", p.get("width"), v["width"]),
            ("height", p.get("height"), v["height"]),
            ("pixel aspect", None if par is None else Fraction(str(par)), act_par),
            ("display aspect", None if dar is None else _ratio(dar), act_dar),
        ], v["index"])


@register
class VideoPixelFormat(Check):
    id = "video.pixel_format"

    def evaluate(self, ctx, candidates):
        v = _v0(ctx)
        return _compare(ctx, [("pixel format", ctx.params.get("expect"), v["pixel_format"])], v["index"])


@register
class VideoFps(Check):
    id = "video.fps"

    def evaluate(self, ctx, candidates):
        v = _v0(ctx)
        exp = parse_fps(ctx.params["expect"])
        out = []
        for label, act in (("frame rate (r_frame_rate)", v["fps"]), ("average frame rate", v["avg_fps"])):
            if act != exp:
                out.append(ctx.finding(f"{label}: expected {exp}, got {act}", expected=str(exp),
                                       actual=str(act), stream=v["index"]))
        return out


@register
class VideoScan(Check):
    id = "video.scan"

    def evaluate(self, ctx, candidates):
        v = _v0(ctx)
        exp, act = ctx.params["expect"], v["scan"]
        if act == "unknown":
            return [ctx.finding(f"scan type not signalled (expected {exp})",
                                Severity.parse(ctx.params.get("unknown_severity", "warn")),
                                expected=exp, actual=act, stream=v["index"])]
        return _compare(ctx, [("scan", exp, act)], v["index"])


@register
class VideoColor(Check):
    """Colour tags. Untagged ('unknown') is reported at `unknown_severity` (default warn):
    e.g. ffprobe never reports color_range for ProRes-in-MOV on the dev machine."""
    id = "video.color"

    def evaluate(self, ctx, candidates):
        v = _v0(ctx)
        exp = dict(ctx.params["expect"])
        if "range" in exp:
            exp["range"] = norm_range(exp["range"])
        unk = Severity.parse(ctx.params.get("unknown_severity", "warn"))
        out = []
        for key, e in exp.items():
            a = v["color"].get(key, "unknown")
            if a == e:
                continue
            sev = unk if a == "unknown" else None
            out.append(ctx.finding(f"colour {key}: expected {e!r}, got {a!r}"
                                   + (" (untagged)" if a == "unknown" else ""), sev,
                                   expected=e, actual=a, stream=v["index"]))
        return out


@register
class AudioFormat(Check):
    id = "audio.format"

    def evaluate(self, ctx, candidates):
        if not ctx.media.audio:
            raise Skip("no audio streams")
        p, out = ctx.params, []
        for a in ctx.media.audio:
            items = [(f"audio stream {a['index']} codec", p.get("codec"), a["codec"]),
                     (f"audio stream {a['index']} sample rate", p.get("sample_rate"), a["sample_rate"])]
            if bit_depth_comparable(a):
                items.append((f"audio stream {a['index']} bit depth", p.get("bit_depth"), a["bit_depth"]))
            out += _compare(ctx, items, a["index"])
        return out


@register
class AudioLayout(Check):
    """`tracks` = spec audio.tracks; track index = position among the file's audio streams."""
    id = "audio.layout"

    def evaluate(self, ctx, candidates):
        tracks = ctx.params["tracks"]
        streams = ctx.media.audio
        out = []
        if len(streams) != len(tracks):
            out.append(ctx.finding(f"audio streams: expected {len(tracks)}, got {len(streams)}",
                                   expected=len(tracks), actual=len(streams)))
        for t in tracks:
            layout = t["layout"]
            if layout not in LAYOUT_CHANNELS:
                raise Skip(f"layout {layout!r} not supported in v1 (known: {', '.join(LAYOUT_CHANNELS)})")
            if t["index"] >= len(streams):
                out.append(ctx.finding(f"audio track {t['index']} ({layout}) missing"))
                continue
            s = streams[t["index"]]
            if s["channels"] != LAYOUT_CHANNELS[layout]:
                out.append(ctx.finding(
                    f"audio track {t['index']} (stream {s['index']}): expected {layout} "
                    f"({LAYOUT_CHANNELS[layout]} ch), got {s['channels']} ch ({s['channel_layout']})",
                    expected=layout, actual=s["channel_layout"], stream=s["index"]))
        return out
