"""Integration: generated media (tests/media/make_media.py) through `cmr check`.

Media goes to $CMR_TEST_MEDIA (default: <system tmp>/cmr_test_media), never the repo,
and is regenerated once per test session."""
import json
import os
import tempfile
from pathlib import Path

import jsonschema
import pytest

import make_media
from cmr.cli import main
from cmr.schemas import REPORT_SCHEMA

ROOT = Path(__file__).resolve().parents[1]
SPEC = str(ROOT / "tests" / "media" / "spec_test.yaml")


@pytest.fixture(scope="session")
def media():
    out = Path(os.environ.get("CMR_TEST_MEDIA", Path(tempfile.gettempdir()) / "cmr_test_media"))
    return {name: make_media.make(out, name, **opts) for name, opts in make_media.CASES.items()}


def run(tmp_path, files, profile="final", extra=()):
    rc = main(["check", *map(str, files), "--profile", profile, "--spec", SPEC,
               "--report", "json", "-o", str(tmp_path), *extra])
    rep = json.loads((tmp_path / "cmr_report.json").read_text())
    jsonschema.validate(rep, REPORT_SCHEMA)
    return rc, rep


def problems(rep, i=0):
    return {(f["check_id"], f["severity"]) for f in rep["files"][i]["findings"] if f["severity"] != "PASS"}


def test_good_file_passes(media, tmp_path):
    rc, rep = run(tmp_path, [media["good"]])
    assert rc == 0, problems(rep)
    assert problems(rep) == set()
    ids = {f["check_id"] for f in rep["files"][0]["findings"]}
    assert {"video.codec", "video.fps", "audio.layout", "filename", "timing.frame_count",
            "timing.layout", "timing.timecode_start", "captions.presence", "decode.errors"} <= ids
    tl = rep["files"][0]["timeline"]
    assert tl["start_timecode"] == "00:59:59:00" and tl["program_start"] == 24 and tl["program_end"] == 71


def test_wrong_metadata(media, tmp_path):
    rc, rep = run(tmp_path, [media["wrong_meta"]])
    assert rc == 2
    p = problems(rep)
    for cid in ("container.format", "video.codec", "video.resolution", "video.pixel_format",
                "video.fps", "video.scan", "audio.format", "audio.layout", "filename",
                "timing.timecode_start", "captions.presence"):
        assert (cid, "FAIL") in p, cid
    assert ("video.color", "WARN") in p           # untagged -> unknown_severity


def test_wrong_timecode_located(media, tmp_path):
    rc, rep = run(tmp_path, [media["wrong_tc"]])
    f = {x["check_id"]: x for x in rep["files"][0]["findings"]}
    assert rc == 2
    assert f["timing.timecode_start"]["severity"] == "FAIL"
    assert "off by +24 frames" in f["timing.timecode_start"]["message"]
    lay = f["timing.layout"]
    assert lay["severity"] == "FAIL" and lay["frame_start"] == 24 and lay["timecode_start"] == "01:00:01:00"


def test_short_program(media, tmp_path):
    rc, rep = run(tmp_path, [media["short"]])
    (f,) = [x for x in rep["files"][0]["findings"] if x["check_id"] == "timing.frame_count"]
    assert f["severity"] == "FAIL" and f["expected"] == 48 and f["actual"] == 40
    assert (f["timecode_start"], f["timecode_end"]) == ("01:00:00:00", "01:00:01:15")


def test_captions_and_profile_severity(media, tmp_path):
    rc, rep = run(tmp_path, [media["no_captions"]])
    assert rc == 2 and problems(rep) == {("captions.presence", "FAIL")}
    rc, rep = run(tmp_path, [media["no_captions"]], profile="review")
    assert rc == 0 and "captions.presence" not in {f["check_id"] for f in rep["files"][0]["findings"]}


def test_decode_errors(media, tmp_path):
    rc, rep = run(tmp_path, [media["corrupt"]])
    assert problems(rep) == {("decode.errors", "FAIL")}
    (f,) = [x for x in rep["files"][0]["findings"] if x["check_id"] == "decode.errors"]
    assert f["evidence"][0]["stderr"]


def test_batch_worst_and_fail_on(media, tmp_path):
    rc, rep = run(tmp_path, [media["good"], media["short"]])
    assert rc == 2 and [f["worst"] for f in rep["files"]] == ["PASS", "FAIL"]
    assert rep["summary"]["worst"] == "FAIL"


def test_config_error_exit_3(media, tmp_path):
    assert main(["check", str(media["good"]), "--profile", "nope", "--spec", SPEC]) == 3


def test_probe_cli(media, capsys):
    assert main(["probe", str(media["good"])]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["video"][0]["codec_profile"] == "422 HQ" and d["timecode"] == "00:59:59:00"


def test_probe_reads_only_a_few_video_frames(media):
    """Regression: unbounded -show_frames on ffmpeg 5.1 printed GBs and held ~60 GB of RAM."""
    from cmr.media import ffprobe
    raw = ffprobe(Path(media["good"]))
    assert 0 < len(raw["frames"]) <= 10
    assert {f["stream_index"] for f in raw["frames"]} <= {
        s["index"] for s in raw["streams"] if s["codec_type"] == "video"}
