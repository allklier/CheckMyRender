"""QC suite comparison logic (tests/suite/compare.py) on synthetic reports - no media, no real data."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "suite_compare", Path(__file__).resolve().parents[1] / "suite" / "compare.py")
compare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compare)


def F(check, sev, fs=None, fe=None, **ev):
    return {"check_id": check, "severity": sev, "frame_start": fs, "frame_end": fe,
            "evidence": [ev] if ev else []}


def R(*findings, exit_code=2):
    return {"summary": {"exit_code": exit_code}, "files": [{"findings": list(findings)}]}


REPORT = R(F("video.fps", "PASS"),
           F("video.blanking", "FAIL", 69, 97, edge="left", kind="edge"),
           F("video.blanking", "WARN", 296, 302, edge="top", kind="matte"),
           F("audio.loudness", "WARN", 0, 359, kind="integrated"),
           F("timing.timecode_start", "FAIL"))
EXP = {"exit": 2, "other": "fail", "findings": [
    {"check": "video.blanking", "severity": "fail", "kind": "edge", "edge": "left", "frames": [69, 97]},
    {"check": "video.blanking", "severity": "warn", "kind": "matte", "frames": [296, 302]},
    {"check": "audio.loudness", "severity": "warn", "kind": "integrated"},
    {"check": "timing.timecode_start", "severity": "fail"}]}


def test_all_expected_pass():
    assert compare.compare_expected(REPORT, EXP) == []


def test_exit_code_mismatch_and_process_exit_wins():
    assert compare.compare_expected(REPORT, {**EXP, "exit": 1}) == ["exit code 2, expected 1"]
    assert compare.compare_expected(REPORT, {**EXP, "exit": 1}, exit_code=1) == []


def test_exit_null_not_checked():
    assert compare.compare_expected(REPORT, {**EXP, "exit": None}) == []


def test_missing_finding():
    exp = {**EXP, "findings": EXP["findings"] + [{"check": "video.black_runs", "severity": "fail"}]}
    assert compare.compare_expected(REPORT, exp) == ["missing: video.black_runs FAIL"]


def test_unexpected_finding_other_fail_and_allow():
    exp = {**EXP, "findings": EXP["findings"][:3]}
    assert compare.compare_expected(REPORT, exp) == ["unexpected: timing.timecode_start FAIL"]
    assert compare.compare_expected(REPORT, {**exp, "other": "allow"}) == []


def test_default_other_is_fail():
    exp = {k: v for k, v in EXP.items() if k != "other"}
    exp["findings"] = EXP["findings"][1:]
    assert compare.compare_expected(REPORT, exp)[0].startswith("unexpected: video.blanking FAIL")


@pytest.mark.parametrize("frames,ok", [([74, 102], True), ([102, 110], True),   # within 5
                                       ([103, 110], False), ([0, 63], False), ([0, 64], True)])
def test_frame_tolerance(frames, ok):
    e = {"check": "video.blanking", "severity": "fail", "frames": frames}
    assert compare.matches(e, REPORT["files"][0]["findings"][1]) is ok


def test_fast_uncertainty_widens_tolerance():
    f = F("video.blanking", "WARN", 100, 120, kind="matte", boundary_uncertainty_frames=12)
    assert compare.matches({"check": "video.blanking", "severity": "warn", "frames": [130, 140]}, f)
    assert not compare.matches({"check": "video.blanking", "severity": "warn", "frames": [133, 140]}, f)


def test_locator_mismatch():
    e = {"check": "video.blanking", "severity": "fail", "edge": "right"}
    assert not compare.matches(e, REPORT["files"][0]["findings"][1])


def test_frames_expected_but_finding_has_none():
    assert not compare.matches({"check": "timing.timecode_start", "severity": "fail", "frames": [0, 0]},
                               REPORT["files"][0]["findings"][4])


def test_count():
    rep = R(F("video.fps", "FAIL"), F("video.fps", "FAIL"))
    assert compare.compare_expected(rep, {"exit": 2, "findings": [
        {"check": "video.fps", "severity": "fail", "count": 2}]}) == []
    out = compare.compare_expected(rep, {"exit": 2, "findings": [
        {"check": "video.fps", "severity": "fail", "count": 1}]})
    assert out == ["count 2 != 1: video.fps FAIL"]


def test_severity_pass_expectation_matches_pass_result():
    assert compare.compare_expected(REPORT, {**EXP, "findings": EXP["findings"] + [
        {"check": "video.fps", "severity": "pass"}]}) == []


def test_absent():
    out = compare.compare_expected(REPORT, {**EXP, "absent": ["audio.loudness", "video.fps"]})
    assert out == ["must be absent: audio.loudness WARN kind=integrated frames 0-359"]


def test_waived_finding_is_not_a_problem():
    rep = R(F("video.blanking", "PASS", 10, 20, kind="matte"), exit_code=0)
    rep["files"][0]["findings"][0]["raw_severity"] = "WARN"
    assert compare.compare_expected(rep, {"exit": 0, "findings": []}) == []


def test_candidate_evidence_used_when_finding_has_none():
    f = {"check_id": "video.blanking", "severity": "FAIL", "frame_start": 1, "frame_end": 2,
         "evidence": [], "candidate": {"evidence": [{"edge": "left", "kind": "edge"}]}}
    assert compare.matches({"check": "video.blanking", "severity": "fail", "edge": "left"}, f)


def test_multi_file_report_rejected():
    with pytest.raises(ValueError):
        compare.compare_expected({"files": [{}, {}]}, EXP)


def test_baseline_same_and_shifted_within_tolerance():
    base = R(*REPORT["files"][0]["findings"])
    assert compare.compare_baseline(REPORT, base) == []
    moved = R(F("video.blanking", "FAIL", 72, 99, edge="left", kind="edge"),
              *REPORT["files"][0]["findings"][2:])
    assert compare.compare_baseline(moved, base) == []


def test_baseline_new_gone_and_exit():
    base = R(F("video.blanking", "FAIL", 69, 97, edge="left", kind="edge"), exit_code=2)
    now = R(F("video.blanking", "FAIL", 200, 230, edge="left", kind="edge"), exit_code=1)
    out = compare.compare_baseline(now, base)
    assert out == ["exit code 1, baseline 2",
                   "new vs baseline: video.blanking FAIL kind=edge edge=left frames 200-230",
                   "gone vs baseline: video.blanking FAIL kind=edge edge=left frames 69-97"]


def test_baseline_severity_change_is_a_difference():
    base = R(F("audio.loudness", "WARN", 0, 9, kind="integrated"))
    now = R(F("audio.loudness", "FAIL", 0, 9, kind="integrated"))
    assert len(compare.compare_baseline(now, base)) == 2


# --- runner helpers (selection, --meta wrapper profile) ---------------------------------------------
_rs = importlib.util.spec_from_file_location(
    "run_suite", Path(__file__).resolve().parents[1] / "suite" / "run_suite.py")
run_suite = importlib.util.module_from_spec(_rs)
_rs.loader.exec_module(run_suite)

RUNS = [{"id": "a", "tags": ["smoke", "acceptance"]}, {"id": "b", "tags": ["acceptance", "slow"]},
        {"id": "c", "tags": ["acceptance"]}]


def test_select_tags_slow_and_ids():
    ids = lambda rs: [r["id"] for r in rs]  # noqa: E731
    assert ids(run_suite.select(RUNS, ["smoke"], [], False)) == ["a"]
    assert ids(run_suite.select(RUNS, ["acceptance"], [], False)) == ["a", "c"]
    assert ids(run_suite.select(RUNS, ["acceptance"], [], True)) == ["a", "b", "c"]
    assert ids(run_suite.select(RUNS, [], ["b"], False)) == ["b"]      # explicit id wins over slow


# quality: the runner passes `cmr check --quality Q` (tests/unit/test_job_entry.py)
def test_wrapper_meta_disables_decoding_checks():
    from cmr.config import find_profile
    w = run_suite.wrapper_profile(find_profile("release").resolve())
    assert w["extends"].endswith("release.yaml")
    for cid in ("video.blanking", "video.black_runs", "audio.loudness", "decode.errors"):
        assert w["checks"][cid] == {"enabled": False}
    assert "video.codec" not in w["checks"] and "timing.layout" not in w["checks"]


# --- strict cross-platform mode (D32) ------------------------------------------------------
def SR(*findings, exit_code=2, path="/linux/media/x.mov", **extra):
    counts = {}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    return {"schema_version": "1.4", "tool": {"ffmpeg": "6.1.1", "python": "3.12.3"},
            "profile": {"name": "final", "sha256": ["aa"]}, "spec": {"path": "/a/s.yaml"},
            "summary": {"exit_code": exit_code, "counts": counts},
            "files": [{"path": path, "identity": {"size": 10, "sha": "s"}, "worst": "FAIL",
                       "findings": list(findings), **extra}]}


def LOUD(i=-23.04, tp=-9.71, conf=None):
    f = F("audio.loudness", "FAIL", 0, 359, kind="integrated", integrated_lufs=i, true_peak_dbtp=tp)
    f["message"] = f"integrated loudness {i} LUFS"
    f["actual"] = i
    return f


def CLIP(peak=-0.05):
    return F("audio.clipping", "FAIL", 40, 47, channel=1, peak_dbfs=peak)


def BARS(y=940, conf=71.5):
    return F("slate.bars", "WARN", 3, 3, kind="bars", y_measured=y, ocr_conf=conf)


def test_strict_identical_and_stamps_ignored():
    a = SR(LOUD(), CLIP(), BARS())
    b = SR(LOUD(), CLIP(), BARS(), path="/Users/x/media/x.mov")
    b["tool"] = {"ffmpeg": "7.1", "python": "3.12.9"}
    b["profile"] = {"name": "final", "sha256": ["bb"]}
    assert compare.compare_strict(a, b) == []


def test_strict_loudness_and_db_tolerances():
    a = SR(LOUD(-23.04, -9.71), CLIP(-0.05))
    assert compare.compare_strict(a, SR(LOUD(-23.00, -9.67), CLIP(-0.14))) == []    # 0.04 / 0.09
    d = compare.compare_strict(a, SR(LOUD(-22.98, -9.71), CLIP(-0.20)))
    assert any("integrated_lufs" in x and "tolerance 0.05" in x for x in d)
    assert any(".actual" not in x and " actual" in x for x in d)      # actual of audio.loudness: 0.05
    assert any("peak_dbfs" in x and "tolerance 0.1" in x for x in d)
    assert not any("true_peak" in x for x in d)


def test_strict_integers_exact_confidence_ignored():
    d = compare.compare_strict(SR(BARS(940, 71.5)), SR(BARS(939, 40.0)))
    assert len(d) == 1 and "y_measured" in d[0] and "940 vs 939" in d[0]


def test_strict_frame_range_severity_and_locator_tolerance_zero():
    a = SR(F("video.blanking", "FAIL", 69, 97, edge="left", kind="edge"))
    for other in (F("video.blanking", "FAIL", 69, 98, edge="left", kind="edge"),
                  F("video.blanking", "WARN", 69, 97, edge="left", kind="edge"),
                  F("video.blanking", "FAIL", 69, 97, edge="right", kind="edge")):
        d = compare.compare_strict(a, SR(other))
        assert any("only on A" in x for x in d) and any("only on B" in x for x in d)


def test_strict_summary_identity_message_and_missing_file():
    a, b = SR(LOUD(), exit_code=2), SR(LOUD(), exit_code=1)
    b["files"][0]["identity"] = {"size": 11, "sha": "t"}
    b["files"][0]["findings"][0]["message"] = "integrated loudness -23.04 LUFS (other words)"
    d = compare.compare_strict(a, b)
    assert "summary.exit_code: 2 vs 1" in d
    assert any("identity differs" in x for x in d) and any("message" in x for x in d)
    c = SR(LOUD(), path="/m/y.mov")
    d = compare.compare_strict(a, c)
    assert "file x.mov: only on A" in d and "file y.mov: only on B" in d


def test_strict_number_masking_in_messages():
    a, b = SR(LOUD(-23.04)), SR(LOUD(-23.01))
    assert compare.compare_strict(a, b) == []


# --- run_suite: timeout kills the whole process group (no orphaned ffmpeg) ---------------------
def test_run_group_timeout_kills_grandchildren(tmp_path):
    import os
    import subprocess
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "suite"))
    import run_suite
    pidfile = tmp_path / "pid"
    cmd = ["sh", "-c", f"sleep 60 & echo $! > {pidfile}; wait"]
    with open(tmp_path / "log", "w") as fh:
        with pytest.raises(subprocess.TimeoutExpired):
            run_suite.run_group(cmd, fh, dict(os.environ), timeout=1)
    pid = int(pidfile.read_text())

    def alive():
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        stat = Path(f"/proc/{pid}/stat")                 # Linux: a zombie counts as gone
        return not (stat.exists() and stat.read_text().split()[2] == "Z")
    import time
    for _ in range(20):                                   # reaping by init may take a moment
        if not alive():
            break
        time.sleep(0.1)
    alive = alive()
    assert not alive
    with open(tmp_path / "log2", "w") as fh:
        assert run_suite.run_group(["sh", "-c", "exit 3"], fh, dict(os.environ), timeout=10) == 3
