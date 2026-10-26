import os
import resource
import subprocess
import sys

from cmr import runmode

DARWIN = sys.platform == "darwin"


def _probe(mode, fake_platform=None, log_file=None):
    """Apply the mode in a child process (nice cannot be undone); optionally pretend the
    platform (runmode reads sys.platform at call time) and log to log_file."""
    code = ("import os, sys, resource\n"
            + (f"sys.platform = {fake_platform!r}\n" if fake_platform else "")
            + "from cmr import log, runmode\n"
            + (f"log.setup('info', {str(log_file)!r})\n" if log_file else "")
            + f"t = runmode.apply({mode!r})\n"
            "print(t, os.nice(0), resource.getrlimit(resource.RLIMIT_AS)[0])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split()
    return out[0], int(out[1]), int(out[2])


def test_foreground_changes_nothing():
    threads, nice, as_limit = _probe("foreground")
    assert threads == "None" and nice == os.nice(0)   # inherited, not changed (the suite may run niced)


def test_background_lowers_priority_threads_and_memory():
    threads, nice, as_limit = _probe("background")
    assert threads == str(runmode.BACKGROUND_FILTER_THREADS)
    assert nice == runmode.BACKGROUND_NICE
    if DARWIN:   # D32: no memory cap on macOS
        assert as_limit == resource.getrlimit(resource.RLIMIT_AS)[0]
    else:
        assert 0 < as_limit <= runmode.BACKGROUND_MEM_BYTES


def test_background_darwin_branch_nice_only_and_says_so(tmp_path):
    """D32 on any OS: the darwin branch sets nice + threads, leaves RLIMIT_AS alone, runs no
    ionice, and the log states that no memory cap applies."""
    logf = tmp_path / "cmr.log"
    threads, nice, as_limit = _probe("background", "darwin", logf)
    assert threads == str(runmode.BACKGROUND_FILTER_THREADS) and nice == runmode.BACKGROUND_NICE
    assert as_limit == resource.getrlimit(resource.RLIMIT_AS)[0]
    text = logf.read_text()
    assert "no memory cap on macOS" in text and "ionice" not in text
