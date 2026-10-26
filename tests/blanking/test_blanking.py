"""Blanking detector tests (pytest 9.1.1 installed; run: PYTHONPATH=src pytest tests/blanking)."""
import shutil
import subprocess

import pytest

from cmr.checks.blanking import detector as d

PIC = (0.0, 0.0, 200.0)      # (defect coverage, matte coverage, level) of a picture edge
BLACK = (0.0, 1.0, 16.0)     # fully black edge (black frame / matte)


def stats(n, override=None):
    """Synthetic stats: n frames of picture; override {edge: {frame: (defect, matte, level)}}."""
    s = {e: {f: PIC for f in range(n)} for e in d.EDGES}
    for e, v in (override or {}).items():
        s[e].update(v)
    return s


def kinds(cands):
    return [(c.evidence[0]["edge"], c.evidence[0]["kind"], c.frame_start,
             c.frame_end, c.evidence[0]["persistent"]) for c in cands]


def test_parse_metadata():
    txt = ("frame:0    pts:0       pts_time:0\nlavfi.signalstats.YAVG=127.5\n"
           "frame:1    pts:1001    pts_time:0.0417\nlavfi.signalstats.YAVG=0\n")
    assert d.parse_metadata(txt, d.K_DEFECT) == {0: 127.5, 1: 0.0}


def test_clean_no_candidates():
    assert d.detect(stats(50)) == []


def test_coverage_threshold_and_tolerance():
    c = d.detect(stats(50, {"right": {7: (1.0, 0, 200)}}))
    assert kinds(c) == [("right", "edge", 7, 7, False)]
    assert c[0].check_id == "video.blanking" and c[0].score == 1.0
    # 0.49 (measured codec loss on a planted 50% line) passes with the default tolerance
    assert kinds(d.detect(stats(50, {"top": {3: (0.49, 0, 200)}}))) == [("top", "edge", 3, 3, False)]
    assert d.detect(stats(50, {"top": {3: (0.30, 0, 200)}})) == []
    assert d.detect(stats(50, {"top": {3: (0.49, 0, 200)}}), {"coverage_tolerance": 0}) == []
    assert len(d.detect(stats(50, {"top": {3: (0.30, 0, 200)}}), {"coverage": 0.3})) == 1


def test_persistent_and_transient_matte():
    c = d.detect(stats(50, {"top": {f: BLACK for f in range(50)}}))
    assert kinds(c) == [("top", "matte", 0, 49, True)]
    c = d.detect(stats(50, {"top": {f: BLACK for f in range(20, 25)}}))
    assert kinds(c) == [("top", "matte", 20, 24, False)]


def test_black_frames_skipped_and_bridged():
    black = {e: {f: BLACK for f in range(20, 30)} for e in d.EDGES}
    assert d.detect(stats(50, black)) == []
    black["top"] = {f: BLACK for f in range(50)}   # letterbox through a black section
    assert kinds(d.detect(stats(50, black))) == [("top", "matte", 0, 49, True)]


def test_dark_frame_suppresses_matte_not_edge():
    dark = {e: {f: (0, 0, 30.0) for f in range(10)} for e in d.EDGES}
    dark["left"][3] = (0, 1.0, 18.0)      # fade: dark, matte-like
    dark["right"][5] = (1.0, 0, 30.0)     # 1px edge in a dark frame
    assert kinds(d.detect(stats(10, dark))) == [("right", "edge", 5, 5, False)]


def test_dark_edge_class_between_edge_and_matte():
    # 4-tuples: (sharp defect, matte, level, dark/soft coverage)
    c = d.detect(stats(50, {"left": {5: (0.0, 0.0, 200, 0.9)},       # soft only -> dark_edge
                            "top": {5: (1.0, 0.0, 200, 1.0)},        # sharp wins
                            "right": {5: (0.0, 1.0, 200, 0.9)}}))    # dark_edge before matte
    assert kinds(c) == [("top", "edge", 5, 5, False), ("left", "dark_edge", 5, 5, False),
                        ("right", "dark_edge", 5, 5, False)]
    sev = {f.evidence[0]["edge"]: f.severity.name for f in d.evaluate(c)}
    assert sev == {"top": "FAIL", "left": "FAIL", "right": "FAIL"}   # flag: normal severity
    assert d.evaluate(c, {"class_severity": {"dark_edge": "info"}})[1].severity.name == "INFO"


def _modes(c, mode, cap="fail"):
    fs = d.evaluate(c, {"dark_edge_mode": mode}, cap=cap)
    return [(f.evidence[0].get("edge"), f.evidence[0].get("kind"), f.severity.name) for f in fs]


def test_dark_edge_mode_flag_info_hide():
    c = d.detect(stats(50, {"left": {5: (0.0, 0.0, 200, 0.9)}, "top": {7: (1.0, 0.0, 200, 1.0)}}))
    assert _modes(c, "flag") == [("top", "edge", "FAIL"), ("left", "dark_edge", "FAIL")]
    assert _modes(c, "flag", cap="warn") == [("top", "edge", "WARN"), ("left", "dark_edge", "WARN")]
    assert _modes(c, "info") == [("top", "edge", "FAIL"), ("left", "dark_edge", "INFO")]
    fs = d.evaluate(c, {"dark_edge_mode": "hide"})
    assert [f.severity.name for f in fs] == ["FAIL", "INFO"]       # count kept as INFO
    assert fs[1].message.startswith("1 dark_edge finding(s) hidden")
    assert fs[1].evidence[0]["hidden_dark_edge"] == [
        {"edge": "left", "frame_start": 5, "frame_end": 5, "coverage_max": 0.9}]
    # only dark_edge hits + hide: a PASS finding carries the count
    only = [x for x in c if x.evidence[0]["kind"] == "dark_edge"]
    fs = d.evaluate(only, {"dark_edge_mode": "hide"})
    assert [f.severity.name for f in fs] == ["PASS"] and fs[0].message.startswith("ok; 1 dark_edge")
    assert d.evaluate([], {"dark_edge_mode": "hide"}) == []
    assert _modes(c, "flag") == [(e, k, s) for e, k, s in
                                 [(f.evidence[0]["edge"], f.evidence[0]["kind"], f.severity.name)
                                  for f in d.evaluate(c)]]   # default = flag
    with pytest.raises(ValueError):
        d.evaluate(c, {"dark_edge_mode": "drop"})


def test_merge_dark_edge_and_matte_runs_not_edge():
    soft = {f: (0.0, 0.0, 200, 0.9) for f in (10, 11, 20, 400)}
    sharp = {f: (1.0, 0.0, 200, 0.0) for f in (10, 20)}
    c = d.detect(stats(500, {"left": soft, "top": sharp}), {"merge_gap_frames": 100})
    assert kinds(c) == [("top", "edge", 10, 10, False), ("top", "edge", 20, 20, False),
                        ("left", "dark_edge", 10, 20, False), ("left", "dark_edge", 400, 400, False)]
    ev = c[2].evidence[0]
    assert ev["runs"] == 2 and ev["frames"] == 3 and ev["coverage_max"] == 0.9
    assert "in 2 runs (merged" in d.evaluate(c, {"merge_gap_frames": 100})[2].message
    # gap 0: only touching runs merge (none here)
    assert len(d.detect(stats(500, {"left": soft}), {"merge_gap_frames": 0})) == 3
    # sampled (fast) runs merge too and keep the widened possible range
    fs = {e: {f: v for f, v in x.items() if f % 5 == 0}
          for e, x in stats(500, {"left": {f: (0.0, 0.0, 200, 0.9) for f in (10, 30)}}).items()}
    c = d.detect(fs, {"merge_gap_frames": 100}, frame_step=5)
    ev = c[0].evidence[0]
    assert (c[0].frame_start, c[0].frame_end, ev["frames_sampled"], ev["runs"],
            ev["possible_frame_start"], ev["possible_frame_end"]) == (10, 30, 2, 2, 6, 34)


def test_evaluate_severity_classes_override_and_cap():
    c = d.detect(stats(50, {"top": {f: BLACK for f in range(50)}, "left": {5: (1.0, 0, 200)}}))
    sev = lambda fs: {x.evidence[0]["edge"]: x.severity.name for x in fs}
    assert sev(d.evaluate(c)) == {"top": "INFO", "left": "FAIL"}
    assert all(x.raw_severity == x.severity and x.candidate is not None for x in d.evaluate(c))
    assert sev(d.evaluate(c, {"class_severity": {"edge": "warn"}}))["left"] == "WARN"
    assert sev(d.evaluate(c, cap="warn")) == {"top": "INFO", "left": "WARN"}


def test_chains_shape():
    ch = d.chains({"max_depth": 4})
    assert list(ch) == [n for e in d.EDGES for n in (e, e + "_dark")]
    assert d.tap_keys("top") == d.KEYS and d.tap_keys("top_dark") == (d.K_DEFECT,)
    assert ch["left_dark"].endswith("[bdleftoc][bdleftic]lut2=c0='255*lte(x,32)*gte(y-x,4)',"
                                    "signalstats")
    # right edge: strip x=iw-5; outer line = strip column 4, adjacent 3, inner 0; near/far
    # blocks (neighbour pairs) are columns 1..4 / 0..3
    r = ch["right"]
    assert r.startswith("crop=w=5:h=ih:x=iw-5:y=0:exact=1,format=yuv444p,shuffleplanes=0:0:0,"
                        "split=5[bkrightn][bkrightf][bkrighto][bkrighti][bkrighta];"
                        "[bkrightn]crop=w=4:h=ih:x=1:y=0:exact=1[bkrightnc];"
                        "[bkrightf]crop=w=4:h=ih:x=0:y=0:exact=1[bkrightfc];")
    assert "[bkrighto1]crop=w=1:h=ih:x=4:y=0:exact=1" in r
    assert "[bkrighta]crop=w=1:h=ih:x=3:y=0:exact=1" in r
    assert "lut2=c0='255*lte(x,20)*gte(y-x,6)'" in r          # deep steps: true black
    assert "lut2=c0='255*lte(x,32)*gte(y-x,6)'" in r          # 1 px step: black_luma
    assert r.count("lut2=c0='max(x,y)'") == 2                 # 4 -> 2 -> 1
    assert ch["top"].endswith("[bktoplj][bktopm1]lut2=c0='255*(eq(x,255)+eq(x,128)*gt(y,0))'"
                              ":c1='x':c2='x',signalstats")


def test_max_reduce_odd_depth_covers_every_column():
    """Odd widths reduce by overlapping halves: 5 -> 3 -> 2 -> 1, every column in a crop."""
    segs, top = d._max_reduce("left", 5, "src", "k")
    assert top == "km2" and len(segs) == 3
    assert "crop=w=3:h=ih:x=0:y=0" in segs[0] and "crop=w=3:h=ih:x=2:y=0" in segs[0]
    assert "crop=w=2:h=ih:x=0:y=0" in segs[1] and "crop=w=2:h=ih:x=1:y=0" in segs[1]
    assert d._max_reduce("top", 1, "src", "k") == ([], "src")


def _clip(tmp_path):
    """320x240 ProRes: right 1px column frames 5-7, top row frame 12, 60% of the
    left column frames 16-17, a flat dark (Y~23) bottom band that is NOT a defect."""
    clip = tmp_path / "c.mov"
    vf = ("testsrc2=s=320x240:r=24,format=yuv422p,"
          "drawbox=x=0:y=ih-40:w=iw:h=40:c=0x080808:t=fill,"
          "drawbox=x=iw-1:y=0:w=1:h=ih:c=black:t=fill:enable='between(n,5,7)',"
          "drawbox=x=0:y=0:w=iw:h=1:c=black:t=fill:enable='between(n,12,12)',"
          "drawbox=x=0:y=0:w=1:h=ih*0.6:c=black:t=fill:enable='between(n,16,17)'")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", vf, "-frames:v", "24",
                    "-c:v", "prores_ks", "-pix_fmt", "yuv422p10le", str(clip)], check=True)
    return clip


EXPECT = [("top", "edge", 12, 12, False), ("left", "edge", 16, 17, False),
          ("right", "edge", 5, 7, False)]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_end_to_end_standalone(tmp_path):
    assert kinds(d.run_standalone(str(_clip(tmp_path)), None, str(tmp_path))) == EXPECT


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_end_to_end_core_engine(tmp_path):
    from cmr import engine
    res = engine.run_file(_clip(tmp_path), {}, {"checks": {"video.blanking": {}}})
    f = [x for x in res["findings"] if x.check_id == "video.blanking"]
    assert sorted(kinds([x.candidate for x in f])) == sorted(EXPECT)
    assert all(x.severity.name == "FAIL" and x.timecode_start for x in f)


def _gradient_clip(tmp_path):
    """320x240 ProRes with dark picture at the left edge that brightens inward
    (Y 20 at x=0, +0.5 per px, i.e. +8 at 16 px; like a dark curtain): v1 flagged it as
    `edge` (outer <= 32, inner 16 px in >= +4); v3 reports it as `dark_edge` (no sharp
    step between neighbours), the planted defects as `edge`.
    Planted: right edge 3 px deep frames 5-7 (multi-px depth), top 1px frame 12."""
    clip = tmp_path / "g.mov"
    vf = ("testsrc2=s=320x240:r=24,format=yuv444p,"
          "geq=lum='if(lt(X,80),20+X/2,lum(X,Y))':cb='cb(X,Y)':cr='cr(X,Y)',"
          "drawbox=x=iw-3:y=0:w=3:h=ih:c=black:t=fill:enable='between(n,5,7)',"
          "drawbox=x=0:y=0:w=iw:h=1:c=black:t=fill:enable='between(n,12,12)'")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", vf, "-frames:v", "24",
                    "-c:v", "prores_ks", "-pix_fmt", "yuv422p10le", str(clip)], check=True)
    return clip


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_dark_gradient_edge_is_dark_edge_not_edge(tmp_path):
    clip = _gradient_clip(tmp_path)
    want = [("top", "edge", 12, 12, False), ("left", "dark_edge", 0, 23, True),
            ("right", "edge", 5, 7, False)]
    assert kinds(d.run_standalone(str(clip), None, str(tmp_path))) == want
    # odd max_depth (5 -> 3 -> 2 -> 1 reduction) still finds the 3 px deep defect; the
    # gradient rises only +2.5 over 5 px (< min_contrast): no dark_edge
    assert kinds(d.run_standalone(str(clip), {"max_depth": 5}, str(tmp_path))) == [
        want[0], want[2]]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
@pytest.mark.parametrize("mode,left", [("flag", "FAIL"), ("info", "INFO"), ("hide", None)])
def test_dark_edge_mode_through_engine(tmp_path, mode, left):
    from cmr import engine
    clip = _gradient_clip(tmp_path)
    cfg = {"dark_edge_mode": mode}
    res = engine.run_file(clip, {}, {"checks": {"video.blanking": cfg}})
    f = [x for x in res["findings"] if x.check_id == "video.blanking"]
    got = {(x.evidence[0].get("edge"), x.severity.name) for x in f if x.candidate}
    want = {("top", "FAIL"), ("right", "FAIL")} | ({("left", left)} if left else set())
    assert got == want
    hidden = [x for x in f if x.candidate is None]
    assert len(hidden) == (1 if mode == "hide" else 0)
    if hidden:
        assert hidden[0].severity.name == "INFO" and "1 dark_edge finding(s) hidden" in hidden[0].message


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_bad_dark_edge_mode_is_config_error(tmp_path):
    from cmr import engine
    res = engine.run_file(_gradient_clip(tmp_path), {},
                          {"checks": {"video.blanking": {"dark_edge_mode": "x"}}})
    f = [x for x in res["findings"] if x.check_id == "video.blanking"]
    assert len(f) == 1 and f[0].severity.name == "ERROR"


def test_profiles_set_dark_edge_mode():
    from cmr.config import load_spec, resolve_profile
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    spec, _ = load_spec(root / "examples" / "spec_hd_broadcast.yaml")
    mode = {n: resolve_profile(n, spec)[0]["checks"]["video.blanking"]["dark_edge_mode"]
            for n in ("final", "release", "review")}
    assert mode == {"final": "flag", "release": "flag", "review": "info"}
