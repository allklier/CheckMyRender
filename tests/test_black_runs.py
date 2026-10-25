"""video.black_runs on generated media with planted black runs (program = file frames 24..71)."""
import json
import os
import tempfile
from pathlib import Path

import pytest

import make_media
from cmr.cli import main

SPEC = str(Path(__file__).resolve().parents[1] / "tests" / "media" / "spec_test.yaml")


def box(color, a, b):
    return f"drawbox=x=0:y=0:w=iw:h=ih:color={color}:t=fill:enable='between(n,{a},{b})'"


# long run: 20 frames, short fade-like run: 5 frames (limit 12), black inside program edges
CASES = {
    "br_long": box("black", 30, 49),
    "br_short": box("black", 30, 34),
    "br_dark": box("0x303030", 30, 49),      # dark but not black: false-positive control
    "br_none": None,
}


@pytest.fixture(scope="module")
def media():
    out = Path(os.environ.get("CMR_TEST_MEDIA", Path(tempfile.gettempdir()) / "cmr_test_media"))
    return {n: make_media.make(out, n, vf_extra=vf, captions=False) for n, vf in CASES.items()}


def findings(tmp_path, f):
    main(["check", str(f), "--profile", "final", "--spec", SPEC, "--report", "json", "-o", str(tmp_path)])
    rep = json.loads((tmp_path / "cmr_report.json").read_text())
    return [x for x in rep["files"][0]["findings"] if x["check_id"] == "video.black_runs"]


def test_long_run_flagged_with_tc(media, tmp_path):
    f = [x for x in findings(tmp_path, media["br_long"]) if x["severity"] != "PASS"]
    assert len(f) == 1 and (f[0]["frame_start"], f[0]["frame_end"]) == (30, 49)
    assert f[0]["severity"] == "FAIL" and f[0]["timecode_start"] == "01:00:00:06"
    assert f[0]["actual"] == 20


@pytest.mark.parametrize("case", ["br_short", "br_dark", "br_none"])
def test_no_finding(media, tmp_path, case):
    assert all(x["severity"] == "PASS" for x in findings(tmp_path, media[case]))


def test_head_tail_black_ignored(media, tmp_path):
    # good-style file: head has 23 black, tail 24 black (both > 12) and must not be flagged
    assert all(x["severity"] == "PASS" for x in findings(tmp_path, media["br_none"]))


def test_run_touching_program_start_counts(tmp_path):
    out = Path(os.environ.get("CMR_TEST_MEDIA", Path(tempfile.gettempdir()) / "cmr_test_media"))
    f = make_media.make(out, "br_edge", vf_extra=box("black", 24, 39), captions=False)
    r = [x for x in findings(tmp_path, f) if x["severity"] != "PASS"]
    assert [(x["frame_start"], x["frame_end"]) for x in r] == [(24, 39)]   # clipped at program start
