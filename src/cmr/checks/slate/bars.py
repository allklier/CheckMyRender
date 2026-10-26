"""slate.bars: colour bars and grey steps on the slate, measured in native Y'CbCr.

Decode: one slate frame (the 2nd of the segment) by seeking, `-f rawvideo` in the stream's
NATIVE pixel format (no -pix_fmt, so no swscale range/matrix/dither step; measured: 1920x1080
yuv422p10le gives 8,294,400 bytes = Y + 2 half-width chroma planes at 2 bytes). Planar
yuv(a)420/422/444 at 8-16 bit is parsed in pure Python; values are compared as 8-bit
equivalents (10-bit / 4). Other pixel formats -> SKIPPED.
Strips: template fractions (`bars.colour/grey: y0 y1 x0 x1`, equal-width segments) or found
automatically like the spike's bars.py: per row of the top/bottom `band` (12 %) of the frame,
cut at steps (|dY|+|dCb|+|dCr| over +-2 samples > `step_threshold`), a row qualifies if it
splits into >= 4 segments of equal width covering > 70 % of the width; the strip = the longest
run of qualifying rows with the same segment count. Median Y/Cb/Cr over the inner half of the
rows and the inner `inner` (55 %) of each segment.
Targets: BT.709 100% bars (limited range 8-bit: white 235/128/128, yellow 219/16/138, cyan
188/154/16, green 173/42/26, magenta 78/214/230, red 63/102/240, blue 32/240/118, black
16/128/128; full range scaled when the file is tagged pc); grey steps: Y from the template
(`y_targets`), Cb/Cr 128. Tolerance +-`tolerance` (default 2) 8-bit code values; H.264 4:2:0
may need ~4. Findings: elevated/crushed black, wrong level, wrong hue (Cb/Cr) per segment, and
a range mismatch (full-range bars in a limited-range file or the reverse).
"""
from __future__ import annotations

import re
import statistics
import sys
from array import array

from cmr.engine import Check, Skip, register
from cmr.model import Severity
from . import reader, template
from .base import _SEV

BT709 = {"white": (235, 128, 128), "yellow": (219, 16, 138), "cyan": (188, 154, 16),
         "green": (173, 42, 26), "magenta": (78, 214, 230), "red": (63, 102, 240),
         "blue": (32, 240, 118), "black": (16, 128, 128)}
_PIX = re.compile(r"^yuva?(420|422|444)p(\d+)?(le|be)?$")


def full_range(t):
    y, cb, cr = t
    return ((y - 16) * 255 / 219, (cb - 128) * 255 / 224 + 128, (cr - 128) * 255 / 224 + 128)


class Frame:
    """Planar Y'CbCr frame as 8-bit-equivalent accessors."""

    def __init__(self, raw: bytes, pix_fmt: str, w: int, h: int):
        m = _PIX.match(pix_fmt or "")
        if not m:
            raise Skip(f"pixel format {pix_fmt!r} not supported for bars (planar YUV only)")
        sub, depth = m.group(1), int(m.group(2) or 8)
        self.w, self.h, self.scale = w, h, 2 ** (depth - 8)
        self.cdx = 1 if sub == "444" else 2
        self.cdy = 2 if sub == "420" else 1
        self.cw, self.ch = -(-w // self.cdx), -(-h // self.cdy)
        bps = 1 if depth == 8 else 2
        need = (w * h + 2 * self.cw * self.ch) * bps
        if len(raw) < need:
            raise RuntimeError(f"decoded frame has {len(raw)} bytes, expected >= {need} ({pix_fmt})")
        a = array("B" if bps == 1 else "H")
        a.frombytes(raw[:need])
        if bps == 2 and (m.group(3) or "le") != sys.byteorder[0] + "e":
            a.byteswap()
        n, c = w * h, self.cw * self.ch
        self.y, self.cb, self.cr = a[:n], a[n:n + c], a[n + c:n + 2 * c]

    def row(self, r: int, xs: list[int]) -> list[tuple[float, float, float]]:
        s, yr, cr_ = self.scale, r * self.w, (r // self.cdy) * self.cw
        return [(self.y[yr + x] / s, self.cb[cr_ + x // self.cdx] / s, self.cr[cr_ + x // self.cdx] / s)
                for x in xs]

    def median(self, rows: range, x0: int, x1: int) -> tuple[float, float, float]:
        ys, cbs, crs = [], [], []
        for r in rows:
            yr, cr_ = r * self.w, (r // self.cdy) * self.cw
            ys.extend(self.y[yr + x0:yr + x1])
            cbs.extend(self.cb[cr_ + x0 // self.cdx:cr_ + max(x0 // self.cdx + 1, x1 // self.cdx)])
            crs.extend(self.cr[cr_ + x0 // self.cdx:cr_ + max(x0 // self.cdx + 1, x1 // self.cdx)])
        s = self.scale
        return (statistics.median(ys) / s, statistics.median(cbs) / s, statistics.median(crs) / s)


def row_segments(vals, min_w, thr, k=2):
    d = [0.0] * len(vals)
    for i in range(k, len(vals) - k):
        a, b = vals[i - k], vals[i + k]
        d[i] = abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])
    cuts, i = [0], 0
    while i < len(d):
        if d[i] > thr:
            j = i
            while j + 1 < len(d) and d[j + 1] > thr:
                j += 1
            cuts.append(max(range(i, j + 1), key=lambda q: d[q]))
            i = j + 1
        else:
            i += 1
    cuts.append(len(vals))
    return [(a + k, b - k) for a, b in zip(cuts[:-1], cuts[1:]) if b - a - 2 * k >= min_w]


def equal_width(segs, n) -> bool:
    if len(segs) < 4:
        return False
    w = [b - a for a, b in segs]
    mean = statistics.mean(w)
    return statistics.pstdev(w) / mean < 0.08 and sum(w) > 0.7 * n


def find_strip(fr: Frame, rows, thr: float) -> tuple[range, list[tuple[int, int]]] | None:
    """Auto-detect: (rows of the strip, [(x0, x1)] segments in full-resolution pixels)."""
    step = max(1, fr.w // 960)
    xs = list(range(0, fr.w, step))
    good = []
    for r in rows:
        segs = row_segments(fr.row(r, xs), max(2, len(xs) // 40), thr)
        if equal_width(segs, len(xs)):
            good.append((r, segs))
    if not good:
        return None
    runs, cur = [], [good[0]]
    for g in good[1:]:
        if g[0] - cur[-1][0] == 1 and len(g[1]) == len(cur[-1][1]):
            cur.append(g)
        else:
            runs.append(cur)
            cur = [g]
    runs.append(cur)
    run = max(runs, key=len)
    segs = run[len(run) // 2][1]
    return range(run[0][0], run[-1][0] + 1), [(a * step, b * step) for a, b in segs]


def fixed_strip(fr: Frame, cfg: dict, n: int):
    y0, y1 = round(cfg.get("y0", 0) * fr.h), round(cfg.get("y1", 1) * fr.h)
    x0, x1 = round(cfg.get("x0", 0) * fr.w), round(cfg.get("x1", 1) * fr.w)
    seg = (x1 - x0) / n
    return range(y0, max(y0 + 1, y1)), [(round(x0 + i * seg), round(x0 + (i + 1) * seg)) for i in range(n)]


def measure(fr: Frame, rows: range, segs, inner: float):
    h = len(rows)
    rs = range(rows.start + h // 4, rows.start + h - h // 4) if h >= 4 else rows
    out = []
    for a, b in segs:
        m = round((b - a) * (1 - inner) / 2)
        out.append(fr.median(rs, a + m, max(a + m + 1, b - m)))
    return out


@register
class SlateBars(Check):
    id = "slate.bars"
    qualities = ("fast", "high")
    params_schema = {
        "type": "object", "additionalProperties": False, "required": ["slate"],
        "properties": {
            "slate": {"type": "object"},
            "tolerance": {"type": "number", "minimum": 0},          # 8-bit code values
            "chroma_tolerance": {"type": "number", "minimum": 0},
            "band": {"type": "number", "exclusiveMinimum": 0, "maximum": 0.5},
            "inner": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
            "step_threshold": {"type": "number", "minimum": 0},
            "missing_severity": _SEV}}

    def evaluate(self, ctx, candidates):
        tpl = template.load((ctx.params.get("slate") or {}).get("template"), ctx.spec)
        if not tpl.bars:
            raise Skip(f"slate template {tpl.name!r} defines no bars")
        frame = reader.slate_frames(ctx.timeline)[0]
        v = ctx.media.video[0]
        try:
            raw = reader.ffmpeg([*reader.seek_args(frame, ctx.timeline.fps), "-i", str(ctx.media.path),
                                 "-map", f"0:{v['index']}", "-frames:v", "1", "-f", "rawvideo", "-"])
        except (RuntimeError, OSError) as e:
            return [ctx.finding(f"slate frame not decoded: {e}", Severity.ERROR)]
        fr = Frame(raw, v.get("pixel_format"), v["width"], v["height"])
        p = ctx.params
        tol, ctol = p.get("tolerance", 2), p.get("chroma_tolerance", p.get("tolerance", 2))
        band, inner, thr = p.get("band", 0.12), p.get("inner", 0.55), p.get("step_threshold", 20)
        full = (v.get("color") or {}).get("range") == "pc"
        out = []
        for strip_name, cfg in tpl.bars.items():
            if strip_name == "colour":
                names = cfg.get("segments") or list(BT709)
                targets = [BT709[n] for n in names]
            else:
                ys = cfg.get("y_targets") or []
                names = [f"step {i + 1}" for i in range(len(ys))]
                targets = [(y, 128, 128) for y in ys]
            if not targets:
                continue
            if full:
                targets = [full_range(t) for t in targets]
            if "y0" in cfg or "y1" in cfg:
                rows, segs = fixed_strip(fr, cfg, len(targets))
            else:
                n = max(1, round(fr.h * band))
                rows_all = range(0, n) if cfg.get("band", "top" if strip_name == "colour" else "bottom") == "top" \
                    else range(fr.h - n, fr.h)
                found = find_strip(fr, rows_all, thr)
                if found is None or len(found[1]) != len(targets):
                    got = "no strip" if found is None else f"{len(found[1])} segments"
                    out.append(self._f(ctx, frame, f"slate {strip_name} strip not found as {len(targets)} "
                                       f"equal segments ({got})", strip_name, got,
                                       Severity.parse(p.get("missing_severity", "warn"))))
                    continue
                rows, segs = found
            meas = measure(fr, rows, segs, inner)
            res = self._judge(ctx, frame, strip_name, names, targets, meas, tol, ctol, full)
            if any(f.evidence[0]["kind"] == "range" for f in res):
                return res     # a range mismatch explains every level: one finding, not 16
            out += res
        return out

    def _f(self, ctx, frame, msg, kind, text, severity=None, **kw):
        return ctx.finding(msg, severity, stream=ctx.media.video[0]["index"], frame_start=frame,
                           frame_end=frame, evidence=[{"kind": kind, "text": text, **kw.pop("ev", {})}],
                           **kw)

    def _judge(self, ctx, frame, strip, names, targets, meas, tol, ctol, full):
        txt = lambda m: f"Y={m[0]:.0f} Cb={m[1]:.0f} Cr={m[2]:.0f}"   # noqa: E731
        if strip == "colour" and "black" in names and "white" in names:
            k, w = meas[names.index("black")], meas[names.index("white")]
            other = (lambda t: t) if full else full_range
            ob, ow = other(BT709["black"])[0], other(BT709["white"])[0]
            if abs(k[0] - ob) <= tol and abs(w[0] - ow) <= tol:
                rng = "limited" if full else "full"
                return [self._f(ctx, frame, f"slate bars are {rng} range (black Y {k[0]:.1f}, white Y "
                                f"{w[0]:.1f}) but the file is tagged {'full' if full else 'limited'} "
                                f"range: range mismatch", "range", f"{txt(k)} / {txt(w)}",
                                expected="full" if full else "limited", actual=rng)]
        out = []
        for i, (name, t, m) in enumerate(zip(names, targets, meas)):
            dy, dcb, dcr = m[0] - t[0], m[1] - t[1], m[2] - t[2]
            bad_y, bad_c = abs(dy) > tol, abs(dcb) > ctol or abs(dcr) > ctol
            if not (bad_y or bad_c):
                continue
            label = f"{name} bar" if strip == "colour" else f"grey {name}"
            is_black = name == "black" or (strip == "grey" and i == len(names) - 1)
            if is_black and bad_y and dy > 0:
                what = "elevated black"
            elif is_black and bad_y:
                what = "crushed black"
            elif bad_c:
                what = "wrong hue" if strip == "colour" else "tinted grey"
            else:
                what = "wrong level"
            exp = f"Y={t[0]:.0f} Cb={t[1]:.0f} Cr={t[2]:.0f}"
            out.append(self._f(ctx, frame, f"slate {label}: {what}: measured {m[0]:.1f}/{m[1]:.1f}/"
                               f"{m[2]:.1f}, expected {exp} (Y/Cb/Cr, 8-bit, +-{tol})",
                               f"{strip}:{name}", txt(m), expected=exp, actual=txt(m)))
        return out
