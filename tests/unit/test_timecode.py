from fractions import Fraction

import pytest

from cmr.timecode import (TimecodeError, frames_to_tc, parse_fps, tc_is_drop, tc_to_frames)

F2398, F24, F25 = Fraction(24000, 1001), Fraction(24), Fraction(25)
F2997, F5994 = Fraction(30000, 1001), Fraction(60000, 1001)


def test_parse_fps():
    assert parse_fps("24000/1001") == F2398
    assert parse_fps(25) == F25
    assert parse_fps("25") == F25
    for bad in (23.976, "abc", "0", -1, True):
        with pytest.raises(TimecodeError):
            parse_fps(bad)


@pytest.mark.parametrize("fps", [F2398, F24, F25])
def test_ndf_known(fps):
    n = round(fps)
    assert tc_to_frames("01:00:00:00", fps, False) == 3600 * n
    assert frames_to_tc(3600 * n, fps, False) == "01:00:00:00"
    assert frames_to_tc(3600 * n - 1, fps, False) == f"00:59:59:{n - 1:02d}"


def test_ndf_2398_counts_24_per_second():
    assert tc_to_frames("00:59:59:00", F2398, False) == 3600 * 24 - 24


def test_df_2997_known_values():
    assert frames_to_tc(1799, F2997, True) == "00:00:59;29"
    assert frames_to_tc(1800, F2997, True) == "00:01:00;02"
    assert frames_to_tc(17982, F2997, True) == "00:10:00;00"
    assert frames_to_tc(107892, F2997, True) == "01:00:00;00"
    assert tc_to_frames("01:00:00;00", F2997, True) == 107892
    assert tc_to_frames("00:01:00;02", F2997, True) == 1800


def test_df_5994_drops_four():
    assert frames_to_tc(3600, F5994, True) == "00:01:00;04"
    assert tc_to_frames("00:01:00;04", F5994, True) == 3600


@pytest.mark.parametrize("fps,drop", [(F2997, True), (F5994, True), (F2398, False), (F2997, False)])
def test_roundtrip(fps, drop):
    last = f"23:59:59{';' if drop else ':'}{round(fps) - 1:02d}"
    per_day = tc_to_frames(last, fps, drop) + 1
    for f in list(range(0, 40000)) + [10**6, per_day - 1, per_day]:
        assert tc_to_frames(frames_to_tc(f, fps, drop), fps, drop) == f % per_day


def test_df_nonexistent_and_invalid():
    with pytest.raises(TimecodeError):
        tc_to_frames("00:01:00;00", F2997, True)     # dropped number
    tc_to_frames("00:10:00;00", F2997, True)         # every 10th minute keeps it
    with pytest.raises(TimecodeError):
        frames_to_tc(0, F2398, True)                 # DF undefined for 23.976
    with pytest.raises(TimecodeError):
        tc_to_frames("00:00:00:24", F2398, False)    # frame field out of range
    with pytest.raises(TimecodeError):
        tc_to_frames("1:00:00:00", F25, False)


def test_wrap_and_negative():
    assert frames_to_tc(-1, F25, False) == "23:59:59:24"
    assert frames_to_tc(24 * 3600 * 25, F25, False) == "00:00:00:00"


def test_drop_detection():
    assert tc_is_drop("01:00:00;00")
    assert not tc_is_drop("01:00:00:00")
