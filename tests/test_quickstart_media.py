"""templates/quickstart/ works UNEDITED with the demo spot (tests/slate/make_spot.py --short):
copy the folder, add the media, run `cmr check FILE` with no options (D31).

Media as tests/test_slate_media.py: $CMR_SLATE_MEDIA (must be a --short OUTDIR) else generated
--short into $CMR_TEST_MEDIA/slate_short (numpy + Pillow). Without tesseract the OCR checks
are ERROR and everything else must pass; with tesseract (CMR_TESSERACT) the clean spot passes.
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
OCR_CHECKS = {"slate.present", "slate.consistency", "slate.content", "slate.claims"}


@pytest.fixture
def quickstart(tmp_path, monkeypatch):
    d = _media_dir()
    man = yaml.safe_load((d / "manifest.yaml").read_text())
    if not man.get("short"):
        pytest.skip("the quick start matches make_spot.py --short media only")
    qs = tmp_path / "qs"
    shutil.copytree(ROOT / "templates" / "quickstart", qs)
    os.symlink(d, qs / "spots")
    monkeypatch.chdir(qs)
    clean = next(Path(f["path"]) for f in man["files"] if Path(f["path"]).parts[0] == "clean")
    return qs, "spots/" + str(clean)


def run(qs, f):
    rc = main(["check", f])
    rep = json.loads((qs / "reports" / "cmr_report_latest.json").read_text())
    probs = [(x["check_id"], x["severity"]) for x in rep["files"][0]["findings"]
             if x["severity"] not in ("PASS", "SKIPPED")]
    return rc, rep, probs


def test_quickstart_without_ocr(quickstart, tmp_path, monkeypatch):
    monkeypatch.setenv(ocr.ENV_BIN, str(tmp_path / "no_tesseract"))
    qs, f = quickstart
    rc, rep, probs = run(qs, f)
    assert rep["summary"]["job_id"] == "demo_sb_league" and rep["profile"]["name"] == "spot_review"
    assert rc == 3 and {c for c, s in probs} == OCR_CHECKS and {s for c, s in probs} == {"ERROR"}


@pytest.mark.skipif(not has_tesseract(), reason="tesseract not available (CMR_TESSERACT)")
def test_quickstart_clean_spot_passes(quickstart):
    qs, f = quickstart
    rc, rep, probs = run(qs, f)
    assert rc == 0 and probs == [] and rep["summary"]["job_id"] == "demo_sb_league"
