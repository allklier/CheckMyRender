"""Diagnostic log on real pipeline runs (D29): privacy grep in default vs --log-private mode, and a
crashing check logged with its traceback while the report keeps the ERROR finding."""
import json
import os
import tempfile
from pathlib import Path

import pytest

import make_media
from cmr import engine, log
from cmr.cli import main
from test_slate_media import has_tesseract, media as slate_media  # noqa: F401 (fixture)

ROOT = Path(__file__).resolve().parents[1]
SPEC = str(ROOT / "tests" / "media" / "spec_test.yaml")
SECRET_DIR, SECRET_STEM = "ProjectFalconRidge", "ZebraSecretClient_Spot_v07"


@pytest.fixture(scope="module")
def secret_file():
    out = Path(os.environ.get("CMR_TEST_MEDIA", Path(tempfile.gettempdir()) / "cmr_test_media"))
    return make_media.make(out, SECRET_DIR, filename=SECRET_STEM + ".mov")


def run_logged(tmp_path, argv, private):
    lf = tmp_path / ("private.log" if private else "default.log")
    rc = main([*argv, "--log-level", "debug", "--log-file", str(lf),
               *(["--log-private"] if private else [])])
    log.setup("off")
    return rc, lf.read_text()


def test_privacy_default_vs_private(secret_file, tmp_path, monkeypatch):
    monkeypatch.delenv("CMR_LOG_PRIVATE", raising=False)
    argv = ["check", str(secret_file), "--profile", "final", "--spec", SPEC,
            "--report", "json", "-o", str(tmp_path / "r")]
    _, text = run_logged(tmp_path, argv, private=False)
    for s in (SECRET_STEM, SECRET_DIR, str(secret_file.parent), str(tmp_path)):
        assert s not in text, s
    assert "<path#" in text and "env privacy: default" in text and "main pass" in text
    _, text = run_logged(tmp_path, argv, private=True)
    assert SECRET_STEM in text and SECRET_DIR in text and "env privacy: PRIVATE" in text


def test_privacy_slate_text(slate_media, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.delenv("CMR_LOG_PRIVATE", raising=False)
    f = slate_media["files"]["clean"][0]
    argv = ["check", str(f), "--profile", "spot_delivery", "--spec", slate_media["spec"],
            "--job", slate_media["job"], "--report", "json", "-o", str(tmp_path / "r")]
    ocr = has_tesseract()
    slate_words = ["Northwind Advertising", "Sample Burgers", "Alex Example"] if ocr else []
    _, text = run_logged(tmp_path, argv, private=False)
    for s in [f.stem, "League 2026", "ABCD1234567H", *slate_words]:
        assert s not in text, s
    if ocr:
        assert "slate OCR" in text and "<text#" in text
    _, text = run_logged(tmp_path, argv, private=True)
    assert f.stem in text
    for s in slate_words:
        assert s in text, s


def test_check_crash_logged_with_traceback(secret_file, tmp_path, monkeypatch):
    engine.load_checks()

    def crash(ctx, cands):
        raise ValueError("planted crash for the log test")
    monkeypatch.setattr(engine.REGISTRY["container.format"], "evaluate", crash)
    rc, text = run_logged(tmp_path, ["check", str(secret_file), "--profile", "final", "--spec", SPEC,
                                     "--report", "json", "-o", str(tmp_path / "r")], private=False)
    assert rc == 3
    rep = json.loads((tmp_path / "r" / "cmr_report_latest.json").read_text())
    errs = [x for x in rep["files"][0]["findings"] if x["check_id"] == "container.format"]
    assert errs[0]["severity"] == "ERROR" and "planted crash" in errs[0]["message"]
    assert "ERROR container.format[" in text and "check crashed" in text
    assert "Traceback (most recent call last)" in text and "planted crash for the log test" in text
