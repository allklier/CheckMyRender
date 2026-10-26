"""slate.claims: what the slate (and the filename) CLAIM about the media, checked against the
media itself (D27).
Fields are found by TYPE in the template (the first `duration` / `audio` field, whatever its
label: DURATION, LENGTH, ...).
- DURATION (`:60`, `60`, `60s`, `1:00`): seconds x round(fps) = program frames in TIMECODE seconds
  (:60 at 29.97 = 1800 frames), compared to the actual program length (file frames - head -
  tail) to the frame (`tolerance_frames`, default 0).
- AUDIO (`Stereo`, `Mono`, `5.1`, `7.1`): channel count of the first audio stream.
- Technical tokens in the slate TITLE and the filename stem: resolution `WxH` and
  `720p/1080p/1080i/2160p`, codec `ProRes [HQ|LT|Proxy|4444|XQ]`, `H264/H.264/AVC`,
  `HEVC/H265`, `DNxHD/DNxHR`, frame rate with `fps` or as 23.976/23.98/29.97/59.94.
  Bare numbers (`_60_`, `2997`) are not read as fps (they are durations/tags in titles).
One mismatch = a finding at the profile severity naming both values.
"""
from __future__ import annotations

import re

from cmr.engine import Skip, register
from cmr.media import LAYOUT_CHANNELS
from .base import SlateCheck, schema
from .compare import canon, seconds

AUDIO_WORDS = {"mono": 1, "stereo": 2, "5.1": 6, "51": 6, "surround": 6, "7.1": 8, "71": 8}
CODECS = {"prores": "prores", "h264": "h264", "h.264": "h264", "avc": "h264", "hevc": "hevc",
          "h265": "hevc", "h.265": "hevc", "dnxhd": "dnxhd", "dnxhr": "dnxhd"}
PRORES_PROFILE_WORDS = {"hq": "422 HQ", "lt": "422 LT", "proxy": "422 Proxy", "4444": "4444",
                        "xq": "4444 XQ"}
_RES = re.compile(r"(?<![0-9])(\d{3,4})\s*[xX]\s*(\d{3,4})(?![0-9])")
_PRES = re.compile(r"(?<![0-9])(480|576|720|1080|2160)([pi])(?![a-z0-9])", re.I)
_FPS = re.compile(r"(?<![0-9.])(\d{2}(?:\.\d{1,3})?)\s*fps(?![a-z0-9])|(?<![0-9.])(23\.976|23\.98|29\.97|59\.94)(?![0-9])",
                  re.I)


def duration_seconds(text: str) -> int | None:
    return seconds(text)


def tokens(text: str) -> list[tuple[str, str]]:
    """Claims in a title/filename: [(kind, value)] with kind resolution|scan_height|codec|
    codec_profile|fps."""
    out = []
    for m in _RES.finditer(text):
        out.append(("resolution", f"{int(m.group(1))}x{int(m.group(2))}"))
    for m in _PRES.finditer(text):
        out.append(("height", m.group(1)))
    words = [w for w in re.split(r"[_\s-]+", text) if w]
    for i, w in enumerate(words):
        c = CODECS.get(w.lower())
        if c:
            out.append(("codec", c))
            nxt = words[i + 1].lower() if i + 1 < len(words) else ""
            if c == "prores" and nxt in PRORES_PROFILE_WORDS:
                out.append(("codec_profile", PRORES_PROFILE_WORDS[nxt]))
    for m in _FPS.finditer(text):
        out.append(("fps", m.group(1) or m.group(2)))
    return out


def layout_name(ch: int) -> str:
    return {v: k for k, v in LAYOUT_CHANNELS.items()}.get(ch, f"{ch} ch")


@register
class SlateClaims(SlateCheck):
    id = "slate.claims"
    params_schema = schema(tolerance_frames={"type": "integer", "minimum": 0},
                           check_filename={"type": "boolean"})

    def evaluate(self, ctx, candidates):
        tpl = self.template(ctx)
        sr, err = self.read(ctx, tpl)
        if err:
            return err
        out = (self._duration(ctx, sr, tpl.field_of_type("duration"))
               + self._audio(ctx, sr, tpl.field_of_type("audio")) + self._technical(ctx, sr))
        return out

    def _uncertain(self, ctx, read):
        return read.uncertain(self.p(ctx, "min_confidence"))

    def _sev(self, ctx, read):
        return self.sev(ctx, "uncertain_severity") if self._uncertain(ctx, read) else None

    def _duration(self, ctx, sr, name):
        read, tl = (sr.best(name) if name else None), ctx.timeline
        if read is None or not read.text:
            return []
        if tl.program_start is None:
            raise Skip("program range unknown (head/tail layout does not fit the file)")
        secs = duration_seconds(read.text)
        if secs is None:
            return [self.finding(ctx, sr, f"slate {name} {read.text!r} is not a duration",
                                 name, read.text, self._sev(ctx, read))]
        tc_fps = round(tl.fps)
        want = secs * tc_fps
        act = tl.program_end - tl.program_start + 1
        tol = ctx.params.get("tolerance_frames", 0)
        if abs(act - want) <= tol:
            return []
        note = " (uncertain read)" if self._uncertain(ctx, read) else ""
        return [self.finding(
            ctx, sr, f"slate {name} {read.text!r}{note} = {want} frames ({secs} s x {tc_fps} "
            f"timecode frames/s) but the program is {act} frames ({act - want:+d})",
            name, read.text, self._sev(ctx, read), expected=want, actual=act)]

    def _audio(self, ctx, sr, name):
        read = sr.best(name) if name else None
        if read is None or not read.text:
            return []
        word = canon(read.text).replace(" ", "")
        claim = AUDIO_WORDS.get(word)
        if claim is None:
            return [self.finding(ctx, sr, f"slate {name} {read.text!r}: not a known layout "
                                 f"({', '.join(sorted(AUDIO_WORDS))}); not verified", name,
                                 read.text, self.sev(ctx, "uncertain_severity"))]
        if not ctx.media.audio:
            return [self.finding(ctx, sr, f"slate {name} {read.text!r} but the file has no audio",
                                 name, read.text, self._sev(ctx, read))]
        ch = int(ctx.media.audio[0].get("channels") or 0)
        if ch != claim:
            return [self.finding(ctx, sr, f"slate {name} {read.text!r} ({claim} ch) but the file's "
                                 f"first audio stream is {layout_name(ch)} ({ch} ch)", name,
                                 read.text, self._sev(ctx, read),
                                 expected=layout_name(claim), actual=layout_name(ch))]
        return []

    def _technical(self, ctx, sr):
        v = ctx.media.video[0]
        actual = {"resolution": f"{v.get('width')}x{v.get('height')}", "height": str(v.get("height")),
                  "codec": v.get("codec"), "codec_profile": v.get("codec_profile"),
                  "fps": v.get("fps")}
        sources = []
        read = sr.best("TITLE")
        if read is not None and read.text:
            sources.append(("slate TITLE", read.text, read))
        if ctx.params.get("check_filename", True):
            sources.append(("filename", ctx.media.path.stem, None))
        seen: dict[tuple, list[str]] = {}
        texts = {}
        for src, text, rd in sources:
            for kind, val in tokens(text):
                seen.setdefault((kind, val), []).append(src)
                texts.setdefault((kind, val), (text, rd))
        out = []
        for (kind, val), srcs in seen.items():
            act = actual[kind]
            if kind == "fps":
                ok = act is not None and abs(float(val) - float(act)) < 0.01
                act = f"{float(act):.3f}".rstrip("0").rstrip(".") if act else None
            elif kind == "codec_profile":
                ok = act == val
            else:
                ok = str(act) == val
            if ok:
                continue
            text, rd = texts[(kind, val)]
            where = " and ".join(srcs)
            msg = f"{where} claims {kind.replace('_', ' ')} {val} but the file is {act}"
            if rd is not None:
                out.append(self.finding(ctx, sr, msg, "TITLE", rd.text, self._sev(ctx, rd),
                                        expected=val, actual=act,
                                        evidence={"claim": kind, "sources": srcs}))
            else:     # filename only: not a slate finding, no frame location
                out.append(ctx.finding(msg, expected=val, actual=act,
                                       evidence=[{"kind": "filename", "text": text, "claim": kind}]))
        return out
