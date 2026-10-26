"""Waiver matching logic (D17) without media: fingerprints are faked."""
import datetime

import pytest

from cmr import waivers
from cmr.config import ConfigError
from cmr.model import Candidate, Finding, Severity

TODAY = datetime.date(2026, 10, 26)
S = dict(waivers.DEFAULTS)


def vfp(values, frames=(30, 40)):
    return {"type": "video_dhash", "algorithm": "dhash64-gray9x8-area", "version": 1,
            "frames": list(frames), "values": list(values)}


def waiver(wid="W0001", a=30, b=40, values=("ffff000000000000", "0"), **kw):
    return {"id": wid, "check_id": "video.black_runs", "stream": 0, "frame_start": a,
            "frame_end": b, "fingerprint": vfp(values), "reason": "ok", **kw}


def finding(a=30, b=40, check="video.black_runs", stream=0, sev=Severity.FAIL, evidence=None):
    return Finding(check, sev, "black run", stream=stream, frame_start=a, frame_end=b,
                   evidence=evidence or [])


def run(findings, ws, current_values=("ffff000000000000", "0"), s=S, today=TODAY):
    state = {}
    waivers.apply(findings, ws, lambda w: vfp(current_values, w["fingerprint"]["frames"]),
                  s, today, state)
    return waivers.summary("w.yaml", ws, state, today)


def test_inside_range_same_fingerprint_is_waived():
    f = finding(32, 38)
    summ = run([f], [waiver()])
    assert f.severity == Severity.PASS and f.raw_severity == Severity.FAIL
    assert f.waiver_ref == "W0001" and f.waiver["status"] == "waived"
    assert [w["id"] for w in summ["applied"]] == ["W0001"] and not summ["stale"]


def test_within_tolerance_is_waived_beyond_is_changed():
    f = finding()
    run([f], [waiver()], current_values=("ffff0000000003ff", "0"))   # 10 bits = default tolerance
    assert f.waiver_ref == "W0001" and f.waiver["distance"] == 10
    g = finding()
    summ = run([g], [waiver()], current_values=("ffff0000000007ff", "0"))   # 11 bits
    assert g.severity == Severity.FAIL and g.waiver_ref is None
    assert g.waiver["status"] == "content_changed"
    assert g.waiver["note"] == "previously waived (W0001), content changed - re-review"
    assert [w["id"] for w in summ["content_changed"]] == ["W0001"]


def test_outside_range_not_matched_and_waiver_stale():
    f = finding(20, 40)          # starts 10 frames before the waived range (margin 5)
    summ = run([f], [waiver()])
    assert f.severity == Severity.FAIL and f.waiver is None
    assert [w["id"] for w in summ["stale"]] == ["W0001"]


def test_margin_and_fast_mode_uncertainty():
    assert run([f := finding(25, 45)], [waiver()]) and f.waiver_ref == "W0001"   # margin 5
    g = finding(24, 40)
    run([g], [waiver()])
    assert g.waiver_ref is None
    h = finding(22, 40, evidence=[{"boundary_uncertainty_frames": 8}])   # fast sampling widens
    run([h], [waiver()])
    assert h.waiver_ref == "W0001"


def test_other_check_stream_or_edge_not_matched():
    fs = [finding(check="video.blanking"), finding(stream=1)]
    run(fs, [waiver()])
    assert all(f.waiver is None for f in fs)
    w = waiver(edge="top", kind="edge")
    top = finding(evidence=[{"edge": "top", "kind": "edge"}])
    left = finding(evidence=[{"edge": "left", "kind": "edge"}])
    cand = Finding("video.black_runs", Severity.FAIL, "x", stream=0, frame_start=30, frame_end=40,
                   candidate=Candidate("video.black_runs", 0, 30, 40, 1.0, [{"edge": "top", "kind": "edge"}]))
    run([top, left, cand], [w])
    assert top.waiver_ref == "W0001" and left.waiver is None and cand.waiver_ref == "W0001"


def test_expiry():
    f = finding()
    summ = run([f], [waiver(expires="2026-10-25")])
    assert f.waiver is None and [w["id"] for w in summ["expired"]] == ["W0001"]
    g = finding()
    run([g], [waiver(expires=datetime.date(2026, 10, 26))])   # last valid day
    assert g.waiver_ref == "W0001"


def test_pass_error_and_metadata_findings_untouched():
    fs = [finding(sev=Severity.PASS), finding(sev=Severity.ERROR)]
    run(fs, [waiver()])
    assert all(f.waiver is None for f in fs)
    assert not waivers.waivable({"check_id": "video.color", "severity": "WARN", "frame_start": None,
                                 "stream": 0})


def test_incomparable_fingerprint_counts_as_changed():
    w = waiver()
    w["fingerprint"]["version"] = 99
    f = finding()
    run([f], [w])
    assert f.waiver["status"] == "content_changed" and f.waiver["distance"] is None


def test_audio_distance_and_sampling():
    a = {"type": "audio_envelope", "algorithm": "x", "version": 1, "frames": [1],
         "values": [[[-3.0, -6.0], [-90.0, -90.0]]]}
    b = {**a, "values": [[[-3.5, -7.5], [-90.0, -90.0]]]}
    assert waivers.distance(a, b) == 1.5
    assert waivers.sample_frames(0, 3, 8) == [0, 1, 2, 3]
    fr = waivers.sample_frames(100, 10099, 8)
    assert len(fr) == 8 and fr[0] == 100 and fr[-1] == 10099
    assert waivers.dhash(bytes([1] + [0] * 71)) == "8000000000000000"


def test_file_roundtrip_and_ids(tmp_path):
    p = tmp_path / "w.yaml"
    waivers.save(p, [waiver(), waiver("W0007")])
    ws = waivers.load(p)
    assert [w["id"] for w in ws] == ["W0001", "W0007"] and waivers.next_id(ws) == "W0008"
    p.write_text("waivers: [{id: W1}]\n")
    with pytest.raises(ConfigError):
        waivers.load(p)
