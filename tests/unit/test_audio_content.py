"""Audio content checks without media: sample/frame arithmetic and detect/evaluate on
synthetic astats / ebur128 records."""
from fractions import Fraction
from types import SimpleNamespace

import pytest

from cmr.checks import audio_content as ac
from cmr.engine import CheckContext, MetaRecord, Skip, Timeline
from cmr.model import Severity


def ctx(check_id, params, fps="24000/1001", p=(24, 71), sr=48000, channels=2):
    fps = Fraction(fps)
    tl = Timeline(fps, False, p[1] + 25, 0, "none", p[0], 24, p[0], p[1]) if p else \
        Timeline(fps, False, 10, 0, "none", 5, 5, None, None)
    media = SimpleNamespace(audio=[{"index": 1, "sample_rate": sr, "channels": channels}])
    return CheckContext(check_id, media, {}, params, Severity.FAIL, tl)


def astats(peaks):
    return [MetaRecord(0, 0.0, {f"lavfi.astats.{c + 1}.Peak_level": str(v) for c, v in enumerate(p)})
            for p in peaks]


def test_samples_half_up_and_integer_frames():
    assert ac._samples(24, 48000, Fraction(24000, 1001)) == 48048            # 2002/frame, exact
    assert ac._samples(1, 48000, Fraction(30000, 1001)) == 1602              # 1601.6 -> 1602
    assert ac._samples(1, 44100, Fraction(24000, 1001)) == 1839              # 1839.3375 -> 1839
    assert ac._runs([3, 4, 5, 9, 11, 12]) == [(3, 5), (9, 9), (11, 12)]


def test_taps_cut_program_range():
    (t,) = ac.Silence().taps(ctx("audio.silence", {}))
    assert t.stream == 1
    assert t.chain.startswith("atrim=start_sample=48048:end_sample=144144,asetnsamples=n=2002:p=0,astats=")


def test_no_program_range_skips():
    with pytest.raises(Skip):
        ac.Silence().taps(ctx("audio.silence", {}, p=None))


def test_silence_runs_and_missing_audio_count_as_silent():
    c = ctx("audio.silence", {"threshold_dbfs": -60, "max_run_frames": 2}, p=(0, 9))
    peaks = [[-20, -20]] * 3 + [["-inf", -70]] * 3 + [[-20, -20]] * 2   # audio stops after frame 7
    cands = ac.Silence().detect(c, {"a1": astats(peaks)})
    assert [(x.frame_start, x.frame_end) for x in cands] == [(3, 5), (8, 9)]
    (f,) = ac.Silence().evaluate(c, cands)                                # run of 2 is allowed
    assert (f.frame_start, f.frame_end, f.actual, f.stream) == (3, 5, 3, 1)


def test_non_integer_samples_per_frame_maps_by_chunk_index():
    # 29.97 at 48 kHz: 1601.6 samples/frame, chunks of 1601 -> chunks 2669 and 2670 both start
    # in frame 2668 (2670*1601 = 4274670 < 2669*1601.6); a frame is silent only if all its chunks are
    c = ctx("audio.silence", {"threshold_dbfs": -60, "max_run_frames": 0}, fps="30000/1001", p=(0, 2671))
    peaks = [[-20, -20]] * 2674
    peaks[2670] = ["-inf", "-inf"]
    assert ac.Silence().detect(c, {"a1": astats(peaks)}) == []
    peaks[2671] = ["-inf", "-inf"]
    assert [(x.frame_start, x.frame_end) for x in ac.Silence().detect(c, {"a1": astats(peaks)})] == [(2669, 2669)]


def test_silent_channel_and_json_safe_inf():
    c = ctx("audio.silent_channel", {"threshold_dbfs": -60}, p=(0, 2))
    cands = ac.SilentChannel().detect(c, {"a1": astats([[-20, "-inf"]] * 3)})
    (f,) = ac.SilentChannel().evaluate(c, cands)
    assert f.candidate.evidence == [{"channel": 2, "peak_dbfs": None}]
    assert "channel 2" in f.message and "-inf" in f.message and (f.frame_start, f.frame_end) == (0, 2)


def test_clipping_channels_and_threshold():
    c = ctx("audio.clipping", {"clip_dbfs": -0.1}, p=(0, 4))
    peaks = [[-3, -3], [-0.05, -3], [-0.000001, -0.0], [-3, -3], [-0.2, -3]]
    (cand,) = ac.Clipping().detect(c, {"a1": astats(peaks)})
    assert (cand.frame_start, cand.frame_end, cand.evidence[0]["channels"]) == (1, 2, [1, 2])


def r128(I, lra, tp):
    return MetaRecord(0, 0.0, {"lavfi.r128.I": str(I), "lavfi.r128.LRA": str(lra), "lavfi.r128.true_peak": str(tp)})


PARAMS = {"loudness": {"standard": "EBU R128", "program_track": 0},
          "standards": {"EBU R128": {"target_lufs": -23, "tolerance_lu": 1, "true_peak_max_dbtp": -1}}}


def test_loudness_uses_last_record_and_locates_true_peak_block():
    c = ctx("audio.loudness", {**PARAMS, "lra_max_lu": 5})
    recs = [r128(-70, 0, 0.1), r128(-30, 0, 0.5), r128(-23.4, 6, 0.95), r128(-23.4, 6, 0.95)]
    cands = ac.Loudness().detect(c, {"r128": recs})
    m = cands[0].evidence[0]
    assert m["integrated_lufs"] == -23.4 and m["lra_lu"] == 6
    assert m["tp_block"] == [28, 31]     # record 2: samples 57648..62447 / 2002 -> frames 28..31
    fs = ac.Loudness().evaluate(c, cands)
    assert sorted(f.message.split(";")[0] for f in fs) == ["loudness range above 5 LU",
                                                            "true peak above -1 dBTP (first in this 100 ms block)"]


def test_loudness_pass_reports_measurement_and_gating():
    c = ctx("audio.loudness", PARAMS)
    (f,) = ac.Loudness().evaluate(c, ac.Loudness().detect(c, {"r128": [r128(-23.4, 1, 0.5)]}))
    assert f.severity == Severity.PASS and "I -23.4 LUFS" in f.message and "not dialogue-gated" in f.message


def test_loudness_unknown_standard_is_an_error():
    c = ctx("audio.loudness", {**PARAMS, "loudness": {"standard": "ARIB"}})
    with pytest.raises(ValueError, match="no preset"):
        ac.Loudness().detect(c, {"r128": [r128(-23, 0, 0.5)]})
