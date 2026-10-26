"""Multi-stream loudness programs, declared silence and multi-input taps (no media)."""
from fractions import Fraction
from types import SimpleNamespace

import pytest

from cmr.checks import audio_content as ac
from cmr.engine import CheckContext, ConfigError, MetaRecord, Tap, Timeline, build_graph
from cmr.model import Severity


def ctx(check_id, params, n_streams=8, channels=1):
    tl = Timeline(Fraction(24000, 1001), False, 96, 86376, "file", 24, 24, 24, 71)  # TC 00:59:59:00
    media = SimpleNamespace(audio=[{"index": i + 1, "sample_rate": 48000, "channels": channels}
                                   for i in range(n_streams)])
    return CheckContext(check_id, media, {}, params, Severity.FAIL, tl)


STD = {"standards": {"A85": {"target_lufs": -24, "tolerance_lu": 2, "true_peak_max_dbtp": -2}}}


def loud(**ld):
    return ctx("audio.loudness", {"loudness": {"standard": "A85", **ld}, **STD})


def test_program_chain_merges_and_drops_lfe():
    c = ac.program_chain(6, 0, 10, ["L", "R", "C", "LFE", "Ls", "Rs"], "x")
    assert c == ("atrim=start_sample=0:end_sample=10[x0];" +
                 ";".join(f"[in{k}]atrim=start_sample=0:end_sample=10[x{k}]" for k in range(1, 6)) +
                 ";[x0][x1][x2][x3][x4][x5]amerge=inputs=6,"
                 "pan=FL+FR+FC+SL+SR|FL=c0|FR=c1|FC=c2|SL=c4|SR=c5,ebur128=metadata=1:peak=true")
    assert ac.program_chain(1, 0, 10, None, "x") == "atrim=start_sample=0:end_sample=10,ebur128=metadata=1:peak=true"


def test_taps_51_plus_stereo():
    t51, tst = ac.Loudness().taps(loud(programs=[
        {"name": "5.1", "streams": [0, 1, 2, 3, 4, 5], "channels": ["L", "R", "C", "LFE", "Ls", "Rs"]},
        {"name": "stereo", "streams": [6, 7]}]))
    assert (t51.name, t51.stream, t51.inputs) == ("r128", 1, (2, 3, 4, 5, 6))
    assert (tst.name, tst.stream, tst.inputs) == ("r128_1", 7, (8,))
    assert "pan=FL+FR|FL=c0|FR=c1" in tst.chain and "start_sample=48048:end_sample=144144" in tst.chain


def test_program_track_unchanged():
    (t,) = ac.Loudness().taps(loud(program_track=6))
    assert t.inputs == () and t.stream == 7 and "pan" not in t.chain


@pytest.mark.parametrize("ld", [
    dict(programs=[{"streams": [0, 1, 2]}]),                                  # 3 ch, no default
    dict(programs=[{"streams": [0, 1], "channels": ["L"]}]),                  # count mismatch
    dict(programs=[{"streams": [0, 1], "channels": ["C", "M"]}]),             # same position
    dict(programs=[{"streams": [0], "channels": ["LFE"]}]),                   # nothing measured
    dict(program_track=0, programs=[{"streams": [0]}]),
])
def test_bad_programs_are_config_errors(ld):
    with pytest.raises(ConfigError):
        ac.Loudness().taps(loud(**ld))


def test_detect_reports_each_program():
    c = loud(programs=[{"name": "5.1", "streams": [0, 1, 2, 3, 4, 5]}, {"name": "st", "streams": [6, 7]}])
    rec = lambda i, tp: [MetaRecord(24, 0, {"lavfi.r128.I": i, "lavfi.r128.LRA": "3",
                                            "lavfi.r128.true_peak": tp})]
    cands = ac.Loudness().detect(c, {"r128": rec("-24.0", "0.1"), "r128_1": rec("-30.0", "0.9")})
    f = ac.Loudness().evaluate(c, cands)
    assert [(x.severity, x.stream, x.evidence[0]["kind"] if x.severity != Severity.PASS else None)
            for x in f] == [(Severity.PASS, 1, None), (Severity.FAIL, 7, "integrated"),
                            (Severity.FAIL, 7, "true_peak")]
    assert f[0].evidence[0]["measured_channels"] == ["L", "R", "C", "Ls", "Rs"]
    assert "5.1 (streams 1,2,3,4,5,6, measured L R C Ls Rs)" in f[0].message
    assert "not dialogue-gated" in f[1].message


def test_allowed_silence_ranges_and_errors():
    c = ctx("audio.silence", {"audio": {"allowed_silence": [{"stream": 2}]},
                              "allowed_silence": [{"stream": 0, "channels": [1], "start": "01:00:00:00",
                                                   "end": "01:00:00:05"}, {"stream": 9}]})
    assert ac.allowed_silence(c) == {(3, 0): [(24, 71)], (1, 0): [(24, 29)]}   # stream 9 ignored
    for bad in ([{"stream": "x"}], [{"stream": 0, "channels": [2]}], [{"stream": 0, "start": "1:2"}]):
        with pytest.raises(ConfigError):
            ac.allowed_silence(ctx("audio.silence", {"allowed_silence": bad}))


def astats(peaks):
    return [MetaRecord(0, 0.0, {f"lavfi.astats.{c + 1}.Peak_level": str(v) for c, v in enumerate(p)})
            for p in peaks]


def test_silence_declared_channels_not_counted():
    p = {"threshold_dbfs": -60, "max_run_frames": 12}
    peaks = [[-20, "-inf"]] * 10 + [["-inf", "-inf"]] * 20 + [[-20, -20]] * 18     # 48 frames
    c = ctx("audio.silence", p, n_streams=1, channels=2)
    (f,) = ac.Silence().evaluate(c, ac.Silence().detect(c, {"a1": astats(peaks)}))
    assert (f.severity, f.frame_start, f.frame_end) == (Severity.FAIL, 34, 53)
    c = ctx("audio.silence", {**p, "allowed_silence": [{"stream": 0, "channels": [1, 2],
                                                        "start": "01:00:00:10", "end": "01:00:01:05"}]},
            n_streams=1, channels=2)
    (f,) = ac.Silence().evaluate(c, ac.Silence().detect(c, {"a1": astats(peaks)}))
    assert (f.severity, f.evidence[0]["kind"], f.evidence[0]["declared_frames"]) == \
        (Severity.INFO, "declared_silence", 20)


def test_silent_channel_declared_is_info_partial_still_fails():
    peaks = [[-20, "-inf"]] * 48
    c = ctx("audio.silent_channel", {"threshold_dbfs": -60, "allowed_silence": [{"stream": 0, "channels": [2]}]},
            n_streams=1, channels=2)
    (f,) = ac.SilentChannel().evaluate(c, ac.SilentChannel().detect(c, {"a1": astats(peaks)}))
    assert f.severity == Severity.INFO and f.evidence[0]["channels"] == [2]
    c = ctx("audio.silent_channel", {"threshold_dbfs": -60, "allowed_silence": [
        {"stream": 0, "channels": [2], "start": "01:00:00:00", "end": "01:00:00:10"}]}, n_streams=1, channels=2)
    (f,) = ac.SilentChannel().evaluate(c, ac.SilentChannel().detect(c, {"a1": astats(peaks)}))
    assert f.severity == Severity.FAIL and f.candidate.evidence[0]["channel"] == 2


def test_build_graph_routes_extra_inputs(tmp_path):
    taps = [("l/r128", Tap("r128", 1, "atrim[a0];[in1]atrim[a1];[a0][a1]amerge=inputs=2,ebur128",
                           inputs=(2,)), "l"),
            ("s/a1", Tap("a1", 1, "astats"), "s"), ("s/a2", Tap("a2", 2, "astats"), "s")]
    graph, labels, _ = build_graph(taps, {1: "audio", 2: "audio"}, tmp_path)
    assert "[0:1]asplit=2[s1_0][s1_1]" in graph and "[0:2]asplit=2[s2_0][s2_1]" in graph
    assert "[s1_0]atrim[a0];[s2_0]atrim[a1];[a0][a1]amerge=inputs=2,ebur128,ametadata=" in graph
    assert "[s1_1]astats,ametadata" in graph and "[s2_1]astats,ametadata" in graph
    assert labels == ["[o0]", "[o1]", "[o2]"]
