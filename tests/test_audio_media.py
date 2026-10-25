"""Integration: audio content checks (loudness, silence, silent channel, clipping) on generated
media with planted defects. Base tone (make_media): stereo 1 kHz sine at -23 dBFS peak on
track 0 = -23 LUFS by BS.1770 definition. `asetnsamples=n=2002` makes audio frame n == video
frame n (48 kHz at 24000/1001), so `enable='between(n,a,b)'` plants defects on exact frames.
Program = file frames 24..71 (01:00:00:00 .. 01:00:01:23)."""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import jsonschema
import pytest
import yaml

import make_media
from cmr.cli import main
from cmr.schemas import REPORT_SCHEMA

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "tests" / "media" / "spec_test.yaml"
N = "asetnsamples=n=2002"

CASES = {
    "a_loud": dict(af_extra="volume=13dB"),                       # -10 LUFS
    "a_quiet": dict(af_extra="volume=-7dB"),                      # -30 LUFS
    "a_24_3": dict(af_extra="volume=-1.3dB"),                     # -24.3: fails R128, passes A/85
    # head and tail at +2 dBFS (clipped): must not affect any program-range result
    "a_hot_head_tail": dict(af_extra=f"{N},volume=25dB:enable='lt(n,24)+gte(n,72)'"),
    # silent head (normal) + 8 silent program frames 40..47
    "a_gap": dict(af_extra=f"{N},volume=0:enable='lt(n,24)+between(n,40,47)'"),
    "a_silent_ch": dict(af_extra="pan=stereo|c0=c0|c1=0*c1"),     # channel 2 silent, both tracks
    "a_clip": dict(af_extra=f"{N},volume=30dB:enable='between(n,50,51)'"),   # +7 dBFS -> clipped
}


@pytest.fixture(scope="session")
def amedia():
    out = Path(os.environ.get("CMR_TEST_MEDIA", Path(tempfile.gettempdir()) / "cmr_test_media"))
    return {name: make_media.make(out, name, **opts) for name, opts in CASES.items()}


def run(tmp_path, f, profile="final", spec=SPEC):
    rc = main(["check", str(f), "--profile", str(profile), "--spec", str(spec),
               "--report", "json", "-o", str(tmp_path)])
    rep = json.loads((tmp_path / "cmr_report.json").read_text())
    jsonschema.validate(rep, REPORT_SCHEMA)
    return rc, [x for x in rep["files"][0]["findings"] if x["check_id"].startswith("audio.")]


def by(findings, cid):
    return [f for f in findings if f["check_id"] == cid]


def r128_summary_I(af: str) -> float:
    """Independent measurement: ffmpeg ebur128 *summary* (stderr) on a raw 2 s stereo sine
    built without make_media / atrim / the engine."""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-f", "lavfi", "-i", "sine=f=1000:r=48000:d=2",
                        "-af", f"volume=-1.94dB,{af},aformat=channel_layouts=stereo,ebur128",
                        "-f", "null", "-"], capture_output=True, text=True, check=True)
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", p.stderr)[-1])


@pytest.mark.parametrize("case,expected_lufs", [("a_loud", -10.0), ("a_quiet", -30.0), ("a_24_3", -24.3)])
def test_loudness_known_levels(amedia, tmp_path, case, expected_lufs):
    rc, fs = run(tmp_path, amedia[case])
    (f,) = by(fs, "audio.loudness")
    measured = f["evidence"][0]["integrated_lufs"]
    # analytic: stereo 1 kHz sine at P dBFS peak = P LUFS
    assert abs(measured - expected_lufs) < 0.1, measured
    # independent ffmpeg run (summary has 0.1 LU resolution)
    vol = {"a_loud": "volume=13dB", "a_quiet": "volume=-7dB", "a_24_3": "volume=-1.3dB"}[case]
    assert abs(measured - r128_summary_I(vol)) <= 0.1
    assert f["severity"] == "FAIL" and rc == 2
    assert "relative-gated" in f["message"] and "not dialogue-gated" in f["message"]
    assert (f["frame_start"], f["frame_end"]) == (24, 71)
    assert (f["timecode_start"], f["timecode_end"]) == ("01:00:00:00", "01:00:01:23")


def test_loudness_atsc_preset_from_spec(amedia, tmp_path):
    spec = yaml.safe_load(SPEC.read_text())
    spec["audio"]["loudness"]["standard"] = "ATSC A/85"
    sp = tmp_path / "spec_atsc.yaml"
    sp.write_text(yaml.safe_dump(spec))
    rc, fs = run(tmp_path, amedia["a_24_3"], spec=sp)
    (f,) = by(fs, "audio.loudness")
    assert f["severity"] == "PASS" and f["evidence"][0]["target_lufs"] == -24
    assert "ATSC A/85: I -24.3 LUFS" in f["message"]


def test_loudness_unknown_standard_is_configuration_error(amedia, tmp_path):
    spec = yaml.safe_load(SPEC.read_text())
    spec["audio"]["loudness"]["standard"] = "ARIB TR-B32"
    sp = tmp_path / "spec_arib.yaml"
    sp.write_text(yaml.safe_dump(spec))
    rc, fs = run(tmp_path, amedia["a_24_3"], spec=sp)
    (f,) = by(fs, "audio.loudness")
    assert rc == 3 and f["severity"] == "ERROR"
    assert f["message"].startswith("configuration error: standard 'ARIB TR-B32' has no preset")


def test_head_tail_excluded(amedia, tmp_path):
    rc, fs = run(tmp_path, amedia["a_hot_head_tail"])
    assert {(f["check_id"], f["severity"]) for f in fs if f["severity"] != "PASS"} == set()
    (f,) = by(fs, "audio.loudness")
    assert abs(f["evidence"][0]["integrated_lufs"] + 23.0) < 0.1
    assert f["evidence"][0]["true_peak_dbtp"] < -22


def test_silence_run_located(amedia, tmp_path):
    rc, fs = run(tmp_path, amedia["a_gap"])                     # final: max_run_frames 12
    assert [f["severity"] for f in by(fs, "audio.silence")] == ["PASS"]
    prof = tmp_path / "strict.yaml"
    prof.write_text("profile_version: 1\nname: strict\nextends: final\n"
                    "checks:\n  audio.silence: {max_run_frames: 4}\n")
    rc, fs = run(tmp_path, amedia["a_gap"], profile=prof)
    sil = by(fs, "audio.silence")
    assert [(f["stream"], f["frame_start"], f["frame_end"], f["timecode_start"], f["timecode_end"])
            for f in sil] == [(1, 40, 47, "01:00:00:16", "01:00:00:23"), (2, 40, 47, "01:00:00:16", "01:00:00:23")]
    assert all(f["severity"] == "FAIL" and f["actual"] == 8 for f in sil)


def test_silent_channel(amedia, tmp_path):
    rc, fs = run(tmp_path, amedia["a_silent_ch"])
    sc = by(fs, "audio.silent_channel")
    assert [(f["stream"], f["candidate"]["evidence"][0]["channel"], f["severity"]) for f in sc] == \
        [(1, 2, "FAIL"), (2, 2, "FAIL")]
    assert sc[0]["timecode_start"] == "01:00:00:00" and sc[0]["frame_end"] == 71
    assert [f["severity"] for f in by(fs, "audio.silence")] == ["PASS"]   # channel 1 still has tone


def test_clipping_located(amedia, tmp_path):
    rc, fs = run(tmp_path, amedia["a_clip"])
    clip = by(fs, "audio.clipping")
    assert [(f["stream"], f["frame_start"], f["frame_end"], f["timecode_start"]) for f in clip] == \
        [(1, 50, 51, "01:00:01:02"), (2, 50, 51, "01:00:01:02")]
    assert all(f["severity"] == "FAIL" and f["actual"] > -0.1 for f in clip)
    tp = [f for f in by(fs, "audio.loudness") if "true peak above" in f["message"]]
    assert len(tp) == 1 and tp[0]["frame_start"] <= 50 <= tp[0]["frame_end"]
    # clipping severity is profile-set
    prof = tmp_path / "warn.yaml"
    prof.write_text("profile_version: 1\nname: w\nextends: final\nchecks:\n  audio.clipping: {severity: warn}\n")
    rc, fs = run(tmp_path, amedia["a_clip"], profile=prof)
    assert {f["severity"] for f in by(fs, "audio.clipping")} == {"WARN"}
