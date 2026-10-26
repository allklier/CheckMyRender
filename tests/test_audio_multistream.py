"""Integration: loudness of merged mono streams (5.1 as 6 mono streams + a stereo mix as 2,
PBS-style; LFE not measured) and declared intended silence (allowed_silence), in .mov and .mxf.
Program = file frames 24..71 = samples 48048..144143 (2002 per frame), 01:00:00:00 at frame 24.

Analytic levels (BS.1770, ebur128 measured to agree): 1 kHz sine at P dBFS peak on one channel
of weight 1 = P - 3.01 LUFS; Ls/Rs weigh 1.41. Tone on L R C Ls Rs: P - 3.01 + 10*log10(3 + 2*1.41)
= P + 4.64 LUFS. The LFE carries a 60 Hz tone at -1 dBFS: it must change neither the loudness
nor the true peak."""
import copy
import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

import make_media_audio as mma
from cmr.cli import main

ROOT = Path(__file__).resolve().parents[1]
SPEC = yaml.safe_load((ROOT / "tests" / "media" / "spec_test.yaml").read_text())
P51 = -28.64                                    # -> -24.0 LUFS on the 5.1 (analytic)
LFE = "sine=f=60:r=48000,volume=17.0618dB"      # -1 dBFS peak
FULL51 = [mma.tone(1000, P51)] * 3 + [LFE] + [mma.tone(1000, P51)] * 2
STEREO = [mma.tone(1000, -24.0)] * 2            # -24.0 LUFS (stereo 1 kHz at P = P LUFS)
PROGRAMS = [{"name": "5.1", "streams": [0, 1, 2, 3, 4, 5], "channels": ["L", "R", "C", "LFE", "Ls", "Rs"]},
            {"name": "stereo", "streams": [6, 7]}]
# streams 2-5 silent for the first 24 program frames (01:00:00:00-01:00:00:23), tone after
LATE = "sine=f=1000:r=48000,volume=-10dB,asetnsamples=n=2002,volume=0:enable='lt(n,48)'"


def out_dir():
    return Path(os.environ.get("CMR_TEST_MEDIA", Path(tempfile.gettempdir()) / "cmr_test_media"))


@pytest.fixture(scope="module", params=["mov", "mxf"])
def master(request):
    c = request.param
    return c, mma.make_multi(out_dir(), f"ms_51_{c}", FULL51 + STEREO, container=c)


@pytest.fixture(scope="module")
def stereo_only():
    tone = mma.tone(1000, -24.0)
    return mma.make_multi(out_dir(), "ms_stereo_only", [tone, tone] + [mma.SILENT] * 4 + [tone, tone])


@pytest.fixture(scope="module")
def late_surround():
    tone = mma.tone(1000, -24.0)
    return mma.make_multi(out_dir(), "ms_late", [tone, tone] + [LATE] * 4 + [tone, tone])


def spec_file(tmp_path, **audio):
    s = copy.deepcopy(SPEC)
    s["audio"].pop("programs", None)            # this module uses the older spelling
    s["audio"]["loudness"] = {"standard": "ATSC A/85", "programs": PROGRAMS}
    s["audio"].update(audio)
    p = tmp_path / "spec.yaml"
    p.write_text(yaml.safe_dump(s))
    return p


def run(tmp_path, f, spec):
    main(["check", str(f), "--profile", "final", "--spec", str(spec), "--report", "json",
          "-o", str(tmp_path), "--max-findings", "0"])
    rep = json.loads((tmp_path / "cmr_report_latest.json").read_text())
    return [x for x in rep["files"][0]["findings"] if x["check_id"].startswith("audio.")]


def r128_independent(f, streams, layout, names):
    """ffmpeg CLI ebur128 summary on the program samples of the given streams, merged with `join`
    (not the engine's amerge + pan) -> (I LUFS, true peak dBFS)."""
    ins = "".join(f"[0:{s}]" for s in streams)
    m = "|".join(f"{i}.0-{n}" for i, n in enumerate(names))
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(f), "-filter_complex",
                        f"{ins}join=inputs={len(streams)}:channel_layout={layout}:map={m},"
                        "atrim=start_sample=48048:end_sample=144144,ebur128=peak=true[o]",
                        "-map", "[o]", "-f", "null", "-"], capture_output=True, text=True, check=True)
    summary = p.stderr[p.stderr.rindex("Summary:"):]
    return (float(re.search(r"I:\s+(-?[\d.]+) LUFS", summary).group(1)),
            float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary).group(1)))


def loudness(findings):
    return {x["evidence"][0].get("program"): x for x in findings if x["check_id"] == "audio.loudness"
            and x["evidence"][0].get("kind") != "undeclared_programs"}


def test_51_and_stereo_programs_measured(master, tmp_path):
    c, f = master
    ld = loudness(run(tmp_path, f, spec_file(tmp_path)))
    m51, st = ld["5.1"]["evidence"][0], ld["stereo"]["evidence"][0]
    assert ld["5.1"]["severity"] == ld["stereo"]["severity"] == "PASS"
    assert abs(m51["integrated_lufs"] - (P51 - 3.0103 + 10 * math.log10(3 + 2 * 1.41))) < 0.15
    assert m51["measured_channels"] == ["L", "R", "C", "Ls", "Rs"] and m51["streams"] == [1, 2, 3, 4, 5, 6]
    assert abs(m51["true_peak_dbtp"] - P51) < 0.3                  # LFE (-1 dBFS) not in the peak
    assert abs(st["integrated_lufs"] - -24.0) < 0.1 and ld["stereo"]["stream"] == 7
    # independent: ffmpeg join + ebur128 on the same streams, LFE left out
    i_ind, tp_ind = r128_independent(f, [1, 2, 3, 5, 6], "5.0(side)", ["FL", "FR", "FC", "SL", "SR"])
    assert abs(m51["integrated_lufs"] - i_ind) <= 0.1 and abs(m51["true_peak_dbtp"] - tp_ind) <= 0.1
    # with the LFE in a 5.1 layout ebur128 keeps I (weight 0) but its true peak sees the LFE
    i_lfe, tp_lfe = r128_independent(f, [1, 2, 3, 4, 5, 6], "5.1(side)",
                                     ["FL", "FR", "FC", "LFE", "SL", "SR"])
    assert abs(i_lfe - i_ind) <= 0.1 and tp_lfe > -2


def test_program_track_still_measures_one_stream(master, tmp_path):
    _, f = master
    (x,) = loudness(run(tmp_path, f, spec_file(tmp_path, loudness={"standard": "ATSC A/85",
                                                                    "program_track": 6}))).values()
    assert x["stream"] == 7 and abs(x["evidence"][0]["integrated_lufs"] - -27.0) < 0.1   # one channel


def problems(findings, cid):
    return [x for x in findings if x["check_id"] == cid and x["severity"] not in ("PASS", "INFO")]


def info(findings, cid):
    return [x for x in findings if x["check_id"] == cid and x["severity"] == "INFO"]


NO_PROGRAMS = {"standard": "ATSC A/85", "program_track": 0}


def test_undeclared_silent_streams_fail(stereo_only, tmp_path):
    # programs declared: judged per program; C Ls Rs dead for the whole program, LFE not judged,
    # the 5.1 as a whole (L R carry sound) is not silent
    f = run(tmp_path / "p", stereo_only, spec_file(tmp_path))
    assert sorted(x["stream"] for x in problems(f, "audio.silent_channel")) == [3, 5, 6]
    assert problems(f, "audio.silence") == []
    # no programs (backward compatible): every mono stream on its own
    f = run(tmp_path / "n", stereo_only, spec_file(tmp_path, loudness=NO_PROGRAMS))
    assert sorted(x["stream"] for x in problems(f, "audio.silent_channel")) == [3, 4, 5, 6]
    assert sorted(x["stream"] for x in problems(f, "audio.silence")) == [3, 4, 5, 6]


def test_declared_silent_streams_are_info(stereo_only, tmp_path):
    spec = spec_file(tmp_path, allowed_silence=[{"stream": s} for s in (2, 3, 4, 5)])
    f = run(tmp_path, stereo_only, spec)
    assert problems(f, "audio.silent_channel") == [] and problems(f, "audio.silence") == []
    assert sorted(x["stream"] for x in info(f, "audio.silent_channel")) == [3, 5, 6]   # LFE not judged
    assert info(f, "audio.silence") == []           # per program: the 5.1 was never silent
    f = run(tmp_path / "n", stereo_only, spec_file(tmp_path, loudness=NO_PROGRAMS,
                                                   allowed_silence=[{"stream": s} for s in (2, 3, 4, 5)]))
    assert problems(f, "audio.silent_channel") == [] and problems(f, "audio.silence") == []
    assert all(x["evidence"][0]["kind"] == "declared_silence" for x in info(f, "audio.silence"))
    assert len(info(f, "audio.silence")) == 4


def test_declared_time_range(late_surround, tmp_path):
    # per program: surrounds silent while L R carry sound is no program silence
    f = run(tmp_path / "p", late_surround, spec_file(tmp_path))
    assert problems(f, "audio.silence") == [] and problems(f, "audio.silent_channel") == []
    # per stream (no programs, backward compatible): the declarations below matter
    f = run(tmp_path / "u", late_surround, spec_file(tmp_path, loudness=NO_PROGRAMS))
    assert sorted((x["stream"], x["frame_start"], x["frame_end"]) for x in problems(f, "audio.silence")) == \
        [(s, 24, 47) for s in (3, 4, 5, 6)]
    assert problems(f, "audio.silent_channel") == []
    spec = spec_file(tmp_path, loudness=NO_PROGRAMS,
                     allowed_silence=[{"stream": s, "channels": [1], "start": "01:00:00:00",
                                       "end": "01:00:00:23"} for s in (2, 3, 4, 5)])
    f = run(tmp_path / "d", late_surround, spec)
    assert problems(f, "audio.silence") == [] and len(info(f, "audio.silence")) == 4
    # a range that ends too early leaves the rest of the gap reported
    spec = spec_file(tmp_path, loudness=NO_PROGRAMS,
                     allowed_silence=[{"stream": 2, "start": "01:00:00:00", "end": "01:00:00:05"}])
    f = run(tmp_path / "e", late_surround, spec)
    assert (3, 30, 47) in [(x["stream"], x["frame_start"], x["frame_end"]) for x in problems(f, "audio.silence")]


def test_channel_count_mismatch_is_config_error(master, tmp_path):
    _, f = master
    s = copy.deepcopy(SPEC)
    s["audio"].pop("programs", None)
    s["audio"]["loudness"] = {"standard": "ATSC A/85",
                              "programs": [{"streams": [0, 1, 2], "channels": ["L", "R"]}]}
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(s))
    (x,) = [y for y in run(tmp_path, f, p) if y["check_id"] == "audio.loudness"]
    assert x["severity"] == "ERROR" and "configuration error" in x["message"]
