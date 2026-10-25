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


def test_evaluate_severity_classes_override_and_cap():
    c = d.detect(stats(50, {"top": {f: BLACK for f in range(50)}, "left": {5: (1.0, 0, 200)}}))
    sev = lambda fs: {x.evidence[0]["edge"]: x.severity.name for x in fs}
    assert sev(d.evaluate(c)) == {"top": "INFO", "left": "FAIL"}
    assert all(x.raw_severity == x.severity and x.candidate is not None for x in d.evaluate(c))
    assert sev(d.evaluate(c, {"class_severity": {"edge": "warn"}}))["left"] == "WARN"
    assert sev(d.evaluate(c, cap="warn")) == {"top": "INFO", "left": "WARN"}


def test_chains_shape():
    ch = d.chains({"max_depth": 4})
    assert list(ch) == list(d.EDGES)
    assert ch["right"].startswith("crop=w=5:h=ih:x=iw-5:y=0:exact=1,format=yuv444p,"
                                  "shuffleplanes=0:0:0,split[bkrighta][bkrightb];"
                                  "[bkrighta]crop=w=1:h=ih:x=4:y=0:exact=1")
    assert ch["top"].endswith("c2='max(x,y)',signalstats")


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
