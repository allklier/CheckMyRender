"""job.yaml `input:` + spec `job.filename_regex` end to end on a generated picture-only clip
(`cmr check --job J` with no FILE): the highest conforming version is picked, names that do not
match the regex are NOTICEs (WARN, exit >= 1), a FILE argument wins, a mistyped name given
explicitly is a filename finding. Profile = a house review profile for picture-only renders
(audio content checks off, filename ON) on a spec with `audio: {tracks: []}`."""
import json
import os
import shutil
import tempfile
from pathlib import Path

import jsonschema
import pytest
import yaml

import make_media
from cmr.cli import main
from cmr.schemas import REPORT_SCHEMA

RX = r"^Color_6K_v(?P<version>\d{2})\.mov$"
SPEC = {"spec_version": 1, "job": {"title": "test colour render", "filename_regex": RX},
        "video": {"container": "mov", "codec": "prores", "codec_profile": "422 HQ",
                  "width": 320, "height": 180, "fps": "24000/1001", "scan": "progressive",
                  "pixel_format": "yuv422p10le", "aspect_ratio": "16:9", "pixel_aspect": 1.0,
                  "color": {"primaries": "bt709", "transfer": "bt709", "matrix": "bt709", "range": "tv"}},
        "timing": {"timecode_mode": "non-drop", "program_start_timecode": "00:00:00:00",
                   "program_duration_frames": 24, "head_layout": [{"type": "program"}],
                   "tail_layout": []},
        "audio": {"tracks": []}, "content": {"captions": {"required": False}}}
PROFILE = {"profile_version": 1, "name": "house_picture_review", "extends": "review",
           "checks": {**{c: {"enabled": False} for c in ("audio.format", "audio.loudness",
                                                         "audio.silence", "audio.silent_channel",
                                                         "audio.clipping")},
                      "filename": {"severity": "warn"}}}


@pytest.fixture(scope="module")
def clip():
    out = Path(os.environ.get("CMR_TEST_MEDIA", Path(tempfile.gettempdir()) / "cmr_test_media"))
    return make_media.make(out, "input_pick", filename="clip.mov", width=320, height=180,
                           slate=0, head_black=0, program=24, tail_black=0,
                           timecode="00:00:00:00", audio_tracks=0, captions=False)


def job(tmp: Path, clip: Path, *names) -> Path:
    for sub in ("jobs", "specs", "profiles", "renders"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)
    (tmp / "specs/spec.yaml").write_text(yaml.safe_dump(SPEC))
    (tmp / "profiles/house.yaml").write_text(yaml.safe_dump(PROFILE))
    for n in names:
        shutil.copy(clip, tmp / "renders" / n)
    j = tmp / "jobs/job.yaml"
    j.write_text(yaml.safe_dump({"job_version": 1, "spec": "../specs/spec.yaml",
                                 "profile": "../profiles/house.yaml", "output": "../reports",
                                 "job": {"id": "t5033"},
                                 "input": {"folder": "../renders", "pattern": "Color*.mov"}}))
    return j


def latest(tmp: Path) -> dict:
    rep = json.loads((tmp / "reports/cmr_report_latest.json").read_text())
    jsonschema.validate(rep, REPORT_SCHEMA)
    return rep


def fn(rep):
    return [f for f in rep["files"][0]["findings"] if f["check_id"] == "filename"]


def test_picks_latest_and_lists_non_conforming(clip, tmp_path, capsys):
    j = job(tmp_path, clip, "Color_6K_v03.mov", "Color_6K_v04.mov", "Color_6k_v05.mov",
            "Color_v06_6K.mov", "._Color_6K_v09.mov")
    rc = main(["check", "--job", str(j)])
    io = capsys.readouterr()
    rep = latest(tmp_path)
    assert rc == 1, io.out
    assert rep["files"][0]["path"].endswith("renders/Color_6K_v04.mov")
    assert rep["input"]["picked"] == "Color_6K_v04.mov" and rep["input"]["version"] == 4
    assert rep["input"]["folder"] == str((tmp_path / "renders").resolve())
    assert [n["file"] for n in rep["input"]["notices"]] == ["Color_6k_v05.mov", "Color_v06_6K.mov"]
    assert rep["summary"]["notices"] == 2 and rep["summary"]["worst"] == "WARN"
    assert [f["severity"] for f in fn(rep)] == ["PASS"]                # the picked name conforms
    assert rep["files"][0]["worst"] == "PASS"
    assert "INPUT Color_6K_v04.mov (version 4" in io.out
    assert "NOTICE  WARN Color_6k_v05.mov" in io.out and "NOTICE  WARN Color_v06_6K.mov" in io.out
    assert "input: Color_6K_v04.mov (version 4)" in io.err
    # a conforming v05 arrives: it takes over, the mistyped one is still listed
    shutil.copy(clip, tmp_path / "renders/Color_6K_v05.mov")
    assert main(["check", "--job", str(j)]) == 1
    rep = latest(tmp_path)
    assert rep["input"]["picked"] == "Color_6K_v05.mov" and rep["summary"]["notices"] == 2


def test_clean_folder_passes(clip, tmp_path):
    j = job(tmp_path, clip, "Color_6K_v03.mov", "Color_6K_v04.mov")
    assert main(["check", "--job", str(j)]) == 0
    rep = latest(tmp_path)
    assert rep["input"]["notices"] == [] and rep["summary"]["notices"] == 0


def test_file_argument_wins_and_mistyped_name_is_a_finding(clip, tmp_path):
    j = job(tmp_path, clip, "Color_6K_v03.mov", "Color_6k_v05.mov")
    assert main(["check", "--job", str(j), str(tmp_path / "renders/Color_6k_v05.mov")]) == 1
    rep = latest(tmp_path)
    assert "input" not in rep and rep["summary"]["notices"] == 0
    f, = [f for f in fn(rep) if f["severity"] != "PASS"]
    assert f["severity"] == "WARN" and f["message"] == \
        f"filename: expected a name matching '{RX}', got 'Color_6k_v05.mov'"


def test_no_conforming_file_exits_3(clip, tmp_path, capsys):
    j = job(tmp_path, clip, "Color_6k_v05.mov")
    assert main(["check", "--job", str(j)]) == 3
    assert "found: Color_6k_v05.mov" in capsys.readouterr().err
