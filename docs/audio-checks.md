# Audio content checks

`src/cmr/checks/audio_content.py`; tests `tests/unit/test_audio_content.py` (no media),
`tests/test_audio_media.py` (generated media with planted defects).
- All four measure the PROGRAM RANGE only: `atrim=start_sample:end_sample` with
  start = program_start*sr/fps, end = (program_end+1)*sr/fps, rounded half-up. No program
  range (layout does not fit) -> SKIPPED.
- audio.loudness: tap `atrim,ebur128=metadata=1:peak=true` on the spec's
  `audio.loudness.program_track`. I/LRA from the last 100 ms record, true peak = running max
  (linear, 3 decimals -> 20*log10). Spec names `standard`; profile `standards` presets hold
  target_lufs / tolerance_lu / true_peak_max_dbtp (final: EBU R128 -23 +-1 / -1 dBTP,
  ATSC A/85 -24 +-2 / -2 dBTP); optional `lra_max_lu`. Every message says "BS.1770
  relative-gated ... not dialogue-gated". A PASS finding carries the measured values.
  Integrated-loudness finding = program range; true-peak finding = the first 100 ms block over
  the ceiling (frame range of that block). Unknown standard -> ERROR.
- audio.silence / silent_channel / clipping: tap per audio stream
  `atrim,asetnsamples=n=floor(sr/fps):p=0,astats=metadata=1:reset=1:measure_perchannel=Peak_level:measure_overall=none`
  -> per-frame per-channel sample peak. Frame of chunk k = floor((start + k*n)*fps/sr) from
  the chunk INDEX (not pts_time, see engine issue below). Frames without audio count as silent.
  silence: frame silent when all channels < `threshold_dbfs`; run > `max_run_frames` is a
  problem (final: -60 dBFS, 12 frames). silent_channel: channel peak < `threshold_dbfs` over the
  whole program (final: -60). clipping: runs of frames with any channel >= `clip_dbfs`
  (final: -0.1, severity fail; severity is the profile `severity`).
- Changes outside the module (noted per ownership rule): 4 entries in profiles/final.yaml;
  tests/media/make_media.py base tone level `volume=-17dB` -> `-1.94dB` so the `good` case is
  -23 LUFS (old docstring "tone at -20 dBFS" was wrong: sine source is 1/8 = -18.06 dBFS, `-ac 2`
  mono->stereo adds -3.01 dB, so the old tone was -38.07 LUFS and would fail loudness).
- Verified (ffmpeg 6.1.1, `ffmpeg -h filter=X` for ebur128, astats, asetnsamples, atrim,
  silencedetect, sine, pan + runs): ebur128 metadata keys lavfi.r128.{M,S,I,LRA,true_peak,
  true_peaks_chN,...}; I per record = summary I; true_peak is a running max in LINEAR units;
  ebur128 re-chunks to 100 ms (pts step 4800 at 48 kHz); astats reset=1 = per-frame values,
  -inf on digital silence; atrim keeps original pts; silencedetect `mono=1` gives
  lavfi.silence_start.<ch> (verified, not used: seconds at run edges only).
- Cross-check: generated tones at -10 / -30 / -24.3 LUFS (analytic: stereo 1 kHz sine at P dBFS
  peak = P LUFS) measure -10.0 / -30.0 / -24.3; independent ffmpeg ebur128 summary on a raw lavfi
  sine (no atrim/engine) gives the same to 0.1 LU. Hot head+tail (+2 dBFS): full-file I = +0.4
  LUFS, program-range I = -23.0 (exclusion works). Planted gap frames 40-47 reported 40-47
  (01:00:00:16-01:00:00:23); silencedetect independently: 1.66833-2.00202 s = frames 40..47.
- Engine-interface issues found:
  1. FIXED in engine (exact pts x time base, regression tests in tests/unit/test_engine.py).
     ametadata/metadata print pts_time with 6 significant digits ("3600.04" for 3600.0417 s;
     verified). `MetaRecord.frame` (floor/round of pts_time*fps) can be off by one frame beyond
     ~1000 s. The integer `pts:` field is exact but not kept in MetaRecord. Audio checks here
     avoid it via chunk index; other checks (black_runs, blanking) rely on MetaRecord.frame:
     engine owner should parse `pts:` + stream time base.
  2. No tap sharing: silence, silent_channel and clipping each run an identical astats tap
     (3x asplit per stream). Cheap, but a shared-tap mechanism would remove it.
  3. FIXED: an unknown loudness standard raises `engine.ConfigError` -> ERROR
     "configuration error: standard ... has no preset" (test
     `test_loudness_unknown_standard_is_configuration_error`).
- Not verified / limits: non-48k-multiple rates (sr/fps not an integer, e.g. 29.97) attribute
  chunks with < 1 frame spill (unit-tested arithmetic only, no 29.97 media); 5.1/7.1 channel
  weighting in ebur128 (no multichannel media); mono program tracks are measured as single
  channel (no `dualmono`); integrated value compared unrounded (3 decimals) to target +- tolerance;
  true-peak location precision = 100 ms block; R128 file-based tolerance is often +-0.5 LU (set
  +-1 per request, profile-tunable); A/85 -2 dBTP ceiling is an assumption (request gave only
  -24 +-2).
