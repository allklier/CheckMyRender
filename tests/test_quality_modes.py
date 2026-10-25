"""Per-check `quality: fast | high` (video.blanking, video.black_runs) and its plumbing.

Media goes to $CMR_TEST_MEDIA (default <tmp>/cmr_test_media), never the repo."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

import jsonschema
import pytest
import yaml

import make_media
from cmr import engine
from cmr.checks.blanking import detector as d
from cmr.cli import main
from cmr.config import ConfigError, load_profile_raw
from cmr.schemas import REPORT_SCHEMA

ROOT = Path(__file__).resolve().parents[1]
SPEC = str(ROOT / "tests" / "media" / "spec_test.yaml")
OUT = Path(os.environ.get("CMR_TEST_MEDIA", Path(tempfile.gettempdir()) / "cmr_test_media"))

# --- blanking: planted 1px left-column defects, step 5, phase = start % 5 ----------------
FIVE = [(10, 14), (21, 25), (32, 36), (43, 47), (54, 58)]           # phases 0..4, 5 frames
FOUR = [(70, 73), (81, 84), (92, 95), (103, 106), (114, 117)]       # phases 0..4, 4 frames
LONG = [(130, 141)]                                                 # 12 frames
DEFECTS = FIVE + FOUR + LONG
# fast (every 5th file frame): each 5-frame defect holds exactly one multiple of 5;
# the 4-frame defect at phase 1 (81..84) holds none -> missed.
FAST_SEEN = {(10, 14): (10, 10), (21, 25): (25, 25), (32, 36): (35, 35), (43, 47): (45, 45),
             (54, 58): (55, 55), (70, 73): (70, 70), (92, 95): (95, 95),
             (103, 106): (105, 105), (114, 117): (115, 115), (130, 141): (130, 140)}


@pytest.fixture(scope="module")
def blank_clip():
    clip = OUT / "q_blanking" / "phases.mov"
    clip.parent.mkdir(parents=True, exist_ok=True)
    boxes = "".join(f",drawbox=x=0:y=0:w=1:h=ih:c=black:t=fill:enable='between(n,{a},{b})'"
                    for a, b in DEFECTS)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=320x240:r=24,format=yuv422p" + boxes, "-frames:v", "150",
                    "-c:v", "prores_ks", "-pix_fmt", "yuv422p10le", str(clip)], check=True)
    return clip


def blanking(clip, **cfg):
    res = engine.run_file(clip, {}, {"checks": {"video.blanking": cfg}})
    return [f for f in res["findings"] if f.check_id == "video.blanking"]


def test_blanking_high_finds_every_defect_frame_exact(blank_clip):
    fs = blanking(blank_clip, quality="high")
    assert sorted((f.frame_start, f.frame_end) for f in fs) == DEFECTS
    assert all(f.quality == "high" and "frame_step" not in f.evidence[0] for f in fs)


def test_blanking_fast_5_and_4_frame_defects_by_phase(blank_clip):
    fs = blanking(blank_clip, quality="fast")
    got = sorted((f.frame_start, f.frame_end) for f in fs)
    assert got == sorted(FAST_SEEN.values())          # 4-frame defect at phase 1 missed
    for (a, b), (s, e) in FAST_SEEN.items():
        (f,) = [f for f in fs if (f.frame_start, f.frame_end) == (s, e)]
        ev = f.evidence[0]
        assert f.quality == "fast" and ev["frame_step"] == 5 and "frames" not in ev
        assert ev["boundary_uncertainty_frames"] == 4
        # the stated possible extent always contains the true defect
        assert ev["possible_frame_start"] <= a and b <= ev["possible_frame_end"]
        assert "sampled frame(s)" in f.message and "up to 4 frame(s)" in f.message
    assert [f.evidence[0]["frames_sampled"] for f in fs if f.frame_start == 130] == [3]


def test_blanking_fast_frame_step_param(blank_clip):
    fs = blanking(blank_clip, quality="fast", fast_frame_step=2)
    assert len(fs) == len(DEFECTS)                    # every defect >= 2 frames is seen
    assert all(f.frame_start % 2 == 0 and f.evidence[0]["frame_step"] == 2 for f in fs)


def test_sampled_run_joining_and_min_run_frames():
    pic, bad = (0.0, 0.0, 200.0), (1.0, 0.0, 200.0)
    frames = range(0, 100, 5)                         # what fast mode hands detect()
    stats = {e: {f: pic for f in frames} for e in d.EDGES}
    stats["top"].update({20: bad, 25: bad, 30: bad, 60: bad})
    c = d.detect(stats, frame_step=5)
    assert [(x.frame_start, x.frame_end, x.evidence[0]["frames_sampled"],
             x.evidence[0]["possible_frame_start"], x.evidence[0]["possible_frame_end"])
            for x in c] == [(20, 30, 3, 16, 34), (60, 60, 1, 56, 64)]
    # min_run_frames compares with the largest possible extent (9 frames for 1 sample)
    assert len(d.detect(stats, {"min_run_frames": 9}, frame_step=5)) == 2
    assert len(d.detect(stats, {"min_run_frames": 10}, frame_step=5)) == 1
    assert d.chains(None, 5)["top"].startswith("select='not(mod(n,5))',crop=")
    assert not d.chains(None)["top"].startswith("select")


# --- black_runs fast / high ------------------------------------------------------------
def box(color, a, b):
    return f"drawbox=x=0:y=0:w=iw:h=ih:color={color}:t=fill:enable='between(n,{a},{b})'"


@pytest.fixture(scope="module")
def black_media():
    cases = {"q_br_long": box("black", 30, 49), "q_br_dark": box("0x303030", 30, 49),
             "q_br_none": None}
    return {n: make_media.make(OUT, n, vf_extra=vf, captions=False) for n, vf in cases.items()}


def check(tmp_path, f, profile):
    rc = main(["check", str(f), "--profile", str(profile), "--spec", SPEC,
               "--report", "json", "-o", str(tmp_path)])
    rep = json.loads((tmp_path / "cmr_report.json").read_text())
    jsonschema.validate(rep, REPORT_SCHEMA)
    return rc, rep["files"][0]["findings"]


def profile(tmp_path, quality):
    p = tmp_path / f"q_{quality}.yaml"
    p.write_text(yaml.safe_dump({"profile_version": 1, "name": f"q_{quality}", "extends": "final",
                                 "checks": {"video.black_runs": {"quality": quality}}}))
    return p


@pytest.mark.parametrize("quality", ["fast", "high"])
def test_black_runs_modes_same_runs(black_media, tmp_path, quality):
    p = profile(tmp_path, quality)
    _, fs = check(tmp_path, black_media["q_br_long"], p)
    (f,) = [x for x in fs if x["check_id"] == "video.black_runs"]
    assert (f["frame_start"], f["frame_end"], f["actual"]) == (30, 49, 20)
    assert f["quality"] == quality
    for case in ("q_br_dark", "q_br_none"):           # dark-not-black + head/tail black ignored
        _, fs = check(tmp_path, black_media[case], p)
        (f,) = [x for x in fs if x["check_id"] == "video.black_runs"]
        assert f["severity"] == "PASS" and f["quality"] == quality


def test_report_and_console_record_quality(black_media, tmp_path, capsys):
    _, fs = check(tmp_path, black_media["q_br_none"], "review")
    q = {x["check_id"]: x["quality"] for x in fs}
    assert q["video.blanking"] == q["video.black_runs"] == "fast"
    assert q["video.codec"] == "high"
    main(["check", str(black_media["q_br_none"]), "--profile", "review", "--spec", SPEC, "-v"])
    out = capsys.readouterr().out
    assert "video.blanking [fast mode]:" in out and "video.codec:" in out


# --- plumbing / config -----------------------------------------------------------------
def test_quality_on_check_without_fast_mode_is_error(black_media, tmp_path):
    res = engine.run_file(black_media["q_br_none"], {}, {"checks": {"decode.errors": {"quality": "fast"}}})
    (f,) = res["findings"]
    assert f.severity.name == "ERROR" and "quality 'fast' not implemented" in f.message


def test_profile_schema_rejects_unknown_quality(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(yaml.safe_dump({"profile_version": 1, "name": "p",
                                 "checks": {"video.blanking": {"quality": "medium"}}}))
    with pytest.raises(ConfigError, match="invalid"):
        load_profile_raw(str(p))


def test_profiles_show_quality(capsys):
    shown = {}
    for name in ("final", "review", "release"):
        assert main(["profiles", "show", name]) == 0
        shown[name] = yaml.safe_load(capsys.readouterr().out)["checks"]
    q = {n: (c["video.blanking"]["quality"], c["video.black_runs"]["quality"]) for n, c in shown.items()}
    assert q == {"final": ("high", "high"), "review": ("fast", "fast"), "release": ("high", "high")}
    assert "quality" not in shown["final"]["video.codec"]
    assert not (ROOT / "profiles" / "internal_review.yaml").exists()
