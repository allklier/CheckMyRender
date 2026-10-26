"""D32 macOS branches of log.py and the engine thread default, run on any OS with a faked
sys.platform and canned sysctl output (verified on the Mac separately, see src/cmr/CLAUDE.md)."""
import pathlib
import resource
import sys
from pathlib import Path

import pytest

from cmr import engine, log

SYSCTL = {"hw.perflevel0.physicalcpu": "16", "hw.perflevel1.physicalcpu": "4",
          "hw.memsize": str(128 * 1024 ** 3), "machdep.cpu.brand_string": "Apple M1 Ultra",
          "sysctl.proc_translated": "0"}


@pytest.fixture
def darwin(monkeypatch):
    real = log._cmd_out

    def fake(cmd, timeout=5):
        if Path(cmd[0]).name == "sysctl":
            return SYSCTL.get(cmd[-1])
        return real(cmd, timeout)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(log, "_cmd_out", fake)
    monkeypatch.setattr(log, "_static", {})
    monkeypatch.setattr(log.platform, "mac_ver", lambda: ("26.7.1", ("", "", ""), "arm64"))
    return SYSCTL


def test_log_dir_darwin_default_and_overrides(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("CMR_LOG_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert log.log_dir() == tmp_path / "Library" / "Logs" / "cmr"
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert log.log_dir() == tmp_path / "xdg" / "cmr" / "logs"
    monkeypatch.setenv("CMR_LOG_DIR", str(tmp_path / "d"))
    assert log.log_dir() == tmp_path / "d"


def test_log_dir_linux_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("CMR_LOG_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert log.log_dir() == tmp_path / ".cache" / "cmr" / "logs"


def test_snapshot_darwin_facts(darwin):
    lines = log.environment(filters=set())
    mac = next(l for l in lines if l.startswith("macos: "))
    assert "26.7.1" in mac and "Apple M1 Ultra" in mac and "16 performance + 4 efficiency" in mac
    assert "Rosetta no" in mac
    assert any(l.startswith("ram: 128.0 GB") for l in lines)
    assert any("no memory cap on macOS" in l for l in lines if l.startswith("limits: "))


def test_snapshot_darwin_rosetta_and_missing_sysctl(darwin, monkeypatch):
    monkeypatch.setitem(darwin, "sysctl.proc_translated", "1")
    monkeypatch.delitem(darwin, "hw.perflevel1.physicalcpu")
    monkeypatch.delitem(darwin, "machdep.cpu.brand_string")
    mac = next(l for l in log.environment(filters=set()) if l.startswith("macos: "))
    assert "Rosetta YES" in mac and "chip unknown" in mac and "16 performance + ? efficiency" in mac


def test_snapshot_linux_has_no_macos_line(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(log, "_static", {})
    assert not any(l.startswith("macos: ") for l in log.environment(filters=set()))


def test_memory_without_proc_uses_getrusage_bytes_on_darwin(monkeypatch):
    real_read = pathlib.Path.read_text

    def no_proc(self, *a, **kw):
        if str(self).startswith("/proc/"):
            raise FileNotFoundError(str(self))
        return real_read(self, *a, **kw)
    monkeypatch.setattr(pathlib.Path, "read_text", no_proc)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(resource, "getrusage",
                        lambda who: type("R", (), {"ru_maxrss": 300 * 1024 * 1024})())
    assert log.memory() == "peak 300 MB, children peak 300 MB"


def test_performance_cores_and_thread_default(darwin, monkeypatch):
    assert log.performance_cores() == 16
    assert engine.default_filter_threads() == 8
    monkeypatch.setitem(darwin, "hw.perflevel0.physicalcpu", "4")
    assert engine.default_filter_threads() == 4
    monkeypatch.delitem(darwin, "hw.perflevel0.physicalcpu")      # Intel / old macOS: CPU rule
    monkeypatch.setattr(engine.os, "cpu_count", lambda: 6)
    assert log.performance_cores() is None and engine.default_filter_threads() == 3


def test_thread_default_linux_unchanged(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(engine.os, "cpu_count", lambda: 48)
    assert engine.default_filter_threads() == 8
    monkeypatch.setattr(engine.os, "cpu_count", lambda: 4)
    assert engine.default_filter_threads() == 2
