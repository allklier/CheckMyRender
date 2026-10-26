"""Diagnostic log (D29): format, rotation, levels, fallback, redaction, snapshot, concurrency,
support bundle, unhandled-exception hook. No media needed."""
import json
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from cmr import cli, log

LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} (DEBUG|INFO |WARN |ERROR) [\w.]+"
                  r"\[\d+/\d+\] run=[0-9a-f]{6} file=(-|[0-9a-f]{8}) \| ")
SRC = str(Path(__file__).resolve().parents[2] / "src")


@pytest.fixture
def logdir(tmp_path, monkeypatch):
    d = tmp_path / "logs"
    monkeypatch.setenv("CMR_LOG_DIR", str(d))
    for k in ("CMR_LOG_LEVEL", "CMR_LOG_PRIVATE"):
        monkeypatch.delenv(k, raising=False)
    yield d
    log.setup("off")


def lines(d: Path) -> list[str]:
    return (d / "cmr.log").read_text().splitlines()


def test_line_format_and_ids(logdir):
    assert log.setup() == logdir / "cmr.log"
    log.get("engine").info("hello %d", 5)
    log.set_file("/some/clip.mov")
    log.get("check.video.blanking").warning("edge")
    log.set_file(None)
    a, b = lines(logdir)
    assert LINE.match(a) and a.endswith("file=- | hello 5")
    assert " engine[" in a and f"[{os.getpid()}/" in a
    assert LINE.match(b) and " WARN  video.blanking[" in b and "file=-" not in b
    run = re.search(r"run=(\w+)", a).group(1)
    assert run == re.search(r"run=(\w+)", b).group(1)
    log.setup()
    log.get("cli").info("x")
    assert re.search(r"run=(\w+)", lines(logdir)[-1]).group(1) != run   # new id per invocation


def test_default_level_constant_and_env(logdir, monkeypatch):
    assert log.LOG_DEFAULT_ENABLED is True and log.DEFAULT_LEVEL == "info"
    log.setup()
    log.get("x").debug("dbg")
    log.get("x").info("inf")
    assert [l.split("| ")[1] for l in lines(logdir)] == ["inf"]
    monkeypatch.setenv("CMR_LOG_LEVEL", "warn")
    log.setup()
    log.get("x").info("inf2")
    log.get("x").warning("w")
    log.setup("debug")              # the flag wins over the env
    log.get("x").debug("dbg2")
    assert [l.split("| ")[1] for l in lines(logdir)][1:] == ["w", "dbg2"]


def test_off_writes_nothing(logdir, monkeypatch):
    monkeypatch.setenv("CMR_LOG_LEVEL", "off")
    assert log.setup() is None
    log.get("x").error("nope")
    assert not (logdir / "cmr.log").exists()
    monkeypatch.setattr(log, "LOG_DEFAULT_ENABLED", False)   # the later default: OFF
    monkeypatch.delenv("CMR_LOG_LEVEL")
    assert log.setup() is None


def test_rotation_at_start_only(logdir, monkeypatch):
    monkeypatch.setattr(log, "MAX_BYTES", 100)
    logdir.mkdir(parents=True)
    (logdir / "cmr.log").write_text("x" * 99)
    log.setup()
    assert not (logdir / "cmr.log.1").exists()          # below the limit: kept
    (logdir / "cmr.log").write_text("old0\n" * 50)
    for i in range(1, log.BACKUPS + 1):
        (logdir / f"cmr.log.{i}").write_text(f"old{i}\n")
    log.setup()
    log.get("x").info("new")
    assert (logdir / "cmr.log.1").read_text().startswith("old0")
    assert (logdir / f"cmr.log.{log.BACKUPS}").read_text() == f"old{log.BACKUPS - 1}\n"
    assert not (logdir / f"cmr.log.{log.BACKUPS + 1}").exists()
    for i in range(500):                                 # no rotation mid-run
        log.get("x").info("y" * 50)
    assert (logdir / "cmr.log").stat().st_size > 100 and (logdir / "cmr.log.1").read_text().startswith("old0")


def test_unwritable_dir_falls_back_to_stderr(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("CMR_LOG_DIR", str(blocker / "logs"))   # parent is a file -> OSError
    try:
        assert log.setup() is None
        log.get("x").info("not shown")
        log.get("x").warning("shown")
        err = capsys.readouterr().err
        assert err.count("cannot write the log") == 1 and "shown" in err and "not shown" not in err
        assert log.redact("path", "/a/b.mov").startswith("<path#")   # ephemeral salt works
    finally:
        log.setup("off")


def test_logging_never_raises(logdir):
    log.setup()
    h = [h for h in log.ROOT.handlers if isinstance(h, log._AppendHandler)][0]
    os.close(h.fd)                       # writes now fail with EBADF
    log.get("x").info("lost %s", "quietly")
    log.get("x").info("bad format %d", "x")   # formatting error swallowed too


def test_redact_stable_and_private(logdir, tmp_path, monkeypatch):
    log.setup()
    a = log.redact("path", "/jobs/ACME_Corp/ACME_spot_v2.mov")
    assert re.fullmatch(r"<path#[0-9a-f]{8}\.mov>", a)
    assert log.redact("path", "/jobs/ACME_Corp/ACME_spot_v2.mov") == a
    t = log.redact("text", "Sample Burgers")
    assert re.fullmatch(r"<text#[0-9a-f]{8} len=14>", t)
    log.setup()                          # new run, same salt file -> same token
    assert log.redact("path", "/jobs/ACME_Corp/ACME_spot_v2.mov") == a
    assert (logdir / log.SALT_NAME).exists()
    # scrub: anything registered disappears from later lines (stderr, tracebacks)
    log.get("x").error("ffmpeg: ACME_spot_v2.mov: Invalid data; /jobs/ACME_Corp/ACME_spot_v2.mov")
    assert "ACME" not in lines(logdir)[-1]
    log.redact("text", "2026")           # numbers and "value" tokens are not scrubbed later:
    log.redact("value", "warn")          # they are ordinary words (and the line timestamp)
    log.get("x").info("fail-on warn in 2026")
    assert lines(logdir)[-1].endswith("| fail-on warn in 2026")
    cmd = log.redact_cmd(["/usr/bin/ffmpeg", "-i", "rel_clip_name.mov", "-filter_complex",
                          "[0:0]signalstats,metadata=mode=print:file=/tmp/x/tap0.txt[o0]",
                          "-ss", "1.5", "/tmp/out dir/f.png"])
    assert "rel_clip_name" not in cmd and "/tmp/x" not in cmd and "out dir" not in cmd
    assert cmd.startswith("/usr/bin/ffmpeg -i") and "signalstats" in cmd and "-ss 1.5" in cmd
    assert "Sample" not in log.redact_quoted("slate CLIENT 'Sample Burgers', expected 'X Y'")
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("CMR_LOG_DIR", str(other))
    log.setup()                          # other installation -> other salt -> other token
    assert log.redact("path", "/jobs/ACME_Corp/ACME_spot_v2.mov") != a
    log.setup(private_mode=True)
    assert log.redact("path", "/jobs/ACME_Corp/ACME_spot_v2.mov") == "/jobs/ACME_Corp/ACME_spot_v2.mov"
    assert log.redact_quoted("'Sample Burgers'") == "'Sample Burgers'"


def test_snapshot_with_missing_tools(logdir, monkeypatch):
    monkeypatch.setenv("CMR_TESSERACT", "/nonexistent/tesseract")
    real_which = log.shutil.which
    monkeypatch.setattr(log.shutil, "which", lambda b: None if b in ("nvidia-smi", "ionice")
                        else real_which(b))
    log._static.clear()
    try:
        log.setup()
        log.snapshot(log.get("cli"), "no_such_ffmpeg_x", "no_such_ffprobe_x")
    finally:
        log._static.clear()
    text = "\n".join(lines(logdir))
    for key in ("cmr", "privacy", "host", "user", "os", "python", "ffmpeg", "ffprobe", "filters",
                "tesseract", "cpu", "ram", "gpu", "limits"):
        assert f"| env {key}: " in text, key
    assert "env ffmpeg: NOT FOUND" in text and "env tesseract: NOT FOUND" in text
    assert "env gpu: none found" in text and "env filters: unavailable" in text
    assert "env privacy: default" in text and "ionice n/a" in text


def test_two_processes_append(logdir):
    code = ("import sys; sys.path.insert(0, %r); from cmr import log; log.setup(); "
            "L = log.get('worker')\nfor i in range(2000): L.info('line %%d %%s', i, 'x' * 80)") % SRC
    ps = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(2)]
    assert [p.wait(timeout=60) for p in ps] == [0, 0]
    ls = lines(logdir)
    assert len(ls) == 4000 and all(LINE.match(l) and l.endswith("x" * 80) for l in ls)
    pids = {re.search(r"worker\[(\d+)/", l).group(1) for l in ls}
    assert len(pids) == 2


def test_support_bundle(logdir, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["doctor"]) in (0, 3)      # writes a log with a snapshot
    rep = tmp_path / "reports" / "run1"
    rep.mkdir(parents=True)
    (rep / "cmr_report.json").write_text(json.dumps({"schema_version": "1.2"}))
    assert cli.main(["logs", "collect", "-o", str(tmp_path / "b.tar.gz"),
                     "--reports", str(tmp_path / "reports")]) == 0
    with tarfile.open(tmp_path / "b.tar.gz") as tar:
        names = tar.getnames()
        env = tar.extractfile("environment.txt").read().decode()
    assert "logs/cmr.log" in names and "environment.txt" in names
    assert "reports/01_cmr_report.json" in names
    assert not any("salt" in n or "lock" in n for n in names)
    assert "privacy: default" in env and "ffmpeg:" in env
    import getpass
    if len(getpass.getuser()) >= 4:
        assert getpass.getuser() not in env
    assert cli.main(["logs", "collect"]) == 0               # default name in the cwd
    assert len(list(tmp_path.glob("cmr-support-*.tar.gz"))) == 1
    capsys.readouterr()
    assert cli.main(["logs", "path"]) == 0
    assert capsys.readouterr().out.strip() == str(logdir / "cmr.log")
    assert cli.main(["logs", "tail", "-n", "3"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 3 and all(LINE.match(l) for l in out)


def test_unhandled_exception_is_logged_exit_3(logdir, monkeypatch, capsys):
    def boom(a):
        raise RuntimeError("kaboom inside probe")
    monkeypatch.setattr(cli, "cmd_probe", boom)
    assert cli.main(["probe", "x.mov"]) == 3
    text = "\n".join(lines(logdir))
    assert "ERROR cli[" in text and "unhandled exception" in text and "Traceback" in text
    assert "kaboom inside probe" in text and "end: exit 3" in text
    assert "internal error" in capsys.readouterr().err
