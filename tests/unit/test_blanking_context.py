"""Blanking stage 2 (temporal context, src/cmr/checks/blanking/context.py): features and
classifier on canned depth sequences and synthetic luma frames; the driver with a faked
decode. No media, no ffmpeg."""
import logging
from fractions import Fraction
from types import SimpleNamespace

import pytest

from cmr.checks.blanking import context as C
from cmr.checks.blanking import detector as D
from cmr.model import Candidate, Finding, Severity

DP = D.params_with_defaults({})
P = C.params_with_defaults({})
DMAX = 120


def feat(depths: dict, outer: dict | None = None, sample: bytes = b"\x40" * 64) -> C.FrameFeat:
    """FrameFeat with given band depth per edge (0 = no band; cov 1.0 when banded)."""
    edges = {}
    for e in D.EDGES:
        d = depths.get(e, 0)
        o = (outer or {}).get(e, 16.0 if d else 60.0)
        edges[e] = C.EdgeFeat(1.0 if d else 0.0, 1.0 if 0 < d <= 16 else 0.0,
                              1.0 if d > 16 else 0.0, float(d), o)
    return C.FrameFeat(edges, sample)


def frames_from(track: list[float], edge="left", start=0, opp=None, outer=None, samples=None):
    out = {}
    for i, d in enumerate(track):
        dep = {edge: d}
        if opp is not None:
            dep[C.OPPOSITE[edge]] = opp[i]
        out[start + i] = feat(dep, outer={edge: outer[i]} if outer else None,
                              sample=samples[i] if samples else b"\x40" * 64)
    return out


def classify(fr, a, b, edge="left", cuts=(), dis=(), p=None):
    return C.classify(fr, edge, a, b, list(cuts), list(dis), DMAX, p or P, DP)


# --- classifier ---------------------------------------------------------------------------
def test_ken_burns_shrinking_bar_is_animation():
    track = [40 - 2 * i for i in range(20)] + [0] * 10          # 40 .. 2, then gone
    fr = frames_from(track)
    r = classify(fr, 13, 19)                                    # depth 14 .. 2
    assert r["context"] == "animation"
    assert r["trend_r2"] > 0.99 and r["monotone"] == 1.0 and r["context_band_frames"] == 12


def test_pulldown_repeats_still_animation():
    # 3:2 pulldown: every 5th frame repeats the depth (zero step); real tracks look like this
    track, d = [], 60.0
    for i in range(30):
        if i % 5 != 4:
            d -= 4
        track.append(max(d, 0.0))
    fr = frames_from(track)
    hit = [f for f, v in enumerate(track) if 0 < v <= 16]
    r = classify(fr, hit[0], hit[-1])
    assert r["context"] == "animation", r


def test_constant_band_abrupt_is_steady():
    track = [0] * 12 + [2] * 5 + [0] * 12
    r = classify(frames_from(track), 12, 16)
    assert r["context"] == "steady" and r["hit_depth_px"] == 2 and r["context_band_frames"] == 0


def test_long_constant_band_with_context_is_steady_not_animation():
    track = [1] * 40
    r = classify(frames_from(track), 12, 27)
    assert r["context"] == "steady" and r["depth_motion_px"] == 0


def test_erratic_depth_is_unknown():
    track = [240, 122, 72, 100, 96, 52, 240, 80, 2, 180, 1, 30, 90, 4]
    r = classify(frames_from(track), 2, 11)
    assert r["context"] == "unknown" and "erratically" in r["reason"]


def test_not_reproduced_is_unknown():
    r = classify(frames_from([0] * 30), 10, 12)
    assert r["context"] == "unknown" and "not reproduced" in r["reason"]


def test_hit_inside_dissolve_with_gradual_onset_is_transition():
    track = [0] * 12 + [6] * 5 + [120] * 8
    outer = [40 - i for i in range(25)]                         # edge darkens gradually
    r = classify(frames_from(track, outer=outer), 12, 16, dis=range(8, 22))
    assert r["context"] == "transition" and r["dissolve_share"] == 1.0
    assert r["onset_jump"] <= P["transition_max_jump"]


def test_mask_switched_on_inside_dissolve_stays_steady():
    # planted 1 px line during a real dissolve: the outer line drops from picture to black
    track = [0] * 12 + [1] * 3 + [0] * 12
    outer = [50.0] * 12 + [16.0] * 3 + [50.0] * 12
    r = classify(frames_from(track, outer=outer), 12, 14, dis=range(5, 25))
    assert r["onset_jump"] == 34 and r["context"] == "steady"


def test_short_hit_at_cut_transition_only_with_gradual_onset():
    track = [0] * 12 + [1] + [0] * 12
    gradual = classify(frames_from(track, outer=[33.0] * 12 + [31.0] + [60.0] * 12), 12, 12,
                       cuts=[13])
    assert gradual["context"] == "transition" and gradual["cut_distance"] == 0
    abrupt = classify(frames_from(track, outer=[90.0] * 12 + [16.0] + [60.0] * 12), 12, 12,
                      cuts=[13])
    assert abrupt["context"] == "steady"
    # a whole shot between two cuts (per-shot mask error) is not a transition
    shot = [0] * 12 + [2] * 6 + [0] * 12
    r = classify(frames_from(shot, outer=[33.0] * 30), 12, 17, cuts=[12, 18])
    assert r["context"] == "steady"


def test_transition_far_from_hit_is_not_used():
    track = [0] * 12 + [2] * 5 + [0] * 12
    r = classify(frames_from(track, outer=[33.0] * 29), 12, 16, cuts=[25], dis=range(22, 29))
    assert r["context"] == "steady" and r["cut_distance"] == 8 and r["dissolve_distance"] == 6


def test_band_run_stops_at_cut():
    # previous shot pillarboxed (148), cut at 12, then a band growing from 1 px: the run stops
    # at the cut; since the band starts AT the cut it is not excused (user-labelled defect)
    track = [148] * 12 + [1 + 2 * i for i in range(20)]
    r = classify(frames_from(track), 12, 18, cuts=[12])
    assert r["band_run"][0] == 12 and r["cut_onset"] == 0
    assert r["context"] != "animation" and r["animation_guard"] == ["cut_onset"]


# --- guards from user labels on a real long-form master (features of the 4 labelled defects;
#     each was linear enough to pass the animation rule and was lowered before the guards) ---
def test_defect_band_at_cut_shrinking_away_stays():
    # image cut in with its first move keyframe too loose: 33 px band from the cut, gone in 8
    track = [0] * 12 + [33, 27, 27, 21, 15, 9, 5, 5, 1] + [0] * 12
    r = classify(frames_from(track), 16, 20, cuts=[12])
    assert r["cut_onset"] == 1 and r["animation_guard"] == ["cut_onset"]
    assert r["trend_r2"] >= P["context_min_r2"] and r["monotone"] == 1.0   # looked like animation
    assert r["context"] not in ("animation", "transition")


def test_defect_band_at_cut_growing_stays():
    # previous shot pillarboxed, cut, band grows 1 -> 36 px (symmetric on both sides)
    after = [1, 2, 2, 4, 6, 8, 10, 10, 12, 14, 17, 19, 19, 21, 23, 25, 27, 27, 29, 32, 34, 36]
    track = [148] * 12 + after
    r = classify(frames_from(track, opp=[170] * 12 + after), 12, 21, cuts=[12])
    assert r["cut_onset"] == 0 and r["context"] not in ("animation", "transition")


@pytest.mark.parametrize("grow", [[4, 20, 37, 37, 54, 73, 91, 105, 105, 121, 123, 123, 123],
                                  [14, 29, 29, 43, 58, 73, 89, 89, 106, 108, 108, 108, 108]])
def test_defect_fast_reveal_stays(grow):
    # mid-shot (no cut detected), the band appears and grows ~15 px per frame, outer line
    # jumps from bright picture to black (onset jump ~170)
    track = [0] * 12 + grow
    outer = [186.0] * 12 + [16.0] * len(grow)
    r = classify(frames_from(track, outer=outer), 12, 12)
    assert r["velocity_px"] > P["context_max_velocity_px"]
    assert r["animation_guard"] == ["fast_move"] and r["context"] == "steady"


def test_slow_mid_shot_move_from_zero_is_still_animation():
    # zoom-out of a still revealing pillars smoothly (2 px per frame), no cut: excused
    track = [0] * 12 + [2, 3, 3, 5, 7, 9, 11, 11, 12, 14, 16, 18, 18, 20, 22, 23, 25]
    r = classify(frames_from(track), 12, 22)
    assert r["context"] == "animation" and "animation_guard" not in r and r["cut_onset"] is None


def test_band_continuing_across_a_cut_is_not_cut_onset():
    # same depth on both sides of a detected cut (held-frame montage): not a new band
    track = [30 - i for i in range(30)]
    r = classify(frames_from(track), 18, 24, cuts=[14])
    assert r["cut_onset"] is None and r["context"] == "animation"


SHRINK_TO_CUT = [
    # (track, hit frame, cut frame, frames between band end and cut): bands shrinking to 0
    # right before a cut (two edges of one real event; user: can't tell -> mirror of cut_onset)
    ([13, 12, 11, 10, 9, 9, 9, 8, 7, 6, 6, 5, 4, 3, 2, 2, 1] + [0] * 12, 16, 19, 2),
    ([14, 13, 12, 11, 11, 10, 9, 8, 7, 7, 6, 5, 4, 3, 3, 2, 1] + [0] * 12, 16, 18, 1)]


@pytest.mark.parametrize("track,hit,cut,gap", SHRINK_TO_CUT)
def test_band_ending_right_before_cut_stays(track, hit, cut, gap):
    r = classify(frames_from(track), hit, hit, cuts=[cut])
    assert r["cut_end"] == gap and r["animation_guard"] == ["cut_end"]
    assert r["trend_r2"] >= P["context_min_r2"] and r["monotone"] == 1.0   # looked like animation
    # a 1-frame hit within 2 frames of the cut: not excused as a cut-frame transition either
    assert r["context"] == "steady"


def test_band_ending_well_before_cut_is_still_animation():
    track, hit, cut, _ = SHRINK_TO_CUT[0]
    r = classify(frames_from(track), hit, hit, cuts=[cut + 6])
    assert r["cut_end"] is None and r["context"] == "animation"


def test_band_continuing_across_a_cut_is_not_cut_end():
    # held-frame montage: a cut is detected but the band goes on with the same depth
    track = [40 - i for i in range(30)]
    r = classify(frames_from(track), 10, 15, cuts=[18])
    assert r["band_run"][1] == 17 and r["cut_end"] is None and r["context"] == "animation"


def test_short_unmoving_band_at_cut_still_transition_with_guards():
    # the field-mixed cut frame (1 frame, no motion) keeps the cut transition rule
    track = [0] * 12 + [1] + [0] * 12
    r = classify(frames_from(track, outer=[33.0] * 12 + [31.0] + [60.0] * 12), 12, 12, cuts=[13])
    assert r["cut_end"] == 0 and r["context"] == "transition"


def test_guard_thresholds_are_params():
    track, hit, cut, _ = SHRINK_TO_CUT[0]
    p = C.params_with_defaults({"context_cut_end_frames": 1})
    r = classify(frames_from(track), hit, hit, cuts=[cut], p=p)
    assert r["cut_end"] is None and r["context"] == "animation"
    track = [0] * 12 + [33, 27, 27, 21, 15, 9, 5, 5, 1] + [0] * 12
    p = C.params_with_defaults({"context_cut_onset_frames": 0})
    r = classify(frames_from(track), 16, 20, cuts=[12], p=p)
    assert r["cut_onset"] is None and r["context"] == "animation"
    p = C.params_with_defaults({"context_max_velocity_px": 20})
    r = classify(frames_from([0] * 12 + [4, 20, 37, 37, 54, 73, 91, 105]), 12, 12, p=p)
    assert r["context"] == "animation"


def test_symmetry_reported():
    track = [20 - i for i in range(20)]
    r = classify(frames_from(track, opp=track), 6, 15)
    assert r["symmetry"] == 1.0 and r["opposite_band"] == 1.0
    r = classify(frames_from(track, opp=[0] * 20), 6, 15)
    assert r["symmetry"] == 0.0


def test_fast_mode_hit_widened_within_possible_range():
    track = [0] * 10 + [2] * 7 + [0] * 10                      # true run 10-16
    r = C.classify(frames_from(track), "left", 15, 15, [], [], DMAX, P, DP, possible=(11, 19))
    assert r["hit"] == [11, 16] and r["context"] == "steady"


def test_r2():
    assert C._r2([(0, 1), (1, 2), (2, 3)]) == 1.0
    assert C._r2([(0, 1), (1, 1), (2, 1)]) == 0.0
    assert C._r2([(0, 1)]) == 0.0


# --- transitions ----------------------------------------------------------------------------
def _samples(values):
    return [bytes([v] * 64) for v in values]


def test_cut_detected_with_local_median():
    vals = [50 + (i % 2) for i in range(20)] + [150 + (i % 2) for i in range(20)]
    fr = frames_from([0] * 40, samples=_samples(vals))
    cuts, dis = C.transitions(fr, P)
    assert cuts == [20] and dis == []


def test_dissolve_linear_ramp_detected_slow_noise_not():
    vals = [40] * 10 + [40 + 5 * i for i in range(1, 21)] + [140] * 10   # 20-frame ramp
    cuts, dis = C.transitions(frames_from([0] * 40, samples=_samples(vals)), P)
    assert cuts == [] and dis and min(dis) >= 7 and max(dis) <= 33
    # noise-like changes (no linear trend) are no dissolve
    noisy = [40 + (7 * i * i) % 23 for i in range(40)]
    cuts, dis = C.transitions(frames_from([0] * 40, samples=_samples(noisy)), P)
    assert dis == []


def test_held_frames_make_each_change_a_cut():
    vals = [v for v in (30, 90, 30, 90) for _ in range(6)]
    cuts, _ = C.transitions(frames_from([0] * 24, samples=_samples(vals)), P)
    assert cuts == [6, 12, 18]


# --- per-frame features -----------------------------------------------------------------------
def _frame(w, h, bands: dict, black=16, pic=120):
    px = bytearray([pic] * (w * h))
    for y in range(h):
        for x in range(w):
            if (x < bands.get("left", 0) or x >= w - bands.get("right", 0)
                    or y < bands.get("top", 0) or y >= h - bands.get("bottom", 0)):
                px[y * w + x] = black
    return bytes(px)


def test_frame_features_depths_and_outer():
    w, h = 64, 36
    f = C.frame_features(_frame(w, h, {"left": 3, "right": 9, "top": 2}), w, h,
                         C.black_table(32), 16, 0.5)
    assert f.edges["left"].depth == 3 and f.edges["left"].cov == 1.0
    assert f.edges["right"].depth == 9 and f.edges["right"].shallow > 0.9   # top rows: corner
    assert f.edges["top"].depth == 2 and f.edges["bottom"].cov < 0.48   # only the side-band columns
    assert f.edges["left"].outer == 16 and f.edges["bottom"].outer > 16
    assert len(f.sample) == len(range(0, w * h, C.SAMPLE_STRIDE))


def test_frame_features_field_medians_and_saturation():
    w, h = 64, 36
    px = bytearray(_frame(w, h, {}))
    for y in range(h):                       # even rows 4 px band, odd rows 6 px (fields)
        for x in range(4 if y % 2 == 0 else 6):
            px[y * w + x] = 16
    f = C.frame_features(bytes(px), w, h, C.black_table(32), 16, 0.5)
    assert f.edges["left"].depth == 5.0
    full = C.frame_features(bytes([16] * (w * h)), w, h, C.black_table(32), 16, 0.5)
    assert full.edges["left"].depth == 32 and full.edges["left"].deep == 1.0


# --- driver ---------------------------------------------------------------------------------
def _ctx(params=None):
    media = SimpleNamespace(path="clip.mov", video=[{"index": 0, "width": 64, "height": 36}],
                            raw={"streams": [{"index": 0, "time_base": "1/25"}]})
    timeline = SimpleNamespace(fps=Fraction(25), file_frames=200)
    return SimpleNamespace(params=params or {}, media=media, timeline=timeline,
                           passinfo=SimpleNamespace(command=["/opt/ff/ffmpeg"]))


def _finding(edge, a, b, kind="edge", sev="FAIL", persistent=False, **ev):
    e = {"edge": edge, "kind": kind, "persistent": persistent, "frames": b - a + 1, **ev}
    c = Candidate(D.CHECK_ID, 0, a, b, 1.0, [e])
    return Finding(D.CHECK_ID, sev, "x", stream=0, frame_start=a, frame_end=b, candidate=c,
                   evidence=c.evidence)


def _fake_decode(tracks: dict, calls: list):
    """tracks: {edge: {frame: depth}} -> decode_window replacement."""
    def fake(path, ffmpeg, stream, fps, w, h, f0, f1, tbs, deadline, featfn):
        calls.append((f0, f1, ffmpeg))
        return {f: feat({e: t.get(f, 0) for e, t in tracks.items()}) for f in range(f0, f1 + 1)}
    return fake


def test_analyse_lowers_animation_keeps_steady(monkeypatch):
    kb = {f: max(0, 40 - 2 * (f - 80)) for f in range(60, 120)}       # shrinking bar
    steady = {f: 2 for f in range(30, 35)}
    calls = []
    monkeypatch.setattr(C, "decode_window", _fake_decode({"left": kb, "right": steady}, calls))
    a = _finding("left", 93, 99)
    s = _finding("right", 30, 34)
    m = _finding("left", 80, 92, kind="matte", sev="WARN")
    C.analyse([a, s, m], _ctx(), DP)
    assert a.severity == Severity.INFO and a.raw_severity == Severity.FAIL
    assert a.evidence[0]["context"] == "animation"
    assert a.verifier["decision"] == "animation" and a.verifier["lowered"] is True
    assert a.verifier["name"] == C.NAME and a.verifier["p"] is None
    assert s.severity == Severity.FAIL and s.evidence[0]["context"] == "steady"
    assert s.verifier["lowered"] is False
    assert m.severity == Severity.INFO and m.raw_severity == Severity.WARN
    assert len(calls) == 2 and all(c[2] == "/opt/ff/ffmpeg" for c in calls)   # a + m share one


def test_analyse_never_raises_and_unchanged(monkeypatch):
    kb = {f: max(0, 40 - 2 * (f - 80)) for f in range(60, 120)}
    monkeypatch.setattr(C, "decode_window", _fake_decode({"left": kb}, []))
    w = _finding("left", 93, 99, sev="WARN")
    C.analyse([w], _ctx({"context_severity": {"animation": "fail"}}), DP)
    assert w.severity == Severity.WARN and w.evidence[0]["context"] == "animation"
    f = _finding("left", 93, 99)
    C.analyse([f], _ctx({"context_severity": {"animation": "unchanged"}}), DP)
    assert f.severity == Severity.FAIL


def test_analyse_off_persistent_span_cap(monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(C, "decode_window", _fake_decode({"left": {}}, calls))
    off = _finding("left", 10, 12)
    C.analyse([off], _ctx({"context_analysis": False}), DP)
    assert "context" not in off.evidence[0] and off.verifier is None and not calls
    per = _finding("left", 0, 199, kind="matte", sev="INFO", persistent=True)
    long = _finding("right", 0, 400, kind="matte", sev="WARN")
    many = [_finding("top", 10 * i, 10 * i + 1) for i in range(5)]
    with caplog.at_level(logging.INFO, logger="cmr"):
        C.analyse([per, long, *many], _ctx({"max_context_candidates": 2}), DP)
    assert per.evidence[0]["context"] == "unknown" and "persistent" in per.evidence[0]["reason"]
    assert "context_max_span_frames" in long.evidence[0]["reason"]
    capped = [f for f in many if "max_context_candidates" in f.evidence[0].get("reason", "")]
    assert len(capped) == 3 and all(f.severity == Severity.FAIL for f in capped)


def test_analyse_decode_failure_is_unknown(monkeypatch):
    def boom(*a, **k):
        raise C.DecodeError("ffmpeg exit 1")
    monkeypatch.setattr(C, "decode_window", boom)
    f = _finding("left", 10, 12)
    C.analyse([f], _ctx(), DP)
    assert f.evidence[0]["context"] == "unknown" and "decode failed" in f.evidence[0]["reason"]
    assert f.severity == Severity.FAIL


def test_analyse_time_budget(monkeypatch):
    monkeypatch.setattr(C, "decode_window", _fake_decode({"left": {}}, []))
    f = _finding("left", 10, 12)
    C.analyse([f], _ctx({"context_max_seconds": 0}), DP)
    assert "context_max_seconds" in f.evidence[0]["reason"]


def test_params_schema_lists_every_default():
    assert set(C.params_schema()) == set(C.DEFAULTS)
    with pytest.raises(Exception):
        Severity.parse("unchanged")       # "unchanged" is a context value, not a severity
