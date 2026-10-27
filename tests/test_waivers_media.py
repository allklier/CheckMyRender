"""Waivers end to end on generated media (D17): check -> waive -> re-check.

Program = file frames 24..71 (tests/media/spec_test.yaml). A 20-frame black run (30-49) is
planted, waived and re-checked (WAIVED, exit 0); a re-render whose run is still black but
carries faint structure (stripes at Y~23, below black_luma 24) is flagged again with the
'content changed' note. Audio: a clipped stretch is waived and re-checked.
"""
import json
import os
import tempfile
from pathlib import Path

import pytest

import make_media
from cmr.cli import main

SPEC = str(Path(__file__).resolve().parents[1] / "tests" / "media" / "spec_test.yaml")
BLACK = "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(n,30,49)'"
# 4 dim stripes (RGB 8 -> Y 23 tv): still a black run, different picture structure
STRIPES = ",".join(f"drawbox=x=iw*{k}/9:y=0:w=iw/9:h=ih:color=0x080808:t=fill:"
                   f"enable='between(n,30,49)'" for k in (1, 3, 5, 7))
CLIP = "volume=24dB:enable='between(t,1.5,1.7)'"


@pytest.fixture(scope="module")
def media():
    out = Path(os.environ.get("CMR_TEST_MEDIA", Path(tempfile.gettempdir()) / "cmr_test_media"))
    return {"wv_black": make_media.make(out, "wv_black", vf_extra=BLACK),
            "wv_black_changed": make_media.make(out, "wv_black_changed", vf_extra=BLACK + "," + STRIPES),
            "wv_clip": make_media.make(out, "wv_clip", af_extra=CLIP)}


def check(f, out, *extra):
    rc = main(["check", str(f), "--profile", "final", "--spec", SPEC, "--report", "json",
               "-o", str(out), *extra])
    return rc, json.loads((out / "cmr_report_latest.json").read_text())


def of(rep, check_id):
    return [x for x in rep["files"][0]["findings"] if x["check_id"] == check_id]


def test_black_run_waived_then_content_changed(media, tmp_path, capsys):
    rc, rep = check(media["wv_black"], tmp_path / "r1")
    assert rc == 2 and [f["severity"] for f in of(rep, "video.black_runs")] == ["FAIL"]
    wfile = tmp_path / "waivers.yaml"
    assert main(["waive", str(tmp_path / "r1" / "cmr_report_latest.json"), "--all-check",
                 "video.black_runs", "--reason", "intended cut to black", "-o", str(wfile)]) == 0
    fp = __import__("yaml").safe_load(wfile.read_text())["waivers"][0]["fingerprint"]
    assert fp["version"] == 2 and fp["plane"] == "y" and fp["ffmpeg"].startswith("ffmpeg version")

    rc, rep = check(media["wv_black"], tmp_path / "r2", "--waivers", str(wfile))
    out = capsys.readouterr().out
    (f,) = of(rep, "video.black_runs")
    assert rc == 0 and rep["summary"]["exit_code"] == 0
    assert f["severity"] == "PASS" and f["raw_severity"] == "FAIL" and f["waiver_ref"] == "W0001"
    assert f["waiver"]["reason"] == "intended cut to black" and f["waiver"]["distance"] == 0
    assert rep["summary"]["counts"]["WAIVED"] == 1 and rep["schema_version"] == "1.5"
    assert [w["id"] for w in rep["waivers"]["applied"]] == ["W0001"]
    assert "WAIVED  video.black_runs" in out and "WAIVED 1" in out   # never hidden

    rc, rep = check(media["wv_black_changed"], tmp_path / "r3", "--waivers", str(wfile))
    (f,) = of(rep, "video.black_runs")
    assert rc == 2 and f["severity"] == "FAIL" and f["waiver_ref"] is None
    assert f["waiver"]["status"] == "content_changed" and f["waiver"]["distance"] > 10
    assert f["waiver"]["note"] == "previously waived (W0001), content changed - re-review"
    assert [w["id"] for w in rep["waivers"]["content_changed"]] == ["W0001"]
    assert "content changed - re-review" in capsys.readouterr().out

    # a clean file: the waiver matches nothing and is listed as stale
    good = make_media.make(Path(media["wv_black"]).parents[1], "good")
    rc, rep = check(good, tmp_path / "r4", "--waivers", str(wfile))
    assert rc == 0 and [w["id"] for w in rep["waivers"]["stale"]] == ["W0001"]


def test_v1_waiver_file_still_matches_and_is_reported(media, tmp_path, capsys):
    """A waiver written before v2 (v1 fingerprint, swscale chain) is matched with the v1 chain
    on the same build and the report says it is v1; re-creating it gives v2."""
    import yaml
    from cmr import waivers
    check(media["wv_black"], tmp_path / "r1")
    wfile = tmp_path / "w.yaml"
    assert main(["waive", str(tmp_path / "r1" / "cmr_report_latest.json"), "--all-check",
                 "video.black_runs", "--reason", "old waiver", "-o", str(wfile)]) == 0
    data = yaml.safe_load(wfile.read_text())
    w = data["waivers"][0]
    w["fingerprint"] = waivers.Fingerprinter(media["wv_black"], "24000/1001").fingerprint(
        w["check_id"], w["stream"], w["fingerprint"]["frames"], version=1)
    del w["fingerprint"]["ffmpeg"]                  # v1 files had no ffmpeg key
    assert w["fingerprint"]["algorithm"] == "dhash64-gray9x8-area"
    wfile.write_text(yaml.safe_dump(data, sort_keys=False))
    capsys.readouterr()
    rc, rep = check(media["wv_black"], tmp_path / "r2", "--waivers", str(wfile))
    (f,) = of(rep, "video.black_runs")
    assert rc == 0 and f["waiver_ref"] == "W0001" and f["waiver"]["fingerprint_version"] == 1
    assert "re-create the waiver to upgrade it to v2" in capsys.readouterr().out


def test_audio_clipping_waived(media, tmp_path):
    rc, rep = check(media["wv_clip"], tmp_path / "r1")
    clip = of(rep, "audio.clipping")
    assert clip and all(f["severity"] == "FAIL" for f in clip)
    wfile = tmp_path / "w.yaml"
    assert main(["waive", str(tmp_path / "r1" / "cmr_report_latest.json"), "--all-check",
                 "audio.clipping", "--reason", "accepted", "-o", str(wfile)]) == 0
    fp = __import__("yaml").safe_load(wfile.read_text())["waivers"][0]["fingerprint"]
    assert fp["type"] == "audio_envelope" and max(c[0] for v in fp["values"] for c in v) == 0.0
    rc, rep = check(media["wv_clip"], tmp_path / "r2", "--waivers", str(wfile))
    assert all(f["waiver_ref"] and f["severity"] == "PASS" for f in of(rep, "audio.clipping"))
    assert rep["summary"]["counts"]["WAIVED"] == len(clip)

    # loudness: waiving the integrated finding (whole program range) must not waive the
    # true-peak finding inside that range (evidence kind is part of the location)
    allf = [x for fi in rep["files"] for x in fi["findings"]]
    n = 1 + next(i for i, x in enumerate(allf) if x["check_id"] == "audio.loudness"
                 and x["evidence"][0]["kind"] == "integrated")
    assert main(["waive", str(tmp_path / "r2" / "cmr_report_latest.json"), "--finding", str(n),
                 "--reason", "loud on purpose", "-o", str(wfile)]) == 0
    rc, rep = check(media["wv_clip"], tmp_path / "r3", "--waivers", str(wfile))
    loud = {x["evidence"][0]["kind"]: x for x in of(rep, "audio.loudness")}
    assert loud["integrated"]["waiver_ref"] == "W0003"     # W0001/W0002 = the two clip runs
    assert loud["true_peak"]["severity"] == "FAIL" and loud["true_peak"]["waiver"] is None


def test_waive_rejects_metadata_finding_and_lists(media, tmp_path, capsys):
    out = Path(media["wv_black"]).parents[1]
    f = make_media.make(out, "no_captions", captions=False)
    check(f, tmp_path)
    rep = json.loads((tmp_path / "cmr_report_latest.json").read_text())
    n = 1 + [x["check_id"] for x in rep["files"][0]["findings"]].index("captions.presence")
    assert main(["waive", str(tmp_path / "cmr_report_latest.json"), "--finding", str(n),
                 "--reason", "x", "-o", str(tmp_path / "w.yaml")]) == 3
    assert main(["waive", str(tmp_path / "cmr_report_latest.json")]) == 0   # listing mode
