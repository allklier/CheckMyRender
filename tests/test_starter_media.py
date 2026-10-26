"""templates/starter/ works UNEDITED with the demo spot (tests/slate/make_spot.py --short):
copy the folder, put the media into media/, run `cmr check media/FILE` with no options (D31),
and `--job job_no_slate.yaml` for the run without slate reading.

Media as tests/test_slate_media.py: $CMR_SLATE_MEDIA (must be a --short OUTDIR) else generated
--short into $CMR_TEST_MEDIA/slate_short (numpy + Pillow). Without tesseract the OCR checks
are ERROR and everything else must pass; with tesseract (CMR_TESSERACT) the clean spot passes
and the errors spot fails slate.content. The no-slate job needs no tesseract.
"""
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from cmr.checks.slate import ocr
from cmr.cli import main
from test_slate_media import _media_dir, has_tesseract

ROOT = Path(__file__).resolve().parents[1]
STARTER = ROOT / "templates" / "starter"
OCR_CHECKS = {"slate.present", "slate.consistency", "slate.content", "slate.claims"}


def test_starter_folder_files():
    """No media: every file is there and both job files load with their profile (exit 0)."""
    for f in ("README.md", "job.yaml", "job_no_slate.yaml", "spec_starter.yaml", "deliverables.csv",
              "slates/slate_template.yaml", "media/README.txt"):
        assert (STARTER / f).is_file(), f
    assert "quickstart" not in "".join(p.read_text() for p in STARTER.rglob("*") if p.is_file())
    for job in ("job.yaml", "job_no_slate.yaml"):
        j = yaml.safe_load((STARTER / job).read_text())
        assert j["spec"] == "spec_starter.yaml" and j["job"]["id"] == "demo_sb_league"
        assert main(["profiles", "show", j["profile"], "--spec", str(STARTER / j["spec"]),
                     "--job", str(STARTER / job)]) == 0, job


@pytest.fixture
def starter(tmp_path, monkeypatch):
    d = _media_dir()
    man = yaml.safe_load((d / "manifest.yaml").read_text())
    if not man.get("short"):
        pytest.skip("the starter folder matches make_spot.py --short media only")
    qs = tmp_path / "starter"
    shutil.copytree(STARTER, qs)
    files = {Path(f["path"]).parts[0]: d / f["path"] for f in man["files"]}
    for kind, src in files.items():               # media/<clean|errors>/<name>.mov
        (qs / "media" / kind).mkdir()
        os.symlink(src, qs / "media" / kind / src.name)
    monkeypatch.chdir(qs)
    return qs, {k: f"media/{k}/{v.name}" for k, v in files.items()}


def run(qs, f, *opts):
    rc = main(["check", f, *opts])
    rep = json.loads((qs / "reports" / "cmr_report_latest.json").read_text())
    probs = [(x["check_id"], x["severity"]) for x in rep["files"][0]["findings"]
             if x["severity"] not in ("PASS", "SKIPPED")]
    return rc, rep, probs


def test_starter_without_ocr(starter, tmp_path, monkeypatch):
    monkeypatch.setenv(ocr.ENV_BIN, str(tmp_path / "no_tesseract"))
    qs, f = starter
    rc, rep, probs = run(qs, f["clean"])
    assert rep["summary"]["job_id"] == "demo_sb_league" and rep["profile"]["name"] == "spot_delivery"
    assert rc == 3 and {c for c, s in probs} == OCR_CHECKS and {s for c, s in probs} == {"ERROR"}


def test_starter_no_slate_job_passes(starter, tmp_path, monkeypatch):
    monkeypatch.setenv(ocr.ENV_BIN, str(tmp_path / "no_tesseract"))   # not needed without slate
    qs, f = starter
    rc, rep, probs = run(qs, f["clean"], "--job", "job_no_slate.yaml")
    assert rc == 0 and probs == [] and rep["profile"]["name"] == "release"
    assert not [x for x in rep["files"][0]["findings"] if x["check_id"].startswith("slate.")]
    assert [x["severity"] for x in rep["files"][0]["findings"] if x["check_id"] == "filename"] == ["PASS"]


@pytest.mark.skipif(not has_tesseract(), reason="tesseract not available (CMR_TESSERACT)")
def test_starter_clean_spot_passes(starter):
    qs, f = starter
    rc, rep, probs = run(qs, f["clean"])
    assert rc == 0 and probs == [] and rep["summary"]["job_id"] == "demo_sb_league"


@pytest.mark.skipif(not has_tesseract(), reason="tesseract not available (CMR_TESSERACT)")
def test_starter_errors_spot_fails(starter):
    qs, f = starter
    rc, rep, probs = run(qs, f["errors"])
    assert rc == 2 and ("slate.content", "FAIL") in probs
