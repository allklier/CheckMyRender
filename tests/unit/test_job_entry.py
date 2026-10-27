"""D31: job.yaml as the entry point (no media): finding the job file, its run settings,
CLI precedence, job-scoped waivers, missing-key messages, console/doctor output, the suite
runner's --quality pass-through."""
import datetime
import sys
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from cmr import cli, waivers
from cmr.config import (ConfigError, MissingSpecKey, build_spec, find_job, job_settings,
                        resolve_profile)
from cmr.report.console import render

ROOT = Path(__file__).resolve().parents[2]
TODAY = datetime.date(2026, 10, 26)


def write_job(d: Path, **top) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    data = {"job_version": 1, **top}
    (d / "job.yaml").write_text(yaml.safe_dump(data))
    return d / "job.yaml"


# --- finding the job file ---------------------------------------------------------------------
def test_find_job_order(tmp_path):
    j = write_job(tmp_path / "a")
    assert find_job(cwd=tmp_path / "a") == tmp_path / "a" / "job.yaml"          # ./job.yaml
    assert find_job(no_job=True, cwd=tmp_path / "a") is None                     # --no-job
    assert find_job(cwd=tmp_path) is None                                        # none here
    assert find_job(job_dir=str(tmp_path / "a")) == j                            # --job-dir
    assert find_job(job=str(j), cwd=tmp_path) == j                               # --job
    with pytest.raises(ConfigError, match="not found"):
        find_job(job=str(tmp_path / "nope.yaml"))
    with pytest.raises(ConfigError, match="not found"):
        find_job(job_dir=str(tmp_path))


def test_job_settings_paths_relative_to_job_file(tmp_path):
    j = write_job(tmp_path / "job", spec="specs/s.yaml", profile="spot_review",
                  waivers="w/waivers.yaml", output="reports", job={"id": "sb_league"})
    s = job_settings(j)
    base = (tmp_path / "job").resolve()
    assert s["id"] == "sb_league" and s["dir"] == str(base)
    assert s["spec"] == str(base / "specs/s.yaml") and s["output"] == str(base / "reports")
    assert s["profile"] == "spot_review"                     # stock name stays a name
    assert s["waivers"] == str(base / "w/waivers.yaml") and s["waivers_named"]
    s = job_settings(write_job(tmp_path / "j2", profile="../profiles/hl.yaml"))
    assert s["profile"] == str((tmp_path / "profiles/hl.yaml").resolve())   # a path resolves
    assert s["spec"] is None and s["output"] is None and s["id"] is None
    assert s["waivers"] == str((tmp_path / "j2").resolve() / "waivers.yaml") and not s["waivers_named"]
    s = job_settings(write_job(tmp_path / "j3", profile="~/qc/p.yaml", spec="/abs/s.yaml"))
    assert s["profile"] == str(Path.home() / "qc/p.yaml") and s["spec"] == "/abs/s.yaml"


def test_job_schema(tmp_path):
    with pytest.raises(ConfigError, match="job/id"):
        job_settings(write_job(tmp_path / "a", job={"id": "has space"}))
    with pytest.raises(ConfigError, match="job/id"):
        job_settings(write_job(tmp_path / "b", job={"id": "*"}))           # reserved
    with pytest.raises(ConfigError, match="reports"):
        job_settings(write_job(tmp_path / "c", reports="r"))               # typo: not a key
    for f in ("examples/spot_xr/job.yaml", "examples/spot_xr/job_label_colon.yaml",
              "templates/slate/job.yaml", "templates/starter/job.yaml",
              "templates/starter/job_no_slate.yaml"):
        assert job_settings(ROOT / f)["id"]


def test_old_job_file_still_merges(tmp_path):
    """A job file without the D31 keys works as before (job sections merged into the spec)."""
    spec = tmp_path / "spec.yaml"
    spec.write_text("spec_version: 1\njob: {client: X}\n")
    j = write_job(tmp_path, job={"agency": "A"})
    s, inputs = build_spec(spec, j)
    assert s["job"] == {"client": "X", "agency": "A"} and inputs["job"]["path"] == str(j)


# --- CLI precedence ---------------------------------------------------------------------------
def ns(**kw):
    d = dict(spec=None, profile=None, waivers=None, output=None, report=None, files=["f.mov"])
    d.update(kw)
    return Namespace(**d)


def test_apply_job_fills_only_what_the_command_line_left_out(tmp_path):
    j = write_job(tmp_path, spec="s.yaml", profile="review", output="reports", job={"id": "x1"})
    js = job_settings(j)
    a = ns()
    assert cli.apply_job(a, js) is None
    assert (a.spec, a.profile, a.output, a.report) == (js["spec"], "review", js["output"], "json")
    assert a.waivers is None                                    # waivers.yaml does not exist yet
    (tmp_path / "waivers.yaml").write_text("waiver_version: 1\nwaivers: []\n")
    a = ns(spec="mine.yaml", profile="release", output="o", waivers="w.yaml")
    assert cli.apply_job(a, js) is None
    assert (a.spec, a.profile, a.output, a.report, a.waivers) == ("mine.yaml", "release", "o", "json", "w.yaml")
    a = ns()
    cli.apply_job(a, js)
    assert a.waivers == js["waivers"]                           # the job's file once it exists


def test_apply_job_reports_what_is_missing(tmp_path):
    assert "no job.yaml in the current folder" in cli.apply_job(ns(), None)
    js = job_settings(write_job(tmp_path, spec="s.yaml"))
    msg = cli.apply_job(ns(), js)
    assert msg.startswith("--profile required") and "names none" in msg
    assert cli.apply_job(ns(spec="s", profile="p"), None) is None   # today's form, no job


# --- missing ${spec.*} keys ---------------------------------------------------------------
def test_missing_key_names_field_spec_sheet_and_check(tmp_path):
    spec = {"spec_version": 1, "video": {}}
    with pytest.raises(MissingSpecKey) as e:
        resolve_profile("final", spec)
    msg = e.value.explain("specs/s.yaml", None)
    assert "used by profile check" in msg and "spec sheet specs/s.yaml" in msg
    assert "Fill in `" in msg and "USERGUIDE.md" in msg and "enabled: false" in msg
    assert str(e.value).startswith("missing spec key ${spec.")         # old text kept


def test_missing_job_section_key_points_to_job_file(tmp_path):
    e = MissingSpecKey("spec.job.title_code", "job.title_code", "job.title_code")
    e.where = "profile check filename (profile 'p')"
    msg = e.explain("s.yaml", "/jobs/x/job.yaml")
    assert "Fill in `job: title_code:` in the job file /jobs/x/job.yaml or in the spec sheet s.yaml" in msg
    assert "none is in use" in e.explain("s.yaml", None)
    e = MissingSpecKey("spec.audio.loudness", "audio.loudness", "audio")
    assert "job file" not in e.explain("s.yaml", "j.yaml") and "`audio: loudness:`" in e.explain()


def test_missing_key_in_job_section(tmp_path):
    spec = tmp_path / "spec.yaml"
    spec.write_text("spec_version: 1\n")
    j = write_job(tmp_path, job={"naming_pattern": "${spec.job.code}_x.mov"})
    with pytest.raises(MissingSpecKey) as e:
        build_spec(spec, j)
    assert e.value.where == "`job:` section of the spec sheet or job file"


def test_cli_prints_explained_missing_key(tmp_path, capsys):
    spec = tmp_path / "s.yaml"
    spec.write_text("spec_version: 1\nvideo: {}\n")
    assert cli.main(["profiles", "show", "final", "--spec", str(spec)]) == 3
    err = capsys.readouterr().err
    assert "used by profile check" in err and f"spec sheet {spec}" in err


# --- waivers per job --------------------------------------------------------------------------
def test_split_job():
    ws = [{"id": "W1"}, {"id": "W2", "job": "a"}, {"id": "W3", "job": "b"}, {"id": "W4", "job": "*"}]
    mine, foreign = waivers.split_job(ws, "a")
    assert [w["id"] for w in mine] == ["W1", "W2", "W4"] and [w["id"] for w in foreign] == ["W3"]
    mine, foreign = waivers.split_job(ws, None)           # a run without a job id
    assert [w["id"] for w in mine] == ["W1", "W4"] and [w["id"] for w in foreign] == ["W2", "W3"]


def test_summary_lists_foreign_and_job():
    mine = [{"id": "W1", "check_id": "video.black_runs", "reason": "r", "job": "a"}]
    foreign = [{"id": "W2", "check_id": "audio.clipping", "reason": "r2", "job": "b"}]
    s = waivers.summary("w.yaml", mine, {}, TODAY, foreign)
    assert [w["id"] for w in s["stale"]] == ["W1"] and s["stale"][0]["job"] == "a"
    assert [(w["id"], w["job"]) for w in s["foreign"]] == [("W2", "b")]
    assert waivers.summary("w.yaml", mine, {}, TODAY)["foreign"] == []


def test_make_and_load_job_field(tmp_path):
    fd = {"check_id": "video.black_runs", "stream": 0, "frame_start": 1, "frame_end": 2}
    w = waivers.make_waiver("W0001", fd, {"type": "video_dhash"}, "ok", TODAY, job="a")
    c = waivers.make_category("W0002", "video.blanking", "warn", "ok", TODAY, job="*")
    n = waivers.make_waiver("W0003", fd, {"type": "video_dhash"}, "ok", TODAY)
    assert w["job"] == "a" and c["job"] == "*" and "job" not in n
    f = tmp_path / "w.yaml"
    waivers.save(f, [w, c, n])
    assert [x.get("job") for x in waivers.load(f)] == ["a", "*", None]
    f.write_text("waiver_version: 1\nwaivers:\n- {id: W1, job: 7, check_id: video.black_runs, "
                 "stream: 0, frame_start: 1, frame_end: 2, fingerprint: {}, reason: r}\n")
    with pytest.raises(ConfigError, match="job"):
        waivers.load(f)


def test_waive_job_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = Namespace(job=None, job_dir=None, no_job=False, output=None, any_job=False)
    assert cli._waive_job(a) is None and a.output == "waivers.yaml" and a.stamp is None
    write_job(tmp_path, job={"id": "sb"}, waivers="w/my.yaml")
    a = Namespace(job=None, job_dir=None, no_job=False, output=None, any_job=False)
    cli._waive_job(a)
    assert a.output == str(tmp_path.resolve() / "w/my.yaml") and a.stamp == "sb"
    a = Namespace(job=None, job_dir=None, no_job=False, output="x.yaml", any_job=True)
    cli._waive_job(a)
    assert a.output == "x.yaml" and a.stamp == "*"


def test_short_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "renders").mkdir()
    assert cli._short("renders/a.mov", str(tmp_path)) == "renders/a.mov"
    assert cli._short("/elsewhere/deep/b.mov", str(tmp_path)) == "b.mov"
    assert cli._short(str(tmp_path / "c.mov"), None) == "c.mov"          # cwd without a job


# --- report / console / doctor ----------------------------------------------------------------
def test_console_job_and_foreign_lines():
    rep = {"files": [], "summary": {"worst": "PASS", "exit_code": 0, "counts": {"PASS": 1},
                                    "job_id": "sb"},
           "waivers": {"file": "w.yaml", "applied": [], "content_changed": [], "stale": [],
                       "expired": [], "foreign": [{"id": "W2", "check_id": "audio.clipping",
                                                   "reason": "r", "job": "other"}]}}
    out = render(rep)
    assert "JOB sb\nRESULT PASS" in out and "foreign 1" in out
    assert "foreign (job other, not applied): W2 audio.clipping: r" in out
    rep["summary"]["job_id"], rep["waivers"]["foreign"] = None, []
    out = render(rep)
    assert "JOB" not in out and "foreign" not in out


def test_doctor_prints_log_and_job(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "\nlog: " in out and "job.yaml in the current folder: none" in out
    write_job(tmp_path, job={"id": "sb"})
    cli.main(["doctor"])
    assert "(job sb)" in capsys.readouterr().out
    (tmp_path / "job.yaml").write_text("job_version: 2\n")
    cli.main(["doctor"])
    assert "INVALID" in capsys.readouterr().out


# --- suite runner: --quality instead of a wrapper profile ----------------------------------------
def test_suite_runner_passes_quality(tmp_path):
    sys.path.insert(0, str(ROOT / "tests" / "suite"))
    import run_suite
    media = tmp_path / "m.mov"
    media.write_bytes(b"x")
    (tmp_path / "spec.yaml").write_text("spec_version: 1\n")
    suite = {"roots": {"r": str(tmp_path)}}
    r = {"id": "x", "file": "r:m.mov", "project": "p", "spec": "spec.yaml", "profile": "review"}
    out = tmp_path / "runs"
    out.mkdir()
    args = Namespace(meta=False, quality="fast", max_size_mb=0, mode="background", dry_run=True,
                     timeout=0, update_baseline=False)
    res = run_suite.run_one(r, args, suite, tmp_path, out, {})
    cmd = res["cmd"]
    assert cmd[cmd.index("--quality") + 1] == "fast" and "--no-job" in cmd
    assert not (out / "profiles").exists()                          # no wrapper profile any more
    args.meta = True
    cmd = run_suite.run_one(r, args, suite, tmp_path, out, {})["cmd"]
    wp = Path(cmd[cmd.index("--profile") + 1])
    assert "--quality" not in cmd and wp.parent == out / "profiles"
    checks = yaml.safe_load(wp.read_text())["checks"]
    assert checks["video.blanking"] == {"enabled": False} and "video.codec" not in checks
