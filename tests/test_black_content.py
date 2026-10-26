"""video.black_runs content-on-black detector on generated media (program = file frames 24..71,
max_black_run_frames 12 in final): a logo or credits on black is not a black run (INFO
`content_on_black` instead); a fade to black and true black still FAIL; the detector can be
switched off; fast mode gives the same result."""
import json
import os
import tempfile
from pathlib import Path

import pytest
import yaml

import make_media
from cmr.cli import main

ROOT = Path(__file__).resolve().parents[1]
SPEC = str(ROOT / "tests" / "media" / "spec_test.yaml")


def box(color, a, b, x="0", y="0", w="iw", h="ih"):
    return f"drawbox=x={x}:y={y}:w={w}:h={h}:color={color}:t=fill:enable='between(n,{a},{b})'"


BLACK = box("black", 30, 49)
LOGO = box("white", 30, 49, "(iw-200)/2", "(ih-100)/2", "200", "100")     # 0.96% of 1080p
# "scrolling credits": 3 thin (2 px) white lines, 400 px wide (0.12% of the frame), moving up
# 200 px every 4 frames (drawbox has no frame-number expression, so one box per position)
CREDITS = ",".join(box("white", 30 + 4 * k, 33 + 4 * k, "760", str(900 - 200 * k + 30 * line), "400", "2")
                   for k in range(5) for line in range(3))

CASES = {
    "bc_logo": BLACK + "," + LOGO,
    "bc_credits": BLACK + "," + CREDITS,
    # logo frames 30-44, then pure black 45-60 (16 frames > 12): split run
    "bc_logo_black": box("black", 30, 60) + "," + box("white", 30, 44, "(iw-200)/2", "(ih-100)/2",
                                                          "200", "100"),
    # fade to black over frames 30-39, black from 40 to the end of the program (71)
    "bc_fade": "fade=t=out:start_frame=30:nb_frames=10",
    "bc_black": BLACK,
}


@pytest.fixture(scope="module")
def media():
    out = Path(os.environ.get("CMR_TEST_MEDIA", Path(tempfile.gettempdir()) / "cmr_test_media"))
    return {n: make_media.make(out, n, vf_extra=vf, captions=False, audio_tracks=0)
            for n, vf in CASES.items()}


def profile(tmp_path, **params):
    prof = yaml.safe_load((ROOT / "profiles" / "final.yaml").read_text())
    prof["name"] = "final_bc"
    prof["checks"]["video.black_runs"].update(params)
    p = tmp_path / "final_bc.yaml"
    p.write_text(yaml.safe_dump(prof))
    return str(p)


def black_findings(tmp_path, f, prof="final"):
    main(["check", str(f), "--profile", prof, "--spec", SPEC, "--report", "json", "-o", str(tmp_path)])
    rep = json.loads((tmp_path / "cmr_report_latest.json").read_text())
    return [(x["severity"], x["frame_start"], x["frame_end"], (x["evidence"] or [{}])[0].get("kind"))
            for x in rep["files"][0]["findings"]
            if x["check_id"] == "video.black_runs" and x["severity"] != "PASS"]


@pytest.mark.parametrize("case", ["bc_logo", "bc_credits"])
def test_content_on_black_is_info_not_fail(media, tmp_path, case):
    assert black_findings(tmp_path, media[case]) == [("INFO", 30, 49, "content_on_black")]


def test_content_then_black_splits_run(media, tmp_path):
    assert sorted(black_findings(tmp_path, media["bc_logo_black"])) == [
        ("FAIL", 45, 60, None), ("INFO", 30, 60, "content_on_black")]


def test_fade_and_true_black_still_fail(media, tmp_path):
    (fade,) = black_findings(tmp_path / "f", media["bc_fade"])
    assert fade[0] == "FAIL" and fade[2] == 71 and 30 < fade[1] <= 40 and fade[3] is None
    assert black_findings(tmp_path / "b", media["bc_black"]) == [("FAIL", 30, 49, None)]


def test_detector_off_restores_old_behaviour(media, tmp_path):
    p = profile(tmp_path, content_on_black=False)
    assert black_findings(tmp_path, media["bc_logo"], p) == [("FAIL", 30, 49, None)]


def test_fast_mode_same_result(media, tmp_path):
    p = profile(tmp_path, quality="fast")
    assert black_findings(tmp_path / "l", media["bc_credits"], p) == [("INFO", 30, 49, "content_on_black")]
    assert black_findings(tmp_path / "b", media["bc_black"], p) == [("FAIL", 30, 49, None)]


def test_min_fraction_is_tunable(media, tmp_path):
    # credits cover ~0.12% of the frame: a 0.5% minimum no longer counts them as content
    p = profile(tmp_path, content_min_fraction=0.005)
    assert black_findings(tmp_path, media["bc_credits"], p) == [("FAIL", 30, 49, None)]
