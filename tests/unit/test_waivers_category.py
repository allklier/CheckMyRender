"""Category waivers (D17 extension) and the per-check findings cap, without media."""
import datetime

import pytest

from cmr import waivers
from cmr.config import ConfigError
from cmr.model import Finding, Severity, exit_code
from cmr.report import build_report, cap_findings
from cmr.report.console import render

TODAY = datetime.date(2026, 10, 26)
S = dict(waivers.DEFAULTS)


def bk(a, sev=Severity.WARN, kind="dark_edge", edge="left", check="video.blanking", raw=None):
    f = Finding(check, raw or sev, "x", stream=0, frame_start=a, frame_end=a + 2,
                evidence=[{"edge": edge, "kind": kind}])
    f.lower_to(sev)
    return f


def cat(wid="W0001", check="video.blanking", ceiling="warn", **kw):
    return waivers.make_category(wid, check, ceiling, "reviewed", TODAY, **kw)


def run(findings, ws, today=TODAY):
    state = {}
    waivers.apply(findings, ws, lambda w: pytest.fail("no fingerprint for category waivers"),
                  S, today, state)
    return waivers.summary("w.yaml", ws, state, today)


# --- matching -------------------------------------------------------------------------
def test_ceiling_and_sharp_fail_survives_warn_ceiling():
    dark = [bk(100 * i) for i in range(5)]
    sharp = bk(1000, Severity.FAIL, kind="edge")
    info = bk(2000, Severity.INFO, kind="matte")
    summ = run(dark + [sharp, info], [cat()])          # no class: any kind up to WARN
    assert all(f.waiver_ref == "W0001" and f.severity == Severity.PASS
               and f.raw_severity == Severity.WARN for f in dark)
    assert dark[0].waiver == {"id": "W0001", "status": "waived", "type": "category",
                              "reason": "reviewed", "max_severity": "warn"}
    assert sharp.severity == Severity.FAIL and sharp.waiver_ref is None and sharp.waiver is None
    assert info.waiver_ref == "W0001"
    (a,) = summ["applied"]
    assert a["type"] == "category" and a["count"] == 6 and a["max_severity"] == "warn"


def test_ceiling_uses_raw_severity():
    f = bk(10, Severity.INFO, raw=Severity.FAIL)     # e.g. lowered by a verifier
    run([f], [cat(ceiling="warn")])
    assert f.waiver_ref is None
    run([f], [cat(ceiling="fail")])
    assert f.waiver_ref == "W0001"


def test_class_and_edge_narrow():
    fs = [bk(10), bk(20, kind="matte"), bk(30, edge="top")]
    run(fs, [cat(kind="dark_edge", edge="left")])
    assert [f.waiver_ref for f in fs] == ["W0001", None, None]


def test_channel_matches_channel_or_channels():
    a = Finding("audio.silent_channel", Severity.WARN, "x", stream=1, frame_start=0, frame_end=9,
                evidence=[{"channel": 2}])
    b = Finding("audio.clipping", Severity.WARN, "x", stream=1, frame_start=0, frame_end=9,
                evidence=[{"channels": [1, 2]}])
    c = Finding("audio.clipping", Severity.WARN, "x", stream=1, frame_start=0, frame_end=9,
                evidence=[{"channels": [1]}])
    run([a], [cat(check="audio.silent_channel", channel=2)])
    run([b, c], [cat(check="audio.clipping", channel=2)])
    assert a.waiver_ref and b.waiver_ref and c.waiver_ref is None


def test_frame_range_with_margin():
    fs = [bk(100), bk(196), bk(300), Finding("video.blanking", Severity.WARN, "no range")]
    meta = Finding("video.color", Severity.WARN, "untagged")   # metadata: never waived (D17)
    run([meta], [cat(check="video.color")])
    assert meta.waiver_ref is None
    summ = run(fs, [cat(frames=(100, 200))])     # 196-198 inside; 300 outside; no range: no
    assert [f.waiver_ref for f in fs] == ["W0001", "W0001", None, None]
    assert summ["applied"][0]["count"] == 2


def test_expiry_and_stale():
    f = bk(10)
    summ = run([f], [cat(expires=datetime.date(2026, 10, 25)), cat("W0002", check="video.black_runs")])
    assert f.waiver_ref is None
    assert [w["id"] for w in summ["expired"]] == ["W0001"]
    assert [w["id"] for w in summ["stale"]] == ["W0002"] and summ["stale"][0]["type"] == "category"
    g = bk(10)
    run([g], [cat(expires=TODAY)])                # last valid day
    assert g.waiver_ref == "W0001"


def test_per_finding_waiver_wins_and_pass_error_untouched():
    fp = {"type": "video_dhash", "algorithm": "dhash64-gray9x8-area", "version": 1,
          "frames": [10], "values": ["0"]}
    per = {"id": "W0001", "check_id": "video.blanking", "stream": 0, "edge": "left",
           "kind": "dark_edge", "frame_start": 10, "frame_end": 12, "fingerprint": fp, "reason": "r"}
    f, ok, err = bk(10), bk(50, Severity.PASS), bk(60, Severity.ERROR)
    state = {}
    waivers.apply([f, ok, err], [per, cat("W0002")], lambda w: fp, S, TODAY, state)
    summ = waivers.summary("w", [per, cat("W0002")], state, TODAY)
    assert f.waiver_ref == "W0001" and ok.waiver is None and err.waiver is None
    assert [w["id"] for w in summ["stale"]] == ["W0002"]
    assert summ["applied"][0]["type"] == "finding" and summ["applied"][0]["count"] == 1


def test_make_category_validation():
    with pytest.raises(ValueError):
        cat(ceiling="error")
    with pytest.raises(ValueError):
        cat(check="timing.layout")                # only video.* / audio.* check ids
    with pytest.raises(ValueError):
        cat(edge="middle")
    with pytest.raises(ValueError):
        cat(frames=(20, 10))


def test_file_versions(tmp_path):
    p = tmp_path / "w.yaml"
    fp = {"type": "video_dhash", "algorithm": "a", "version": 1, "frames": [1], "values": ["0"]}
    per = {"id": "W0001", "check_id": "video.black_runs", "stream": 0, "frame_start": 1,
           "frame_end": 2, "fingerprint": fp, "reason": "r"}
    waivers.save(p, [per])
    assert "waiver_version: 1" in p.read_text()  # still readable by older builds
    waivers.save(p, [per, cat("W0002", kind="dark_edge")])
    assert "waiver_version: 2" in p.read_text()
    ws = waivers.load(p)
    assert waivers.is_category(ws[1]) and waivers.next_id(ws) == "W0003"
    p.write_text("waiver_version: 1\nwaivers:\n- {id: W1, type: category, check_id: video.blanking,"
                 " max_severity: warn, reason: r}\n")
    with pytest.raises(ConfigError):              # category entries need version 2
        waivers.load(p)
    p.write_text("waiver_version: 2\nwaivers:\n- {id: W1, type: category, check_id: video.blanking,"
                 " reason: r}\n")
    with pytest.raises(ConfigError):              # max_severity is required
        waivers.load(p)


# --- findings cap ---------------------------------------------------------------------
def report(findings, cap=10, limits=None, fail_on="warn"):
    worst = max((f.severity for f in findings), default=Severity.PASS).name
    files = [{"path": "f.mov", "identity": {}, "timeline": None, "worst": worst,
              "findings": findings}]
    return build_report(files, "p", [], "s", "", "ffmpeg", fail_on, None, cap, limits)


def test_cap_keeps_worst_first_fail_not_hidden_behind_info():
    fs = [bk(i, Severity.INFO) for i in range(0, 300, 10)] + [bk(500, Severity.FAIL, kind="edge")]
    listed, sup, _ = cap_findings(fs, lambda c: 5)
    assert len(listed) == 5 and listed[-1].severity == Severity.FAIL
    assert [f.frame_start for f in listed[:4]] == [0, 10, 20, 30]   # then by time, report order
    (s,) = sup
    assert s["count"] == 26 and s["worst_severity"] == "INFO" and s["limit"] == 5
    assert s["by_severity"] == {"INFO": 26} and s["frame_start"] == 40 and s["frame_end"] == 292


def test_counts_and_exit_code_include_suppressed():
    fs = [bk(i, Severity.INFO) for i in range(20)] + [bk(900, Severity.WARN, kind="matte"),
                                                     bk(950, Severity.WARN, kind="matte")]
    rep = report(fs, cap=1)
    f = rep["files"][0]
    assert len(f["findings"]) == 1 and f["findings"][0]["severity"] == "WARN"
    assert f["suppressed"][0]["count"] == 21 and f["suppressed"][0]["worst_severity"] == "WARN"
    assert rep["summary"]["counts"]["INFO"] == 20 and rep["summary"]["counts"]["WARN"] == 2
    assert rep["summary"]["suppressed"] == 21 and rep["summary"]["exit_code"] == exit_code(Severity.WARN)
    assert rep["schema_version"] == "1.2"
    out = render(rep)
    assert "(21 additional video.blanking findings suppressed; worst: WARN)" in out
    assert "INFO 20, WARN 2" in out


def test_cap_is_per_check_and_pass_always_listed():
    fs = [bk(i) for i in range(12)] + [bk(i, check="video.black_runs") for i in range(3)] + \
         [Finding("video.color", Severity.PASS, "ok")]
    rep = report(fs, cap=10)
    f = rep["files"][0]
    assert [(s["check_id"], s["count"]) for s in f["suppressed"]] == [("video.blanking", 2)]
    assert len(f["findings"]) == 10 + 3 + 1


def test_zero_is_unlimited_and_profile_override():
    fs = [bk(i) for i in range(30)] + [bk(i, check="video.black_runs") for i in range(30)]
    rep = report(fs, cap=0)
    assert len(rep["files"][0]["findings"]) == 60 and rep["summary"]["suppressed"] == 0
    rep = report(fs, cap=10, limits={"video.blanking": 25})
    sup = {s["check_id"]: s["count"] for s in rep["files"][0]["suppressed"]}
    assert sup == {"video.blanking": 5, "video.black_runs": 20}
    rep = report(fs, cap=10, limits={"video.blanking": 0})
    assert {s["check_id"] for s in rep["files"][0]["suppressed"]} == {"video.black_runs"}


def test_waived_do_not_count_against_cap_and_are_summarised():
    fs = [bk(i * 10) for i in range(50)] + [bk(1000 + i, Severity.FAIL, kind="edge") for i in range(3)]
    summ = run(fs, [cat(kind="dark_edge")])
    rep = report(fs, cap=2)
    rep_w = {**rep, "waivers": summ}
    f = rep["files"][0]
    assert [x["severity"] for x in f["findings"]] == ["FAIL", "FAIL"]      # no WAIVED rows
    assert f["suppressed"][0]["count"] == 1
    (w,) = f["waived_by_category"]
    assert w["waiver_id"] == "W0001" and w["count"] == 50 and w["worst_raw_severity"] == "WARN"
    assert rep["summary"]["counts"]["WAIVED"] == 50 and rep["summary"]["waived_by_category"] == 50
    assert rep["summary"]["exit_code"] == 2
    out = render(rep_w)
    assert "WAIVED  video.blanking: 50 finding(s) by category waiver W0001 (worst was WARN)" in out
    assert "category W0001 video.blanking kind=dark_edge <= WARN: 50 finding(s) waived: reviewed" in out
    # unlimited: every waived finding is listed again
    assert len(report(fs, cap=0)["files"][0]["findings"]) == 53


def test_all_waived_exit_zero():
    fs = [bk(i * 10) for i in range(20)]
    run(fs, [cat()])
    rep = report(fs)
    assert rep["summary"]["exit_code"] == 0 and rep["files"][0]["findings"] == []
