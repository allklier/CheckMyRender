from fractions import Fraction
from pathlib import Path

from cmr.checks.filename import expected_filename
from cmr.media import from_probe
from cmr.model import Finding, Severity, exit_code


def test_severity_order_and_exit_codes():
    assert Severity.PASS < Severity.SKIPPED < Severity.INFO < Severity.WARN < Severity.FAIL < Severity.ERROR
    assert exit_code(Severity.PASS) == 0
    assert exit_code(Severity.INFO) == 0
    assert exit_code(Severity.WARN) == 1
    assert exit_code(Severity.WARN, fail_on="fail") == 0
    assert exit_code(Severity.FAIL, fail_on="fail") == 2
    assert exit_code(Severity.ERROR) == 3


def test_lower_only():
    f = Finding("x", "fail", "m")
    f.lower_to("error")
    assert f.severity == Severity.FAIL
    f.lower_to("warn")
    assert f.severity == Severity.WARN and f.raw_severity == Severity.FAIL


RAW = {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "tags": {"major_brand": "qt  "}},
       "streams": [
           {"index": 0, "codec_type": "video", "codec_name": "prores", "profile": "HQ",
            "width": 1920, "height": 1080, "pix_fmt": "yuv422p10le", "field_order": "progressive",
            "r_frame_rate": "24000/1001", "avg_frame_rate": "24000/1001", "nb_frames": "96",
            "color_primaries": "bt709", "tags": {"timecode": "00:59:59:00"}},
           {"index": 1, "codec_type": "audio", "codec_name": "pcm_s24le", "sample_rate": "48000",
            "channels": 2, "channel_layout": "stereo", "bits_per_sample": 24, "bits_per_raw_sample": "24"},
           {"index": 2, "codec_type": "subtitle", "codec_name": "eia_608"}],
       "frames": [{"stream_index": 0, "media_type": "video", "color_range": "tv"}]}


def test_value_mapping():
    m = from_probe(Path("x.mov"), RAW)
    v = m.video[0]
    assert m.container == "mov" and m.timecode == "00:59:59:00"
    assert v["codec_profile"] == "422 HQ" and v["scan"] == "progressive"
    assert v["fps"] == Fraction(24000, 1001) and v["nb_frames"] == 96
    assert v["color"] == {"primaries": "bt709", "transfer": "unknown", "matrix": "unknown", "range": "tv"}
    assert v["color_source"] == {"color_range": "first_frame"}
    assert m.audio[0]["bit_depth"] == 24 and m.subtitles[0]["codec"] == "eia_608"
    mp4 = from_probe(Path("x.mp4"), {**RAW, "format": {"format_name": "mov,mp4", "tags": {"major_brand": "isom"}}})
    assert mp4.container == "mp4"


def test_filename_explicit_wins_and_tags():
    job = {"title": "SHOW", "episode": "101", "naming_pattern": "{title}_{episode}_{fps_tag}.mov"}
    tags = {"fps_tag": {"from": "24000/1001", "map": {"24000/1001": "2398"}}}
    assert expected_filename(job, tags) == "SHOW_101_2398.mov"
    assert expected_filename({**job, "filename": "explicit.mov"}, tags) == "explicit.mov"
    assert expected_filename({**job, "naming_pattern": "{title}_{h}.mov"},
                             {"h": {"from": 1080, "map": {1080: "HD"}}}) == "SHOW_HD.mov"
