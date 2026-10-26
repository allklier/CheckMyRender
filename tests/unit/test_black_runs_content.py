"""video.black_runs content-on-black classification (no media)."""
from cmr.checks.black_runs import bright_chain, classify, runs


def test_runs():
    assert runs([1, 2, 3, 7, 9, 10]) == [(1, 3), (7, 7), (9, 10)]


def test_true_black_unchanged():
    dark = list(range(30, 50))
    assert classify(dark, {}, 12, 0.0001) == ([(30, 49)], [])


def test_logo_excused():
    dark = list(range(30, 50))
    black, ex = classify(dark, {f: 0.0096 for f in dark}, 12, 0.0001)
    assert black == [] and ex == [((30, 49), dark)]


def test_split_run_keeps_black_piece():
    dark = list(range(30, 61))
    black, ex = classify(dark, {f: 0.01 for f in range(30, 45)}, 12, 0.0001)
    assert black == [(45, 60)] and ex == [((30, 60), list(range(30, 45)))]


def test_below_min_fraction_and_hot_pixel_stay_black():
    dark = list(range(30, 50))
    hot = {f: 1 / (1920 * 1080) for f in dark}           # one bright pixel per frame
    assert classify(dark, hot, 12, 0.0001) == ([(30, 49)], [])


def test_detector_off():
    dark = list(range(30, 50))
    assert classify(dark, {f: 0.5 for f in dark}, 12, None) == ([(30, 49)], [])


def test_short_raw_run_with_content_not_reported():
    dark = list(range(30, 35))
    assert classify(dark, {f: 0.5 for f in dark}, 12, 0.0001) == ([], [])


def test_bright_chain_threshold_in_input_range():
    c = bright_chain(100)
    assert c.startswith("lutyuv=y='if(gt(val,minval+(100-16)*(maxval-minval)/219),maxval,minval)'")
    assert c.endswith("scale=iw/4:ih/4:flags=area+accurate_rnd,format=yuv420p,signalstats")
