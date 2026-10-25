"""SMPTE timecode <-> integer frame conversion (DF and NDF), rational fps.

Frame rates are fractions.Fraction (e.g. Fraction(24000, 1001)). Timecode counts
at the *nominal* rate (round(fps)): 23.976 NDF counts 24 frames per TC second.
Drop-frame is only defined for nominal 30 and 60 (29.97 / 59.94): 2 (resp. 4)
frame numbers are skipped at the start of every minute except minutes 0,10,20..
DF timecode uses ';' before the frames field; NDF uses ':'.
Timecode wraps at 24 hours.
"""
from __future__ import annotations

import re
from fractions import Fraction

_TC_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})([:;.,])(\d{2,3})$")


class TimecodeError(ValueError):
    pass


def parse_fps(value) -> Fraction:
    """'24000/1001', '25', 25, 29.97 is rejected (floats are ambiguous)."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool) or isinstance(value, float):
        raise TimecodeError(f"fps must be an integer or a rational string, got {value!r}")
    try:
        f = Fraction(str(value).strip())
    except (ValueError, ZeroDivisionError) as e:
        raise TimecodeError(f"bad fps {value!r}") from e
    if f <= 0:
        raise TimecodeError(f"bad fps {value!r}")
    return f


def nominal(fps: Fraction) -> int:
    return round(fps)


def _drop_per_minute(fps: Fraction, drop: bool) -> int:
    if not drop:
        return 0
    n = nominal(fps)
    if n % 30 != 0 or fps.denominator != 1001:
        raise TimecodeError(f"drop-frame timecode is not defined for {fps} fps")
    return 2 * (n // 30)


def tc_to_frames(tc: str, fps: Fraction, drop: bool) -> int:
    m = _TC_RE.match(tc.strip())
    if not m:
        raise TimecodeError(f"bad timecode {tc!r}")
    hh, mm, ss, _sep, ff = (int(g) if g.isdigit() else g for g in m.groups())
    n = nominal(fps)
    d = _drop_per_minute(fps, drop)
    if mm > 59 or ss > 59 or ff >= n:
        raise TimecodeError(f"timecode {tc!r} out of range for {fps} fps")
    if d and ss == 0 and mm % 10 != 0 and ff < d:
        raise TimecodeError(f"timecode {tc!r} does not exist in drop-frame")
    total_min = hh * 60 + mm
    return ((hh * 3600 + mm * 60 + ss) * n + ff) - d * (total_min - total_min // 10)


def frames_to_tc(frames: int, fps: Fraction, drop: bool) -> str:
    n = nominal(fps)
    d = _drop_per_minute(fps, drop)
    per_day = 24 * 3600 * n - d * (24 * 60 - 24 * 6)
    f = frames % per_day
    if d:
        per_10min = 10 * 60 * n - 9 * d
        per_min = 60 * n - d
        tens, rem = divmod(f, per_10min)
        # first minute of each 10-minute block keeps all its frame numbers
        if rem >= d:
            f += 9 * d * tens + d * ((rem - d) // per_min)
        else:
            f += 9 * d * tens
    ff = f % n
    s = f // n
    sep = ";" if d else ":"
    return f"{s // 3600:02d}:{(s // 60) % 60:02d}:{s % 60:02d}{sep}{ff:02d}"


def tc_is_drop(tc: str) -> bool:
    """ffmpeg/QuickTime convention: ';' (or ',') before frames marks drop-frame."""
    m = _TC_RE.match(tc.strip())
    if not m:
        raise TimecodeError(f"bad timecode {tc!r}")
    return m.group(4) in ";,"
