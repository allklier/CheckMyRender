"""installer/install.sh and installer/build_bundle.sh, run with fake tools only.

Nothing real is installed: every run uses a temporary HOME and a PATH made of a toolbox of
coreutils plus fakes from tests/installer/fakes (conda, ffmpeg/ffprobe, curl, df, uname, id;
the fake conda creates fake python/tesseract/ffmpeg in the env, the fake pip creates a fake
cmr). The system ffmpeg, conda and network are never reached. See installer/CLAUDE.md.
"""
import hashlib
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INST = ROOT / "installer"
FAKES = Path(__file__).resolve().parent / "fakes"
# CMR_TEST_BASH: run the installer with another bash, e.g. /bin/bash 3.2 on macOS (D32)
BASH = os.environ.get("CMR_TEST_BASH") or shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH, reason="bash is needed for the installer tests")

REAL_TOOLS = ["bash", "env", "cat", "sed", "awk", "grep", "mkdir", "rm", "cp", "ln", "chmod",
              "date", "mktemp", "tee", "head", "tail", "cmp", "find", "dirname",
              "basename", "tr", "cut", "sort", "od", "wc", "mv", "touch", "ls", "readlink"]
FAKE_TOOLS = ["curl", "df", "uname", "id", "sysctl", "sw_vers", "getconf"]
FILTERS = ["crop", "ebur128", "lut2", "signalstats"]
LOG_LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} (INFO |WARN |ERROR|DEBUG) "
                      r"install\[\d+/\d+\] run=[0-9a-f]{6} file=- \| ")


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write_sums(bundle: Path) -> None:
    files = sorted(p for p in bundle.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
    (bundle / "SHA256SUMS").write_text(
        "".join(f"{sha256(p)}  {p.relative_to(bundle)}\n" for p in files))


def make_bundle(dst: Path, version: str = "0.0.1") -> Path:
    """A bundle like build_bundle.sh makes, with a stand-in wheel (the fake pip ignores it)."""
    (dst / "wheels").mkdir(parents=True)
    shutil.copy(INST / "install.sh", dst / "install.sh")
    shutil.copy(INST / "environment.yml", dst / "environment.yml")
    (dst / "VERSION").write_text(version + "\n")
    (dst / "required_filters.txt").write_text("# test list\n" + "\n".join(FILTERS) + "\n")
    (dst / "wheels" / f"check_my_render-{version}-py3-none-any.whl").write_bytes(b"stand-in")
    (dst / "templates" / "specs").mkdir(parents=True)
    shutil.copy(ROOT / "templates" / "specs" / "spec_review_render.yaml", dst / "templates" / "specs")
    (dst / "templates" / "README.md").write_text("templates\n")
    (dst / "examples" / "sub").mkdir(parents=True)
    (dst / "examples" / "spec_a.yaml").write_text("a: 1\n")
    (dst / "examples" / "sub" / "b.csv").write_text("b\n")
    (dst / "examples" / "CLAUDE.md").write_text("must never reach the workspace\n")
    (dst / "templates" / "starter" / "media").mkdir(parents=True)
    (dst / "templates" / "starter" / "media" / "README.txt").write_text("put your video here\n")
    (dst / "demo").mkdir()
    (dst / "demo" / "make_spot.py").write_text("# stand-in: the fake env python plays the generator\n")
    for rel in ("README.md", "jobs/job_template.yaml", "specs/spec_review_render.yaml",
                "profiles/client_review.yaml"):       # work area seeds (build_bundle.sh)
        (dst / "work" / rel).parent.mkdir(parents=True, exist_ok=True)
        (dst / "work" / rel).write_text(f"seed {rel}\n")
    write_sums(dst)
    return dst


class Rig:
    def __init__(self, tmp: Path, bundle: Path | None = None):
        self.tmp = tmp
        self.home = tmp / "home"
        self.home.mkdir()
        self.tools = tmp / "tools"
        self.tools.mkdir()
        for t in REAL_TOOLS:
            real = shutil.which(t)
            assert real, f"test toolbox needs {t}"
            (self.tools / t).symlink_to(real)
        for t in ("sha256sum", "shasum"):     # GNU coreutils / macOS: whichever exists
            if shutil.which(t):
                (self.tools / t).symlink_to(shutil.which(t))
        assert any((self.tools / t).exists() for t in ("sha256sum", "shasum")), "need sha256sum or shasum"
        for t in FAKE_TOOLS:
            (self.tools / t).symlink_to(FAKES / t)
        self.sysbin = tmp / "sysbin"          # the "system" ffmpeg/ffprobe
        self.sysbin.mkdir()
        for t in ("ffmpeg", "ffprobe"):
            shutil.copy(FAKES / "ffmpeg", self.sysbin / t)
        self.condabin = tmp / "condabin"      # conda on PATH
        self.condabin.mkdir()
        shutil.copy(FAKES / "conda", self.condabin / "conda")
        self.calls = tmp / "calls.log"
        self.calls.touch()
        self.bundle = bundle or make_bundle(tmp / "bundle")
        self.root = self.home / "cmr"                 # D33 default: ~/cmr/{bin,demo,work}
        self.prefix = self.root / "bin"
        self.env_dir = self.prefix / "env"
        self.launcher = self.prefix / "cmr"           # the real launcher lives in the prefix
        self.link = self.home / ".local/bin/cmr"      # PATH link to it
        self.workspace = self.root / "demo"
        self.work = self.root / "work"
        self.log = self.home / ".cache/cmr/logs/install.log"

    def path(self, conda=True, ffmpeg=True, extra=()):
        parts = list(extra) + ([str(self.condabin)] if conda else []) + \
            ([str(self.sysbin)] if ffmpeg else []) + [str(self.tools)]
        return ":".join(parts)

    def run(self, *args, stdin="", conda=True, ffmpeg=True, extra_path=(), **fake):
        env = {"HOME": str(self.home), "PATH": self.path(conda, ffmpeg, extra_path),
               "FAKES": str(FAKES), "FAKE_CALLS": str(self.calls),
               "FAKE_FILTERS": str(self.bundle / "required_filters.txt"), "LANG": "C"}
        env.update({k: str(v) for k, v in fake.items()})
        return subprocess.run([BASH, str(self.bundle / "install.sh"), *args], input=stdin,
                              capture_output=True, text=True, env=env, cwd=self.tmp, timeout=60)

    def calls_text(self) -> str:
        return self.calls.read_text()

    def reset_calls(self):
        self.calls.write_text("")


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def out(p) -> str:
    return p.stdout + p.stderr


def link_target(p: Path) -> str:
    """Where a symlink points, made absolute (the ffmpeg-bin links into env/bin are relative)."""
    return os.path.normpath(os.path.join(p.parent, os.readlink(p)))


# --- static ---------------------------------------------------------------------------------
def test_syntax():
    for f in ("install.sh", "build_bundle.sh"):
        assert subprocess.run([BASH, "-n", str(INST / f)]).returncode == 0


def test_shellcheck_if_available():
    sc = shutil.which("shellcheck")
    if not sc:
        pytest.skip("shellcheck not installed")
    p = subprocess.run([sc, "-S", "error", str(INST / "install.sh"), str(INST / "build_bundle.sh")],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout


# --- arguments ------------------------------------------------------------------------------
def test_help(rig):
    p = rig.run("--help")
    assert p.returncode == 0
    for flag in ("--root", "--no-work", "--prefix", "--workspace", "--bin-dir", "--no-slate", "--upgrade", "--uninstall",
                 "--bootstrap-conda", "--offline-dir", "--yes", "--dry-run", "--use-system-ffmpeg"):
        assert flag in p.stdout


@pytest.mark.parametrize("args, msg", [
    (["--bogus"], "unknown option: --bogus"),
    (["--prefix"], "--prefix needs a directory"),
    (["--prefix", "/"], "refusing --prefix"),
    (["--offline-dir", "/x"], "planned but not built"),
])
def test_bad_arguments(rig, args, msg):
    p = rig.run("--dry-run", *args)
    assert p.returncode == 2 and msg in p.stderr


def test_prefix_home_refused(rig):
    p = rig.run("--prefix", str(rig.home), "--dry-run")
    assert p.returncode == 2 and "refusing --prefix" in p.stderr


def test_root_refused(rig):
    p = rig.run("--dry-run", FAKE_UID=0)
    assert p.returncode == 2 and "do not run the installer as root" in p.stderr


@pytest.mark.parametrize("s, m, tr, msg", [("Linux", "aarch64", "0", "unsupported platform Linux/aarch64"),
                                           ("Darwin", "x86_64", "0", "Intel Mac"),
                                           ("Darwin", "x86_64", "1", "runs under Rosetta"),
                                           ("Darwin", "arm64", "1", "runs under Rosetta")])
def test_unsupported_os(rig, s, m, tr, msg):
    p = rig.run("--yes", FAKE_UNAME_S=s, FAKE_UNAME_M=m, FAKE_TRANSLATED=tr)
    assert p.returncode == 2 and msg in p.stderr and "nothing was changed" in p.stderr
    assert not (rig.home / ".local").exists() and not rig.workspace.exists()


# --- dry run --------------------------------------------------------------------------------
def test_dry_run_changes_nothing_and_prints_actions(rig):
    p = rig.run("--dry-run")
    assert p.returncode == 0, out(p)
    o = p.stdout
    assert "DRY RUN" in o
    assert re.search(r"\[dry-run\] .*conda create -y -q -p .*/env --override-channels -c conda-forge "
                     r"--no-default-packages python=3.12 pip pyyaml jsonschema ffmpeg=7.1.1 tesseract=5.5.2 leptonica=1.87 pillow numpy\n", o)
    assert "pip install --no-deps --no-index --force-reinstall" in o
    assert "would write " + str(rig.launcher) in o
    assert "| cmr_slate=1" in o and "| PATH=\"$CMR_BIN/ffmpeg-bin:$PATH\"" in o
    assert "Using the pinned, tested conda-forge ffmpeg 7.1.1" in o
    assert f"System ffmpeg {rig.sysbin}/ffmpeg is not used and not touched" in o
    assert f"would link {rig.prefix}/ffmpeg-bin/ffmpeg -> ../env/bin/ffmpeg" in o
    assert f"would link {rig.link} -> {rig.launcher}" in o
    assert "would ask" not in o
    assert "Production Python policy" in o
    # nothing written anywhere in HOME, conda only asked for its version
    assert list(rig.home.iterdir()) == []
    assert [l for l in rig.calls_text().splitlines() if l.startswith("conda")] == ["conda --version"] * 2


def test_dry_run_no_slate(rig):
    p = rig.run("--dry-run", "--no-slate")
    assert p.returncode == 0
    assert "tesseract" not in p.stdout.split("Packages")[1].splitlines()[0]
    assert "| cmr_slate=0" in p.stdout
    assert "Slate OCR skipped (--no-slate)" in p.stdout


# --- ffmpeg acceptance ----------------------------------------------------------------------
@pytest.mark.parametrize("fake, ffmpeg, accepted, why", [
    ({}, True, True, ""),
    ({"FAKE_FF_VERSION": "5.1.10"}, True, True, ""),
    ({"FAKE_FF_VERSION": "n6.0"}, True, True, ""),
    ({"FAKE_FF_VERSION": "6.1.1-3ubuntu5"}, True, True, ""),
    ({"FAKE_FF_VERSION": "5.0.3"}, True, False, "older than 5.1"),
    ({"FAKE_FF_VERSION": "4.4.2"}, True, False, "older than 5.1"),
    ({"FAKE_FF_VERSION": "N-112233-gabcdef"}, True, False, "unreadable"),
    ({"FAKE_FP_VERSION": "4.4"}, True, False, "ffprobe '4.4'"),
    ({"FAKE_FF_MISSING": "lut2 ebur128"}, True, False, "lacks filters: ebur128, lut2"),
    ({}, False, False, "ffmpeg/ffprobe not found on PATH"),
])
def test_ffmpeg_acceptance(rig, fake, ffmpeg, accepted, why):
    """--use-system-ffmpeg: the old acceptance test; a failing system ffmpeg falls back to 7.1.1."""
    p = rig.run("--dry-run", "--use-system-ffmpeg", ffmpeg=ffmpeg, **fake)
    assert p.returncode == 0, out(p)
    pkgs = p.stdout.split("Packages (conda-forge only): ")[1].splitlines()[0]
    if accepted:
        assert "--use-system-ffmpeg: " + str(rig.sysbin / "ffmpeg") in p.stdout
        assert "results may differ from the tested ffmpeg 7.1.1" in p.stderr
        assert "ffmpeg" not in pkgs
        assert f"would link {rig.prefix}/ffmpeg-bin/ffmpeg -> {rig.sysbin}/ffmpeg" in p.stdout
    else:
        assert why in p.stderr and "the system ffmpeg cannot be used" in p.stderr
        assert "installing the pinned conda-forge ffmpeg 7.1.1 into cmr's own environment" in p.stdout
        assert pkgs.endswith("jsonschema ffmpeg=7.1.1 tesseract=5.5.2 leptonica=1.87 pillow numpy")   # one env
        assert "ffmpeg -> ../env/bin/ffmpeg" in p.stdout and "ffmpeg-env" not in p.stdout


@pytest.mark.parametrize("fake", [{}, {"FAKE_FF_VERSION": "7.1.1"}, {"FAKE_FF_VERSION": "5.1.10"}])
def test_default_always_uses_pinned_ffmpeg(rig, fake):
    """User decision 2026-10-26: every machine runs the same ffmpeg; a good system ffmpeg (even
    7.1.1) is not used without --use-system-ffmpeg, and it is never even probed."""
    p = rig.run("--dry-run", **fake)
    assert p.returncode == 0, out(p)
    pkgs = p.stdout.split("Packages (conda-forge only): ")[1].splitlines()[0]
    assert "ffmpeg=7.1.1" in pkgs
    assert f"would link {rig.prefix}/ffmpeg-bin/ffmpeg -> ../env/bin/ffmpeg" in p.stdout
    assert "WARNING" not in p.stderr
    assert not re.search(r"^ffmpeg ", rig.calls_text(), re.M)        # system ffmpeg not run


def test_ffmpeg_inside_prefix_is_not_the_system_one(rig):
    """A PATH that already contains cmr's own env (activated) must not count as system ffmpeg."""
    fake_env_bin = rig.env_dir / "bin"
    fake_env_bin.mkdir(parents=True)
    shutil.copy(FAKES / "ffmpeg", fake_env_bin / "ffmpeg")
    shutil.copy(FAKES / "ffmpeg", fake_env_bin / "ffprobe")
    (rig.prefix / ".cmr-install-prefix").touch()
    p = rig.run("--dry-run", "--use-system-ffmpeg", "--prefix", str(rig.prefix), ffmpeg=False,
                extra_path=[str(fake_env_bin)])
    assert "ffmpeg/ffprobe not found on PATH" in p.stderr


# --- conda discovery ------------------------------------------------------------------------
def put_conda(d: Path, version: str) -> Path:
    (d / "bin").mkdir(parents=True)
    c = d / "bin" / "conda"
    c.write_text(f'#!/bin/bash\n[ "$1" = --version ] && echo "conda {version}"\n')
    c.chmod(0o755)
    return c


def test_conda_discovery_order(rig):
    tools = put_conda(rig.home / "Tools/anaconda3", "1.0-tools")
    put_conda(rig.home / "anaconda3", "2.0-anaconda")
    put_conda(rig.home / "miniforge3", "4.0-miniforge")
    exe = put_conda(rig.tmp / "condaexe", "5.0-exe")
    p = rig.run("--dry-run", CONDA_EXE=exe)                     # PATH first
    assert f"Using {rig.condabin}/conda" in p.stdout and "found via PATH" in p.stdout
    p = rig.run("--dry-run", conda=False, CONDA_EXE=exe)        # then ~/Tools/anaconda3 ...
    assert f"Using {tools} (conda 1.0-tools" in p.stdout
    shutil.rmtree(rig.home / "Tools")
    p = rig.run("--dry-run", conda=False, CONDA_EXE=exe)
    assert "conda 2.0-anaconda" in p.stdout
    shutil.rmtree(rig.home / "anaconda3")
    p = rig.run("--dry-run", conda=False, CONDA_EXE=exe)
    assert "conda 4.0-miniforge" in p.stdout                    # (~/miniconda3 absent)
    shutil.rmtree(rig.home / "miniforge3")
    p = rig.run("--dry-run", conda=False, CONDA_EXE=exe)        # CONDA_EXE last
    assert "conda 5.0-exe" in p.stdout and "found via CONDA_EXE" in p.stdout


def test_broken_conda_is_skipped(rig):
    bad = rig.home / "anaconda3/bin"
    bad.mkdir(parents=True)
    (bad / "conda").write_text("#!/bin/bash\nexit 1\n")
    (bad / "conda").chmod(0o755)
    put_conda(rig.home / "miniconda3", "3.0-mini")
    p = rig.run("--dry-run", conda=False)
    assert "conda 3.0-mini" in p.stdout


def test_missing_conda_message(rig):
    p = rig.run(conda=False)        # stdin empty = "no"
    assert p.returncode == 3
    o = out(p)
    assert "No conda found (searched: PATH, ~/Tools/anaconda3, ~/anaconda3, ~/miniconda3, ~/miniforge3" in o
    assert "--bootstrap-conda" in o and "Install log (kept)" in o
    assert not rig.prefix.exists() and not rig.launcher.exists()
    assert "curl" not in rig.calls_text()


def test_bootstrap_dry_run(rig):
    p = rig.run("--dry-run", "--bootstrap-conda", conda=False)
    assert p.returncode == 0
    assert re.search(r"releases/download/[0-9.]+-\d+/Miniforge3-[0-9.]+-\d+-Linux-x86_64\.sh", p.stdout)
    assert "would verify the Miniforge sha256" in p.stdout
    assert f"bash {rig.prefix}/miniforge-download/miniforge.sh -b -p {rig.prefix}/conda" in p.stdout
    assert list(rig.home.iterdir()) == []


def test_bootstrap_installs_miniforge_after_checksum(rig):
    p = rig.run("--bootstrap-conda", conda=False)
    assert p.returncode == 0, out(p)
    assert (rig.prefix / "conda/bin/conda").exists()
    assert not (rig.prefix / "miniforge-download").exists()
    assert f"conda={rig.prefix}/conda/bin/conda" in (rig.prefix / "install-state").read_text()
    assert "| bash" not in rig.calls_text()


def test_bootstrap_refuses_prefix_with_space(rig):
    """Found on the Mac (2026-10-27): the Miniforge installer refuses a prefix with a space."""
    root = rig.tmp / "Moved Folder/cmr"
    p = rig.run("--yes", "--bootstrap-conda", "--root", str(root), conda=False)
    assert p.returncode == 1 and "contains a space" in p.stderr and "nothing was changed" in p.stderr
    assert not root.exists() and "curl -fsSL" not in rig.calls_text()


def test_bootstrap_checksum_mismatch_not_run(rig):
    p = rig.run("--bootstrap-conda", conda=False, FAKE_SHA_BAD=1)
    assert p.returncode == 1 and "Miniforge checksum mismatch; not run" in p.stderr
    assert not (rig.prefix / "conda").exists()


# --- preflight failures ---------------------------------------------------------------------
def test_free_space_check(rig):
    p = rig.run("--yes", FAKE_DF_AVAIL_KB=1024 * 1024)       # 1 GB free, 3 GB + 0.5 GB demo needed
    assert p.returncode == 1
    assert "not enough free disk space: 1024 MB free" in p.stderr and "--no-slate" in p.stderr
    assert "about 3584 MB needed" in p.stderr and "--no-demo" in p.stderr
    assert not rig.prefix.exists()
    p = rig.run("--yes", "--no-slate", "--no-demo", FAKE_DF_AVAIL_KB=1024 * 1024)
    # 2048 MB: the --no-slate env with conda-forge ffmpeg 7.1 is 765 MB + package cache (Rocky run)
    assert p.returncode == 1 and "about 2048 MB needed" in p.stderr and "--no-demo" not in p.stderr
    p = rig.run("--yes", "--no-slate", "--no-demo", FAKE_DF_AVAIL_KB=2047 * 1024)
    assert p.returncode == 1
    p = rig.run("--yes", "--no-slate", FAKE_DF_AVAIL_KB=2559 * 1024)      # demo adds 512 MB
    assert p.returncode == 1 and "about 2560 MB needed" in p.stderr
    p = rig.run("--yes", "--no-slate", "--no-demo", FAKE_DF_AVAIL_KB=2048 * 1024)
    assert p.returncode == 0, out(p)


def test_bundle_tampered(rig):
    with open(rig.bundle / "environment.yml", "a") as f:
        f.write("  - evil\n")
    p = rig.run("--yes")
    assert p.returncode == 1 and "bundle checksum mismatch" in p.stderr
    assert not rig.prefix.exists()


def test_not_a_bundle(rig):
    for w in (rig.bundle / "wheels").iterdir():
        w.unlink()
    p = rig.run("--dry-run")
    assert p.returncode == 1 and "not a complete release bundle" in p.stderr


def test_network_failure(rig):
    p = rig.run("--yes", FAKE_CURL_FAIL=1)
    assert p.returncode == 1
    assert "cannot reach https://conda.anaconda.org" in p.stderr and "HTTPS_PROXY" in p.stderr
    assert rig.log.exists()


def test_conda_failure_keeps_log(rig):
    p = rig.run("--yes", FAKE_CONDA_FAIL=1)
    assert p.returncode == 1 and "conda could not create the environment" in p.stderr
    assert f"Install log (kept): {rig.log}" in p.stderr
    assert "fake failure" in rig.log.read_text()


def test_foreign_prefix_refused(rig):
    rig.prefix.mkdir(parents=True)
    (rig.prefix / "something").write_text("x")
    p = rig.run("--yes")
    assert p.returncode == 1 and "was not created by this installer" in p.stderr
    assert (rig.prefix / "something").exists()


def test_foreign_launcher_refused(rig):
    rig.link.parent.mkdir(parents=True)
    rig.link.write_text("#!/bin/sh\nexec ~/envs/cmr/bin/cmr \"$@\"\n")
    p = rig.run("--yes")
    assert p.returncode == 1 and "is not a CheckMyRender launcher" in p.stderr
    assert not rig.prefix.exists()


def test_doctor_problems_fail_install(rig):
    p = rig.run("--yes", FAKE_DOCTOR_RC=3, FAKE_DOCTOR_OUT="PROBLEMS FOUND")
    assert p.returncode == 1 and "cmr doctor reports problems (exit 3)" in p.stderr
    assert "PROBLEMS FOUND" in rig.log.read_text()


# --- full (fake) install --------------------------------------------------------------------
def test_install_system_ffmpeg(rig):
    p = rig.run("--yes", "--use-system-ffmpeg")
    assert p.returncode == 0, out(p)
    assert "results may differ from the tested ffmpeg 7.1.1" in p.stderr
    calls = rig.calls_text()
    assert re.search(r"^conda create -y -q -p \S+/env --override-channels -c conda-forge "
                     r"--no-default-packages python=3.12 pip pyyaml jsonschema tesseract=5.5.2 leptonica=1.87 pillow numpy$", calls, re.M)
    assert re.search(r"^python -m pip install --no-deps --no-index --force-reinstall \S+\.whl "
                     r"PYTHONPATH=unset PIP_CONFIG_FILE=/dev/null$", calls, re.M)
    assert "python -m pip check" in calls
    # launcher: in the prefix, relocatable (no install path in it), PATH link to it
    text = rig.launcher.read_text()
    assert os.access(rig.launcher, os.X_OK)
    assert "CheckMyRender launcher (generated by install.sh)" in text
    assert str(rig.home) not in text.replace(f"cmr_ffmpeg='{rig.sysbin}/ffmpeg'", "")
    assert f"cmr_ffmpeg='{rig.sysbin}/ffmpeg'; cmr_ffprobe='{rig.sysbin}/ffprobe'" in text
    assert "cmr_demo='demo'; cmr_work='work'" in text and "cmr_rooted=1" in text
    assert 'PATH="$CMR_BIN/ffmpeg-bin:$PATH"; export PATH' in text
    assert "CMR_TESSERACT=$CMR_ENV/bin/tesseract; export CMR_TESSERACT" in text
    assert "TESSDATA_PREFIX=$CMR_ENV/share/tessdata; export TESSDATA_PREFIX" in text
    assert "unset PYTHONPATH PYTHONHOME" in text
    assert text.rstrip().endswith('exec "$CMR_ENV/bin/python" -I -m cmr "$@"')
    assert os.readlink(rig.link) == str(rig.launcher)
    assert link_target(rig.prefix / "ffmpeg-bin/ffmpeg") == str(rig.sysbin / "ffmpeg")
    assert link_target(rig.prefix / "ffmpeg-bin/ffprobe") == str(rig.sysbin / "ffprobe")
    # verification ran through the launcher: env tesseract, the accepted ffmpeg, clean PYTHONPATH
    assert (f"cmr doctor CMR_TESSERACT={rig.env_dir}/bin/tesseract ffmpeg={rig.sysbin}/ffmpeg "
            f"PYTHONPATH=unset TMPDIR=unset TESSDATA_PREFIX={rig.env_dir}/share/tessdata") in calls
    assert "getconf" not in text                                          # macOS-only line
    assert "cmr check " in calls and "tesseract --version" in calls and "tesseract --list-langs" in calls
    assert "Self-test ok" in p.stdout
    assert not list(rig.prefix.glob("selftest.*"))
    # workspace: everything but CLAUDE.md
    assert (rig.workspace / "examples/sub/b.csv").exists()
    assert (rig.workspace / "templates/specs/spec_review_render.yaml").exists()
    assert not list(rig.workspace.rglob("CLAUDE.md"))
    # state, summary, PATH hint (~/.local/bin is not on the test PATH)
    state = (rig.prefix / "install-state").read_text()
    assert "version=0.0.1\nffmpeg_mode=system\n" in state and "slate=1" in state
    assert "is not on your PATH" in p.stdout and f"export PATH=\"{rig.home}/.local/bin:$PATH\"" in p.stdout
    assert "Production Python policy" in p.stdout
    # log: D29 line style, every line
    lines = [l for l in rig.log.read_text().splitlines() if LOG_LINE.match(l)]
    assert lines and any("step 11/11" in l for l in lines)
    assert all(LOG_LINE.match(l) for l in rig.log.read_text().splitlines() if l[:2] == "20")


def test_install_env_ffmpeg_no_slate(rig):
    p = rig.run("--yes", "--no-slate", extra_path=[str(rig.home / ".local/bin")])
    assert p.returncode == 0, out(p)
    calls = rig.calls_text()
    assert re.search(r"^conda create -y -q -p \S+/env --override-channels -c conda-forge "
                     r"--no-default-packages python=3.12 pip pyyaml jsonschema ffmpeg=7.1.1 pillow numpy$", calls, re.M)
    assert "ffmpeg-env" not in calls and not (rig.prefix / "ffmpeg-env").exists()
    text = rig.launcher.read_text()
    assert "cmr_slate=0" in text and "cmr_ffmpeg=''; cmr_ffprobe=''" in text
    assert os.readlink(rig.prefix / "ffmpeg-bin/ffmpeg") == "../env/bin/ffmpeg"      # relative: survives a move
    assert link_target(rig.prefix / "ffmpeg-bin/ffmpeg") == str(rig.env_dir / "bin/ffmpeg")
    assert link_target(rig.prefix / "ffmpeg-bin/ffprobe") == str(rig.env_dir / "bin/ffprobe")
    assert f"ffmpeg={rig.env_dir}/bin/ffmpeg" in calls        # cmr sees the env ffmpeg first
    assert "tesseract" not in calls
    assert "is not on your PATH" not in p.stdout and "Run: cmr doctor" in p.stdout


def test_launcher_quotes_prefix(rig):
    """A folder name with a quote and a space: the launcher runs and cmr-paths.conf sources."""
    pre = rig.tmp / "it's here"
    p = rig.run("--yes", "--prefix", str(pre))
    assert p.returncode == 0, out(p)
    assert os.readlink(rig.link) == str(pre / "cmr")
    conf = pre / "cmr-paths.conf"
    r = subprocess.run(["sh", "-c", '. "$1"; printf "%s|%s|%s" "$CMR_BIN" "$CMR_LAUNCHER" "$CMR_ROOT"', "x", str(conf)],
                       capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert r.stdout == f"{pre}|{pre}/cmr|"              # --prefix outside the root: no CMR_ROOT


# --- idempotence / upgrade ------------------------------------------------------------------
def test_rerun_same_version_is_idempotent(rig):
    assert rig.run("--yes").returncode == 0
    edited = rig.workspace / "examples/spec_a.yaml"
    edited.write_text("a: 2  # my edit\n")
    (rig.workspace / "examples/sub/b.csv").unlink()
    launcher_before = rig.launcher.read_text()
    rig.reset_calls()
    p = rig.run("--yes")
    assert p.returncode == 0, out(p)
    calls = rig.calls_text()
    assert "conda create" not in calls and "conda install" not in calls and "pip install" not in calls
    assert "already installed" in p.stdout and "Environment is up to date" in p.stdout
    assert edited.read_text() == "a: 2  # my edit\n"                      # never overwritten
    assert (rig.workspace / "examples/sub/b.csv").exists()               # missing file restored
    assert "1 new" in p.stdout and "1 kept as you edited them" in p.stdout
    assert rig.launcher.read_text() == launcher_before
    assert "cmr doctor" in calls                                          # still verified


def test_upgrade_flag_refreshes(rig):
    assert rig.run("--yes").returncode == 0
    rig.reset_calls()
    p = rig.run("--yes", "--upgrade")
    assert p.returncode == 0, out(p)
    calls = rig.calls_text()
    assert re.search(r"^conda install -y -q -p \S+/env --override-channels -c conda-forge python=3.12", calls, re.M)
    assert "pip install --no-deps --no-index --force-reinstall" in calls
    assert "refreshing the environment" in p.stdout


def test_new_version_upgrades_in_place(rig):
    assert rig.run("--yes").returncode == 0
    new = make_bundle(rig.tmp / "bundle2", version="0.0.2")
    rig.bundle = new
    rig.reset_calls()
    p = rig.run("--yes")
    assert p.returncode == 0, out(p)
    assert "Upgrade in place: cmr 0.0.1 -> 0.0.2" in p.stdout
    assert "check_my_render-0.0.2-py3-none-any.whl" in rig.calls_text()
    assert "version=0.0.2" in (rig.prefix / "install-state").read_text()
    assert "conda create" not in rig.calls_text()


def test_upgrade_updates_unedited_workspace_files_only(rig):
    """Found on the first real run: a template changed in the new bundle was reported as
    'kept as you edited them' although nobody edited it, and never reached the workspace."""
    assert rig.run("--yes").returncode == 0
    (rig.workspace / "examples/sub/b.csv").write_text("my edit\n")
    new = make_bundle(rig.tmp / "bundle2", version="0.0.2")
    (new / "examples/spec_a.yaml").write_text("a: 2  # new template\n")
    (new / "examples/sub/b.csv").write_text("b v2\n")
    write_sums(new)
    rig.bundle = new
    p = rig.run("--yes")
    assert p.returncode == 0, out(p)
    assert (rig.workspace / "examples/spec_a.yaml").read_text() == "a: 2  # new template\n"
    assert (rig.workspace / "examples/sub/b.csv").read_text() == "my edit\n"
    assert "1 updated (not edited by you)" in p.stdout and "1 kept as you edited them" in p.stdout
    p = rig.run("--yes")                              # stable on the next run
    assert "0 updated" in p.stdout and "1 kept as you edited them" in p.stdout
    # the record lives in the workspace: it survives --uninstall + a fresh install
    assert rig.run("--uninstall", "--yes").returncode == 0
    (new / "examples/spec_a.yaml").write_text("a: 3\n")
    write_sums(new)
    p = rig.run("--yes")
    assert p.returncode == 0, out(p)
    assert (rig.workspace / "examples/spec_a.yaml").read_text() == "a: 3\n"
    assert (rig.workspace / ".cmr-installed-files").exists()


def test_env_ffmpeg_and_tesseract_share_one_env(rig):
    """ffmpeg 7.1.1 and tesseract 5.5.2 go into ONE env (the fake conda refuses ffmpeg 6 +
    tesseract like the real solver, so a regression of the pin to 6.x fails here)."""
    p = rig.run("--yes")
    assert p.returncode == 0, out(p)
    calls = rig.calls_text()
    assert re.search(r"^conda create -y -q -p \S+/env --override-channels -c conda-forge --no-default-packages "
                     r"python=3.12 pip pyyaml jsonschema ffmpeg=7.1.1 tesseract=5.5.2 leptonica=1.87 pillow numpy$", calls, re.M)
    assert calls.count("conda create") == 1 and "ffmpeg-env" not in calls
    assert link_target(rig.prefix / "ffmpeg-bin/ffmpeg") == str(rig.env_dir / "bin/ffmpeg")
    assert (f"cmr doctor CMR_TESSERACT={rig.env_dir}/bin/tesseract ffmpeg={rig.env_dir}/bin/ffmpeg") in calls
    state = (rig.prefix / "install-state").read_text()
    assert "ffmpeg_mode=env\n" in state and f"ffmpeg={rig.env_dir}/bin/ffmpeg\n" in state
    assert "conda-forge 7.1.1, pinned, in cmr's environment" in p.stdout


def test_environment_yml_pins_ffmpeg_71():
    specs = [l.split("#")[0].strip() for l in (INST / "environment.yml").read_text().splitlines()
             if "# cmr:ffmpeg" in l and l.lstrip().startswith("-")]
    assert specs == ["- ffmpeg=7.1.1"]


def test_rerun_adds_ffmpeg_when_system_one_turns_bad(rig):
    assert rig.run("--yes", "--use-system-ffmpeg").returncode == 0
    rig.reset_calls()
    p = rig.run("--yes", "--use-system-ffmpeg", FAKE_FF_MISSING="lut2")
    assert p.returncode == 0, out(p)
    # ffmpeg goes into the existing env next to tesseract (no separate ffmpeg env since 7.1)
    assert re.search(r"^conda install -y -q -p \S+/env .* ffmpeg=7\.1\.1 tesseract=5\.5\.2", rig.calls_text(), re.M)
    assert "conda create" not in rig.calls_text() and not (rig.prefix / "ffmpeg-env").exists()
    assert link_target(rig.prefix / "ffmpeg-bin/ffmpeg") == str(rig.env_dir / "bin/ffmpeg")
    rig.reset_calls()                                 # next run: nothing to add
    p = rig.run("--yes", "--use-system-ffmpeg", FAKE_FF_MISSING="lut2")
    assert p.returncode == 0, out(p)
    assert "conda create" not in rig.calls_text() and "conda install" not in rig.calls_text()
    assert "Environment is up to date" in p.stdout
    rig.reset_calls()                                 # system ffmpeg good again + opt-in: it wins
    p = rig.run("--yes", "--use-system-ffmpeg")
    assert p.returncode == 0, out(p)
    assert link_target(rig.prefix / "ffmpeg-bin/ffmpeg") == str(rig.sysbin / "ffmpeg")
    assert "conda install" not in rig.calls_text()


def test_rerun_without_flag_moves_system_install_to_pinned_ffmpeg(rig):
    """An install that used the system ffmpeg gets the pinned 7.1.1 added to its env on a plain
    re-run (same version, no --upgrade); --use-system-ffmpeg keeps the system one."""
    assert rig.run("--yes", "--use-system-ffmpeg").returncode == 0
    rig.reset_calls()
    p = rig.run("--yes", "--use-system-ffmpeg")            # opt-in again: nothing changes
    assert p.returncode == 0, out(p)
    assert "conda install" not in rig.calls_text()
    assert link_target(rig.prefix / "ffmpeg-bin/ffmpeg") == str(rig.sysbin / "ffmpeg")
    rig.reset_calls()
    p = rig.run("--yes")
    assert p.returncode == 0, out(p)
    assert "The previous install used the system ffmpeg" in p.stdout and "about 1024 MB needed" in p.stdout
    assert re.search(r"^conda install -y -q -p \S+/env .* ffmpeg=7\.1\.1", rig.calls_text(), re.M)
    assert "pip install" not in rig.calls_text()            # same version: only the package added
    for t in ("ffmpeg", "ffprobe"):
        assert link_target(rig.prefix / "ffmpeg-bin" / t) == str(rig.env_dir / "bin" / t)
    state = (rig.prefix / "install-state").read_text()
    assert "ffmpeg_mode=env\n" in state and f"ffmpeg={rig.env_dir}/bin/ffmpeg\n" in state
    assert f"cmr doctor CMR_TESSERACT={rig.env_dir}/bin/tesseract ffmpeg={rig.env_dir}/bin/ffmpeg" in rig.calls_text()
    rig.reset_calls()                                 # next plain run: nothing left to do
    p = rig.run("--yes")
    assert p.returncode == 0, out(p)
    assert "conda install" not in rig.calls_text() and "Environment is up to date" in p.stdout
    assert "The previous install used the system ffmpeg" not in p.stdout


def test_rerun_needing_ffmpeg_checks_free_space(rig):
    assert rig.run("--yes", "--use-system-ffmpeg").returncode == 0
    p = rig.run("--yes", FAKE_DF_AVAIL_KB=800 * 1024)      # enough for an update, not for ffmpeg
    assert p.returncode == 1 and "about 1024 MB needed" in p.stderr
    assert link_target(rig.prefix / "ffmpeg-bin/ffmpeg") == str(rig.sysbin / "ffmpeg")
    p = rig.run("--yes", "--use-system-ffmpeg", FAKE_DF_AVAIL_KB=800 * 1024)
    assert p.returncode == 0, out(p)


def old_ffmpeg_env_layout(rig):
    """An install made by the previous installer: ffmpeg in <prefix>/ffmpeg-env, links into it."""
    assert rig.run("--yes", "--use-system-ffmpeg").returncode == 0
    ffenv = rig.prefix / "ffmpeg-env/bin"
    ffenv.mkdir(parents=True)
    (rig.prefix / "ffmpeg-env/conda-meta").mkdir()
    for t in ("ffmpeg", "ffprobe"):
        shutil.copy(FAKES / "ffmpeg", ffenv / t)
        (rig.prefix / "ffmpeg-bin" / t).unlink()
        (rig.prefix / "ffmpeg-bin" / t).symlink_to(ffenv / t)
    st = rig.prefix / "install-state"
    st.write_text(st.read_text().replace("ffmpeg_mode=system", "ffmpeg_mode=env")
                  .replace(f"ffmpeg={rig.sysbin}/ffmpeg", f"ffmpeg={ffenv}/ffmpeg"))
    rig.reset_calls()


def test_migration_from_ffmpeg_env_dry_run(rig):
    old_ffmpeg_env_layout(rig)
    p = rig.run("--dry-run", ffmpeg=False)
    assert p.returncode == 0, out(p)
    assert f"would remove the old separate ffmpeg environment {rig.prefix}/ffmpeg-env" in p.stdout
    assert re.search(r"\[dry-run\] .*conda install -y -q -p \S+/env .* ffmpeg=7\.1\.1", p.stdout)
    assert (rig.prefix / "ffmpeg-env/bin/ffmpeg").exists()


def test_migration_from_ffmpeg_env_into_env(rig):
    """Old layout (default run, a good system ffmpeg on PATH): ffmpeg is installed into env, the
    links switch, ffmpeg-env is removed and the log says so; the next run has nothing left to do."""
    old_ffmpeg_env_layout(rig)
    p = rig.run("--yes")
    assert p.returncode == 0, out(p)
    calls = rig.calls_text()
    assert re.search(r"^conda install -y -q -p \S+/env .* ffmpeg=7\.1\.1", calls, re.M)
    assert not re.search(r"^conda .*ffmpeg-env", calls, re.M)
    assert not (rig.prefix / "ffmpeg-env").exists()
    for t in ("ffmpeg", "ffprobe"):
        assert link_target(rig.prefix / "ffmpeg-bin" / t) == str(rig.env_dir / "bin" / t)
    assert f"cmr doctor CMR_TESSERACT={rig.env_dir}/bin/tesseract ffmpeg={rig.env_dir}/bin/ffmpeg" in calls
    assert "Migrated: removed the old separate ffmpeg environment" in p.stdout
    assert "remove the old separate ffmpeg environment" in rig.log.read_text()
    assert f"ffmpeg={rig.env_dir}/bin/ffmpeg\n" in (rig.prefix / "install-state").read_text()
    rig.reset_calls()
    p = rig.run("--yes", ffmpeg=False)
    assert p.returncode == 0, out(p)
    assert "conda install" not in rig.calls_text() and "Migrated" not in p.stdout


def test_migration_from_ffmpeg_env_system_ffmpeg_good(rig):
    """Old layout, --use-system-ffmpeg and the system ffmpeg passes: links go to it, the unused
    ffmpeg-env goes."""
    old_ffmpeg_env_layout(rig)
    p = rig.run("--yes", "--use-system-ffmpeg")
    assert p.returncode == 0, out(p)
    assert "conda install" not in rig.calls_text()
    assert not (rig.prefix / "ffmpeg-env").exists() and not (rig.env_dir / "bin/ffmpeg").exists()
    assert link_target(rig.prefix / "ffmpeg-bin/ffmpeg") == str(rig.sysbin / "ffmpeg")
    assert "Migrated" in p.stdout


def test_migration_keeps_ffmpeg_env_when_conda_fails(rig):
    """A failed conda install leaves the old, working layout alone (links still valid)."""
    old_ffmpeg_env_layout(rig)
    p = rig.run("--yes", ffmpeg=False, FAKE_CONDA_FAIL=1)
    assert p.returncode == 1 and "conda could not update the environment" in p.stderr
    assert (rig.prefix / "ffmpeg-env/bin/ffmpeg").exists()
    assert link_target(rig.prefix / "ffmpeg-bin/ffmpeg") == str(rig.prefix / "ffmpeg-env/bin/ffmpeg")


def test_incomplete_env_is_recreated(rig):
    (rig.env_dir / "conda-meta").mkdir(parents=True)
    (rig.prefix / ".cmr-install-prefix").touch()
    p = rig.run("--yes")
    assert p.returncode == 0, out(p)
    assert "conda create" in rig.calls_text()


def test_bin_dir_option_is_remembered(rig):
    bindir = rig.tmp / "mybin"
    rig.workspace = rig.tmp / "ws"
    p = rig.run("--yes", "--bin-dir", str(bindir), "--workspace", str(rig.workspace))
    assert p.returncode == 0, out(p)
    assert os.readlink(bindir / "cmr") == str(rig.launcher) and not rig.link.exists()
    assert f"bin_dir={bindir}\n" in (rig.prefix / "install-state").read_text()
    assert f"export PATH=\"{bindir}:$PATH\"" in p.stdout
    p = rig.run("--yes")                              # re-run without the flag: same folder
    assert p.returncode == 0, out(p)
    assert not rig.link.exists() and not os.path.lexists(rig.link)
    p = rig.run("--uninstall", "--yes")               # uninstall finds the launcher there
    assert p.returncode == 0, out(p)
    assert f"Kept: your workspace ({rig.workspace})" in p.stdout
    assert not (bindir / "cmr").exists() and not rig.prefix.exists()


def test_workspace_gets_the_guide(rig):
    """templates/README.md links ../USERGUIDE.md: the guide is copied (and refreshed)."""
    (rig.bundle / "USERGUIDE.md").write_text("guide v1\n")
    (rig.bundle / "README.md").write_text("readme\n")
    write_sums(rig.bundle)
    assert rig.run("--yes").returncode == 0
    assert (rig.workspace / "USERGUIDE.md").read_text() == "guide v1\n"
    assert (rig.workspace / "README.md").exists()
    (rig.bundle / "USERGUIDE.md").write_text("guide v2\n")
    write_sums(rig.bundle)
    assert rig.run("--yes").returncode == 0
    assert (rig.workspace / "USERGUIDE.md").read_text() == "guide v2\n"


# --- demo spots (generated at install time by the bundled demo/make_spot.py) -----------------
DEMO_CLEAN = "SB X League 2026_QB Pat_03_ABCD1234567H_1920x1080_Slated.mov"      # fake generator names
DEMO_ERRORS = "SB X League 2026_QB Pat_03_ABCD1234567H_1280x720_Slated.mov"
DEMO_FILES = [f"templates/starter/media/{DEMO_CLEAN}", f"demo_spots/clean/{DEMO_CLEAN}",
              f"demo_spots/errors/many_errors/{DEMO_ERRORS}", "demo_spots/errors/many_errors/manifest.yaml",
              f"demo_spots/errors/client_typo/{DEMO_CLEAN}", "demo_spots/errors/client_typo/manifest.yaml"]


def demo_calls(rig) -> list:
    return [l for l in rig.calls_text().splitlines() if "make_spot.py" in l]


def test_demo_on_by_default(rig):
    p = rig.run("--yes")
    assert p.returncode == 0, out(p)
    ws = rig.workspace
    for f in DEMO_FILES:
        assert (ws / f).is_file(), f
    assert (ws / f"templates/starter/media/{DEMO_CLEAN}").read_text() == "clean spot\n"
    assert (ws / f"demo_spots/errors/client_typo/{DEMO_CLEAN}").read_text() == "spot client_typo\n"
    assert (ws / "templates/starter/media/README.txt").exists()           # the template stays
    assert sorted((ws / ".cmr-demo-files").read_text().splitlines()) == sorted(DEMO_FILES)
    # generated media is not a template: not in the template record
    assert "demo_spots" not in (ws / ".cmr-installed-files").read_text()
    assert ".mov" not in (ws / ".cmr-installed-files").read_text()
    assert not list(ws.glob(".cmr-demo-tmp.*"))                            # temp folder removed
    calls = demo_calls(rig)
    assert len(calls) == 2 and all("--short" in c and "PYTHONPATH=unset" in c for c in calls)
    assert calls[1].split(" PYTHONPATH")[0].endswith("--short --defect client_typo")
    assert f"make_spot ffmpeg={rig.prefix}/ffmpeg-bin/ffmpeg" in rig.calls_text()   # cmr's ffmpeg
    assert (rig.env_dir / "conda-meta/pillow-0.0-fake.json").exists()
    assert "demo=1\n" in (rig.prefix / "install-state").read_text()
    assert "Created in" in p.stdout and "demo_spots/" in p.stdout


def test_no_demo(rig):
    p = rig.run("--yes", "--no-demo")
    assert p.returncode == 0, out(p)
    assert "pillow" not in rig.calls_text() and "numpy" not in rig.calls_text()
    assert not demo_calls(rig) and not (rig.workspace / "demo_spots").exists()
    assert not (rig.workspace / ".cmr-demo-files").exists()
    assert list((rig.workspace / "templates/starter/media").iterdir()) == [rig.workspace / "templates/starter/media/README.txt"]
    assert "Skipped (--no-demo)" in p.stdout and "make_spot.py ~/cmr-demo --short" in p.stdout
    assert "demo=0\n" in (rig.prefix / "install-state").read_text()
    # turned on later: Pillow/numpy added to the same env, spots generated
    rig.reset_calls()
    p = rig.run("--yes")
    assert p.returncode == 0, out(p)
    assert re.search(r"^conda install .* pillow numpy$", rig.calls_text(), re.M)
    assert "pip install" not in rig.calls_text()
    assert len(demo_calls(rig)) == 2 and (rig.workspace / DEMO_FILES[0]).exists()


def test_demo_dry_run(rig):
    p = rig.run("--dry-run")
    assert p.returncode == 0, out(p)
    assert re.search(r"\[dry-run\] would run .*env/bin/python \S+/demo/make_spot.py TMP/main --short", p.stdout)
    assert "--defect client_typo" in p.stdout and "would place" in p.stdout
    assert "First check (the starter folder already holds a clean demo spot)" in p.stdout \
        or "is not on your PATH" in p.stdout
    assert list(rig.home.iterdir()) == []
    p = rig.run("--dry-run", "--no-demo")
    assert "make_spot.py TMP" not in p.stdout and "pillow" not in p.stdout


def test_demo_rerun_is_idempotent(rig):
    assert rig.run("--yes").returncode == 0
    ws = rig.workspace
    (ws / DEMO_FILES[0]).unlink()                                  # the user removed/moved it
    (ws / "demo_spots/errors/many_errors/manifest.yaml").write_text("my notes\n")
    for args in ((), ("--upgrade",)):
        rig.reset_calls()
        p = rig.run("--yes", *args)
        assert p.returncode == 0, out(p)
        assert not demo_calls(rig) and "Already created before" in p.stdout
        assert not (ws / DEMO_FILES[0]).exists()                   # not created again
        assert (ws / "demo_spots/errors/many_errors/manifest.yaml").read_text() == "my notes\n"
        assert "conda install" not in rig.calls_text() or args       # pillow/numpy already there


def test_demo_never_overwrites_existing_files(rig):
    mine = rig.workspace / f"templates/starter/media/{DEMO_CLEAN}"
    mine.parent.mkdir(parents=True)
    mine.write_text("my own render\n")
    p = rig.run("--yes")
    assert p.returncode == 0, out(p)
    assert mine.read_text() == "my own render\n"
    assert (rig.workspace / f"demo_spots/clean/{DEMO_CLEAN}").read_text() == "clean spot\n"
    assert f"templates/starter/media/{DEMO_CLEAN}" not in (rig.workspace / ".cmr-demo-files").read_text()


def test_demo_failure_warns_but_install_succeeds(rig):
    p = rig.run("--yes", FAKE_DEMO_FAIL=1)
    assert p.returncode == 0, out(p)
    assert "the demo spots could not be generated (the install itself is fine)" in p.stderr
    assert "no font found" in p.stderr and "CMR_TEST_FONT" in p.stdout
    assert "Run the installer again to retry, or make them by hand: mkdir -p ~/cmr-demo && PATH=" in p.stdout
    assert f"{rig.env_dir}/bin/python {rig.bundle}/demo/make_spot.py ~/cmr-demo --short" in p.stdout
    assert "no font found" in rig.log.read_text()
    assert not (rig.workspace / ".cmr-demo-files").exists() and not (rig.workspace / "demo_spots").exists()
    assert not list(rig.workspace.glob(".cmr-demo-tmp.*"))
    assert "CheckMyRender 0.0.1 is installed." in p.stdout
    p = rig.run("--yes")                                           # a re-run retries
    assert p.returncode == 0 and (rig.workspace / DEMO_FILES[0]).exists()


def test_bundle_without_generator_warns(rig):
    shutil.rmtree(rig.bundle / "demo")
    write_sums(rig.bundle)
    p = rig.run("--yes")
    assert p.returncode == 0, out(p)
    assert "this bundle has no demo generator" in p.stderr


def test_uninstall_keeps_demo_spots(rig):
    assert rig.run("--yes").returncode == 0
    p = rig.run("--uninstall", "--yes")
    assert p.returncode == 0, out(p)
    assert "The demo spots stay in the workspace too" in p.stdout
    assert all((rig.workspace / f).exists() for f in DEMO_FILES)


# --- uninstall ------------------------------------------------------------------------------
def test_uninstall_asks_and_cancel_keeps_everything(rig):
    assert rig.run("--yes").returncode == 0
    p = rig.run("--uninstall", stdin="n\n")
    assert p.returncode == 3 and "Cancelled" in p.stdout
    assert rig.prefix.exists() and rig.launcher.exists()
    p = rig.run("--uninstall")                    # no answer (EOF) = no
    assert p.returncode == 3 and rig.prefix.exists()


def test_uninstall_dry_run(rig):
    assert rig.run("--yes").returncode == 0
    p = rig.run("--uninstall", "--dry-run")
    assert p.returncode == 0 and f"would remove {rig.prefix}" in p.stdout
    assert rig.prefix.exists() and rig.launcher.exists()


def test_uninstall_yes_removes_only_cmr(rig):
    assert rig.run("--yes").returncode == 0
    p = rig.run("--uninstall", stdin="y\n")
    assert p.returncode == 0, out(p)
    assert not rig.prefix.exists() and not os.path.lexists(rig.link)
    assert rig.workspace.exists() and rig.log.exists() and rig.work.exists()   # kept
    assert (rig.home / ".local/bin").exists()                       # only our file removed


def test_uninstall_refuses_foreign_prefix(rig):
    rig.prefix.mkdir(parents=True)
    (rig.prefix / "data").write_text("x")
    p = rig.run("--uninstall", "--yes")
    assert p.returncode == 1 and "was not created by this installer" in p.stderr
    assert (rig.prefix / "data").exists()


def test_uninstall_keeps_foreign_launcher(rig):
    assert rig.run("--yes").returncode == 0
    rig.link.unlink()
    rig.link.write_text("#!/bin/sh\necho mine\n")
    assert rig.run("--uninstall", "--yes").returncode == 0
    assert rig.link.read_text() == "#!/bin/sh\necho mine\n"
    assert not rig.prefix.exists()


# --- single cmr folder (D33): --root, PATH link, work area, relocatable launcher, moves -----
def conf_of(prefix: Path) -> dict:
    """cmr-paths.conf read by sh itself (it is sh syntax)."""
    keys = ("CMR_VERSION CMR_ROOT CMR_BIN CMR_LAUNCHER CMR_ENV CMR_FFMPEG CMR_FFPROBE "
            "CMR_TESSERACT CMR_TESSDATA CMR_DEMO CMR_WORK").split()
    script = '. "$1"; for k in ' + " ".join(keys) + '; do eval "printf \'%s=%s\\\\n\' $k \\"\\$$k\\""; done'
    r = subprocess.run(["sh", "-c", script, "x", str(prefix / "cmr-paths.conf")],
                       capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    return dict(l.split("=", 1) for l in r.stdout.splitlines())


def run_launcher(rig, cmd, *args, cwd=None, path=None):
    """Run the launcher (or a link to it) like a user would: /bin/sh script, fake tools on PATH."""
    env = {"HOME": str(rig.home), "PATH": path or rig.path(), "FAKES": str(FAKES), "FAKE_CALLS": str(rig.calls)}
    return subprocess.run([str(cmd), *args], env=env, capture_output=True, text=True, cwd=cwd or rig.tmp)


def test_root_layout(rig):
    root = rig.tmp / "Tools/cmr"
    p = rig.run("--yes", "--root", str(root))
    assert p.returncode == 0, out(p)
    b = root / "bin"
    for f in ("cmr", "env/bin/python", "ffmpeg-bin/ffmpeg", "install-state", "cmr-paths.conf", ".cmr-install-prefix"):
        assert os.path.lexists(b / f), f
    assert (root / "demo/templates/specs/spec_review_render.yaml").exists()
    assert (root / "demo/demo_spots/clean").is_dir()
    for d in ("specs", "profiles", "jobs", "reports"):
        assert (root / "work" / d).is_dir(), d
    assert (root / "work/jobs/job_template.yaml").read_text() == "seed jobs/job_template.yaml\n"
    assert os.readlink(rig.link) == str(b / "cmr")
    assert not (rig.home / "cmr").exists()                    # nothing in the default root
    c = conf_of(b)
    assert c == {"CMR_VERSION": "0.0.1", "CMR_ROOT": str(root), "CMR_BIN": str(b), "CMR_LAUNCHER": str(b / "cmr"),
                 "CMR_ENV": str(b / "env"), "CMR_FFMPEG": str(b / "env/bin/ffmpeg"),
                 "CMR_FFPROBE": str(b / "env/bin/ffprobe"), "CMR_TESSERACT": str(b / "env/bin/tesseract"),
                 "CMR_TESSDATA": str(b / "env/share/tessdata"), "CMR_DEMO": str(root / "demo"),
                 "CMR_WORK": str(root / "work")}
    assert (b / "cmr-paths.conf").read_text().startswith(f"# for {b} 0.0.1\n")
    state = (b / "install-state").read_text()
    for kv in (f"prefix={b}", f"root={root}", f"workspace={root}/demo", f"work={root}/work", f"bin_dir={rig.home}/.local/bin"):
        assert kv + "\n" in state, kv
    assert f"cmr doctor CMR_TESSERACT={b}/env/bin/tesseract" in rig.calls_text()      # through the link
    assert f"PATH link   : {rig.link} -> {b}/cmr" in p.stdout and f"Work area   : {root}/work" in p.stdout
    p = rig.run("--uninstall", "--yes", "--root", str(root))
    assert p.returncode == 0, out(p)
    assert not b.exists() and not os.path.lexists(rig.link)
    assert (root / "demo").exists() and (root / "work/jobs/job_template.yaml").exists()   # kept
    assert f"your work area ({root}/work)" in p.stdout


def test_root_refused(rig):
    for r in (str(rig.home), "/"):
        p = rig.run("--dry-run", "--root", r)
        assert p.returncode == 2 and "refusing --root" in p.stderr


def test_dry_run_root_writes_nothing(rig):
    p = rig.run("--dry-run", "--root", str(rig.tmp / "x/cmr"))
    assert p.returncode == 0, out(p)
    assert "would create " + str(rig.tmp / "x/cmr/work") in p.stdout
    assert not (rig.tmp / "x").exists() and list(rig.home.iterdir()) == []


def test_bin_dir_equal_to_prefix_makes_no_link(rig):
    p = rig.run("--yes", "--bin-dir", str(rig.prefix))
    assert p.returncode == 0, out(p)
    assert rig.launcher.is_file() and not rig.launcher.is_symlink() and not os.path.lexists(rig.link)
    assert "PATH link" not in p.stdout
    assert rig.run("--uninstall", "--yes").returncode == 0 and not rig.prefix.exists()


def test_link_safety(rig):
    """The PATH link: a foreign link is refused (nothing changed), a dangling one is repaired, a link
    to another CheckMyRender launcher is re-pointed; uninstall removes only a link to this launcher."""
    rig.link.parent.mkdir(parents=True)
    foreign = rig.tmp / "devcmr"
    foreign.write_text("#!/bin/sh\necho dev\n")
    rig.link.symlink_to(foreign)
    p = rig.run("--yes")
    assert p.returncode == 1 and f"{rig.link} is a link to {foreign}, which is not a CheckMyRender launcher" in p.stderr
    assert os.readlink(rig.link) == str(foreign) and not rig.prefix.exists()
    rig.link.unlink()
    rig.link.symlink_to(rig.tmp / "gone/bin/cmr")                    # dangling (folder moved away)
    p = rig.run("--yes")
    assert p.returncode == 0, out(p)
    assert "Repaired the PATH link" in p.stdout and os.readlink(rig.link) == str(rig.launcher)
    other = rig.tmp / "other/bin/cmr"                                  # another install's launcher
    other.parent.mkdir(parents=True)
    other.write_text("#!/bin/sh\n# CheckMyRender launcher (generated by install.sh)\n")
    rig.link.unlink()
    rig.link.symlink_to(other)
    p = rig.run("--yes")
    assert p.returncode == 0 and "now points to this install" in p.stdout
    assert os.readlink(rig.link) == str(rig.launcher)
    rig.link.unlink()                                                  # user points it elsewhere
    rig.link.symlink_to(other)
    p = rig.run("--uninstall", "--yes")
    assert p.returncode == 0 and f"Kept: {rig.link} (it points to {other}" in p.stdout
    assert os.readlink(rig.link) == str(other) and not rig.prefix.exists()


def test_launcher_finds_itself_through_links(rig):
    """Through the PATH link, a chain of two links (one relative), from another folder and by
    PATH lookup: every path comes from the real launcher folder."""
    assert rig.run("--yes").returncode == 0
    a = rig.tmp / "links/a"
    a.parent.mkdir()
    a.symlink_to(os.path.relpath(rig.link, a.parent))                  # relative -> absolute link -> launcher
    for cmd, path in ((rig.link, None), (a, None), ("cmr", f"{rig.link.parent}:{rig.path()}")):
        rig.reset_calls()
        r = run_launcher(rig, cmd, "doctor", cwd=rig.home, path=path)
        assert r.returncode == 0, r.stderr
        assert (f"cmr doctor CMR_TESSERACT={rig.env_dir}/bin/tesseract ffmpeg={rig.env_dir}/bin/ffmpeg "
                f"PYTHONPATH=unset TMPDIR=unset TESSDATA_PREFIX={rig.env_dir}/share/tessdata") in rig.calls_text(), cmd


def test_moved_folder_launcher_repairs_paths_file(rig):
    """mv of the whole cmr folder: the launcher runs from the new place (ffmpeg links are
    relative, tesseract/tessdata follow), rewrites cmr-paths.conf once, tolerates a read-only folder."""
    assert rig.run("--yes").returncode == 0
    new = rig.tmp / "elsewhere/cmr"
    new.parent.mkdir()
    shutil.move(str(rig.root), str(new))
    b = new / "bin"
    assert conf_of(b)["CMR_BIN"] == str(rig.prefix)                    # stale until the first start
    rig.reset_calls()
    r = run_launcher(rig, b / "cmr", "doctor")
    assert r.returncode == 0 and r.stderr == "", r.stderr
    assert (f"cmr doctor CMR_TESSERACT={b}/env/bin/tesseract ffmpeg={b}/env/bin/ffmpeg "
            f"PYTHONPATH=unset TMPDIR=unset TESSDATA_PREFIX={b}/env/share/tessdata") in rig.calls_text()
    c = conf_of(b)
    assert c["CMR_BIN"] == str(b) and c["CMR_ROOT"] == str(new) and c["CMR_WORK"] == str(new / "work")
    assert c["CMR_DEMO"] == str(new / "demo") and c["CMR_FFMPEG"] == str(b / "env/bin/ffmpeg")
    assert not list(b.glob("cmr-paths.conf.tmp.*"))
    mtime = (b / "cmr-paths.conf").stat().st_mtime_ns
    assert run_launcher(rig, b / "cmr", "--version").returncode == 0
    assert (b / "cmr-paths.conf").stat().st_mtime_ns == mtime          # current: not rewritten
    # moved again into a read-only place: runs, says nothing, the stale file stays
    new2 = rig.tmp / "ro/cmr"
    new2.parent.mkdir()
    shutil.move(str(new), str(new2))
    (new2 / "bin").chmod(0o555)
    try:
        r = run_launcher(rig, new2 / "bin/cmr", "doctor")
        assert r.returncode == 0 and r.stderr == "", r.stderr
        assert conf_of(new2 / "bin")["CMR_BIN"] == str(b)
    finally:
        (new2 / "bin").chmod(0o755)


def test_moved_install_is_installed_fresh(rig):
    """The installer pointed at a moved folder: no conda repair, a fresh install of the same
    version (env rebuilt with conda create, wheel installed), demo/ and work/ kept untouched,
    the dangling PATH link repaired, state and paths file at the new place."""
    assert rig.run("--yes").returncode == 0
    (rig.work / "specs/mine.yaml").write_text("mine\n")
    new = rig.tmp / "moved/cmr"
    new.parent.mkdir()
    shutil.move(str(rig.root), str(new))
    assert not rig.link.exists()                                      # dangling now
    rig.reset_calls()
    p = rig.run("--yes", "--root", str(new))
    assert p.returncode == 0, out(p)
    assert f"This cmr folder was moved: installed at {rig.prefix}, now at {new}/bin" in p.stdout
    assert "installs cmr 0.0.1 FRESH here" in p.stdout
    calls = rig.calls_text()
    assert re.search(rf"^conda create -y -q -p {re.escape(str(new))}/bin/env ", calls, re.M)
    assert "pip install --no-deps" in calls and not demo_calls(rig)
    assert not list((new / "bin").glob("*.before-move"))
    assert "Repaired the PATH link" in p.stdout and os.readlink(rig.link) == str(new / "bin/cmr")
    assert (new / "work/specs/mine.yaml").read_text() == "mine\n" and "Already set up" in p.stdout
    assert "Already created before" in p.stdout                        # demo record moved along
    state = (new / "bin/install-state").read_text()
    assert f"prefix={new}/bin\n" in state and f"workspace={new}/demo\n" in state and f"work={new}/work\n" in state
    assert conf_of(new / "bin")["CMR_BIN"] == str(new / "bin")
    rig.reset_calls()                                                  # next run: a normal re-run
    p = rig.run("--yes", "--root", str(new))
    assert p.returncode == 0 and "This cmr folder was moved" not in p.stdout and "conda create" not in rig.calls_text()


def test_moved_install_failure_puts_old_env_back(rig):
    assert rig.run("--yes").returncode == 0
    new = rig.tmp / "moved/cmr"
    new.parent.mkdir()
    shutil.move(str(rig.root), str(new))
    p = rig.run("--yes", "--root", str(new), FAKE_CONDA_FAIL=1)
    assert p.returncode == 1 and "conda could not create the environment" in p.stderr
    assert "Put back the previous env/" in p.stderr
    assert (new / "bin/env/bin/python").exists() and not (new / "bin/env.before-move").exists()


def test_moved_install_with_own_miniforge_bootstraps_again(rig):
    assert rig.run("--yes", "--bootstrap-conda", conda=False).returncode == 0
    new = rig.tmp / "moved/cmr"
    new.parent.mkdir()
    shutil.move(str(rig.root), str(new))
    rig.reset_calls()
    p = rig.run("--root", str(new), conda=False)                       # no --yes: no question asked
    assert p.returncode == 0, out(p)
    assert "own Miniforge is not used after the move" in p.stdout
    assert f"-b -p {new}/bin/conda" in rig.log.read_text()
    assert f"conda={new}/bin/conda/bin/conda\n" in (new / "bin/install-state").read_text()


def test_work_area_seeded_once_never_overwritten(rig):
    mine = rig.work / "jobs/job_template.yaml"                         # exists before the first install
    mine.parent.mkdir(parents=True)
    mine.write_text("my job\n")
    p = rig.run("--yes")
    assert p.returncode == 0, out(p)
    assert mine.read_text() == "my job\n" and "(1 existing files kept)" in p.stdout
    assert (rig.work / "README.md").read_text() == "seed README.md\n"
    listed = (rig.work / ".cmr-work-files").read_text().splitlines()
    assert sorted(listed) == ["README.md", "profiles/client_review.yaml", "specs/spec_review_render.yaml"]
    (rig.work / "README.md").unlink()                                 # the user deletes/edits seeds
    (rig.work / "specs/spec_review_render.yaml").write_text("edited\n")
    new = make_bundle(rig.tmp / "bundle2", version="0.0.2")
    (new / "work/README.md").write_text("seed v2\n")
    write_sums(new)
    for bundle, args in ((rig.bundle, ()), (rig.bundle, ("--upgrade",)), (new, ())):
        rig.bundle = bundle
        p = rig.run("--yes", *args)
        assert p.returncode == 0, out(p)
        assert "Already set up" in p.stdout
        assert not (rig.work / "README.md").exists()
        assert (rig.work / "specs/spec_review_render.yaml").read_text() == "edited\n"
    assert rig.run("--uninstall", "--yes").returncode == 0
    assert (rig.work / "specs/spec_review_render.yaml").exists() and (rig.work / ".cmr-work-files").exists()


def test_no_work(rig):
    p = rig.run("--yes", "--no-work")
    assert p.returncode == 0, out(p)
    assert not rig.work.exists() and "Skipped (--no-work)" in p.stdout
    assert "cmr_work=''" in rig.launcher.read_text() and conf_of(rig.prefix)["CMR_WORK"] == ""


def old_layout_install(rig):
    """An install made by the installer before D33: prefix ~/.local/share/cmr, workspace
    ~/cmr-workspace, a launcher FILE in ~/.local/bin, state without prefix/root/work."""
    legacy = rig.home / ".local/share/cmr"
    ws = rig.home / "cmr-workspace"
    assert rig.run("--yes", "--prefix", str(legacy), "--workspace", str(ws), "--no-work").returncode == 0
    (legacy / "cmr").unlink()
    (legacy / "cmr-paths.conf").unlink()
    rig.link.unlink()
    rig.link.write_text(f"#!/bin/sh\n# CheckMyRender launcher (generated by install.sh)\nCMR_PREFIX='{legacy}'\n"
                        'exec "$CMR_PREFIX/env/bin/cmr" "$@"\n')
    rig.link.chmod(0o755)
    st = legacy / "install-state"
    st.write_text("".join(l for l in st.read_text().splitlines(keepends=True)
                          if not l.startswith(("prefix=", "root=", "work="))))
    rig.reset_calls()
    return legacy, ws


def test_old_layout_rerun_upgrade_uninstall(rig):
    legacy, ws = old_layout_install(rig)
    p = rig.run("--yes")
    assert p.returncode == 0, out(p)
    assert f"Previous layout found: {legacy}" in p.stdout and "--uninstall, then" in p.stdout
    assert "Replaced the launcher file" in p.stdout and os.readlink(rig.link) == str(legacy / "cmr")
    assert "conda create" not in rig.calls_text() and "This cmr folder was moved" not in p.stdout
    assert not (rig.home / "cmr").exists()                            # the new default root is not created
    assert f"workspace={ws}\n" in (legacy / "install-state").read_text()
    assert run_launcher(rig, rig.link, "doctor").returncode == 0
    new = make_bundle(rig.tmp / "bundle2", version="0.0.2")
    rig.bundle = new
    p = rig.run("--yes")
    assert p.returncode == 0 and "Upgrade in place: cmr 0.0.1 -> 0.0.2" in p.stdout
    p = rig.run("--uninstall", "--yes")
    assert p.returncode == 0, out(p)
    assert not legacy.exists() and not os.path.lexists(rig.link) and ws.exists()


def test_old_layout_launcher_file_uninstall(rig):
    """Uninstall straight from the old layout removes the old launcher FILE (marker) too."""
    legacy, ws = old_layout_install(rig)
    p = rig.run("--uninstall", "--yes")
    assert p.returncode == 0 and "(previous layout)" in p.stdout
    assert not legacy.exists() and not rig.link.exists() and ws.exists()


def test_single_folder_trial_state(rig):
    """The hand-made trial (D33): --prefix ROOT/bin --bin-dir ROOT/bin, a state without prefix=;
    a run with --root makes the PATH link in ~/.local/bin instead of reusing bin_dir=prefix."""
    assert rig.run("--yes", "--bin-dir", str(rig.prefix)).returncode == 0
    st = rig.prefix / "install-state"
    st.write_text("".join(l for l in st.read_text().splitlines(keepends=True)
                          if not l.startswith(("prefix=", "root=", "work="))))
    shutil.rmtree(rig.work)
    p = rig.run("--yes", "--root", str(rig.root))
    assert p.returncode == 0, out(p)
    assert "This cmr folder was moved" not in p.stdout and os.readlink(rig.link) == str(rig.launcher)
    assert (rig.work / "jobs/job_template.yaml").exists()


# --- bundle builder -------------------------------------------------------------------------
SYS_PY = "/usr/bin/python3"


def _can_build() -> bool:
    if not Path(SYS_PY).exists():
        return False
    return subprocess.run([SYS_PY, "-c", "import setuptools, wheel, yaml, jsonschema"],
                          capture_output=True).returncode == 0


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    if not _can_build():
        pytest.skip(f"{SYS_PY} lacks setuptools/wheel/PyYAML/jsonschema (wheel build needs no network)")
    out_dir = tmp_path_factory.mktemp("dist")
    before = {p.name for p in ROOT.iterdir()}
    p = subprocess.run([BASH, str(INST / "build_bundle.sh"), "-o", str(out_dir), "--python", SYS_PY],
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    assert {p.name for p in ROOT.iterdir()} == before            # no build/ dist/ in the repo
    tars = list(out_dir.glob("cmr-*-bundle.tar.gz"))
    assert len(tars) == 1
    x = tmp_path_factory.mktemp("x")
    with tarfile.open(tars[0]) as t:
        t.extractall(x, filter="data")
    return tars[0], next(x.iterdir())


def test_bundle_contents(built):
    tar, top = built
    version = re.search(r'__version__ = "([^"]+)"', (ROOT / "src/cmr/__init__.py").read_text())[1]
    assert tar.name == f"cmr-{version}-bundle.tar.gz" and top.name == f"cmr-{version}"
    files = {str(p.relative_to(top)) for p in top.rglob("*") if p.is_file()}
    for f in ("install.sh", "environment.yml", "required_filters.txt", "VERSION", "README.txt",
              "README.md", "USERGUIDE.md",
              "SHA256SUMS", f"wheels/check_my_render-{version}-py3-none-any.whl",
              "templates/README.md", "templates/specs/spec_review_render.yaml",
              "templates/starter/job.yaml", "templates/starter/media/README.txt",
              "demo/make_spot.py", "demo/values_fictional.yaml", "work/README.md",
              "work/jobs/job_template.yaml", "work/specs/spec_review_render.yaml",
              "work/profiles/client_review.yaml"):
        assert f in files, f
    for f in ("make_spot.py", "values_fictional.yaml"):      # the test copy is the single source
        assert (top / "demo" / f).read_bytes() == (ROOT / "tests/slate" / f).read_bytes()
    assert (top / "work/specs/spec_review_render.yaml").read_bytes() == (ROOT / "templates/specs/spec_review_render.yaml").read_bytes()
    assert (top / "work/profiles/client_review.yaml").read_bytes() == (ROOT / "templates/profiles/client_review.yaml").read_bytes()
    assert not [f for f in files if f.endswith(".mov")]
    assert not [f for f in files if f.endswith("CLAUDE.md") or "__pycache__" in f]
    src = {str(p.relative_to(ROOT)) for d in ("examples", "templates") for p in (ROOT / d).rglob("*")
           if p.is_file() and p.name != "CLAUDE.md" and "__pycache__" not in p.parts}
    assert src <= files
    assert os.access(top / "install.sh", os.X_OK)
    assert (top / "VERSION").read_text().strip() == version
    for doc in ("README.md", "USERGUIDE.md"):     # templates/README.md links ../USERGUIDE.md
        assert (top / doc).read_bytes() == (ROOT / doc).read_bytes(), doc
    assert "../USERGUIDE.md" in (top / "templates/README.md").read_text()
    assert (tar.parent / (tar.name + ".sha256")).read_text().split()[0] == sha256(tar)


def test_bundle_checksums(built):
    _, top = built
    sums = dict(reversed(l.split("  ", 1)) for l in (top / "SHA256SUMS").read_text().splitlines())
    files = {str(p.relative_to(top)) for p in top.rglob("*") if p.is_file()} - {"SHA256SUMS"}
    assert set(sums) == files
    for f, h in sums.items():
        assert sha256(top / f) == h, f


def test_bundle_filters_match_the_package(built):
    """required_filters.txt is generated from the checks' requires_filters (cmr doctor's list)."""
    _, top = built
    listed = [l for l in (top / "required_filters.txt").read_text().splitlines() if l and l[0] != "#"]
    code = ("import sys; sys.path.insert(0, 'src'); from cmr import engine; engine.load_checks(); "
            "print('\\n'.join(sorted({f for c in engine.REGISTRY.values() for f in c.requires_filters})))")
    p = subprocess.run([SYS_PY, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert listed == p.stdout.split()
    assert "signalstats" in listed and "lut2" in listed


def test_bundle_wheel_has_profiles(built):
    import zipfile
    _, top = built
    whl = next((top / "wheels").glob("*.whl"))
    names = zipfile.ZipFile(whl).namelist()
    assert "cmr/profiles/final.yaml" in names and "cmr/cli.py" in names
    assert not [n for n in names if n.endswith("CLAUDE.md")]


def test_build_rejects_mismatched_wheel(tmp_path):
    w = tmp_path / "check_my_render-9.9.9-py3-none-any.whl"
    w.write_bytes(b"x")
    p = subprocess.run([BASH, str(INST / "build_bundle.sh"), "-o", str(tmp_path / "o"), "--wheel", str(w)],
                       capture_output=True, text=True)
    assert p.returncode == 1 and "does not match version" in p.stderr


def test_real_bundle_installs_with_fakes(built, tmp_path):
    """The built bundle, unchanged, through the installer (fake conda/ffmpeg; real filter list)."""
    _, top = built
    r = Rig(tmp_path, bundle=top)
    p = r.run("--yes")
    assert p.returncode == 0, out(p)
    assert "Using the pinned, tested conda-forge ffmpeg 7.1.1" in p.stdout
    assert link_target(r.prefix / "ffmpeg-bin/ffmpeg") == str(r.env_dir / "bin/ffmpeg")
    assert not list(r.workspace.rglob("CLAUDE.md")) and (r.workspace / "examples/spot_xr/job.yaml").exists()
    assert (r.workspace / f"templates/starter/media/{DEMO_CLEAN}").exists()     # bundled generator (faked)


# --- macOS (D32): Apple silicon accepted, faked on any OS -----------------------------------------
def test_macos_arm64_install_log_and_hints(rig):
    """Darwin/arm64 (not translated) installs like Linux; the log goes to ~/Library/Logs/cmr, the
    PATH hint names ~/.zprofile, a bootstrap would fetch the macOS arm64 Miniforge."""
    p = rig.run("--dry-run", "--bootstrap-conda", conda=False, FAKE_UNAME_S="Darwin", FAKE_UNAME_M="arm64")
    assert p.returncode == 0, out(p)
    assert re.search(r"Miniforge3-[0-9.]+-\d+-MacOSX-arm64\.sh", p.stdout)
    p = rig.run("--yes", FAKE_UNAME_S="Darwin", FAKE_UNAME_M="arm64")
    assert p.returncode == 0, out(p)
    log = rig.home / "Library/Logs/cmr/install.log"
    assert log.exists() and not rig.log.exists()
    assert LOG_LINE.match(log.read_text().splitlines()[0])
    assert "os=Darwin arch=arm64" in log.read_text() and "macOS 26.7.1" in log.read_text()
    assert "~/.zprofile" in p.stdout
    assert rig.launcher.exists() and (rig.env_dir / "bin/python").exists()


def test_macos_launcher_sets_tmpdir_when_unset(rig):
    """Found on the Mac run (2026-10-26): with no TMPDIR (cron, launchd, env -i) Python's temp dir
    is /tmp, and leptonica on macOS cannot open /tmp paths (it rewrites /tmp to the per-user temp
    dir): every slate OCR check crashed. The macOS launcher sets TMPDIR from getconf when unset."""
    p = rig.run("--yes", FAKE_UNAME_S="Darwin", FAKE_UNAME_M="arm64")
    assert p.returncode == 0, out(p)
    text = rig.launcher.read_text()
    assert "getconf DARWIN_USER_TEMP_DIR" in text
    assert "cmr doctor " in rig.calls_text() and "TMPDIR=/fake/var/folders/T/" in rig.calls_text()
    rig.reset_calls()
    env = {"HOME": str(rig.home), "PATH": rig.path(), "FAKES": str(FAKES), "FAKE_CALLS": str(rig.calls)}
    subprocess.run(["/bin/sh", str(rig.launcher), "doctor"], env={**env, "TMPDIR": "/my/tmp"}, capture_output=True)
    assert "TMPDIR=/my/tmp" in rig.calls_text()                          # a set TMPDIR is kept
    rig.reset_calls()
    tools = rig.tmp / "nogetconf"                                           # no getconf: TMPDIR stays unset
    tools.mkdir()
    for t in rig.tools.iterdir():
        if t.name != "getconf":
            (tools / t.name).symlink_to(os.readlink(t))
    subprocess.run(["/bin/sh", str(rig.launcher), "doctor"], env={**env, "PATH": str(tools)}, capture_output=True)
    assert "TMPDIR=unset" in rig.calls_text()
