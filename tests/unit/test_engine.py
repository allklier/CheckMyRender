"""Engine mechanics with throwaway checks on a tiny lavfi file (real ffmpeg)."""
import math
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from cmr import engine
from cmr.engine import Check, Skip, Tap, build_graph, parse_metadata_file
from cmr.model import Candidate, Severity

SPEC = {"spec_version": 1, "timing": {"program_start_timecode": "01:00:00:00",
                                      "head_layout": [{"type": "black", "frames": 2}, {"type": "program"}],
                                      "tail_layout": [{"type": "black", "frames": 2}]}}


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    p = tmp_path_factory.mktemp("tiny") / "tiny.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=64x48:r=25",
                    "-f", "lavfi", "-i", "sine=r=48000", "-t", "0.4",   # no -frames:v: cuts audio on 5.1
                    "-c:v", "prores_ks", "-pix_fmt", "yuv422p10le", "-c:a", "pcm_s16le",
                    "-timecode", "00:59:59:23", str(p)], check=True)
    return p


@pytest.fixture
def fake_checks():
    added = []

    def add(cls):
        engine.register(cls)
        added.append(cls.id)
        return cls
    yield add
    for cid in added:
        engine.REGISTRY.pop(cid, None)
        engine.VERIFIERS.pop(cid, None)


def test_parse_metadata_file():
    text = "frame:0    pts:0       pts_time:0\nlavfi.a=1\nlavfi.b=x=y\nframe:1    pts:512     pts_time:0.04\nlavfi.a=2\n"
    assert parse_metadata_file(text) == [("0", "0", {"lavfi.a": "1", "lavfi.b": "x=y"}),
                                         ("512", "0.04", {"lavfi.a": "2"})]


def test_build_graph_splits_shared_streams(tmp_path):
    taps = [("c/a", Tap("a", 0, "signalstats"), "c"), ("c/b", Tap("b", 0, "crop=iw:1:0:0"), "c"),
            ("d/x", Tap("x", 1, "astats=metadata=1"), "d")]
    graph, labels, files = build_graph(taps, {0: "video", 1: "audio"}, tmp_path)
    assert graph.startswith("[0:0]split=2[s0_0][s0_1];[s0_0]signalstats,metadata=mode=print:file=")
    assert "[0:1]astats=metadata=1,ametadata=mode=print:file=" in graph
    assert labels == ["[o0]", "[o1]", "[o2]"] and set(files) == {"c/a", "c/b", "d/x"}


def test_single_pass_records_verifier_and_timecodes(tiny, fake_checks):
    @fake_checks
    class Luma(Check):
        id = "test.luma"
        requires_filters = ("signalstats", "astats")

        def taps(self, ctx):
            return [Tap("full", ctx.media.video[0]["index"], "signalstats"),
                    Tap("edge", ctx.media.video[0]["index"], "crop=iw:1:0:0:exact=1,signalstats"),
                    Tap("aud", ctx.media.audio[0]["index"], "asetnsamples=n=1920,astats=metadata=1:reset=1")]

        def detect(self, ctx, records):
            assert [r.frame for r in records["full"]] == list(range(10))
            assert len(records["edge"]) == 10
            assert "lavfi.signalstats.YAVG" in records["full"][0].values
            # 1920 samples = one 25p frame
            assert [r.frame for r in records["aud"]] == list(range(10))
            return [Candidate(self.id, 0, 3, 5, 0.9)]

        def evaluate(self, ctx, cands):
            return [ctx.finding("cand", frame_start=c.frame_start, frame_end=c.frame_end, candidate=c)
                    for c in cands]

    engine.VERIFIERS["test.luma"] = lambda c, ctx: {"decision": "dismiss", "p": 0.1,
                                                    "model_hash": "none", "severity": "error"}
    res = engine.run_file(tiny, SPEC, {"checks": {"test.luma": {"severity": "warn"}}})
    (f,) = res["findings"]
    assert f.severity == Severity.WARN            # verifier asked for ERROR: not raised
    assert f.timecode_start == "01:00:00:01" and f.timecode_end == "01:00:00:03"
    engine.VERIFIERS["test.luma"] = lambda c, ctx: {"decision": "dismiss", "p": 0.1,
                                                    "model_hash": "none", "severity": "info"}
    (f,) = engine.run_file(tiny, SPEC, {"checks": {"test.luma": {"severity": "warn"}}})["findings"]
    assert f.severity == Severity.INFO and f.raw_severity == Severity.WARN
    assert res["timeline"]["program_start"] == 2 and res["timeline"]["program_end"] == 7


def test_pass_has_frame_counter_and_status_paths(tiny, fake_checks):
    @fake_checks
    class Dec(Check):
        id = "test.dec"
        needs = frozenset({"probe", "decode"})

        def evaluate(self, ctx, c):
            assert ctx.passinfo.ran and ctx.passinfo.decoded_frames == 10
            return []

    @fake_checks
    class Sk(Check):
        id = "test.skip"

        def evaluate(self, ctx, c):
            raise Skip("nothing to do")

    @fake_checks
    class Boom(Check):
        id = "test.boom"

        def evaluate(self, ctx, c):
            raise ZeroDivisionError("x")

    @fake_checks
    class NoFilt(Check):
        id = "test.nofilter"
        requires_filters = ("no_such_filter_xyz",)

    prof = {"checks": {"test.dec": {}, "test.skip": {}, "test.boom": {}, "test.nofilter": {},
                       "test.unknown": {}, "test.off": {"enabled": False}}}
    sev = {f.check_id: f.severity for f in engine.run_file(tiny, SPEC, prof)["findings"]}
    assert sev == {"test.dec": Severity.PASS, "test.skip": Severity.SKIPPED,
                   "test.boom": Severity.ERROR, "test.nofilter": Severity.ERROR,
                   "test.unknown": Severity.ERROR}


def test_probe_error_is_tool_error(tmp_path):
    bad = tmp_path / "x.mov"
    bad.write_bytes(b"not a movie")
    res = engine.run_file(bad, SPEC, {"checks": {}})
    assert res["worst"] == "ERROR"


def test_to_records_exact_pts_long_timestamps():
    """pts_time prints 6 significant digits; frames must come from the integer pts."""
    fps = Fraction(24000, 1001)
    # video at ~10 h (round): printed resolution 0.1 s > half a frame
    tb = Fraction(1, 24000)
    frames = range(863136, 863146)
    raw = [(str(n * 1001), f"{float(n * 1001 * tb):.6g}", {}) for n in frames]
    assert [round(Fraction(t) * fps) for _, t, _ in raw] != list(frames)   # old behaviour
    assert [r.frame for r in engine._to_records(raw, fps, False, [Fraction(1, 12800), tb])] == list(frames)
    with pytest.raises(RuntimeError, match="time base"):     # no consistent time base
        engine._to_records(raw, fps, False, [Fraction(1, 12800)])
    # audio at ~1000 s (floor): chunk of 2002 samples @48 kHz = one 23.976 frame
    raw = [(str(n * 2002), f"{n * 2002 / 48000:.6g}", {}) for n in (24001, 24002)]
    assert raw[0][1] == "1001.04"
    assert math.floor(Fraction(raw[0][1]) * fps) == 24000                  # old: off by one
    assert [r.frame for r in engine._to_records(raw, fps, True, [Fraction(1, 48000)])] == [24001, 24002]


def test_single_key_print_keeps_pts_and_long_timestamps(tiny, fake_checks):
    """Real ffmpeg: key-filtered prints (one per key) merge per frame, and frames
    stay exact at a 10 h timestamp (setpts offset inside the tap)."""
    @fake_checks
    class Late(Check):
        id = "test.late"

        def taps(self, ctx):
            v = ctx.media.video[0]["index"]
            return [Tap("y", v, "setpts=PTS+36000/TB,signalstats",
                        ("lavfi.signalstats.YAVG", "lavfi.signalstats.YMAX"))]

        def detect(self, ctx, records):
            r = records["y"]
            assert [x.frame for x in r] == [900000 + i for i in range(10)]
            assert all(set(x.values) == {"lavfi.signalstats.YAVG", "lavfi.signalstats.YMAX"}
                       for x in r)
            return []

        def evaluate(self, ctx, c):
            return []

    (f,) = engine.run_file(tiny, SPEC, {"checks": {"test.late": {}}})["findings"]
    assert f.severity == Severity.PASS, f.message


def test_config_error_wording(tiny, fake_checks):
    @fake_checks
    class Bad(Check):
        id = "test.badcfg"

        def evaluate(self, ctx, c):
            raise engine.ConfigError("unknown loudness standard 'X'")

    (f,) = engine.run_file(tiny, SPEC, {"checks": {"test.badcfg": {}}})["findings"]
    assert f.severity == Severity.ERROR and f.message.startswith("configuration error:")


def test_filter_list_parses_ffmpeg6_and_ffmpeg9_formats():
    """`ffmpeg -filters`: 3 flag columns (T S C) in 6.1.1 and 7.1.1, 2 columns (T S) in 8.0.1 and
    9.0.2 (found on conda-forge ffmpeg 9.0.2: every filter read as missing before the fix)."""
    from cmr.engine import _FILTER_LINE
    v6 = [" .S. signalstats       V->V       Generate statistics from video analysis.",
          " ..C acompressor       A->A       Audio compressor.",
          " T.C crop              V->V       Crop the input video.",
          " ... abuffer           |->A       Buffer audio frames."]
    v9 = [" .S signalstats       V->V       Generate statistics from video analysis.",
          " TS aap               AA->A      Apply Affine Projection algorithm.",
          " .. ebur128           A->N       EBU R128 scanner.",
          " .. abuffer           |->A       Buffer audio frames."]
    hdr = ["  T.. = Timeline support", "  ------", "Filters:"]
    for lines, want in ((v6, ["signalstats", "acompressor", "crop", "abuffer"]),
                        (v9, ["signalstats", "aap", "ebur128", "abuffer"])):
        assert [m.group(1) for l in lines + hdr if (m := _FILTER_LINE.match(l))] == want
