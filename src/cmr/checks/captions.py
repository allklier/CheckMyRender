"""Embedded caption presence only (caption quality and sidecars = roadmap).

Verified with ffprobe 6.1.1: a c608 track in MOV probes as a subtitle stream with
codec_name 'eia_608'. H.264 A53 captions set the video stream field closed_captions=1
(UNVERIFIED here: no A53 test media generated)."""
from __future__ import annotations

from ..engine import Check, Skip, register


def _has(media, fmt: str) -> bool:
    if fmt == "embedded_608":
        return (any(s["codec"] == "eia_608" for s in media.subtitles)
                or any(v["closed_captions"] for v in media.video))
    if fmt == "embedded_708":
        return any(v["closed_captions"] for v in media.video)
    raise Skip(f"caption format {fmt!r} not supported in v1 (embedded_608/embedded_708 only)")


@register
class CaptionPresence(Check):
    id = "captions.presence"

    def evaluate(self, ctx, candidates):
        cap = ctx.params.get("captions") or {}
        if not cap.get("required"):
            raise Skip("captions not required by spec")
        missing = [f for f in cap.get("formats") or ["embedded_608"] if not _has(ctx.media, f)]
        if missing:
            return [ctx.finding(f"required captions missing: {', '.join(missing)}",
                                expected=cap.get("formats"), actual=None)]
        return []
