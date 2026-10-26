"""`Label: value` slates end to end on generated media (make_spot.py --layout label_colon).

Media: $CMR_SLATE_LC_MEDIA (an OUTDIR of `make_spot.py --layout label_colon`, full or --short),
else generated with --short into $CMR_TEST_MEDIA/slate_lc_short (needs numpy + Pillow). The
real-delivery head: slate 145 + black 65 while the spec says 150 + 60, so the profile wrapper
sets `split_tolerance_frames: 5` on the OCR checks. OCR tests SKIP without tesseract.
Spec/job: examples/spot_xr (spec_spot_xr.yaml, job_label_colon.yaml).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

from cmr.checks.slate import ocr
from cmr.cli import main

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "examples" / "spot_xr"
OCR_CHECKS = ("slate.present", "slate.consistency", "slate.content", "slate.claims")


def _media_dir() -> Path:
    if os.environ.get("CMR_SLATE_LC_MEDIA"):
        return Path(os.environ["CMR_SLATE_LC_MEDIA"])
    out = Path(os.environ.get("CMR_TEST_MEDIA", Path(tempfile.gettempdir()) / "cmr_test_media")) / "slate_lc_short"
    if not (out / "manifest.yaml").exists():
        try:
            import numpy, PIL  # noqa: F401
        except ImportError:
            pytest.skip("no slate media: set CMR_SLATE_LC_MEDIA or install numpy + Pillow to generate")
        subprocess.run([sys.executable, str(ROOT / "tests" / "slate" / "make_spot.py"), str(out),
                        "--short", "--layout", "label_colon"], check=True, capture_output=True)
    return out


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = _media_dir()
    man = yaml.safe_load((d / "manifest.yaml").read_text())
    files = {Path(f["path"]).parts[0]: (d / f["path"], f["errors"]) for f in man["files"]}
    tmp = tmp_path_factory.mktemp("spot_lc")
    spec = yaml.safe_load((EX / "spec_spot_xr.yaml").read_text())
    job = yaml.safe_load((EX / "job_label_colon.yaml").read_text())
    csv = (EX / "deliverables_label_colon.csv").read_text()
    if man.get("short"):
        spec["timing"]["program_duration_frames"] = 90
        csv = csv.replace(",:60,", ",:03,")
    (tmp / "spec.yaml").write_text(yaml.safe_dump(spec))
    (tmp / "deliverables_label_colon.csv").write_text(csv)
    job["slate"]["template"] = str(EX / "slates" / "xr_label_colon.yaml")
    (tmp / "job.yaml").write_text(yaml.safe_dump(job))
    profiles = {}
    for base in ("spot_review", "spot_delivery"):
        p = tmp / f"{base}_lc.yaml"
        p.write_text(yaml.safe_dump({"profile_version": 1, "name": f"{base}_lc", "extends": base,
                                     "checks": {c: {"split_tolerance_frames": 5} for c in OCR_CHECKS}}))
        profiles[base] = str(p)
    return {"files": files, "spec": str(tmp / "spec.yaml"), "job": str(tmp / "job.yaml"),
            "profiles": profiles}


def has_tesseract() -> bool:
    try:
        ocr.resolve_bin(ocr.tesseract_bin({}))
        return True
    except ocr.OCRUnavailable:
        return False


needs_ocr = pytest.mark.skipif(not has_tesseract(), reason="tesseract not available (CMR_TESSERACT)")


def check(media, which, out, profile="spot_review", *extra):
    f = media["files"][which][0]
    rc = main(["check", str(f), "--profile", media["profiles"][profile], "--spec", media["spec"],
               "--job", media["job"], "--report", "json", "-o", str(out), "--max-findings", "0", *extra])
    return rc, json.loads((out / "cmr_report_latest.json").read_text())["files"][0]["findings"]


def problems(findings):
    return [f for f in findings if f["severity"] not in ("PASS", "SKIPPED")]


def test_without_ocr_bars_skipped_and_ocr_errors(media, tmp_path, monkeypatch):
    monkeypatch.setenv(ocr.ENV_BIN, str(tmp_path / "no_tesseract"))
    rc, fs = check(media, "clean", tmp_path / "c", "spot_delivery")
    errs = [f for f in fs if f["severity"] == "ERROR"]
    assert {f["check_id"] for f in errs} == set(OCR_CHECKS) and rc == 3
    assert [f for f in problems(fs) if f["severity"] != "ERROR"] == []
    (bars,) = [f for f in fs if f["check_id"] == "slate.bars"]
    assert bars["severity"] == "SKIPPED" and "defines no bars" in bars["message"]


@needs_ocr
def test_clean_label_colon_spot_is_clean(media, tmp_path):
    rc, fs = check(media, "clean", tmp_path / "c", "spot_delivery")
    assert problems(fs) == [] and rc == 0


EXPECT = {   # manifest error id -> (check_id, evidence kind or None) that must be reported
    "adid_wrong": [("slate.content", "AD-ID")],
    "frame_short": [("timing.frame_count", None), ("slate.claims", "LENGTH")],
    "audio_mono": [("slate.claims", "AUDIO")],
    "date_old": [("slate.content", "DATE")],
    "late_differ": [("slate.consistency", "AGENCY")],
    "client_typo": [("slate.content", "ADVERTISER")],
    "length_wrong": [("slate.claims", "LENGTH"), ("slate.content", "LENGTH")],
    "edge_line": [("video.blanking", None)],
    "clipping": [("audio.clipping", None)],
    "black_run": [("video.black_runs", None)],
    "loud": [("audio.loudness", None)],
}


@needs_ocr
def test_errors_label_colon_spot(media, tmp_path):
    rc, fs = check(media, "errors", tmp_path / "e", "spot_delivery")
    got = {(f["check_id"], (f.get("evidence") or [{}])[0].get("kind")) for f in problems(fs)}
    got_checks = {c for c, _ in got}
    for err in media["files"]["errors"][1]:
        for cid, kind in EXPECT.get(err["id"], []):
            assert (cid, kind) in got if kind else cid in got_checks, (err["id"], cid, kind)
    expected_slate = {k for e in media["files"]["errors"][1] for k in EXPECT.get(e["id"], [])
                      if k[0].startswith("slate.")}
    extra = {(c, k) for c, k in got if c.startswith("slate.")} - expected_slate
    assert extra == set(), f"unexpected slate findings: {extra}"
    assert rc == 2
