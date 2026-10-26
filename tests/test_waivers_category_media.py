"""Category waivers + findings cap end to end on generated media.

Program = file frames 24..71 (tests/media/spec_test.yaml). Planted with vf_extra:
12 one-frame 1 px black top lines (frames 26, 28, ..., 48 -> 12 sharp `edge` FAILs) and two
5-frame 24 px black side bars (left 55-59, right 62-66 -> 2 `matte` WARNs).
"""
import json
import os
import tempfile
from pathlib import Path

import pytest
import yaml

import make_media
from cmr.cli import main

SPEC = str(Path(__file__).resolve().parents[1] / "tests" / "media" / "spec_test.yaml")
LINES = ",".join(f"drawbox=x=0:y=0:w=iw:h=1:color=black:t=fill:enable='eq(n,{n})'"
                 for n in range(26, 50, 2))
BARS = ("drawbox=x=0:y=0:w=24:h=ih:color=black:t=fill:enable='between(n,55,59)',"
        "drawbox=x=iw-24:y=0:w=24:h=ih:color=black:t=fill:enable='between(n,62,66)'")


@pytest.fixture(scope="module")
def clip():
    out = Path(os.environ.get("CMR_TEST_MEDIA", Path(tempfile.gettempdir()) / "cmr_test_media"))
    return make_media.make(out, "wc_blanking", vf_extra=LINES + "," + BARS)


def check(f, out, *extra):
    rc = main(["check", str(f), "--profile", "final", "--spec", SPEC, "--report", "json",
               "-o", str(out), *extra])
    return rc, json.loads((out / "cmr_report_latest.json").read_text())


def blanking(rep):
    return [x for x in rep["files"][0]["findings"] if x["check_id"] == "video.blanking"]


def test_cap_default_explicit_and_unlimited(clip, tmp_path, capsys):
    rc, rep = check(clip, tmp_path / "d")                        # default cap 10
    f = rep["files"][0]
    assert rc == 2 and rep["schema_version"] == "1.4"
    assert len(blanking(rep)) == 10 and all(x["severity"] == "FAIL" for x in blanking(rep))
    (s,) = f["suppressed"]
    assert (s["check_id"], s["count"], s["worst_severity"]) == ("video.blanking", 4, "FAIL")
    assert s["by_severity"] == {"FAIL": 2, "WARN": 2}
    c = rep["summary"]["counts"]
    assert c["FAIL"] == 12 and c["WARN"] == 2 and rep["summary"]["suppressed"] == 4
    assert "(4 additional video.blanking findings suppressed; worst: FAIL)" in capsys.readouterr().out

    rc, rep = check(clip, tmp_path / "z", "--max-findings", "0")
    assert len(blanking(rep)) == 14 and rep["files"][0]["suppressed"] == []

    prof = yaml.safe_load((Path(__file__).resolve().parents[1] / "profiles" / "final.yaml").read_text())
    prof["name"] = "final_cap"
    prof["checks"]["video.blanking"]["max_findings"] = 3
    pfile = tmp_path / "final_cap.yaml"
    pfile.write_text(yaml.safe_dump(prof))
    rc, rep = check(clip, tmp_path / "p", "--profile", str(pfile))   # later --profile wins
    assert len(blanking(rep)) == 3 and rep["summary"]["check_limits"] == {"video.blanking": 3}
    rc, rep = check(clip, tmp_path / "po", "--profile", str(pfile), "--max-findings", "12")
    assert len(blanking(rep)) == 12                              # explicit flag overrides profile


def test_category_ceiling_class_and_stale(clip, tmp_path, capsys):
    w = tmp_path / "w.yaml"
    assert main(["waive", "--category", "video.blanking", "--max-severity", "warn",
                 "--reason", "side bars are intended", "-o", str(w)]) == 0
    assert main(["waive", "--category", "video.blanking", "--class", "dark_edge", "--edge", "top",
                 "--frames", "0-95", "--max-severity", "fail", "--reason", "dark curtain",
                 "--expires", "2099-01-01", "-o", str(w)]) == 0
    assert yaml.safe_load(w.read_text())["waiver_version"] == 2
    capsys.readouterr()
    rc, rep = check(clip, tmp_path / "r", "--waivers", str(w))
    out = capsys.readouterr().out
    f = rep["files"][0]
    # the WARN ceiling covers the two mattes; the 12 sharp edge FAILs get through
    assert rc == 2 and rep["summary"]["counts"]["FAIL"] == 12 and rep["summary"]["counts"]["WAIVED"] == 2
    assert f["waived_by_category"] == [{
        "check_id": "video.blanking", "waiver_id": "W0001", "count": 2, "worst_raw_severity": "WARN",
        "frame_start": 55, "frame_end": 66, "timecode_start": "01:00:01:07",
        "timecode_end": "01:00:01:18"}]
    assert len(blanking(rep)) == 10 and f["suppressed"][0]["count"] == 2   # waived not capped
    assert [(x["id"], x["count"]) for x in rep["waivers"]["applied"]] == [("W0001", 2)]
    assert [x["id"] for x in rep["waivers"]["stale"]] == ["W0002"]     # no dark_edge on top
    assert "WAIVED  video.blanking: 2 finding(s) by category waiver W0001" in out
    assert "stale: W0002 category video.blanking kind=dark_edge edge=top frames 0-95 <= FAIL" in out

    # an `edge` ceiling FAIL waiver clears the rest: exit 0
    assert main(["waive", "--category", "video.blanking", "--class", "edge", "--max-severity",
                 "fail", "--reason", "known", "-o", str(w)]) == 0
    rc, rep = check(clip, tmp_path / "r2", "--waivers", str(w))
    assert rc == 0 and rep["summary"]["counts"]["WAIVED"] == 14 and blanking(rep) == []


def test_waive_cli_validation(tmp_path, capsys):
    w = str(tmp_path / "w.yaml")
    bad = [["waive", "--category", "video.blanking", "--reason", "r", "-o", w],   # no ceiling
           ["waive", "--category", "video.blanking", "--max-severity", "warn", "-o", w],  # no reason
           ["waive", "--category", "video.nope", "--max-severity", "warn", "--reason", "r", "-o", w],
           ["waive", "--category", "audio.clipping", "--edge", "top", "--max-severity", "warn",
            "--reason", "r", "-o", w],
           ["waive", "--category", "video.blanking", "--frames", "9-3", "--max-severity", "warn",
            "--reason", "r", "-o", w],
           ["waive", "r.json", "--category", "video.blanking", "--max-severity", "warn",
            "--reason", "r", "-o", w],
           ["waive", "r.json", "--class", "edge", "--reason", "r", "-o", w],
           ["waive", "--reason", "r", "-o", w]]
    for argv in bad:
        assert main(argv) == 3, argv
    assert not Path(w).exists()
    with pytest.raises(SystemExit):        # argparse: --edge and --channel are exclusive
        main(["waive", "--category", "video.blanking", "--edge", "top", "--channel", "1",
              "--max-severity", "warn", "--reason", "r", "-o", w])
