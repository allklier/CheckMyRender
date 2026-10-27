"""D31 end to end on generated media: a job folder (job.yaml naming spec, profile, waivers,
report folder) drives `cmr check FILE` and `cmr waive` with no other options; waivers carry the
job id and only apply to their own job. Also the one-track demo for quick start A
(make_media case `one_track` against templates/specs/spec_review_render.yaml)."""
import json
import os
import shutil
import tempfile
from pathlib import Path

import jsonschema
import pytest
import yaml

import make_media
from cmr.cli import main
from cmr.schemas import REPORT_SCHEMA

ROOT = Path(__file__).resolve().parents[1]
BLACK = "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(n,30,49)'"


@pytest.fixture(scope="module")
def media():
    out = Path(os.environ.get("CMR_TEST_MEDIA", Path(tempfile.gettempdir()) / "cmr_test_media"))
    return {"black": make_media.make(out, "job_black", vf_extra=BLACK),
            "one_track": make_media.make(out, "one_track", **make_media.CASES["one_track"])}


def job_folder(d: Path, media_file: Path, job_id="tjob", **extra) -> Path:
    """A job folder: job.yaml + spec copy + the render under renders/ (job-relative)."""
    d.mkdir(parents=True)
    shutil.copy(ROOT / "tests/media/spec_test.yaml", d / "spec.yaml")
    (d / "renders").mkdir()
    os.symlink(media_file, d / "renders" / media_file.name)
    job = {"job_version": 1, "spec": "spec.yaml", "profile": "final", "waivers": "waivers.yaml",
           "output": "reports", "job": {"id": job_id}, **extra}
    (d / "job.yaml").write_text(yaml.safe_dump(job))
    return d / "renders" / media_file.name


def latest(d: Path) -> dict:
    rep = json.loads((d / "cmr_report_latest.json").read_text())
    jsonschema.validate(rep, REPORT_SCHEMA)
    return rep


def black(rep):
    return [f for f in rep["files"][0]["findings"] if f["check_id"] == "video.black_runs"]


def test_job_folder_needs_no_options(media, tmp_path, monkeypatch, capsys):
    jd = tmp_path / "job"
    f = job_folder(jd, media["black"])
    monkeypatch.chdir(jd)
    assert main(["check", "renders/" + f.name]) == 2
    io = capsys.readouterr()
    assert "job file:" in io.err and "(job tjob)" in io.err
    assert "JOB tjob" in io.out and "report: " in io.out
    rep = latest(jd / "reports")
    assert rep["schema_version"] == "1.5" and rep["summary"]["job_id"] == "tjob"
    assert rep["profile"]["name"] == "final" and rep["spec"]["job"]["path"].endswith("job.yaml")
    assert [x["severity"] for x in black(rep)] == ["FAIL"]

    # explicit options win over the job file: profile, report folder
    assert main(["check", "renders/" + f.name, "--profile", "review", "-o", str(tmp_path / "o")]) == 0
    rep = latest(tmp_path / "o")
    assert rep["profile"]["name"] == "review" and rep["summary"]["job_id"] == "tjob"

    # --no-job: today's behaviour (needs --spec/--profile)
    assert main(["check", "renders/" + f.name, "--no-job"]) == 3
    assert "--spec and --profile required" in capsys.readouterr().err


def test_job_dir_from_elsewhere_and_old_form(media, tmp_path, monkeypatch):
    jd = tmp_path / "job"
    f = job_folder(jd, media["black"])
    monkeypatch.chdir(tmp_path)
    assert main(["check", str(f), "--job-dir", str(jd)]) == 2
    assert latest(jd / "reports")["summary"]["job_id"] == "tjob"
    # the pre-D31 form, with a job file that has none of the new keys
    old = tmp_path / "old_job.yaml"
    old.write_text("job_version: 1\njob: {agency: X}\n")
    assert main(["check", str(f), "--profile", "final", "--spec", str(jd / "spec.yaml"),
                 "--job", str(old), "--report", "json", "-o", str(tmp_path / "old")]) == 2
    rep = latest(tmp_path / "old")
    assert rep["summary"]["job_id"] is None and "waivers" not in rep


def test_waivers_are_per_job(media, tmp_path, monkeypatch, capsys):
    jd = tmp_path / "job"
    f = job_folder(jd, media["black"])
    monkeypatch.chdir(jd)
    rel = "renders/" + f.name
    assert main(["check", rel]) == 2
    capsys.readouterr()
    shutil.copy(jd / "reports/cmr_report_latest.json", jd / "tjob_report.json")
    assert main(["waive", "reports/cmr_report_latest.json"]) == 0      # list: job-relative path
    listing = capsys.readouterr().out
    assert f"({rel})" in listing and str(tmp_path) not in listing
    n = next(l.split()[0] for l in listing.splitlines() if "video.black_runs" in l)
    assert main(["waive", "reports/cmr_report_latest.json", "--finding", n,
                 "--reason", "cut to black"]) == 0
    assert "(job tjob)" in capsys.readouterr().out
    wl = yaml.safe_load((jd / "waivers.yaml").read_text())["waivers"]
    assert [(w["id"], w["job"]) for w in wl] == [("W0001", "tjob")]

    assert main(["check", rel]) == 0                    # the job's waiver file is picked up
    rep = latest(jd / "reports")
    assert black(rep)[0]["waiver_ref"] == "W0001" and rep["waivers"]["foreign"] == []

    # the same waiver file in another job: skipped, listed as foreign
    y = yaml.safe_load((jd / "job.yaml").read_text())
    y["job"]["id"] = "other"
    (jd / "job.yaml").write_text(yaml.safe_dump(y))
    capsys.readouterr()
    assert main(["check", rel]) == 2
    rep = latest(jd / "reports")
    assert black(rep)[0]["severity"] == "FAIL" and not black(rep)[0]["waiver_ref"]
    assert [(w["id"], w["job"]) for w in rep["waivers"]["foreign"]] == [("W0001", "tjob")]
    assert "foreign (job tjob, not applied): W0001" in capsys.readouterr().out

    # a report of job tjob cannot be waived into job `other` by accident
    assert main(["waive", "tjob_report.json", "--finding", n, "--reason", "x"]) == 3
    assert "the report is from job 'tjob'" in capsys.readouterr().err

    # a cross-job category waiver (job "*") applies in any job
    assert main(["waive", "--category", "video.black_runs", "--max-severity", "fail",
                 "--reason", "house rule", "--any-job"]) == 0
    assert main(["check", rel]) == 0
    rep = latest(jd / "reports")
    assert [w["id"] for w in rep["waivers"]["applied"]] == ["W0002"]
    wl = yaml.safe_load((jd / "waivers.yaml").read_text())["waivers"]
    assert wl[1]["job"] == "*"


def test_waivers_without_job_field_still_apply(media, tmp_path, monkeypatch):
    """Waiver files written before D31 (no `job`) keep working inside a job."""
    jd = tmp_path / "job"
    f = job_folder(jd, media["black"])
    monkeypatch.chdir(jd)
    assert main(["waive", "--category", "video.black_runs", "--max-severity", "fail",
                 "--reason", "old", "--no-job", "-o", "waivers.yaml"]) == 0
    assert "job" not in yaml.safe_load((jd / "waivers.yaml").read_text())["waivers"][0]
    assert main(["check", "renders/" + f.name]) == 0


def test_missing_spec_key_message(media, tmp_path, monkeypatch, capsys):
    jd = tmp_path / "job"
    f = job_folder(jd, media["black"])
    spec = yaml.safe_load((jd / "spec.yaml").read_text())
    del spec["audio"]["loudness"]
    (jd / "spec.yaml").write_text(yaml.safe_dump(spec))
    monkeypatch.chdir(jd)
    assert main(["check", "renders/" + f.name]) == 3
    err = capsys.readouterr().err
    assert "${spec.audio.loudness}" in err and "used by profile check audio.loudness" in err
    assert "`audio: loudness:` in the spec sheet" in err and "USERGUIDE.md" in err


def test_quick_start_a_one_track_demo(media, tmp_path):
    """The review-render template edited as the guide's table says (no audio edit) passes
    the one_track demo file: template and demo agree on one audio stream."""
    spec = yaml.safe_load((ROOT / "templates/specs/spec_review_render.yaml").read_text())
    spec["job"].update(title_code="SHOWNAME", episode="101", version="v03")
    spec["video"]["codec_profile"] = "422 HQ"
    spec["timing"]["program_duration_frames"] = 48
    s = tmp_path / "spec.yaml"
    s.write_text(yaml.safe_dump(spec))
    assert len(spec["audio"]["tracks"]) == 1
    rc = main(["check", str(media["one_track"]), "--no-job", "--profile", "review", "--spec", str(s),
               "--report", "json", "-o", str(tmp_path / "r")])
    rep = latest(tmp_path / "r")
    probs = [(x["check_id"], x["severity"]) for x in rep["files"][0]["findings"]
             if x["severity"] not in ("PASS", "SKIPPED")]
    assert rc == 0 and probs == []
