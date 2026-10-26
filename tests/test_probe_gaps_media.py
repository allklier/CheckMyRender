"""Generated media for the probe/metadata/captions gaps: MXF OP1a DNxHD with a format-level
timecode, interlaced MPEG-2 MXF with drop-frame timecode, Avid-style 2997/125 MOV with yuvj420p,
and the bounded 436M packet reader run on a real data stream (MOV tmcd).
Media goes to $CMR_TEST_MEDIA (never the repo)."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

import make_media
from cmr import vanc
from cmr.cli import main
from cmr.media import probe

ROOT = Path(__file__).resolve().parents[1]
SPEC = yaml.safe_load((ROOT / "tests" / "media" / "spec_test.yaml").read_text())
MXF_AUDIO = dict(audio_tracks=1, captions=False)


@pytest.fixture(scope="session")
def gmedia():
    out = Path(os.environ.get("CMR_TEST_MEDIA", Path(tempfile.gettempdir()) / "cmr_test_media"))
    return {
        "dnxhd_mxf": make_media.make(out, "pg_dnxhd_mxf", filename="pg_dnxhd.mxf", pix_fmt="yuv422p",
                                     vcodec=["-c:v", "dnxhd", "-b:v", "175M"], **MXF_AUDIO),
        "mpeg2_il_mxf": make_media.make(
            out, "pg_mpeg2_il_mxf", filename="pg_mpeg2_il.mxf", fps="30000/1001", pix_fmt="yuv422p",
            vcodec=["-c:v", "mpeg2video", "-b:v", "50M", "-flags", "+ilme+ildct", "-top", "1"],
            color=[], timecode="00:59:59;00", **MXF_AUDIO),
        "avid_mov": make_media.make(out, "pg_avid_mov", filename="pg_avid.mov", fps="2997/125",
                                    pix_fmt="yuvj420p", vcodec=["-c:v", "libx264", "-preset", "ultrafast"],
                                    color=[], captions=False),
    }


def spec_file(tmp_path, **video):
    s = json.loads(json.dumps(SPEC))
    s["video"].update(video)
    s["audio"]["tracks"] = [s["audio"]["tracks"][0]]
    s["content"]["captions"] = {"required": False}
    p = tmp_path / "spec.yaml"
    p.write_text(yaml.safe_dump(s))
    return p


def check(tmp_path, f, spec, profile="final"):
    rc = main(["check", str(f), "--profile", str(profile), "--spec", str(spec), "--report", "json",
               "-o", str(tmp_path), "--max-findings", "0"])
    rep = json.loads((tmp_path / "cmr_report_latest.json").read_text())
    by = {}
    for x in rep["files"][0]["findings"]:
        by.setdefault(x["check_id"], []).append(x)
    return rc, by, rep


def sev(by, cid):
    return sorted(x["severity"] for x in by[cid])


def test_mxf_dnxhd_timecode_op_profile_stereo(gmedia, tmp_path):
    mi = probe(gmedia["dnxhd_mxf"])
    assert (mi.timecode, mi.timecode_source, mi.mxf_op) == ("00:59:59:00", "format", "OP1a")
    assert mi.video[0]["codec_profile"] == "DNxHD 175"
    assert mi.audio[0]["channel_layout"] == "stereo"
    sp = spec_file(tmp_path, container="mxf", codec="dnxhd", codec_profile="DNxHD 175",
                   pixel_format="yuv422p", field_order="progressive", operational_pattern="OP1a")
    rc, by, rep = check(tmp_path, gmedia["dnxhd_mxf"], sp)
    for cid in ("timing.timecode_start", "timing.layout", "timing.frame_count", "container.op",
                "container.format", "video.codec", "video.field_order", "audio.layout", "video.fps"):
        assert sev(by, cid) == ["PASS"], (cid, by[cid])
    assert rep["files"][0]["timeline"]["start_timecode"] == "00:59:59:00"
    sp = spec_file(tmp_path, container="mxf", codec="dnxhd", operational_pattern="OPAtom",
                   codec_profile="DNxHD 115")
    rc, by, _ = check(tmp_path, gmedia["dnxhd_mxf"], sp)
    assert sev(by, "container.op") == ["FAIL"] and sev(by, "video.codec") == ["FAIL"]


def test_mxf_interlaced_drop_frame(gmedia, tmp_path):
    mi = probe(gmedia["mpeg2_il_mxf"])
    assert mi.timecode == "00:59:59;00" and mi.video[0]["field_order"] == "tt"
    sp = spec_file(tmp_path, container="mxf", fps="30000/1001", field_order="tt")
    s = yaml.safe_load(sp.read_text())
    s["timing"]["timecode_mode"] = "drop"
    s["timing"]["program_start_timecode"] = "01:00:00;00"
    s["timing"]["head_layout"] = [{"type": "slate", "frames": 1}, {"type": "black", "frames": 29},
                                  {"type": "program"}]
    s["timing"]["tail_layout"] = [{"type": "black", "frames": 42}]   # 96 frames written
    sp.write_text(yaml.safe_dump(s))
    rc, by, rep = check(tmp_path, gmedia["mpeg2_il_mxf"], sp)
    assert rep["files"][0]["timeline"]["drop_frame"] is True
    assert sev(by, "timing.timecode_start") == ["PASS"], by["timing.timecode_start"]
    assert sev(by, "timing.layout") == ["PASS"], by["timing.layout"]
    assert sev(by, "video.field_order") == ["PASS"]
    s["video"]["field_order"] = "bb"
    sp.write_text(yaml.safe_dump(s))
    rc, by, _ = check(tmp_path, gmedia["mpeg2_il_mxf"], sp)
    assert sev(by, "video.field_order") == ["FAIL"] and by["video.field_order"][0]["actual"] == "tt"


def test_avid_fps_and_yuvj_range_once(gmedia, tmp_path):
    mi = probe(gmedia["avid_mov"])
    v = mi.video[0]
    assert str(v["fps"]) == "2997/125" and v["pixel_format"] == "yuv420p" and v["color"]["range"] == "pc"
    sp = spec_file(tmp_path, codec="h264", codec_profile=None, pixel_format="yuv420p",
                   color={"range": "tv"})
    prof = tmp_path / "p.yaml"
    prof.write_text("profile_version: 1\nname: p\nextends: final\nchecks:\n"
                    "  video.color: {key_severity: {range: info}}\n")
    rc, by, rep = check(tmp_path, gmedia["avid_mov"], sp, prof)
    assert sev(by, "video.fps") == ["INFO", "INFO"]
    assert "2997/125" in by["video.fps"][0]["message"]
    assert sev(by, "video.pixel_format") == ["PASS"]
    assert sev(by, "video.color") == ["INFO"] and by["video.color"][0]["actual"] == "pc"
    assert sev(by, "timing.timecode_start") == ["PASS"]      # timecode maths at 2997/125 (nominal 24)


def test_436m_reader_is_bounded_on_a_real_data_stream(gmedia):
    """No 436M file can be generated with ffmpeg 6.1/5.1 (no muxer support): run the exact reader
    command on the MOV tmcd data stream to verify the ffprobe options, the hexdump parsing and
    the packet bound on this ffmpeg build; the content is not 436M, so no ANC packet is found."""
    f = gmedia["dnxhd_mxf"].parent.parent / "good" / make_media.FILENAME
    if not f.exists():
        make_media.make(f.parent.parent, "good")
    tmcd = next(s["index"] for s in probe(f).raw["streams"] if s["codec_type"] == "data")
    s = vanc.read_436m(f, tmcd, packets=1)
    assert "error" not in s and s["packets_read"] == 1 and s["cdp"] == 0
    p = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-select_streams", str(tmcd),
                        "-show_packets", "-show_data", "-read_intervals", "%+#1", str(f)],
                       capture_output=True, text=True, check=True)
    pk = json.loads(p.stdout)["packets"]
    assert len(pk) == 1 and len(vanc.hexdump_to_bytes(pk[0]["data"])) == int(pk[0]["size"])
