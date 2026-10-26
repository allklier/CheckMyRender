"""Per-program silence / silent channel / clipping naming / undeclared-programs notice (no media).
Canned per-frame peaks per mono stream; program range = file frames 24..71 (48 frames)."""
from fractions import Fraction
from types import SimpleNamespace

import pytest

from cmr.checks import audio_content as ac
from cmr.engine import CheckContext, ConfigError, MetaRecord, Timeline
from cmr.model import Severity

P51 = {"name": "5.1", "streams": [0, 1, 2, 3, 4, 5], "channels": ["L", "R", "C", "LFE", "Ls", "Rs"]}
PST = {"name": "stereo", "streams": [6, 7]}
SIL = {"threshold_dbfs": -60, "max_run_frames": 12}
ON, OFF = -20, "-inf"


def ctx(check_id, params, n_streams=8):
    tl = Timeline(Fraction(24000, 1001), False, 96, 86376, "file", 24, 24, 24, 71)  # TC 00:59:59:00
    media = SimpleNamespace(audio=[{"index": i + 1, "sample_rate": 48000, "channels": 1}
                                   for i in range(n_streams)])
    return CheckContext(check_id, media, {}, params, Severity.FAIL, tl)


def recs(*streams):
    """streams: per stream a list of 48 peaks (one mono channel) -> records a1..aN."""
    return {f"a{i + 1}": [MetaRecord(0, 0.0, {"lavfi.astats.1.Peak_level": str(v)}) for v in s]
            for i, s in enumerate(streams)}


def tone():
    return [ON] * 48


def gap(a, b):
    """silent on program frames a..b (0-based within the program), tone elsewhere."""
    return [OFF if a <= k <= b else ON for k in range(48)]


def run(cls, params, streams, n=8):
    c = ctx(cls.id, params, n)
    return cls().evaluate(c, cls().detect(c, recs(*streams)))


def problems(f):
    return [x for x in f if x.severity not in (Severity.PASS, Severity.INFO)]


def test_surrounds_silent_for_stretches_is_not_program_silence():
    # C LFE Ls Rs silent for 30 frames (head stereo-only), L R carry sound: no program silence
    st = [tone(), tone()] + [gap(0, 29)] * 4 + [tone(), tone()]
    assert run(ac.Silence, {**SIL, "audio": {"programs": [P51, PST]}}, st) == []
    # without programs (backward compatible): one FAIL per silent mono stream, as before
    f = run(ac.Silence, SIL, st)
    assert sorted((x.stream, x.frame_start, x.frame_end) for x in problems(f)) == \
        [(s, 24, 53) for s in (3, 4, 5, 6)]


def test_all_channels_silent_is_one_finding_per_program():
    st = [gap(10, 29)] * 6 + [tone(), tone()]
    (f,) = run(ac.Silence, {**SIL, "audio": {"programs": [P51, PST]}}, st)
    assert (f.severity, f.stream, f.frame_start, f.frame_end) == (Severity.FAIL, 1, 34, 53)
    ev = f.candidate.evidence[0]
    assert ev["program"] == "5.1" and ev["roles"] == ["L", "R", "C", "Ls", "Rs"]
    assert ev["streams"] == [1, 2, 3, 4, 5, 6]
    assert "program 5.1 (L(1) R(2) C(3) Ls(5) Rs(6))" in f.message and "silence 20 frames" in f.message


def test_lfe_is_not_measured_for_silence():
    # everything silent except the LFE: still program silence
    st = [gap(10, 29)] * 3 + [tone()] + [gap(10, 29)] * 2 + [tone(), tone()]
    (f,) = run(ac.Silence, {**SIL, "audio": {"programs": [P51, PST]}}, st)
    assert (f.stream, f.frame_start, f.frame_end) == (1, 34, 53)


def test_dash_channel_not_measured_and_unprogrammed_stream_per_stream():
    prog = {"name": "st", "streams": [0, 1, 2], "channels": ["L", "R", "-"]}
    st = [tone(), tone(), [OFF] * 48, gap(0, 20)]
    f = problems(run(ac.Silence, {**SIL, "audio": {"programs": [prog]}}, st, n=4))
    assert [(x.stream, x.frame_start, x.frame_end) for x in f] == [(4, 24, 44)]   # stream 4: no program
    assert f[0].message.startswith("stream 4:")


def test_declared_silence_in_program():
    st = [gap(10, 29)] * 6 + [tone(), tone()]
    allowed = [{"stream": s, "start": "01:00:00:10", "end": "01:00:01:05"} for s in range(6)]
    (f,) = run(ac.Silence, {**SIL, "audio": {"programs": [P51, PST], "allowed_silence": allowed}}, st)
    assert f.severity == Severity.INFO and f.evidence[0]["kind"] == "declared_silence"
    assert f.evidence[0]["declared_frames"] == 20 and f.evidence[0]["program"] == "5.1"
    # declared on L and R only: C Ls Rs are still judged and silent -> FAIL remains
    part = [{"stream": s, "start": "01:00:00:10", "end": "01:00:01:05"} for s in (0, 1)]
    f = run(ac.Silence, {**SIL, "audio": {"programs": [P51, PST], "allowed_silence": part}}, st)
    assert [x.severity for x in f] == [Severity.FAIL]


def test_silent_channel_by_role_lfe_excluded():
    st = [tone(), tone(), tone(), [OFF] * 48, [OFF] * 48, tone(), tone(), tone()]
    (f,) = problems(run(ac.SilentChannel, {"threshold_dbfs": -60, "audio": {"programs": [P51, PST]}}, st))
    assert f.stream == 5 and f.candidate.evidence[0]["role"] == "Ls"
    assert f.message.startswith("program 5.1 channel Ls (stream 5): silent for the whole program")
    f = problems(run(ac.SilentChannel, {"threshold_dbfs": -60, "include_lfe": True,
                                        "audio": {"programs": [P51, PST]}}, st))
    assert sorted(x.candidate.evidence[0]["role"] for x in f) == ["LFE", "Ls"]
    # partial silence of a surround is not a silent channel
    st2 = [tone()] * 4 + [gap(0, 40)] + [tone()] * 3
    assert problems(run(ac.SilentChannel, {"threshold_dbfs": -60, "audio": {"programs": [P51, PST]}}, st2)) == []


def test_silent_channel_declared_whole_program_info():
    st = [tone(), tone(), tone(), [OFF] * 48, [OFF] * 48, tone(), tone(), tone()]
    f = run(ac.SilentChannel, {"threshold_dbfs": -60, "audio": {"programs": [P51, PST],
                                                               "allowed_silence": [{"stream": 4}]}}, st)
    (x,) = f
    assert x.severity == Severity.INFO and x.evidence[0]["roles"] == ["Ls"] and x.stream == 5


def test_clipping_names_program_and_role():
    st = [tone()] * 2 + [[ON] * 10 + [0.0] * 3 + [ON] * 35] + [tone()] * 5
    (f,) = run(ac.Clipping, {"clip_dbfs": -0.1, "audio": {"programs": [P51, PST]}}, st)
    assert (f.stream, f.frame_start, f.frame_end) == (3, 34, 36)
    assert f.message.startswith("stream 3 (program 5.1 C): sample peak")
    (f,) = run(ac.Clipping, {"clip_dbfs": -0.1}, st)                 # no programs: as before
    assert f.message.startswith("stream 3: sample peak")


def test_old_spelling_and_conflicts():
    st = [gap(10, 29)] * 6 + [tone(), tone()]
    old = {**SIL, "audio": {"loudness": {"standard": "x", "programs": [P51, PST]}}}
    assert [x.stream for x in run(ac.Silence, old, st)] == [1]                      # older spelling
    same = {**SIL, "audio": {"programs": [P51, PST], "loudness": {"programs": [P51, PST]}}}
    assert [x.stream for x in run(ac.Silence, same, st)] == [1]                     # equal: fine
    for bad in ({"programs": [P51], "loudness": {"programs": [PST]}},              # disagree
                {"programs": [{"streams": []}]},                                    # schema
                {"programs": [{"streams": [0, 1, 2]}]}):                            # 3 ch, no names
        with pytest.raises(ConfigError):
            ac.Silence().detect(ctx("audio.silence", {**SIL, "audio": bad}), recs(*[tone()] * 8))


STD = {"standards": {"A85": {"target_lufs": -24, "tolerance_lu": 2, "true_peak_max_dbtp": -2}}}
R128 = [MetaRecord(24, 0, {"lavfi.r128.I": "-24.0", "lavfi.r128.LRA": "3", "lavfi.r128.true_peak": "0.1"})]


def loud(params, n=8):
    c = ctx("audio.loudness", {"loudness": {"standard": "A85"}, **STD, **params}, n)
    return [t.name for t in ac.Loudness().taps(c)], ac.Loudness().evaluate(c, ac.Loudness().detect(
        c, {"r128": R128, "r128_1": R128}))


def test_loudness_audio_programs_and_conflicts():
    names, f = loud({"audio": {"programs": [P51, PST]}})
    assert names == ["r128", "r128_1"] and [x.evidence[0]["program"] for x in f] == ["5.1", "stereo"]
    with pytest.raises(ConfigError):                       # program_track + programs
        loud({"loudness": {"standard": "A85", "program_track": 0}, "audio": {"programs": [P51]}})
    with pytest.raises(ConfigError):                       # both spellings, different
        loud({"loudness": {"standard": "A85", "programs": [PST]}, "audio": {"programs": [P51]}})


def test_undeclared_programs_notice():
    _, f = loud({})
    pas, note = f
    assert pas.severity == Severity.PASS
    assert note.severity == Severity.WARN and note.evidence[0]["kind"] == "undeclared_programs"
    assert "measured only audio stream 0" in note.message and "audio.programs" in note.message
    _, f = loud({"undeclared_programs_severity": "info"})
    assert f[1].severity == Severity.INFO
    _, f = loud({}, n=1)                                   # one stream: nothing to say
    assert [x.severity for x in f] == [Severity.PASS]


def test_explicit_program_track_declares_the_program():
    _, f = loud({"loudness": {"standard": "A85", "program_track": 6}})
    assert [x.severity for x in f] == [Severity.PASS]      # no notice although 8 streams
    _, f = loud({"loudness": {"standard": "A85", "program_track": 6},
                 "undeclared_programs_severity": "info"})
    assert [x.severity for x in f] == [Severity.PASS]
    _, f = loud({"audio": {"programs": [P51, PST]}})       # programs: no notice either
    assert all(x.evidence[0].get("kind") != "undeclared_programs" for x in f)


def tiered(streams, **p):
    return run(ac.Silence, {**SIL, **p}, streams)


def test_two_tier_silence():
    prog = {"audio": {"programs": [P51, PST]}}
    st = [gap(10, 29)] * 6 + [tone(), tone()]            # 20 frames on the 5.1 program
    # single tier (limit 12): FAIL, no tier key
    (f,) = tiered(st, **prog)
    assert f.severity == Severity.FAIL and "tier" not in f.candidate.evidence[0] and not f.evidence and "max 12" in f.message
    # below warn: nothing; between: WARN; above max: FAIL
    assert tiered(st, max_run_frames=40, warn_run_frames=20, **prog) == []
    (f,) = tiered(st, max_run_frames=30, warn_run_frames=6, **prog)
    assert f.severity == Severity.WARN and f.evidence[0]["tier"] == "warn"
    assert "warn tier" in f.message and "30" in f.message and "6" in f.message
    (f,) = tiered(st, max_run_frames=15, warn_run_frames=6, **prog)
    assert f.severity == Severity.FAIL and f.evidence[0]["tier"] == "limit"
    assert "warn from 6" in f.message
    # warn >= max: single tier
    (f,) = tiered(st, max_run_frames=12, warn_run_frames=12, **prog)
    assert f.severity == Severity.FAIL and "tier" not in f.candidate.evidence[0] and not f.evidence
    # the profile severity caps the warn tier (info stays info)
    c = ctx("audio.silence", {**SIL, "max_run_frames": 30, "warn_run_frames": 6, **prog})
    c.severity = Severity.INFO
    (f,) = ac.Silence().evaluate(c, ac.Silence().detect(c, recs(*st)))
    assert f.severity == Severity.INFO


def test_two_tier_silence_per_stream_and_declared():
    st = [gap(0, 9), gap(0, 24)] + [tone()] * 6           # no programs: per stream, 10 and 25 frames
    f = tiered(st, max_run_frames=20, warn_run_frames=6)
    assert sorted((x.stream, x.severity) for x in f) == [(1, Severity.WARN), (2, Severity.FAIL)]
    # declared: a run above the WARN limit that lost frames to the declaration gives the INFO
    st = [gap(10, 29)] * 6 + [tone(), tone()]
    allowed = [{"stream": s, "start": "01:00:00:10", "end": "01:00:01:05"} for s in range(6)]
    (f,) = tiered(st, max_run_frames=30, warn_run_frames=6,
                  audio={"programs": [P51, PST], "allowed_silence": allowed})
    assert f.severity == Severity.INFO and f.evidence[0]["kind"] == "declared_silence"
