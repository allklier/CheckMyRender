"""slate.bars on synthetic raw frames (pure Python planes; no media, no ffmpeg)."""
from array import array

import pytest

from cmr.checks.slate import bars, reader
from cmr.engine import Skip
from cmr.model import Severity
from test_slate_ocr import PARAMS, make_ctx, run_check

W, H = 960, 540
COLOUR = list(bars.BT709.values())
GREY = [(y, 128, 128) for y in (235, 204, 172, 141, 110, 79, 47, 16)]


def frame(colour=COLOUR, grey=GREY, depth=10, sub="422", strip=16, bg=(18, 128, 128),
          full=False):
    """Planar Y'CbCr bytes: colour strip on top, grey steps at the bottom."""
    cdx, cdy = (1, 1) if sub == "444" else (2, 2 if sub == "420" else 1)
    cw, ch, s = W // cdx, H // cdy, 2 ** (depth - 8)
    conv = bars.full_range if full else (lambda t: t)
    y = [[bg[0] * s] * W for _ in range(H)]
    cb = [[bg[1] * s] * cw for _ in range(ch)]
    cr = [[bg[2] * s] * cw for _ in range(ch)]
    for rows, cols in ((range(0, strip), colour), (range(H - strip, H), grey)):
        seg = W // len(cols)
        for i, t in enumerate(cols):
            t = conv(t)
            for r in rows:
                for x in range(i * seg, (i + 1) * seg):
                    y[r][x] = round(t[0] * s)
                    if r % cdy == 0 and x % cdx == 0:
                        cb[r // cdy][x // cdx] = round(t[1] * s)
                        cr[r // cdy][x // cdx] = round(t[2] * s)
    a = array("B" if depth == 8 else "H")
    for plane in (y, cb, cr):
        for row in plane:
            a.extend(row)
    return a.tobytes(), f"yuv{sub}p" + ("" if depth == 8 else f"{depth}le")


def bars_ctx(tmp_path, monkeypatch, raw_fmt, rng="tv", **params):
    raw, fmt = raw_fmt
    monkeypatch.setattr(reader, "ffmpeg", lambda args, *a, **k: raw)
    chk, ctx = make_ctx(tmp_path, "slate.bars", {**PARAMS, **params})
    ctx.media.video[0].update(width=W, height=H, pixel_format=fmt, color={"range": rng})
    return chk, ctx


@pytest.mark.parametrize("depth, sub", [(10, "422"), (8, "420"), (8, "444"), (12, "444")])
def test_clean_bars_pass(tmp_path, monkeypatch, depth, sub):
    chk, ctx = bars_ctx(tmp_path, monkeypatch, frame(depth=depth, sub=sub))
    assert run_check(chk, ctx) == []


def test_elevated_black_and_wrong_hue(tmp_path, monkeypatch):
    c = list(COLOUR)
    c[7] = (32, 128, 128)            # black bar lifted
    c[5] = (63, 102, 228)            # red Cr off
    g = list(GREY)
    g[3] = (141, 134, 128)           # tinted grey step
    chk, ctx = bars_ctx(tmp_path, monkeypatch, frame(c, g))
    out = {f.evidence[0]["kind"]: f for f in run_check(chk, ctx)}
    assert set(out) == {"colour:black", "colour:red", "grey:step 4"}
    assert "elevated black" in out["colour:black"].message
    assert "wrong hue" in out["colour:red"].message and out["colour:red"].evidence[0]["text"] == "Y=63 Cb=102 Cr=228"
    assert "tinted grey" in out["grey:step 4"].message
    assert all(f.severity == Severity.FAIL and f.frame_start == 1 for f in out.values())


def test_tolerance_is_narrow_but_not_zero(tmp_path, monkeypatch):
    c = list(COLOUR)
    c[0] = (233, 129, 127)           # within +-2
    chk, ctx = bars_ctx(tmp_path, monkeypatch, frame(c))
    assert run_check(chk, ctx) == []
    c[0] = (232, 128, 128)
    chk, ctx = bars_ctx(tmp_path, monkeypatch, frame(c))
    assert [f.evidence[0]["kind"] for f in run_check(chk, ctx)] == ["colour:white"]
    chk, ctx = bars_ctx(tmp_path, monkeypatch, frame(c), tolerance=4)
    assert run_check(chk, ctx) == []


def test_range_mismatch(tmp_path, monkeypatch):
    chk, ctx = bars_ctx(tmp_path, monkeypatch, frame(full=True))
    out = run_check(chk, ctx)
    assert [f.evidence[0]["kind"] for f in out] == ["range"] and "range mismatch" in out[0].message
    chk, ctx = bars_ctx(tmp_path, monkeypatch, frame(full=True), rng="pc")
    assert run_check(chk, ctx) == []                       # full-range file: full targets


def test_strip_not_found_and_fixed_fractions(tmp_path, monkeypatch):
    chk, ctx = bars_ctx(tmp_path, monkeypatch, frame(colour=[(18, 128, 128)] * 8))
    out = run_check(chk, ctx)
    assert len(out) == 1 and out[0].severity == Severity.WARN and "colour strip not found" in out[0].message
    t = tmp_path / "t.yaml"
    t.write_text("template_version: 1\nfields: {TITLE: {}}\nbars:\n"
                 f"  colour: {{y0: 0, y1: {16 / H}}}\n")
    chk, ctx = bars_ctx(tmp_path, monkeypatch, frame(), slate={"template": str(t)})
    assert run_check(chk, ctx) == []


def test_unsupported_pixel_format_skips(tmp_path, monkeypatch):
    chk, ctx = bars_ctx(tmp_path, monkeypatch, (b"", "rgb24"))
    with pytest.raises(Skip, match="rgb24"):
        run_check(chk, ctx)
