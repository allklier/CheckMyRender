# video.black_runs

`src/cmr/checks/black_runs.py`, tests `tests/test_black_runs.py`, `tests/test_quality_modes.py`.
Tap `format=yuv420p,signalstats` (quality high; YHIGH identical to the former yuv444p chain) or
`scale=iw/4:ih/4:flags=area+accurate_rnd,format=yuv420p,signalstats` (quality fast; every frame,
see docs/quality-modes.md) (8-bit, tv range kept); frame is black when YHIGH <= `black_luma` (default 24; tv black = 16).
Runs found in Python on integer frames, clipped to program_start..program_end (head/tail layout
black ignored; a run touching program start is counted from program start). Candidate only if
run length > profile `max_black_run_frames` (required; final.yaml sets 12). Severity = profile
`severity` (default fail). Finding gives frame range + timecode + actual run length.
Registration/profile change outside the check: one entry `video.black_runs` in profiles/final.yaml.
Edge black borders stay with video.blanking. Not verified: behaviour on real-world noisy/film-grain
black (YHIGH threshold may need tuning), logo-over-black (90th percentile ignores a small logo);
a frame missing from the signalstats output breaks a run.
