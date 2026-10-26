"""Integration: audio.silence / silent_channel judged per PROGRAM on 8 mono streams (5.1 on
streams 0-5 = L R C LFE Ls Rs, stereo mix on 6-7; PBS-style). File streams: 0 video, 1-8 audio.
Program = file frames 24..71 (01:00:00:00 = frame 24, 23.976 NDF, 24 fps nominal TC)."""
import copy
import json
import os
import tempfile
from pathlib import Path

import pytest
import yaml

import make_media_audio as mma
from cmr.cli import main

ROOT = Path(__file__).resolve().parents[1]
SPEC = yaml.safe_load((ROOT / "tests" / "media" / "spec_test.yaml").read_text())
PROGRAMS = [{"name": "5.1", "streams": [0, 1, 2, 3, 4, 5], "channels": ["L", "R", "C", "LFE", "Ls", "Rs"]},
            {"name": "stereo", "streams": [6, 7]}]
T = mma.tone(1000, -24.0)
LFE = mma.tone(60, -20.0)


def out_dir():
    return Path(os.environ.get("CMR_TEST_MEDIA", Path(tempfile.gettempdir()) / "cmr_test_media"))


def media(name, streams):
    return mma.make_multi(out_dir(), f"ps_{name}", streams)


@pytest.fixture(scope="module")
def surround_gaps():          # C LFE Ls Rs silent on 24-45 and 55-71; L R and stereo carry sound
    g = mma.gated(1000, -24.0, [(24, 45), (55, 71)])
    return media("surround_gaps", [T, T, g, mma.gated(60, -20.0, [(24, 45), (55, 71)]), g, g, T, T])


@pytest.fixture(scope="module")
def all_silent():             # the whole 5.1 silent on 40-59 (20 frames > 12); stereo carries sound
    return media("all_silent", [mma.gated(1000, -24.0, [(40, 59)])] * 3 + [mma.gated(60, -20.0, [(40, 59)])]
                 + [mma.gated(1000, -24.0, [(40, 59)])] * 2 + [T, T])


@pytest.fixture(scope="module")
def rs_dead():                # Rs silent for the whole file
    return media("rs_dead", [T, T, T, LFE, T, mma.SILENT, T, T])


@pytest.fixture(scope="module")
def head_tail():              # stereo-only head/tail: ch3-6 silent on the first/last 10 program frames
    g = mma.gated(1000, -24.0, [(24, 33), (62, 71)])
    return media("head_tail", [T, T, g, mma.gated(60, -20.0, [(24, 33), (62, 71)]), g, g, T, T])


def spec_file(tmp_path, programs=PROGRAMS, track=True, **audio):
    s = copy.deepcopy(SPEC)
    s["audio"].pop("programs", None)
    s["audio"]["loudness"] = {"standard": "ATSC A/85", **({"program_track": 0} if track else {})}
    if programs:
        s["audio"]["programs"] = programs
        s["audio"]["loudness"] = {"standard": "ATSC A/85"}
    s["audio"].update(audio)
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "spec.yaml"
    p.write_text(yaml.safe_dump(s))
    return p


def run(tmp_path, f, spec, profile="final"):
    main(["check", str(f), "--profile", str(profile), "--spec", str(spec), "--report", "json",
          "-o", str(tmp_path), "--max-findings", "0"])
    rep = json.loads((tmp_path / "cmr_report_latest.json").read_text())
    return [x for x in rep["files"][0]["findings"] if x["check_id"].startswith("audio.")]


def problems(findings, cid):
    return [x for x in findings if x["check_id"] == cid and x["severity"] not in ("PASS", "INFO")]


def info(findings, cid):
    return [x for x in findings if x["check_id"] == cid and x["severity"] == "INFO"]


def test_surround_gaps_pass_per_program(surround_gaps, tmp_path):
    f = run(tmp_path / "p", surround_gaps, spec_file(tmp_path / "p"))
    assert problems(f, "audio.silence") == [] and problems(f, "audio.silent_channel") == []
    assert {x["evidence"][0].get("program") for x in f if x["check_id"] == "audio.loudness"} == {"5.1", "stereo"}
    # backward compatible: no programs -> per mono stream, as before, plus the loudness notice
    f = run(tmp_path / "n", surround_gaps, spec_file(tmp_path / "n", programs=None))
    assert sorted((x["stream"], x["frame_start"], x["frame_end"]) for x in problems(f, "audio.silence")) == \
        [(s, a, b) for s in (3, 4, 5, 6) for a, b in ((24, 45), (55, 71))]
    # an explicit program_track declares the program: no notice
    assert [x for x in f if x["evidence"] and x["evidence"][0].get("kind") == "undeclared_programs"] == []
    # neither programs nor program_track: the notice
    f = run(tmp_path / "u", surround_gaps, spec_file(tmp_path / "u", programs=None, track=False))
    (n,) = [x for x in f if x["evidence"] and x["evidence"][0].get("kind") == "undeclared_programs"]
    assert n["severity"] == "WARN" and n["stream"] == 1


def test_all_channels_silent_fails_once(all_silent, tmp_path):
    f = run(tmp_path, all_silent, spec_file(tmp_path))
    (x,) = problems(f, "audio.silence")
    assert (x["severity"], x["stream"], x["frame_start"], x["frame_end"]) == ("FAIL", 1, 40, 59)
    assert x["message"].startswith("program 5.1 (L(1) R(2) C(3) Ls(5) Rs(6)): silence 20 frames")
    assert x["timecode_start"] == "01:00:00:16" and problems(f, "audio.silent_channel") == []


def test_declared_program_silence_is_info(all_silent, tmp_path):
    allowed = [{"stream": s, "start": "01:00:00:16", "end": "01:00:01:11"} for s in range(6)]
    f = run(tmp_path, all_silent, spec_file(tmp_path, allowed_silence=allowed))
    assert problems(f, "audio.silence") == []
    (x,) = info(f, "audio.silence")
    assert x["evidence"][0]["kind"] == "declared_silence" and x["evidence"][0]["program"] == "5.1"


def test_channel_silent_whole_program(rs_dead, tmp_path):
    f = run(tmp_path, rs_dead, spec_file(tmp_path))
    (x,) = problems(f, "audio.silent_channel")
    assert x["stream"] == 6 and x["message"].startswith("program 5.1 channel Rs (stream 6)")
    assert problems(f, "audio.silence") == []


def test_stereo_only_head_tail_passes_without_declarations(head_tail, tmp_path):
    f = run(tmp_path, head_tail, spec_file(tmp_path))
    assert problems(f, "audio.silence") == [] and problems(f, "audio.silent_channel") == []
    assert info(f, "audio.silence") == [] and problems(f, "audio.clipping") == []


def tier_profile(tmp_path, **params):
    p = tmp_path / "tier.yaml"
    p.write_text(yaml.safe_dump({"profile_version": 1, "name": "tier", "extends": "final",
                                 "checks": {"audio.silence": params}}))
    return p


@pytest.mark.parametrize("params,sev,tier", [
    ({"max_run_frames": 30, "warn_run_frames": 6}, "WARN", "warn"),     # 20 frames: between
    ({"max_run_frames": 15, "warn_run_frames": 6}, "FAIL", "limit"),    # above max
    ({"max_run_frames": 12}, "FAIL", None),                             # single tier unchanged
    ({"max_run_frames": 12, "warn_run_frames": 12}, "FAIL", None),      # warn >= max: single tier
    ({"max_run_frames": 40, "warn_run_frames": 20}, None, None)])       # not above warn: nothing
def test_two_tier_silence_per_program(all_silent, tmp_path, params, sev, tier):
    prof = tier_profile(tmp_path, **params)
    f = problems(run(tmp_path, all_silent, spec_file(tmp_path), prof), "audio.silence")
    if sev is None:
        assert f == []
        return
    (x,) = f
    assert x["severity"] == sev and x["stream"] == 1 and (x["frame_start"], x["frame_end"]) == (40, 59)
    assert (x["evidence"] or [{}])[0].get("tier") == tier
    assert x["message"].startswith("program 5.1")


def test_two_tier_silence_per_stream_without_programs(surround_gaps, tmp_path):
    prof = tier_profile(tmp_path, warn_run_frames=18, max_run_frames=20)   # runs 22 (24-45) and 17 (55-71)
    f = problems(run(tmp_path, surround_gaps, spec_file(tmp_path, programs=None), prof), "audio.silence")
    got = sorted((x["stream"], x["frame_start"], x["severity"]) for x in f)
    assert got == [(s, 24, "FAIL") for s in (3, 4, 5, 6)]     # 17 frames <= warn 18: nothing
