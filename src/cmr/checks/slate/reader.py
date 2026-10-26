"""Read the slate once per file (shared by all slate checks): pick frames, decode them by
seeking (after the main pass, like the waiver fingerprints), OCR, pair.

Frames: the `slate` segment of head_layout (D7), the 2nd and the 2nd-to-last frame of the
segment (one frame if the segment has one frame). Profile param `split_tolerance_frames` N
(default 0 = strict): the real slate/black split may be up to N frames EARLIER than the spec's
(e.g. spec slate 120 + black 48, file 119 + 49; only the program start matters), so the last
frame read moves N frames earlier (never before the first one).
Decode for OCR: `ffmpeg -ss (f-0.5)/fps -i FILE -map 0:S -frames:v 1 -pix_fmt gray OUT.png` -
the half-frame-early accurate seek that src/cmr/CLAUDE.md verified to return frame f (waiver
fingerprints); PNG output by extension as in the spike (make_media.sh, ffmpeg 5.1.10). 8-bit
gray, not ffmpeg's default for 10-bit sources (rgb48be, 16-bit PNG): on real DNxHD 10-bit
slates tesseract 5.5.2 misread the first line of a 16-bit PNG ('AB002' -> 'mereley', conf 19)
and read it right from the 8-bit gray PNG of the same frame (`ffmpeg -h encoder=png` lists
gray; 2026-10-26, 4 spots).
"""
from __future__ import annotations

import logging
import statistics
import tempfile
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from cmr import log
from cmr.engine import Skip
from . import ocr
from .template import Template

_CACHE: dict = {}
_CACHE_MAX = 4
LOG = log.get("slate")


def slate_frames(timeline, split_tolerance: int = 0) -> list[int]:
    seg = timeline.segment("slate")
    if seg is None:
        raise Skip("head_layout has no slate segment (unslated variant?)")
    a, b = seg["start"], seg["end"]
    if timeline.file_frames is not None and b >= timeline.file_frames:
        raise Skip(f"slate segment {a}-{b} lies outside the file ({timeline.file_frames} frames)")
    if split_tolerance:
        b = max(a + min(2, b - a), b - split_tolerance)
    return [a] if a == b else sorted({a + 1, b - 1}) if b - a >= 2 else [a, b]


def seek_args(frame: int, fps: Fraction) -> list[str]:
    t = max(Fraction(0), (frame - Fraction(1, 2)) / fps)
    return ["-ss", f"{float(t):.6f}"]


def ffmpeg(args: list[str], ffmpeg_bin: str = "ffmpeg", timeout: int = 120) -> bytes:
    p = log.run(LOG, [ffmpeg_bin, "-hide_banner", "-nostdin", "-v", "error", *args],
                "ffmpeg slate seek", capture_output=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {p.stderr.decode(errors='replace').strip()[-300:]}")
    return p.stdout


@dataclass
class SlateRead:
    frames: list[int]
    reads: list[dict[str, ocr.FieldRead]] = field(default_factory=list)   # per frame
    words: list[list[ocr.Word]] = field(default_factory=list)            # per frame, all

    def best(self, name: str) -> ocr.FieldRead | None:
        """The read of a field to compare: the frame with the higher minimum confidence."""
        rs = [r[name] for r in self.reads if name in r]
        if not rs:
            return None
        return max(rs, key=lambda r: (r.min_conf if r.min_conf is not None else 101))


def _log_read(frame: int, words: list, fields: dict, tpl: Template, thr: float) -> None:
    """Per frame: word count and confidences, labels found/missing (INFO); per field the text
    (redacted in default mode), its length and confidence (DEBUG); uncertain reads (WARN)."""
    confs = [w.conf for w in words]
    LOG.info("frame %d: %d word(s), conf mean %.0f min %.0f; %d/%d label(s) found, missing %s",
             frame, len(words), statistics.mean(confs) if confs else -1, min(confs, default=-1),
             len(fields), len(tpl.fields), sorted(set(tpl.fields) - set(fields)))
    for name, r in fields.items():
        if LOG.isEnabledFor(logging.DEBUG):
            LOG.debug("frame %d %s (line %s): %s len %d, %d word(s), min conf %s", frame, name, r.line,
                      log.redact("text", r.text), len(r.text), len(r.words), r.min_conf)
        if r.uncertain(thr):
            LOG.warning("frame %d %s: uncertain read (min conf %s < %s), %s", frame, name,
                        r.min_conf, thr, log.redact("text", r.text))


def read(ctx, tpl: Template, params: dict) -> SlateRead:
    """OCR the slate frames of ctx's file (cached per file + settings). Raises Skip (no slate
    segment), ocr.OCRUnavailable (no tesseract) or RuntimeError (decode/OCR failure)."""
    frames = slate_frames(ctx.timeline, params.get("split_tolerance_frames", 0))
    binary = ocr.resolve_bin(ocr.tesseract_bin(params))
    path = Path(ctx.media.path)
    st = path.stat()
    v = ctx.media.video[0]
    key = (str(path.resolve()), st.st_size, st.st_mtime_ns, tuple(frames), binary,
           params.get("psm", 3), params.get("lang", "eng"), tpl.path, tpl.layout, repr(tpl.countdown),
           repr(sorted(tpl.alias.items())))
    if key in _CACHE:
        LOG.debug("slate read from cache (%s)", ctx.check_id)
        return _CACHE[key]
    out = SlateRead(frames)
    LOG.info("slate OCR: frames %s, template %s (%d fields), %s", frames, log.redact("path", tpl.path),
             len(tpl.fields), log.redact("tool", binary))
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="cmr_slate_") as tmp:
        for f in frames:
            img = Path(tmp).resolve() / f"slate_{f}.png"   # macOS: /tmp -> /private/tmp (leptonica rewrites /tmp paths)
            ffmpeg([*seek_args(f, ctx.timeline.fps), "-i", str(path), "-map", f"0:{v['index']}",
                    "-frames:v", "1", "-pix_fmt", "gray", str(img)])
            words = ocr.run(str(img), binary, params.get("psm", 3), params.get("lang", "eng"),
                            params.get("ocr_timeout", 60))
            words = ocr.drop_region(words, tpl.countdown, v.get("width"), v.get("height"))
            out.words.append(words)
            out.reads.append(ocr.pair(words, tpl))
            _log_read(f, words, out.reads[-1], tpl, params.get("min_confidence", 60))
    LOG.info("phase slate OCR: %.3f s", time.monotonic() - t0)
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = out
    return out
