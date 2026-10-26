"""Run modes (D26, D32). foreground (default) = shortest time to QC, no limits. background = capped
so it does not disturb other apps on the machine: lower CPU priority, idle I/O priority (Linux),
fewer ffmpeg filter threads and a virtual-memory cap (Linux). Limits are inherited by the
ffmpeg/ffprobe children. Verified: `ionice --help` (-c 3 = idle, -p PID); resource.RLIMIT_AS
exists on this Python.
macOS (D32): background = nice 15 and 2 filter threads only. RLIMIT_AS is not enforced there and
ionice does not exist, so NO memory cap and no I/O priority are applied, and the log says so;
Darwin background QoS (taskpolicy / PRIO_DARWIN_BG) is deliberately not used."""
from __future__ import annotations

import os
import resource
import shutil
import sys

from . import log

LOG = log.get("runmode")
MODES = ("foreground", "background")
BACKGROUND_NICE = 15
BACKGROUND_FILTER_THREADS = 2
BACKGROUND_MEM_BYTES = 16 * 1024 ** 3


def apply(mode: str) -> int | None:
    """Apply the mode to this process (and so to its children). Returns the ffmpeg
    filter-thread count the mode wants, or None for the engine default."""
    if mode != "background":
        return None
    os.nice(BACKGROUND_NICE - os.nice(0))
    if sys.platform == "darwin":
        LOG.info("background mode applied: nice %d, %d filter threads; no memory cap on macOS "
                 "(RLIMIT_AS is not enforced there), I/O priority unchanged", os.nice(0),
                 BACKGROUND_FILTER_THREADS)
        return BACKGROUND_FILTER_THREADS
    ionice = shutil.which("ionice")
    if ionice:
        log.run(LOG, [ionice, "-c", "3", "-p", str(os.getpid())], "ionice", capture_output=True)
    else:
        LOG.info("ionice not available: I/O priority unchanged")
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    cap = BACKGROUND_MEM_BYTES if soft == resource.RLIM_INFINITY else min(soft, BACKGROUND_MEM_BYTES)
    try:
        resource.setrlimit(resource.RLIMIT_AS, (cap, hard))
    except (ValueError, OSError) as e:
        LOG.warning("RLIMIT_AS not set: %s", e)
    LOG.info("background mode applied: nice %d, RLIMIT_AS %.1f GB, %d filter threads", os.nice(0),
             cap / 1024 ** 3, BACKGROUND_FILTER_THREADS)
    return BACKGROUND_FILTER_THREADS
