"""Config / CLI / report gaps found by the user-guide author: stock profiles from an installed
package, strict check params, lazy ${spec.*} for disabled checks, SKIPPED-only verdict, loudness
SKIPPED when the layout does not fit, report names, --report without -o, usage errors exit 3,
--quality, single-sourced version."""
import importlib.resources
import json
import re
import subprocess
import tomllib
from pathlib import Path

import jsonschema
import pytest
import yaml

import cmr
from cmr import cli, config, engine
from cmr.config import ConfigError, load_spec, resolve_profile
from cmr.model import Finding, Severity
from cmr.report import build_report
from cmr.report.console import render
from cmr.schemas import REPORT_SCHEMA

ROOT = Path(__file__).resolve().parents[2]


def w(d: Path, name: str, data: dict) -> Path:
    p = d / f"{name}.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    return p


# --- 1. packaging ------------------------------------------------------------------------
def test_stock_profiles_from_source_tree():
    assert config.stock_profiles_dir() == ROOT / "profiles"
    assert "final" in config.list_profiles()


def test_stock_profiles_prefers_package_data(tmp_path, monkeypatch):
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "final.yaml").write_text("x: 1\n")
    monkeypatch.setattr(importlib.resources, "files", lambda pkg: tmp_path)
    assert config.stock_profiles_dir() == tmp_path / "profiles"


def test_pyproject_lists_every_package_and_ships_profiles():
    py = tomllib.loads((ROOT / "pyproject.toml").read_text())
    st = py["tool"]["setuptools"]
    found = {".".join(p.parent.relative_to(ROOT / "src").parts)
             for p in (ROOT / "src" / "cmr").rglob("__init__.py")}
    assert found | {"cmr.profiles"} == set(st["packages"])
    assert st["package-dir"]["cmr.profiles"] == "profiles"
    assert st["package-data"]["cmr.profiles"] == ["*.yaml"]


# --- 10. version ------------------------------------------------------------------------
def test_version_single_sourced():
    py = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "version" not in py["project"] and "version" in py["project"]["dynamic"]
    assert py["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "cmr.__version__"}
    assert re.fullmatch(r"\d+\.\d+\.\d+", cmr.__version__)


# --- 2. strict params -----------------------------------------------------------------------
@pytest.mark.parametrize("cid,params,key", [
    ("container.format", {"expct": "mov"}, "expct"),
    ("video.codec", {"codec": "prores", "profle": "HQ"}, "profle"),
    ("video.resolution", {"widht": 1920}, "widht"),
    ("audio.format", {"sample_rte": 48000}, "sample_rte"),
    ("video.color", {"expect": {}, "unkown_severity": "info"}, "unkown_severity"),
    ("timing.frame_count", {"tolerance": 2}, "tolerance"),
    ("decode.errors", {"max_line": 3}, "max_line"),
])
def test_misspelt_param_is_config_error(tmp_path, capsys, cid, params, key):
    p = w(tmp_path, "typo", {"profile_version": 1, "name": "typo", "extends": "final",
                             "checks": {cid: params}})
    assert cli.main(["--log-level", "off", "profiles", "show", str(p)]) == 3
    err = capsys.readouterr().err
    assert f"checks.{cid}: unknown parameter(s) '{key}'" in err


def test_misspelt_param_in_disabled_check_is_still_reported(tmp_path):
    p = w(tmp_path, "typo", {"profile_version": 1, "name": "typo", "extends": "final",
                             "checks": {"video.scan": {"enabled": False, "expcet": "x"}}})
    prof, _ = config.load_profile_raw(str(p))
    assert any("'expcet'" in e for e in engine.validate_profile_params(prof, False))


def test_param_values_validated_with_spec(tmp_path, capsys):
    p = w(tmp_path, "bad", {"profile_version": 1, "name": "bad", "extends": "final",
                            "checks": {"timing.frame_count": {"tolerance_frames": "two"}}})
    spec = ROOT / "examples" / "spec_hd_broadcast.yaml"
    assert cli.main(["--log-level", "off", "profiles", "show", str(p), "--spec", str(spec)]) == 3
    assert "checks.timing.frame_count.tolerance_frames" in capsys.readouterr().err


def test_check_with_misspelt_param_exits_3_before_running(tmp_path, capsys):
    p = w(tmp_path, "typo", {"profile_version": 1, "name": "typo", "extends": "final",
                             "checks": {"container.format": {"expct": "mov"}}})
    rc = cli.main(["--log-level", "off", "check", str(tmp_path / "none.mov"), "--profile", str(p),
                   "--spec", str(ROOT / "examples" / "spec_hd_broadcast.yaml")])
    out = capsys.readouterr()
    assert rc == 3 and "'expct'" in out.err and "RESULT" not in out.out


PAIRS = [(s, None) for s in sorted((ROOT / "examples").glob("*.yaml"))] + \
        [(s, None) for s in sorted((ROOT / "templates" / "specs").glob("*.yaml"))] + \
        [(ROOT / "examples" / "spot_xr" / "spec_spot_xr.yaml", ROOT / "examples" / "spot_xr" / "job.yaml"),
         (ROOT / "templates" / "slate" / "spec_spot_slated.yaml", ROOT / "templates" / "slate" / "job.yaml")]
PROFILES = sorted((ROOT / "profiles").glob("*.yaml")) + sorted((ROOT / "templates" / "profiles").glob("*.yaml"))


def test_every_profile_spec_pair_loads_or_misses_a_spec_key(capsys):
    """Every stock/template profile against every example/template spec: either it loads, or
    the only error is a missing spec key (wrong pairing, D4) - never a params-schema error."""
    loaded = 0
    for prof in PROFILES:
        assert cli.main(["--log-level", "off", "profiles", "show", str(prof)]) == 0, prof
        for spec, job in PAIRS:
            argv = ["--log-level", "off", "profiles", "show", str(prof), "--spec", str(spec)]
            rc = cli.main(argv + (["--job", str(job)] if job else []))
            err = capsys.readouterr().err
            if rc == 0:
                loaded += 1
            else:
                assert "missing spec key" in err and "params invalid" not in err, (prof, spec, err)
    assert loaded >= 60


# --- 3. lazy substitution, nulls -------------------------------------------------------------
def test_disabled_check_is_not_substituted(tmp_path):
    p = w(tmp_path, "p", {"profile_version": 1, "name": "p", "checks": {
        "audio.format": {"enabled": False, "codec": "${spec.audio.codec}"},
        "video.fps": {"expect": "${spec.video.fps}"}}})
    prof, _ = resolve_profile(str(p), {"video": {"fps": "25"}})
    assert prof["checks"]["audio.format"]["codec"] == "${spec.audio.codec}"
    assert prof["checks"]["video.fps"]["expect"] == "25"
    with pytest.raises(ConfigError, match=r"missing spec key \$\{spec.video.fps\}"):
        resolve_profile(str(p), {"audio": {}})      # enabled check: still loud (D4)


def test_colour_interim_with_spec_without_audio_section(tmp_path):
    spec = yaml.safe_load((ROOT / "templates" / "specs" / "spec_colour_interim_noaudio.yaml").read_text())
    spec.pop("audio")
    s, _ = load_spec(w(tmp_path, "noaudio", spec))
    prof, _ = resolve_profile("colour_interim", s)
    assert prof["checks"]["audio.format"]["enabled"] is False
    assert engine.validate_profile_params(prof, True) == []
    with pytest.raises(ConfigError, match="spec.audio"):
        resolve_profile("review", s)


def test_spec_accepts_null_scalars(tmp_path):
    p = w(tmp_path, "s", {"spec_version": 1, "video": {"width": None, "height": None},
                          "timing": {"program_duration_frames": None, "program_start_timecode": None},
                          "audio": {"sample_rate": None, "bit_depth": None, "codec": None, "tracks": []}})
    spec, _ = load_spec(p)
    pp = w(tmp_path, "p", {"profile_version": 1, "name": "p", "checks": {"audio.format": {
        "codec": "${spec.audio.codec}", "sample_rate": "${spec.audio.sample_rate}"}}})
    prof, _ = resolve_profile(str(pp), spec)
    assert prof["checks"]["audio.format"] == {"codec": None, "sample_rate": None}   # not checked
    assert engine.validate_profile_params(prof, True) == []
    with pytest.raises(ConfigError):
        load_spec(w(tmp_path, "s2", {"spec_version": 1, "video": {"width": 0}}))


# --- 4. SKIPPED-only file verdict ---------------------------------------------------------------
def _files(*sevs):
    fs = [Finding("c%d" % i, s, "m") for i, s in enumerate(sevs)]
    return [{"path": "a.mov", "identity": None, "timeline": None, "findings": fs,
             "worst": max(sevs).name}]


def test_skipped_only_file_is_pass():
    rep = build_report(_files(Severity.PASS, Severity.SKIPPED), "p", [], "s", "x", "ff", "warn")
    assert rep["files"][0]["worst"] == "PASS" and rep["summary"]["worst"] == "PASS"
    assert rep["summary"]["counts"]["SKIPPED"] == 1 and rep["summary"]["exit_code"] == 0
    out = render(rep)
    assert out.startswith("PASS    a.mov") and "SKIPPED c1" in out


def test_skipped_does_not_hide_warn():
    rep = build_report(_files(Severity.SKIPPED, Severity.WARN), "p", [], "s", "x", "ff", "warn")
    assert rep["files"][0]["worst"] == "WARN" and rep["summary"]["exit_code"] == 1


# --- 5. loudness SKIPPED when the layout does not fit -------------------------------------------
def test_loudness_skipped_when_layout_does_not_fit(tmp_path):
    f = tmp_path / "tiny.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=64x48:r=25",
                    "-f", "lavfi", "-i", "sine=r=48000", "-t", "0.4", "-c:v", "prores_ks",
                    "-c:a", "pcm_s16le", str(f)], check=True)
    spec = {"spec_version": 1, "timing": {"head_layout": [{"type": "black", "frames": 50},
                                                          {"type": "program"}]}}
    prof = {"name": "t", "checks": {"audio.loudness": {
        "loudness": {"standard": "EBU R128"},
        "standards": {"EBU R128": {"target_lufs": -23, "tolerance_lu": 1, "true_peak_max_dbtp": -1}}}}}
    res = engine.run_file(f, spec, prof)
    (fd,) = res["findings"]
    assert fd.severity == Severity.SKIPPED and "does not fit" in fd.message


# --- 6/7/9. report file names, -o default, usage errors, --quality ------------------------------
def _check(tmp_path, *extra):
    return cli.main(["--log-level", "off", "check", str(tmp_path / "missing.mov"), "--profile",
                     "final", "--spec", str(ROOT / "examples" / "spec_hd_broadcast.yaml"),
                     "--report", "json", *extra])


def test_report_names_unique_with_latest_copy(tmp_path):
    assert _check(tmp_path, "-o", str(tmp_path / "r")) == 3     # probe ERROR: report still written
    assert _check(tmp_path, "-o", str(tmp_path / "r")) == 3
    reps = sorted((tmp_path / "r").glob("cmr_report_2*.json"))
    assert len(reps) == 2 and all(re.fullmatch(r"cmr_report_\d{8}-\d{6}_[0-9a-f]{6}\.json", p.name)
                                  for p in reps)
    latest = (tmp_path / "r" / cli.REPORT_LATEST).read_text()
    assert latest in {p.read_text() for p in reps}
    assert not list((tmp_path / "r").glob(".*tmp"))


def test_report_without_o_goes_to_cwd(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _check(tmp_path)
    assert (tmp_path / cli.REPORT_LATEST).exists()
    assert "report: cmr_report_" in capsys.readouterr().out


def test_usage_error_exits_3(capsys, tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as e:
        cli.main(["check", "x.mov", "--quality", "medium"])
    assert e.value.code == 3 and "--quality" in capsys.readouterr().err
    # --spec/--profile are optional since D31 (job.yaml), but still needed without a job file
    monkeypatch.chdir(tmp_path)
    assert cli.main(["check", "x.mov"]) == 3
    err = capsys.readouterr().err
    assert "--spec and --profile required" in err and "job.yaml" in err


def test_report_records_mode_and_quality(tmp_path):
    _check(tmp_path, "-o", str(tmp_path), "--quality", "fast", "--mode", "background")
    rep = json.loads((tmp_path / cli.REPORT_LATEST).read_text())
    jsonschema.validate(rep, REPORT_SCHEMA)
    assert rep["schema_version"] == "1.5"
    assert rep["summary"]["mode"] == "background" and rep["summary"]["quality"] == "fast"
    _check(tmp_path, "-o", str(tmp_path))
    rep = json.loads((tmp_path / cli.REPORT_LATEST).read_text())
    assert rep["summary"]["mode"] == "foreground" and rep["summary"]["quality"] == "profile"


def test_apply_quality_overrides_profile_where_supported():
    prof, _ = config.load_profile_raw("release")       # release sets quality: high explicitly
    prof["checks"]["audio.loudness"] = {"enabled": False}
    applied, kept = cli.apply_quality(prof, "fast")
    c = prof["checks"]
    assert c["video.blanking"]["quality"] == "fast" and c["video.black_runs"]["quality"] == "fast"
    assert "quality" not in c["decode.errors"] and "decode.errors" in kept
    assert "audio.loudness" not in applied + kept
    assert set(applied) == {cid for cid in applied if "fast" in engine.REGISTRY[cid].qualities}
    applied, _ = cli.apply_quality(prof, "high")
    assert c["video.blanking"]["quality"] == "high" and "decode.errors" in applied
