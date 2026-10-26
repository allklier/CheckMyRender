"""Embedded caption presence only (caption quality and sidecars = roadmap).

Sources (`captions.formats` values):
- embedded_608: MOV c608 track (subtitle stream codec_name 'eia_608', verified ffprobe 6.1.1),
  H.264 A53 (video field closed_captions=1; UNVERIFIED, no A53 test media), or 608 data in a
  SMPTE 436M VANC track: CDP 608 compatibility bytes (cc_type 0/1, cc_valid) or a SMPTE 334-1
  608 packet (DID 0x61 SDID 0x02).
- embedded_708: H.264 A53 (closed_captions=1), or a 436M CDP with valid DTVCC bytes (cc_type 2/3).
- vanc_436m: a 436M data track with at least one CEA-708 CDP (DID 0x61 SDID 0x01) or 334-1 packet.
436M data comes from media.MediaInfo.data[].captions_436m (cmr/vanc.py): only the first
vanc.PACKETS packets are read, so a track whose captions start later reads as CDPs with
padding only (708 presence still counts a CDP's valid DTVCC bytes, 608 counts valid bytes
including null padding; `cc608_nonnull` is reported in the evidence)."""
from __future__ import annotations

from ..engine import Check, Skip, register
from ..model import Severity
from ..schemas import OPT_OBJ, check_params

FORMATS = ("embedded_608", "embedded_708", "vanc_436m")


def _vanc(media) -> list[dict]:
    return [d["captions_436m"] for d in getattr(media, "data", []) if d.get("captions_436m")]


def _has(media, fmt: str) -> bool:
    vanc = [v for v in _vanc(media) if "error" not in v]
    if fmt == "embedded_608":
        return (any(s["codec"] == "eia_608" for s in media.subtitles)
                or any(v["closed_captions"] for v in media.video)
                or any(v["cc608_valid"] or v["s334_608"] for v in vanc))
    if fmt == "embedded_708":
        return (any(v["closed_captions"] for v in media.video)
                or any(v["cc708_valid"] for v in vanc))
    if fmt == "vanc_436m":
        return any(v["cdp"] or v["s334_608"] for v in vanc)
    raise Skip(f"caption format {fmt!r} not supported (known: {', '.join(FORMATS)})")


@register
class CaptionPresence(Check):
    id = "captions.presence"
    params_schema = check_params(captions=OPT_OBJ)

    def evaluate(self, ctx, candidates):
        cap = ctx.params.get("captions") or {}
        if not cap.get("required"):
            raise Skip("captions not required by spec")
        vanc = _vanc(ctx.media)
        evidence = [dict(x) for x in vanc]
        missing = [f for f in cap.get("formats") or ["embedded_608"] if not _has(ctx.media, f)]
        if not missing:
            return []
        errors = [v for v in vanc if "error" in v]
        if errors:   # a 436M track exists but could not be read: tool problem, not a missing caption
            return [ctx.finding(f"required captions not verified ({', '.join(missing)}): 436M track "
                                f"{errors[0]['stream']} not read: {errors[0]['error']}", Severity.ERROR,
                                expected=cap.get("formats"), actual=None, evidence=evidence)]
        return [ctx.finding(f"required captions missing: {', '.join(missing)}",
                            expected=cap.get("formats"), actual=None, evidence=evidence)]
