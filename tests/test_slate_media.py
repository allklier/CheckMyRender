"""Slate checks end to end on the generated spot media (tests/slate/make_spot.py, D27).

Media: $CMR_SLATE_MEDIA (an OUTDIR of make_spot.py with clean/, errors/, manifest.yaml; full
or --short), else generated with --short into $CMR_TEST_MEDIA/slate_short (needs numpy +
Pillow). OCR tests need tesseract (CMR_TESSERACT / PATH) and SKIP without it; the bars,
filename, claims-without-OCR and tesseract-missing tests run anywhere.
Spec/job: examples/spot_xr (spec program length and CSV length follow the manifest).
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


def _media_dir() -> Path:
    if os.environ.get("CMR_SLATE_MEDIA"):
        return Path(os.environ["CMR_SLATE_MEDIA"])
    out = Path(os.environ.get("CMR_TEST_MEDIA", Path(tempfile.gettempdir()) / "cmr_test_media")) / "slate_short"
    if not (out / "manifest.yaml").exists():
        try:
            import numpy, PIL  # noqa: F401
        except ImportError:
            pytest.skip("no slate media: set CMR_SLATE_MEDIA or install numpy + Pillow to generate")
        subprocess.run([sys.executable, str(ROOT / "tests" / "slate" / "make_spot.py"), str(out),
                        "--short"], check=True, capture_output=True)
    return out


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = _media_dir()
    man = yaml.safe_load((d / "manifest.yaml").read_text())
    files = {Path(f["path"]).parts[0]: (d / f["path"], f["errors"]) for f in man["files"]}
    short = man.get("short", False)
    # spec + job + CSV for this media length (examples are the :60 version)
    tmp = tmp_path_factory.mktemp("spot_xr")
    spec = yaml.safe_load((EX / "spec_spot_xr.yaml").read_text())
    job = yaml.safe_load((EX / "job.yaml").read_text())
    csv = (EX / "deliverables.csv").read_text()
    if short:
        spec["timing"]["program_duration_frames"] = 90
        csv = csv.replace("Pat,60,", "Pat,03,")
    (tmp / "spec.yaml").write_text(yaml.safe_dump(spec))
    (tmp / "deliverables.csv").write_text(csv)
    job["slate"]["template"] = str(EX / "slates" / "xr_standard.yaml")
    (tmp / "job.yaml").write_text(yaml.safe_dump(job))
    return {"files": files, "spec": str(tmp / "spec.yaml"), "job": str(tmp / "job.yaml"),
            "short": short}


def has_tesseract() -> bool:
    try:
        ocr.resolve_bin(ocr.tesseract_bin({}))
        return True
    except ocr.OCRUnavailable:
        return False


needs_ocr = pytest.mark.skipif(not has_tesseract(), reason="tesseract not available (CMR_TESSERACT)")


def check(media, which, out, profile="spot_review", *extra):
    f = media["files"][which][0]
    rc = main(["check", str(f), "--profile", profile, "--spec", media["spec"], "--job", media["job"],
               "--report", "json", "-o", str(out), "--max-findings", "0", *extra])
    return rc, json.loads((out / "cmr_report_latest.json").read_text())["files"][0]["findings"]


def problems(findings):
    return [f for f in findings if f["severity"] not in ("PASS", "SKIPPED")]


# --- anywhere (no OCR) ----------------------------------------------------------------------
def test_bars_and_filename_without_ocr(media, tmp_path, monkeypatch):
    monkeypatch.setenv(ocr.ENV_BIN, str(tmp_path / "no_tesseract"))
    rc, fs = check(media, "clean", tmp_path / "c")
    errs = [f for f in fs if f["severity"] == "ERROR"]
    assert rc == 3 and {f["check_id"] for f in errs} == {"slate.present", "slate.consistency",
                                                         "slate.content", "slate.claims"}
    assert all("tesseract not found" in f["message"] for f in errs)
    assert [f for f in problems(fs) if f["severity"] != "ERROR"] == []
    rc, fs = check(media, "errors", tmp_path / "e")
    bars = {f["evidence"][0]["kind"]: f for f in fs if f["check_id"] == "slate.bars"}
    assert set(bars) == {"colour:black", "colour:red"}
    assert "elevated black" in bars["colour:black"]["message"] and bars["colour:red"]["severity"] == "WARN"
    assert any(f["check_id"] == "filename" and f["severity"] == "FAIL" for f in fs)


def test_blanking_ignores_slate_segment(media, tmp_path, monkeypatch):
    monkeypatch.setenv(ocr.ENV_BIN, str(tmp_path / "no_tesseract"))
    rc, fs = check(media, "clean", tmp_path / "c", "spot_delivery")
    assert [f for f in fs if f["check_id"] == "video.blanking" and f["severity"] != "PASS"] == []


# --- OCR (tesseract) -----------------------------------------------------------------------
@needs_ocr
def test_clean_spot_is_clean(media, tmp_path):
    rc, fs = check(media, "clean", tmp_path / "c", "spot_delivery")
    assert problems(fs) == [] and rc == 0


EXPECT = {   # manifest error id -> (check_id, evidence kind or None) that must be reported
    "adid_wrong": [("slate.content", "AD-ID"), ("slate.content", "TITLE")],
    "frame_short": [("timing.frame_count", None), ("slate.claims", "DURATION")],
    "audio_mono": [("slate.claims", "AUDIO")],
    "title_res": [("slate.claims", "TITLE"), ("filename", None)],
    "bars_black": [("slate.bars", "colour:black")],
    "bars_hue": [("slate.bars", "colour:red")],
    "date_old": [("slate.content", "DATE")],
    "late_differ": [("slate.consistency", "ARTIST")],
    "client_typo": [("slate.content", "CLIENT")],
    "edge_line": [("video.blanking", None)],
    "clipping": [("audio.clipping", None)],
    "black_run": [("video.black_runs", None)],
    "loud": [("audio.loudness", None)],
}


@needs_ocr
def test_errors_spot_reports_every_planted_error(media, tmp_path):
    rc, fs = check(media, "errors", tmp_path / "e", "spot_delivery")
    got = {(f["check_id"], (f.get("evidence") or [{}])[0].get("kind")) for f in problems(fs)}
    got_checks = {c for c, _ in got}
    for err in media["files"]["errors"][1]:
        for cid, kind in EXPECT.get(err["id"], []):
            assert (cid, kind) in got if kind else cid in got_checks, (err["id"], cid, kind)
    slate = [f for f in problems(fs) if f["check_id"].startswith("slate.")]
    expected_slate = {k for e in media["files"]["errors"][1] for k in EXPECT.get(e["id"], [])
                      if k[0].startswith("slate.")}
    extra = {(f["check_id"], f["evidence"][0]["kind"]) for f in slate} - expected_slate
    assert extra == set(), f"unexpected slate findings: {extra}"
    assert rc == 2


@needs_ocr
def test_slate_waiver_by_text(media, tmp_path):
    rc, fs = check(media, "errors", tmp_path / "r1")
    w = tmp_path / "w.yaml"
    n = 1 + next(i for i, f in enumerate(json.loads((tmp_path / "r1" / "cmr_report_latest.json").read_text())
                                         ["files"][0]["findings"])
                 if f["check_id"] == "slate.content" and f["evidence"][0]["kind"] == "CLIENT")
    assert main(["waive", str(tmp_path / "r1" / "cmr_report_latest.json"), "--finding", str(n),
                 "--reason", "client approved the spelling", "-o", str(w)]) == 0
    assert yaml.safe_load(w.read_text())["waivers"][0]["fingerprint"]["values"] == ["CLIENT", "Sample Bugrers"]
    rc, fs = check(media, "errors", tmp_path / "r2", "spot_review", "--waivers", str(w))
    (c,) = [f for f in fs if f["check_id"] == "slate.content" and f["evidence"][0]["kind"] == "CLIENT"]
    assert c["severity"] == "PASS" and c["waiver_ref"] == "W0001"
