import os
import subprocess
import sys

from cmr import runmode


def _probe(mode):
    code = ("import os, resource; from cmr import runmode; "
            f"t = runmode.apply({mode!r}); "
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
    assert 0 < as_limit <= runmode.BACKGROUND_MEM_BYTES
