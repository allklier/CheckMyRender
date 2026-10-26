"""Video waiver fingerprint v2 (D17): pure-Python 9x8 luma reduction, no media."""
import datetime
from array import array

import pytest

from cmr import waivers
from cmr.model import Finding, Severity
from cmr.report.console import render

STRIPES = (1, 3, 5, 7)          # columns (of 9) that carry a dim stripe, as tests/test_waivers_media.py


def plane(w, h, f, depth=8, big_endian=False):
    """Raw plane bytes: f(x, y) gives an 8-bit code, scaled to `depth` like an encoder would."""
    vals = [f(x, y) << (depth - 8) for y in range(h) for x in range(w)]
    if depth == 8:
        return bytes(vals)
    a = array("H", vals)
    if big_endian:
        a.byteswap()
    return a.tobytes()


def stripes(w, level=23):
    return lambda x, y: level if (9 * x // w) in STRIPES else 16


def ramp(w, h):
    return lambda x, y: 16 + (x * 200 // w + y * 7) % 220


def test_cell_edges_integer_and_nonempty():
    for n, k in ((9, 9), (1001, 9), (563, 8), (1920, 9), (1080, 8)):
        e = waivers.cell_edges(n, k)
        assert e[0] == 0 and e[-1] == n and len(e) == k + 1
        assert all(b > a for a, b in zip(e, e[1:]))
        assert e == [i * n // k for i in range(k + 1)]
    assert waivers.cell_edges(1001, 9)[1] == 111     # floor(1001/9)


@pytest.mark.parametrize("depth", [8, 10, 12, 16])
def test_same_picture_same_cells_at_every_depth(depth):
    w, h = 1001, 563                                  # odd size: uneven cells
    ref = waivers.luma_cells(plane(w, h, ramp(w, h)), w, h, 8)
    assert waivers.luma_cells(plane(w, h, ramp(w, h), depth), w, h, depth) == ref
    assert waivers.luma_cells(plane(w, h, ramp(w, h), depth, big_endian=True), w, h, depth,
                              big_endian=True) == ref


def test_known_values_and_determinism():
    w, h = 63, 32                                     # 9 cells of 7 columns: stripes on cells
    black = plane(w, h, lambda x, y: 16)
    assert waivers.luma_dhash(black, w, h, 8) == "0000000000000000"
    raw = plane(w, h, stripes(w), 10)
    cells = waivers.luma_cells(raw, w, h, 10)
    assert cells[:9] == [16, 23, 16, 23, 16, 23, 16, 23, 16]
    assert waivers.luma_dhash(raw, w, h, 10) == "5555555555555555"
    assert all(waivers.luma_dhash(raw, w, h, 10) == "5555555555555555" for _ in range(3))


def test_dim_stripes_differ_from_black_beyond_tolerance():
    w, h = 1920, 1080
    a = waivers.luma_dhash(plane(w, h, stripes(w), 10), w, h, 10)
    b = waivers.luma_dhash(plane(w, h, lambda x, y: 16, 10), w, h, 10)
    d = waivers.distance(*({"type": "video_dhash", "algorithm": "a", "version": 2, "frames": [1],
                            "values": [v]} for v in (a, b)))
    assert d == 32 and d > waivers.DEFAULTS["video_max_bits"]


def test_rounding_dead_zone_and_half_up():
    w, h = 18, 8                                      # cells of 2x1 samples
    # 10-bit 64 and 65 (8-bit 16.0 and 16.25) round to the same code: grain does not flip bits
    a = array("H", [64 + (x % 2) for y in range(h) for x in range(w)])
    assert set(waivers.luma_cells(a.tobytes(), w, h, 10)) == {16}
    # mean exactly x.5 in 8-bit codes rounds up: 10-bit 66 = 16.5 -> 17
    b = array("H", [66] * (w * h))
    assert set(waivers.luma_cells(b.tobytes(), w, h, 10)) == {17}


def test_rgb_planes_give_luma():
    w, h = 90, 40
    g = plane(w, h, ramp(w, h))
    y = waivers.luma_cells(g, w, h, 8)
    assert waivers.luma_cells(g * 3, w, h, 8, rgb=True) == y          # grey: R = G = B = Y
    g12 = plane(w, h, ramp(w, h), 12, big_endian=True)
    assert waivers.luma_cells(g12 * 3, w, h, 12, big_endian=True, rgb=True) == y
    # a pure red / pure blue picture: BT.709 weights on the cell sums
    r_only = plane(w, h, lambda x, y: 200) + bytes(2 * w * h)
    assert set(waivers.luma_cells(r_only, w, h, 8, rgb=True)) == {round(200 * 0.2126)}


def test_bad_input_raises():
    with pytest.raises(waivers.FingerprintError):
        waivers.luma_cells(bytes(8 * 8), 8, 8, 8)                   # narrower than 9 cells
    with pytest.raises(waivers.FingerprintError):
        waivers.luma_cells(bytes(100), 20, 10, 8)                   # wrong byte count
    with pytest.raises(waivers.FingerprintError):
        waivers.luma_cells(bytes(4 * 20 * 10), 20, 10, 32)          # float planes: not v2


def test_plane_choice_and_pix_fmt_table(monkeypatch):
    assert [waivers.plane_for(p) for p in ("yuv422p10le", "yuvj420p", "gray16le", "p010le",
                                           "rgb24", "rgb48be", "gbrp12le", "bgra")] == \
        ["y"] * 4 + ["rgb"] * 4
    canned = (b"Pixel formats:\nI.... = Supported Input  format for conversion\n"
              b"FLAGS NAME            NB_COMPONENTS BITS_PER_PIXEL BIT_DEPTHS\n-----\n"
              b"IO... yuv420p                3             12      8-8-8\n"
              b"IO... yuv422p10le            3             20      10-10-10\n"
              b"IO... gbrpf32le              3             96      32-32-32\n")
    monkeypatch.setattr(waivers, "_ffmpeg", lambda ff, args: canned)
    monkeypatch.setattr(waivers, "_DEPTHS", {})
    assert waivers.pix_fmt_depths("fake") == {"yuv420p": 8, "yuv422p10le": 10, "gbrpf32le": 32}


class _FakeFP(waivers.Fingerprinter):
    """Fingerprinter without media: records which algorithm ran."""

    def __init__(self, plane_info=("y", 10, False, 64, 32)):
        super().__init__("x.mov", 24)
        self._plane, self._version, self.calls = {0: plane_info}, "ffmpeg version test", []

    def video_frame(self, stream, frame):
        self.calls.append(("v2", frame))
        return "5555555555555555"

    def video_frame_v1(self, stream, frame):
        self.calls.append(("v1", frame))
        return "0000000000000000"


def test_new_fingerprint_is_v2_with_ffmpeg_info():
    fp = _FakeFP().fingerprint("video.black_runs", 0, [30, 40])
    assert (fp["type"], fp["algorithm"], fp["version"]) == ("video_dhash", "dhash64-luma9x8-box", 2)
    assert fp["plane"] == "y" and fp["ffmpeg"] == "ffmpeg version test"
    other = {**fp, "ffmpeg": "ffmpeg version 9.0.2", "plane": "rgb"}       # informational only
    assert waivers.distance(fp, other) == 0


def test_v1_waiver_recomputed_with_v1_chain_and_reported():
    f = _FakeFP()
    v1 = f.fingerprint("video.black_runs", 0, [30, 40], version=1)
    assert (v1["algorithm"], v1["version"]) == ("dhash64-gray9x8-area", 1)
    assert [c[0] for c in f.calls] == ["v1", "v1"]
    assert waivers.distance(v1, f.fingerprint("video.black_runs", 0, [30, 40])) is None  # v1 vs v2

    w = {"id": "W0001", "check_id": "video.black_runs", "stream": 0, "frame_start": 30,
         "frame_end": 40, "fingerprint": v1, "reason": "ok"}
    fd = Finding("video.black_runs", Severity.FAIL, "black run", stream=0, frame_start=30, frame_end=40)
    state = {}
    waivers.apply([fd], [w], lambda w: f.fingerprint(w["check_id"], w["stream"], w["fingerprint"]["frames"],
                                                     w["fingerprint"].get("version")),
                  dict(waivers.DEFAULTS), datetime.date(2026, 10, 26), state)
    assert fd.waiver_ref == "W0001" and fd.waiver["fingerprint_version"] == 1
    assert fd.waiver["fingerprint_note"] == waivers.V1_NOTE
    rep = {"files": [{"path": "x.mov", "worst": "PASS", "findings": [fd.to_dict()]}],
           "summary": {"counts": {"WAIVED": 1}, "worst": "PASS", "exit_code": 0}}
    assert "waiver W0001: fingerprint v1 (depends on the ffmpeg build)" in render(rep, False)


def test_v2_waiver_has_no_legacy_note_and_unsupported_format_falls_back():
    f = _FakeFP()
    w = {"id": "W0002", "check_id": "video.black_runs", "stream": 0, "frame_start": 30,
         "frame_end": 40, "fingerprint": f.fingerprint("video.black_runs", 0, [30, 40]), "reason": "ok"}
    fd = Finding("video.black_runs", Severity.FAIL, "black run", stream=0, frame_start=30, frame_end=40)
    waivers.apply([fd], [w], lambda w: f.fingerprint(w["check_id"], w["stream"], w["fingerprint"]["frames"],
                                                     w["fingerprint"].get("version")),
                  dict(waivers.DEFAULTS), datetime.date(2026, 10, 26), {})
    assert fd.waiver_ref == "W0002" and "fingerprint_version" not in fd.waiver
    # float / unknown pixel format: new waivers fall back to v1; a v2 waiver cannot be matched
    g = _FakeFP(plane_info=None)
    assert g.fingerprint("video.black_runs", 0, [30])["version"] == 1
    with pytest.raises(waivers.FingerprintError):
        g.fingerprint("video.black_runs", 0, [30], version=2)
