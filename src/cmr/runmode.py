"""Run modes (D26). foreground (default) = shortest time to QC, no limits. background = capped
so it does not disturb other apps on the machine: lower CPU priority, idle I/O priority (Linux),
fewer ffmpeg filter threads and a virtual-memory cap. Limits are inherited by the ffmpeg/ffprobe
children. Verified: `ionice --help` (-c 3 = idle, -p PID); resource.RLIMIT_AS exists on this
Python. RLIMIT_AS is not enforced on macOS and ionice does not exist there; both are skipped."""
from __future__ import annotations

import os
import resource
import shutil
import subprocess

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
    ionice = shutil.which("ionice")
    if ionice:
        subprocess.run([ionice, "-c", "3", "-p", str(os.getpid())], capture_output=True)
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    cap = BACKGROUND_MEM_BYTES if soft == resource.RLIM_INFINITY else min(soft, BACKGROUND_MEM_BYTES)
    try:
        resource.setrlimit(resource.RLIMIT_AS, (cap, hard))
    except (ValueError, OSError):
        pass
    return BACKGROUND_FILTER_THREADS
