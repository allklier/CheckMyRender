"""Blanking stage 2 (temporal context) through the engine on generated media
(tests/blanking/gen_context.py): Ken Burns zoom/pan -> animation (lowered to INFO),
fade -> transition, steady 2-3 px mask errors (moving shot, next to a fade, dark scene,
abrupt band at a cut) stay FAIL; quality fast; context off = stage-1 severities."""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from cmr import engine

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
HERE = Path(__file__).resolve().parent


@pytest.fixture(scope="module")
def media():
    out = Path(os.environ.get("CMR_TEST_MEDIA") or Path(tempfile.gettempdir()) / "cmr_test_media")
    out = out / "blanking_context"
    if not (out / "manifest.json").exists():
        sys.path.insert(0, str(HERE))
        import gen_context
        gen_context.main(out)
    return out, json.loads((out / "manifest.json").read_text())


def run(path, **params):
    res = engine.run_file(path, {}, {"checks": {"video.blanking": params}})
    return [f for f in res["findings"] if f.candidate is not None]


def overlapping(fs, edge, frames=None):
    return [f for f in fs if f.evidence[0]["edge"] == edge and (
        frames is None or (f.frame_start <= frames[1] and f.frame_end >= frames[0]))]


@pytest.mark.parametrize("quality", ["high", "fast"])
def test_expected_context_classes(media, quality):
    out, man = media
    for name, clip in man["clips"].items():
        fs = run(out / clip["file"], quality=quality)
        for exp in clip["expect"]:
            hits = overlapping(fs, exp["edge"], exp.get("frames"))
            if not hits and quality == "fast" and name == "cut_field":
                continue           # a 1-frame defect off the 1-in-5 sampling grid (D14)
            assert hits, (name, exp, quality)
            for f in hits:
                ev = f.evidence[0]
                assert ev["context"] == exp["cls"], (name, exp, quality, ev)
                assert f.verifier["decision"] == exp["cls"]
                if exp.get("fail"):
                    assert f.severity.name == "FAIL", (name, exp, quality)
                elif exp["cls"] in ("animation", "transition"):
                    assert f.severity.name == "INFO" and f.raw_severity.name in ("FAIL", "WARN")


def test_ken_burns_features(media):
    out, man = media
    fs = run(out / man["clips"]["kenburns_zoom"]["file"])
    edges = [f for f in fs if f.evidence[0]["kind"] == "edge"]
    assert {f.evidence[0]["edge"] for f in edges} == {"left", "right"}
    for f in edges:
        ev = f.evidence[0]
        assert ev["trend_r2"] >= 0.95 and ev["monotone"] == 1.0
        assert ev["context_band_frames"] >= 10 and ev["depth_motion_px"] >= 20


def test_context_off_keeps_stage1(media):
    out, man = media
    fs = run(out / man["clips"]["kenburns_zoom"]["file"], context_analysis=False)
    assert fs and all("context" not in f.evidence[0] and f.verifier is None for f in fs)
    assert {f.severity.name for f in fs if f.evidence[0]["kind"] == "edge"} == {"FAIL"}


def test_context_severity_param_and_schema(media):
    out, man = media
    fs = run(out / man["clips"]["kenburns_zoom"]["file"],
             context_severity={"animation": "warn"})
    assert {f.severity.name for f in fs if f.evidence[0]["kind"] == "edge"} == {"WARN"}
    res = engine.run_file(out / man["clips"]["kenburns_zoom"]["file"], {},
                          {"checks": {"video.blanking": {"context_severity": {"animation": "x"}}}})
    assert any(f.severity.name == "ERROR" and "bad profile params" in f.message
               for f in res["findings"])
