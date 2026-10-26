"""Probe/metadata/captions gaps: MXF timecode + OP, DNxHD class, yuvj, field order, stereo
inference, NTSC-equivalent fps, per-key colour severity, SMPTE 436M VANC caption parsing.
Canned ffprobe JSON and hand-built 436M bytes (fictional caption text); no media."""
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmr import vanc
from cmr.checks import captions, metadata
from cmr.engine import CheckContext, ConfigError, Skip, Timeline, build_timeline
from cmr.media import base_pix_fmt, dnxhd_class, from_probe, map_codec_profile, mxf_op
from cmr.model import Severity
from cmr.timecode import fps_equivalent, frames_to_tc, is_ntsc, tc_to_frames

OP1A = "060e2b34.04010101.0d010201.01010900"


def mxf_probe(**over):
    v = {"index": 0, "codec_type": "video", "codec_name": "dnxhd", "profile": "DNXHD",
         "width": 1920, "height": 1080, "pix_fmt": "yuv422p10le", "r_frame_rate": "24000/1001",
         "avg_frame_rate": "24000/1001", "field_order": "progressive", "color_range": "tv",
         "duration": "15.015000", "tags": {"track_name": "Track 1"}}
    a = {"index": 1, "codec_type": "audio", "codec_name": "pcm_s24le", "sample_rate": "48000",
         "channels": 2, "bits_per_sample": 24}
    raw = {"format": {"format_name": "mxf", "tags": {"operational_pattern_ul": OP1A,
                                                     "timecode": "00:59:59:00"}},
           "streams": [{**v, **over.get("video", {})}, a] + over.get("extra", []),
           "frames": [{"stream_index": 0, "pkt_size": "917504", "color_range": "tv"}]}
    raw["format"]["tags"].update(over.get("fmt_tags", {}))
    raw.update(over.get("raw", {}))
    return raw


# --- gap 1: MXF timecode from the format tag ----------------------------------------
def test_mxf_timecode_from_format_tag():
    mi = from_probe(Path("x.mxf"), mxf_probe())
    assert (mi.timecode, mi.timecode_source) == ("00:59:59:00", "format")


def test_stream_timecode_wins_over_format_tag():
    raw = mxf_probe(video={"tags": {"timecode": "10:00:00:00"}})
    mi = from_probe(Path("x.mxf"), raw)
    assert (mi.timecode, mi.timecode_source) == ("10:00:00:00", "stream 0")


def test_mxf_drop_frame_from_semicolon():
    raw = mxf_probe(video={"r_frame_rate": "30000/1001", "avg_frame_rate": "30000/1001"},
                    fmt_tags={"timecode": "01:00:00;00"})
    mi = from_probe(Path("x.mxf"), raw)
    tl = build_timeline(mi, {"timing": {}})
    assert tl.drop and tl.tc_source == "file" and tl.tc(0) == "01:00:00;00"
    assert not [p for p in tl.problems if "timecode" in p]       # MXF: frames from duration


# --- gap 5: OP, DNxHD class, field order, stereo -----------------------------------
def test_mxf_operational_pattern():
    assert mxf_op(OP1A) == "OP1a"
    assert mxf_op("060e2b34.04010101.0d010201.02030000") == "OP2c"
    assert mxf_op("060e2b34.04010102.0d010201.10000000") == "OPAtom"
    assert mxf_op("garbage") is None and mxf_op(None) is None
    assert from_probe(Path("x.mxf"), mxf_probe()).mxf_op == "OP1a"


def test_dnxhd_class_from_frame_size():
    f23, f25, f2997 = Fraction(24000, 1001), Fraction(25), Fraction(30000, 1001)
    assert dnxhd_class(917504, f23, "yuv422p10le") == "DNxHD 175x"     # CID 1235
    assert dnxhd_class(917504, f23, "yuv422p") == "DNxHD 175"          # CID 1238
    assert dnxhd_class(917504, f25, "yuv422p") == "DNxHD 185"
    assert dnxhd_class(917504, f2997, "yuv422p") == "DNxHD 220"
    assert dnxhd_class(606208, f23, "yuv422p") == "DNxHD 115"           # CID 1237
    assert dnxhd_class(188416, f23, "yuv422p") == "DNxHD 36"            # CID 1253
    assert dnxhd_class(None, f23, "yuv422p", bit_rate=145_000_000) == "DNxHD 145"
    assert dnxhd_class(None, None, "yuv422p") is None
    assert map_codec_profile("dnxhd", "DNXHR HQX") == "DNxHR HQX"
    mi = from_probe(Path("x.mxf"), mxf_probe())
    assert mi.video[0]["codec_profile"] == "DNxHD 175x"


def test_mxf_stereo_layout_inferred_and_field_order():
    mi = from_probe(Path("x.mxf"), mxf_probe(video={"field_order": "tb"}))
    assert mi.audio[0]["channel_layout"] == "stereo"
    assert mi.audio[0]["channel_layout_source"] == "inferred"
    assert mi.video[0]["field_order"] == "tb" and mi.video[0]["scan"] == "interlaced"
    mi = from_probe(Path("x.mxf"), mxf_probe(video={"field_order": None}))
    assert mi.video[0]["field_order"] == "unknown"


# --- gap 4: yuvj ------------------------------------------------------------------
def test_yuvj_maps_to_base_format_and_full_range():
    assert base_pix_fmt("yuvj420p") == ("yuv420p", True)
    assert base_pix_fmt("yuv420p") == ("yuv420p", False)
    raw = {"format": {"format_name": "mov,mp4", "tags": {"major_brand": "isom"}}, "streams": [
        {"index": 0, "codec_type": "video", "codec_name": "h264", "profile": "High",
         "pix_fmt": "yuvj420p", "r_frame_rate": "25/1", "avg_frame_rate": "25/1"}]}
    v = from_probe(Path("x.mp4"), raw).video[0]
    assert (v["pixel_format"], v["pixel_format_raw"], v["color"]["range"]) == ("yuv420p", "yuvj420p", "pc")
    assert v["color_source"]["color_range"] == "pixel_format"


# --- checks -------------------------------------------------------------------------
def ctx(check_id, params, video=None, media=None):
    tl = Timeline(Fraction(24000, 1001), False, 100, 0, "none", 0, 0, 0, 99)
    media = media or SimpleNamespace(video=[video], audio=[], subtitles=[], data=[], container="mxf",
                                     mxf_op="OP1a")
    return CheckContext(check_id, media, {}, params, Severity.FAIL, tl)


def run(check_cls, c):
    return check_cls().evaluate(c, [])


V = {"index": 0, "fps": Fraction(2997, 125), "avg_fps": Fraction(2997, 125), "field_order": "tt",
     "color": {"primaries": "bt709", "transfer": "bt709", "matrix": "bt709", "range": "pc"},
     "pixel_format": "yuv420p"}


def test_fps_equivalence_rule():
    ntsc23, avid23 = Fraction(24000, 1001), Fraction(2997, 125)
    assert fps_equivalent(avid23, ntsc23)
    assert fps_equivalent(Fraction(2997, 100), Fraction(30000, 1001))
    assert fps_equivalent(Fraction(5994, 100), Fraction(60000, 1001))
    assert not fps_equivalent(Fraction(24), ntsc23)
    assert not fps_equivalent(Fraction(24), avid23)
    assert not fps_equivalent(Fraction(25), Fraction(24))
    assert not fps_equivalent(Fraction(30), Fraction(30000, 1001))
    assert is_ntsc(avid23) and is_ntsc(Fraction(2997, 100)) and not is_ntsc(Fraction(24))


def test_fps_check_avid_rational_is_info():
    out = run(metadata.VideoFps, ctx("video.fps", {"expect": "24000/1001"}, V))
    assert [f.severity for f in out] == [Severity.INFO, Severity.INFO]
    assert "2997/125" in out[0].message and "24000/1001" in out[0].message
    out = run(metadata.VideoFps, ctx("video.fps", {"expect": "24"}, V))
    assert [f.severity for f in out] == [Severity.FAIL, Severity.FAIL]


def test_drop_frame_timecode_at_avid_2997():
    f = Fraction(2997, 100)
    assert tc_to_frames("01:00:00;00", f, True) == tc_to_frames("01:00:00;00", Fraction(30000, 1001), True)
    assert frames_to_tc(1800, f, True) == "00:01:00;02"


def test_color_key_severity_and_yuvj_reported_once():
    p = {"expect": {"primaries": "bt709", "range": "tv"}, "key_severity": {"range": "info"}}
    out = run(metadata.VideoColor, ctx("video.color", p, V))
    assert [(f.severity, f.actual) for f in out] == [(Severity.INFO, "pc")]
    assert run(metadata.VideoPixelFormat, ctx("video.pixel_format", {"expect": "yuv420p"}, V)) == []
    out = run(metadata.VideoColor, ctx("video.color", {"expect": {"range": "tv"}}, V))
    assert [f.severity for f in out] == [Severity.FAIL]          # default: check severity
    with pytest.raises(ConfigError):
        run(metadata.VideoColor, ctx("video.color", {"expect": {}, "key_severity": {"gamma": "info"}}, V))
    with pytest.raises(ConfigError):
        run(metadata.VideoColor, ctx("video.color", {"expect": {}, "key_severity": {"range": "loud"}}, V))


def test_field_order_check():
    assert run(metadata.VideoFieldOrder, ctx("video.field_order", {"video": {"field_order": "tt"}}, V)) == []
    out = run(metadata.VideoFieldOrder, ctx("video.field_order", {"video": {"field_order": "bb"}}, V))
    assert [(f.severity, f.actual) for f in out] == [(Severity.FAIL, "tt")]
    out = run(metadata.VideoFieldOrder, ctx("video.field_order", {"video": {"field_order": "bb"}},
                                            {**V, "field_order": "unknown"}))
    assert out[0].severity == Severity.WARN
    assert run(metadata.VideoFieldOrder, ctx("video.field_order", {"video": {}}, V)) == []
    with pytest.raises(ConfigError):
        run(metadata.VideoFieldOrder, ctx("video.field_order", {"video": {"field_order": "tff"}}, V))


def test_container_op_check():
    assert run(metadata.ContainerOp, ctx("container.op", {"video": {"operational_pattern": "OP1a"}}, V)) == []
    out = run(metadata.ContainerOp, ctx("container.op", {"video": {"operational_pattern": "OP-Atom"}}, V))
    assert out[0].severity == Severity.FAIL and out[0].actual == "OP1a"
    assert run(metadata.ContainerOp, ctx("container.op", {"video": {}}, V)) == []
    mov = SimpleNamespace(video=[V], container="mov", mxf_op=None)
    with pytest.raises(Skip):
        run(metadata.ContainerOp, ctx("container.op", {"video": {"operational_pattern": "OP1a"}}, media=mov))


# --- gap 2: SMPTE 436M VANC ------------------------------------------------------------
def cdp(triplets, flags=0x67, timecode=False):
    body = bytes([0x72, 0xE0 | len(triplets)]) + b"".join(bytes(t) for t in triplets)
    if timecode:
        flags |= 0x80
        body = bytes([0x71, 0, 0, 0, 0]) + body
    body += bytes([0x74, 0, 0, 0])                        # footer (sequence + checksum, unchecked)
    hdr = bytes([0x96, 0x69, 0, 0x4F, flags, 0, 1])
    out = bytearray(hdr + body)
    out[2] = len(out)
    return bytes(out)


def anc(did, sdid, udw, coding=4, line=9):
    payload = bytes([did, sdid, len(udw)]) + udw + b"\x00"          # + checksum
    count = len(payload)
    padded = payload + b"\x00" * (-count % 4)
    return (line.to_bytes(2, "big") + bytes([1, coding]) + count.to_bytes(2, "big")
            + len(padded).to_bytes(4, "big") + (1).to_bytes(4, "big") + padded)


def element(*packets):
    return len(packets).to_bytes(2, "big") + b"".join(packets)


# fictional caption bytes: 608 'HI' (with parity) on field 1, null on field 2, one DTVCC start
CC = [(0xFC, 0xC8, 0x49), (0xFD, 0x80, 0x80), (0xFF, 0xC2, 0x22), (0xFE, 0x8C, 0xFF),
      (0xFA, 0x00, 0x00), (0xFA, 0x00, 0x00)]


def test_436m_cdp_parse_608_and_708():
    s = vanc.summarize([element(anc(0x61, 0x01, cdp(CC)))])
    assert s == {"packets_read": 1, "anc_packets": 1, "cdp": 1, "cdp_active": 1, "cc608_valid": 2,
                 "cc608_nonnull": 1, "cc708_valid": 2, "s334_608": 0}


def test_436m_cdp_with_timecode_section_and_other_anc():
    s = vanc.summarize([element(anc(0x41, 0x05, b"\x01\x02"), anc(0x61, 0x01, cdp(CC, timecode=True)))])
    assert (s["anc_packets"], s["cdp"], s["cc608_valid"], s["cc708_valid"]) == (2, 1, 2, 2)


def test_436m_padding_only_and_608_direct():
    pad = [(0xFA, 0, 0)] * 4
    s = vanc.summarize([element(anc(0x61, 0x01, cdp(pad, flags=0x41)))])
    assert (s["cdp"], s["cdp_active"], s["cc608_valid"], s["cc708_valid"]) == (1, 0, 0, 0)
    s = vanc.summarize([element(anc(0x61, 0x02, bytes([0x80, 0x94, 0x20])))])
    assert s["s334_608"] == 1 and s["cdp"] == 0


def test_436m_10bit_coding_and_truncated_data():
    udw = cdp(CC)
    words = bytes([0x61, 0x01, len(udw)]) + udw + b"\x00"
    words += b"\x00" * (-len(words) % 3)
    packed = b"".join(((words[i] << 20) | (words[i + 1] << 10) | words[i + 2]).to_bytes(4, "big")
                      for i in range(0, len(words), 3))
    pkt = ((9).to_bytes(2, "big") + bytes([1, 7]) + (len(udw) + 4).to_bytes(2, "big")
           + len(packed).to_bytes(4, "big") + (1).to_bytes(4, "big") + packed)
    assert vanc.summarize([element(pkt)])["cc708_valid"] == 2
    assert vanc.summarize([b"\x00\x05\x00", b""])["anc_packets"] == 0     # never raises


def test_hexdump_round_trip():
    data = element(anc(0x61, 0x01, cdp(CC)))
    lines = []
    for off in range(0, len(data), 16):
        chunk = data[off:off + 16]
        hexpart = " ".join(chunk[i:i + 2].hex() for i in range(0, len(chunk), 2))
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{off:08x}: {hexpart:<39}  {asc}")
    assert vanc.hexdump_to_bytes("\n" + "\n".join(lines) + "\n") == data


def cap_media(summary=None, subtitles=(), cc=False):
    data = [{"index": 2, "data_type": vanc.DATA_TYPE_436M, "captions_436m": summary}] if summary else []
    return SimpleNamespace(subtitles=list(subtitles), video=[{"closed_captions": cc}], data=data)


def cap_ctx(media, formats):
    return ctx("captions.presence", {"captions": {"required": True, "formats": formats}}, media=media)


def test_captions_from_436m():
    s = {**vanc.summarize([element(anc(0x61, 0x01, cdp(CC)))]), "stream": 2}
    m = cap_media(s)
    assert run(captions.CaptionPresence, cap_ctx(m, ["embedded_608", "embedded_708", "vanc_436m"])) == []
    pad = {**vanc.summarize([element(anc(0x61, 0x01, cdp([(0xFA, 0, 0)])))]), "stream": 2}
    out = run(captions.CaptionPresence, cap_ctx(cap_media(pad), ["embedded_608", "vanc_436m"]))
    assert out[0].severity == Severity.FAIL and "embedded_608" in out[0].message
    assert "vanc_436m" not in out[0].message and out[0].evidence[0]["cdp"] == 1
    err = cap_media({"stream": 2, "error": "ffprobe timed out"})
    out = run(captions.CaptionPresence, cap_ctx(err, ["embedded_608"]))
    assert out[0].severity == Severity.ERROR and "not read" in out[0].message


def test_captions_mov_608_unchanged():
    m = cap_media(subtitles=[{"codec": "eia_608"}])
    assert run(captions.CaptionPresence, cap_ctx(m, ["embedded_608"])) == []
    out = run(captions.CaptionPresence, cap_ctx(m, ["embedded_708"]))
    assert out[0].severity == Severity.FAIL
    with pytest.raises(Skip):
        run(captions.CaptionPresence, cap_ctx(m, ["sidecar_scc"]))
