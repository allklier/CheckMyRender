"""job.yaml `input:` (which file to check) and spec `job.filename_regex` (no media):
schema, folder resolution, `pick: latest` = highest version number, notices for names that do
not match the regex, config errors, CLI usage error, filename check text, lazy filename tags on
a picture-only spec."""
import os
from argparse import Namespace
from pathlib import Path

import jsonschema
import pytest
import yaml

from cmr import cli
from cmr.config import ConfigError, build_spec, job_settings, pick_input, resolve_profile
from cmr.report import build_report
from cmr.report.console import render
from cmr.schemas import REPORT_SCHEMA

RX = r"^Color_6K_v(?P<version>\d+)\.mov$"
JOB = {"filename_regex": RX}


def write_job(d: Path, **top) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "job.yaml").write_text(yaml.safe_dump({"job_version": 1, **top}))
    return d / "job.yaml"


def folder(d: Path, *names) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_bytes(b"x")
    return d


# --- schema + resolution ----------------------------------------------------------------------
def test_input_schema(tmp_path):
    s = job_settings(write_job(tmp_path / "a", input={"folder": "renders"}))
    assert s["input"] == {"folder": str((tmp_path / "a/renders").resolve()), "pattern": "*",
                          "pick": "latest"}
    assert job_settings(write_job(tmp_path / "b"))["input"] is None
    for bad, key in (({"pattern": "*.mov"}, "folder"),                     # folder required
                     ({"folder": "r", "pick": "newest"}, "pick"),          # only latest
                     ({"folder": "r", "pattern": "sub/*.mov"}, "pattern"),  # not recursive
                     ({"folder": "r", "sort": "x"}, "sort")):               # typo
        with pytest.raises(ConfigError, match=key):
            job_settings(write_job(tmp_path / "c", input=bad))


def test_input_folder_relative_absolute_home(tmp_path):
    j = tmp_path / "jobs"
    assert job_settings(write_job(j, input={"folder": "../renders"}))["input"]["folder"] == \
        str((tmp_path / "renders").resolve())
    assert job_settings(write_job(j, input={"folder": "/Volumes/BFD/Local Jobs/25_Review"}))[
        "input"]["folder"] == "/Volumes/BFD/Local Jobs/25_Review"
    assert job_settings(write_job(j, input={"folder": "~/renders"}))["input"]["folder"] == \
        str(Path.home() / "renders")


def test_spec_filename_regex_schema_and_errors(tmp_path):
    spec = tmp_path / "s.yaml"
    spec.write_text(yaml.safe_dump({"spec_version": 1, "job": {"filename_regex": RX}}))
    assert build_spec(spec)[0]["job"]["filename_regex"] == RX
    spec.write_text(yaml.safe_dump({"spec_version": 1, "job": {"filename_regex": "Color_(v"}}))
    with pytest.raises(ConfigError, match="not a valid regular expression"):
        build_spec(spec)
    for other in ({"filename": "a.mov"}, {"naming_pattern": "{title}.mov"}):
        spec.write_text(yaml.safe_dump({"spec_version": 1, "job": {"filename_regex": RX, **other}}))
        with pytest.raises(ConfigError, match="give only one"):
            build_spec(spec)
    spec.write_text(yaml.safe_dump({"spec_version": 1, "job": {"filename_regex": 3}}))
    with pytest.raises(ConfigError, match="filename_regex"):
        build_spec(spec)


# --- picking ------------------------------------------------------------------------------------
def inp(d, pattern="Color*.mov"):
    return {"folder": str(d), "pattern": pattern, "pick": "latest"}


def test_latest_is_highest_version_numeric_not_lexicographic(tmp_path):
    d = folder(tmp_path / "r", "Color_6K_v9.mov", "Color_6K_v10.mov", "Color_6K_v03.mov")
    os.utime(d / "Color_6K_v9.mov", (2e9, 2e9))                 # newest mtime is NOT what counts
    sel = pick_input(inp(d), JOB)
    assert (sel["picked"], sel["version"], sel["conforming"]) == ("Color_6K_v10.mov", 10, 3)
    assert sel["path"] == str(d / "Color_6K_v10.mov") and sel["notices"] == []


def test_non_conforming_names_become_notices(tmp_path):
    d = folder(tmp_path / "r", "Color_6K_v03.mov", "Color_6K_v04.mov", "Color_6k_v05.mov",
               "Color_v06_6K.mov", "notes.txt")
    sel = pick_input(inp(d), JOB)
    assert sel["picked"] == "Color_6K_v04.mov"
    assert [(n["file"], n["severity"]) for n in sel["notices"]] == [
        ("Color_6k_v05.mov", "WARN"), ("Color_v06_6K.mov", "WARN")]  # notes.txt: not in the glob
    assert all("does not match job.filename_regex" in n["message"] for n in sel["notices"])


def test_dot_files_and_folders_ignored(tmp_path):
    d = folder(tmp_path / "r", "Color_6K_v03.mov", "._Color_6K_v09.mov", ".Color_6K_v08.mov")
    (d / "Color_6K_v07.mov").mkdir()
    sel = pick_input(inp(d, "*"), JOB)
    assert sel["picked"] == "Color_6K_v03.mov" and sel["notices"] == []


def test_duplicate_version_is_config_error(tmp_path):
    d = folder(tmp_path / "r", "Color_6K_v4.mov", "Color_6K_v04.mov", "Color_6K_v03.mov")
    with pytest.raises(ConfigError, match=r"same version number: version 4: Color_6K_v04.mov, Color_6K_v4.mov"):
        pick_input(inp(d), JOB)


def test_no_conforming_file_is_config_error(tmp_path):
    d = folder(tmp_path / "r", "Color_6k_v05.mov", "Color_v06_6K.mov")
    with pytest.raises(ConfigError, match=r"matches job.filename_regex .*found: Color_6k_v05.mov, Color_v06_6K.mov"):
        pick_input(inp(d), JOB)
    with pytest.raises(ConfigError, match="found: nothing"):
        pick_input(inp(folder(tmp_path / "empty")), JOB)
    with pytest.raises(ConfigError, match="not found"):
        pick_input(inp(tmp_path / "missing"), JOB)


def test_latest_needs_a_version_group(tmp_path):
    d = folder(tmp_path / "r", "Color_6K_v03.mov")
    with pytest.raises(ConfigError, match=r"\(\?P<version>.*the spec has none"):
        pick_input(inp(d), {})
    with pytest.raises(ConfigError, match="has none"):
        pick_input(inp(d), {"filename_regex": r"^Color_6K_v\d+\.mov$"})


def test_nfd_names_compare_as_nfc(tmp_path):
    nfd = "Farbe_̈a_v02.mov".replace("̈a", "ä")   # 'ä' decomposed
    d = folder(tmp_path / "r", nfd)
    sel = pick_input(inp(d, "Farbe_ä_v*.mov"), {"filename_regex": r"Farbe_ä_v(?P<version>\d+)\.mov"})
    assert sel["version"] == 2 and (d / sel["picked"]).exists()


# --- CLI ------------------------------------------------------------------------------------------
def test_cli_usage_error_without_files_or_input(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["check", "--no-job", "--spec", "s.yaml", "--profile", "final"]) == 3
    assert "no file to check" in capsys.readouterr().err
    j = write_job(tmp_path / "j", spec="s.yaml", profile="final")
    assert cli.main(["check", "--job", str(j)]) == 3
    assert "`input:` block" in capsys.readouterr().err


def test_cli_files_override_input(tmp_path):
    js = {"input": {"folder": str(tmp_path), "pattern": "*", "pick": "latest"}}
    a = Namespace(files=["given.mov"])
    assert cli.pick_job_input(a, js, {"job": JOB}) is None and a.files == ["given.mov"]
    folder(tmp_path, "Color_6K_v02.mov")
    a = Namespace(files=[])
    sel = cli.pick_job_input(a, js, {"job": JOB})
    assert a.files == [str(tmp_path / "Color_6K_v02.mov")] and sel["version"] == 2


def test_report_input_block_and_notice_exit_code(tmp_path):
    d = folder(tmp_path / "r", "Color_6K_v03.mov", "Color_6k_v05.mov")
    sel = pick_input(inp(d), JOB)
    files = [{"path": sel["path"], "identity": {}, "timeline": None, "worst": "PASS", "findings": []}]
    rep = build_report(files, "p", [], "s", "x", "ffmpeg", "warn", input_sel=sel)
    jsonschema.validate(rep, REPORT_SCHEMA)
    assert rep["schema_version"] == "1.5" and rep["summary"]["notices"] == 1
    assert rep["files"][0]["worst"] == "PASS"                 # the file itself is fine
    assert rep["summary"]["worst"] == "WARN" and rep["summary"]["exit_code"] == 1
    assert rep["input"]["picked"] == "Color_6K_v03.mov" and rep["input"]["folder"] == str(d)
    out = render(rep)
    assert "INPUT Color_6K_v03.mov (version 3" in out and str(d) in out
    assert "NOTICE  WARN Color_6k_v05.mov: does not match job.filename_regex" in out
    assert "NOTICE 1" in out
    rep = build_report(files, "p", [], "s", "x", "ffmpeg", "warn")    # no input: unchanged
    assert "input" not in rep and rep["summary"]["notices"] == 0 and rep["summary"]["exit_code"] == 0


# --- filename check + picture-only spec ------------------------------------------------------------
PICTURE_ONLY = {"spec_version": 1, "job": {"filename_regex": RX},
                "video": {"container": "mov", "codec": "prores", "codec_profile": "422 HQ",
                          "width": 320, "height": 180, "fps": "24000/1001", "scan": "progressive",
                          "pixel_format": "yuv422p10le", "aspect_ratio": "16:9", "pixel_aspect": 1.0,
                          "color": {"primaries": "bt709", "transfer": "bt709", "matrix": "bt709",
                                    "range": "tv"}},
                "timing": {"timecode_mode": "non-drop", "head_layout": [{"type": "program"}],
                           "tail_layout": []},
                "audio": {"tracks": []}, "content": {"captions": {"required": False}}}


AUDIO_OFF = {c: {"enabled": False} for c in ("audio.format", "audio.loudness", "audio.silence",
                                              "audio.silent_channel", "audio.clipping")}


def test_picture_only_spec_loads_stock_profiles_with_filename_on(tmp_path):
    """The house picture-only profile (as the user's client_review: audio content checks off,
    audio.layout on) no longer needs `filename: {enabled: false}`: the stock audio_tag is lazy."""
    spec = tmp_path / "s.yaml"
    spec.write_text(yaml.safe_dump(PICTURE_ONLY))
    s, _ = build_spec(spec)
    for name in ("final", "review", "release"):
        p = tmp_path / f"house_{name}.yaml"
        p.write_text(yaml.safe_dump({"profile_version": 1, "name": f"house_{name}",
                                     "extends": name, "checks": AUDIO_OFF}))
        prof, _ = resolve_profile(str(p), s)
        fn = prof["checks"]["filename"]
        assert fn.get("enabled", True) and fn["job"]["filename_regex"] == RX
        assert "$missing" in fn["tags"]["audio_tag"]                 # unresolved, not a load error
        assert fn["tags"]["format"] == {"from": 180, "map": {720: "HD", 1080: "HD", 2160: "UHD"}}
        cli.check_profile(prof, substituted=True)


def _evaluate(name, job, tags=None):
    from cmr import engine
    engine.load_checks()
    chk = engine.REGISTRY["filename"]

    class Ctx:
        params = {"job": job, "tags": tags or {}}
        media = Namespace(name=name)

        def finding(self, msg, severity=None, **kw):
            return Namespace(message=msg, severity=severity, **kw)
    return chk.evaluate(Ctx(), [])


def test_filename_regex_check_text():
    assert _evaluate("Color_6K_v03.mov", JOB) == []
    f, = _evaluate("Color_6k_v05.mov", JOB)
    assert f.message == f"filename: expected a name matching '{RX}', got 'Color_6k_v05.mov'"
    assert (f.expected, f.actual) == (RX, "Color_6k_v05.mov")
    from cmr.engine import ConfigError as EngineConfigError
    with pytest.raises(EngineConfigError, match="not a valid regular expression"):
        _evaluate("x.mov", {"filename_regex": "("})


def test_missing_tag_is_an_error_only_when_the_pattern_uses_it():
    tags = {"audio_tag": {"$missing": "missing spec key ${spec.audio.tracks.0.layout} (no 'audio.tracks.0')"},
            "fmt": "HD"}
    assert _evaluate("A_HD.mov", {"naming_pattern": "A_{fmt}.mov"}, tags) == []
    f, = _evaluate("A_HD.mov", {"naming_pattern": "A_{fmt}_{audio_tag}.mov"}, tags)
    assert "cannot build expected filename: tag audio_tag: missing spec key" in f.message
