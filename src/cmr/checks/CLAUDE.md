# src/cmr/checks - check plugins

Every module here is auto-imported by `engine.load_checks()`. Contract: `../CLAUDE.md`.
Blanking has its own folder: `blanking/CLAUDE.md`. Decisions: ../../../CLAUDE.md.

## Metadata checks
Check ids (profiles/final.yaml): container.format, video.codec (codec + profile via value map),
video.resolution (w/h, pixel aspect, display aspect), video.pixel_format, video.fps (r_frame_rate
and avg_frame_rate, exact rational), video.scan, video.color (unknown -> unknown_severity),
audio.format (codec/sample rate/bit depth per stream), audio.layout (stream count + channels per
spec track), filename (explicit filename beats naming_pattern), timing.frame_count (program
frames = file - head - tail), timing.layout (layout fits; first program frame TC ==
program_start_timecode), timing.timecode_start (embedded TC + DF/NDF vs spec, derived if no
file_start_timecode), captions.presence (embedded_608/708 only), decode.errors.
Profiles: `final`, `review` and `release` (both extend final).

## video.black_runs
`src/cmr/checks/black_runs.py`, tests `tests/test_black_runs.py`, `tests/test_quality_modes.py`.
Tap `format=yuv420p,signalstats` (quality high; YHIGH identical to the former yuv444p chain) or
`scale=iw/4:ih/4:flags=area+accurate_rnd,format=yuv420p,signalstats` (quality fast; every frame,
see src/cmr/checks/CLAUDE.md) (8-bit, tv range kept); frame is black when YHIGH <= `black_luma` (default 24; tv black = 16).
Runs found in Python on integer frames, clipped to program_start..program_end (head/tail layout
black ignored; a run touching program start is counted from program start). Candidate only if
run length > profile `max_black_run_frames` (required; final.yaml sets 12). Severity = profile
`severity` (default fail). Finding gives frame range + timecode + actual run length.
Registration/profile change outside the check: one entry `video.black_runs` in profiles/final.yaml.
Edge black borders stay with video.blanking. Not verified: behaviour on real-world noisy/film-grain
black (YHIGH threshold may need tuning), logo-over-black (90th percentile ignores a small logo);
a frame missing from the signalstats output breaks a run.

## Audio content checks
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
  Each loudness problem finding's evidence[0] carries `kind` (`integrated`, `true_peak`, `lra`)
  so a waiver of one does not cover the others (D17, src/cmr/CLAUDE.md "Waivers").
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

## Quality modes (`quality: fast | high`)
### Design
- `quality` is a reserved per-check profile key (like `enabled`/`severity`), default `high`.
  `PROFILE_SCHEMA` only allows `fast` or `high`, so a typo is a config error when the profile
  loads (exit 3).
- `Check.qualities` (default `("high",)`) lists the modes a check implements. If a profile sets
  a mode the check does not implement (e.g. `decode.errors: {quality: fast}`), the engine
  reports an ERROR finding: `bad profile params: quality 'fast' not implemented (supports:
  high)`. That way no report can claim a fast pass for a check that has no fast mode.
- The engine takes `quality` out of the params before the check's `params_schema` validation
  (so no check schema has to list it) and passes it on as `ctx.quality`. taps(), detect()
  and evaluate() read it from `ctx`.
- Every finding a check returns, PASS included, gets `Finding.quality` (`fast`/`high`) in the
  JSON report. Engine-level errors such as an unknown check id leave it `null`. The console
  tags fast findings as `check_id [fast mode]:`. This is an additive optional field, so the
  report schema version stays `1.0`.
- `cmr profiles show` prints the effective `quality` of every check that has more than one
  mode (`high` when the profile does not set it).
- Checks with a fast mode: `video.blanking` and `video.black_runs`. Profiles: `review` sets
  `fast` for both, `release` sets `high` explicitly, `final` defaults to `high`.
  `internal_review.yaml` was removed; `review` replaces it.

### video.blanking
- high: unchanged (every frame).
- fast: each edge tap starts with `select='not(mod(n,S))'` (S = `fast_frame_step`, default 5,
  integer >= 1). Only file frames 0, S, 2S, ... are measured. `n` counts the frames entering
  the tap, i.e. the decoded frames from 0. The engine's split comes before the select, so the
  other checks still see every frame.
- What fast can miss: a defect lasting >= S consecutive frames always contains a multiple of
  S, so it is always seen at least once. A shorter defect is seen only if it covers one; a
  4-frame defect at S = 5 is missed when its start % 5 == 1 (1 phase out of 5). Tested in
  `tests/test_quality_modes.py`: five 5-frame defects at phases 0..4 are all caught; of five
  4-frame defects at phases 0..4, the phase-1 one (frames 81-84) is missed; high finds all
  of them frame-exact.
- Sampled runs: consecutive samples with the same label join into one run. The finding's
  frame range is the first..last SAMPLED frame. The evidence has `frame_step`,
  `frames_sampled`, `possible_frame_start/end` (sampled range widened by S-1 on each side,
  clipped at 0) and `boundary_uncertainty_frames` = S-1. Fast evidence has no `frames` key,
  so there is no exact run length (no false precision). The message reads "seen on N sampled
  frame(s) (fast: 1 in S frames; true start/end up to S-1 frame(s) outside the range shown)".
  `min_run_frames` is compared with the largest possible extent, so a sampled hit is never
  dropped as too short. Two defects separated by fewer than S clean frames can merge into one
  sampled run.
- `persistent` uses sample counts (run samples >= persistent_fraction x labelled samples).

### video.black_runs
- high: `format=yuv420p,signalstats` (was `format=yuv444p,signalstats`). Per-frame YHIGH was
  identical on every frame tested (1,980 frames: stress clip 156, 1440-frame clip, br_long,
  br_dark, br_short, good). Only the chroma planes, which this check does not read, differ.
- fast: `scale=iw/4:ih/4:flags=area+accurate_rnd,format=yuv420p,signalstats`. That is 1/16
  of the pixels, but EVERY frame is still measured, so integer-frame run logic, head/tail
  exclusion and `max_black_run_frames` work exactly as in high. Frame sampling was not used:
  it saved only another 0.3 s per 1440 frames on top of the downscale and would make run
  lengths uncertain.
- Without `accurate_rnd`, area scaling from 10-bit added +1 to flat levels (black measured 17,
  Y=24 measured 25, so a frame at the threshold flipped to "not black"). With it, flat levels
  match high exactly.
- Stress clip (1080p ProRes HQ, 12-frame segments; YHIGH in high / fast; * = black decision
  at <= 24 differs):

  | segment | high | fast | note |
  |---|---|---|---|
  | black | 16 | 16 | |
  | black + noise 10 | 21 | 18 | |
  | black + noise 30 | 32 | 22* | grain averaged: fast calls it black (extra candidate) |
  | Y=20 + noise 10 | 25 | 22* | same |
  | flat Y 22/24/25/26 | 22/24/25/26 | same | threshold behaviour identical |
  | black + 1% white logo | 16 | 16 | |
  | 1px white lines every 8 rows (12.5%) | 235 | 71 | both not black |
  | 1px white lines every 16 rows (6.25%) | 16 | 71* | high: black (90th pct ignores <10%); fast: not black |
  | dark picture / picture | 40 / 210 | 40 / 210 | |

  So fast leans towards more black-frame candidates on grainy near-black. It can miss a
  "black" frame only when thin bright detail covers less than 10% of it, and such a frame
  is visibly not black anyway. Real film grain was not tested.
- `nearest neighbor` downscale was rejected: it keeps the noise statistics but aliases thin
  lines (lines every 8 rows measured 16 = "black").

### Measured timings
16-CPU box, ffmpeg 6.1.1, 1920x1080 ProRes 422 HQ 10-bit, 1440 frames (testsrc2 content),
`-filter_complex_threads 8`, best of 2-3 runs.

Single tap (benchmark script, YHIGH printed):

| chain | s |
|---|---|
| decode only | 0.69 |
| `format=yuv444p,signalstats` (old black_runs) | 19.90 |
| `format=yuv420p,signalstats` (new high) | 8.91 |
| `signalstats` native 10-bit | 12.48 |
| `format=gray,signalstats` | 15.70 |
| `extractplanes=y,signalstats` | 17.29 |
| `scale=480:270:flags=area,format=yuv420p,signalstats` | 1.23 |
| same + `accurate_rnd` (new fast) | 1.21 |
| `select` 1/5 + yuv444p signalstats | 4.29 |
| `select` 1/5 + quarter-res area | 0.92 |
| half-res area | 2.53-3.29 |

Engine single pass (`engine.run_pass`, all four blanking taps + frame counter):

| taps | s |
|---|---|
| counter only (decode) | 0.70 |
| blanking high | 1.24 |
| blanking fast (1/5) | 0.99 |
| blanking high + black_runs old yuv444p | 20.85 |
| blanking high + black_runs high yuv420p | 9.73 |
| blanking fast + black_runs fast | 1.40 |

Whole `cmr check` (all checks incl. audio), same file:

| run | s |
|---|---|
| before: `final`, old black chain | 21.68 |
| after: `final` / `release` (high) | 10.44 / 10.41 |
| after: `review` (fast blanking + black_runs) | 2.05 |

Blanking fast saves only ~0.25 s per 1440 frames (~20% of the blanking pass, ~15 s per hour
of 24p). Decode (0.7 s) dominates and cannot be skipped: ProRes is intra-only and `select`
runs after decode. The mode ships anyway, as requested. The large saving is black_runs:
high is 2.1x faster with equivalent results, and fast is 16x faster than the old chain.
Real footage decodes slower than testsrc2, so the relative saving on real renders will be
smaller (UNVERIFIED, no real renders here).

### Verified ffmpeg facts (ffmpeg 6.1.1)
- `ffmpeg -h filter=select`: options `expr`/`e`, `outputs`. `select='not(mod(n,5))'`
  inside `-filter_complex` passes 288 of 1440 frames, and the sampled pts give file frames
  0, 5, 10, ... (engine frames, tested).
- `ffmpeg -h filter=scale`: `w`, `h`, `flags`. The swscale flag names `area`, `neighbor`,
  `fast_bilinear` and `accurate_rnd` come from `ffmpeg -h full` (`-sws_flags`). Odd output
  sizes (1998x1081 / 4) run without error.
- `ffmpeg -h filter=signalstats`: options `stat`, `out`, `c`/`color` only. There is no way
  to restrict it to luma or to switch off the chroma statistics.
- `ffmpeg -h filter=format|extractplanes|noise|lutyuv|geq|concat` were used to build the
  benchmark and stress media.

### Limitations / not verified
- Equivalence of the high chain was shown on synthetic media (testsrc2, flat levels, noise,
  lines), not on real renders. It relies on swscale producing the same luma for 10->8-bit
  conversion to yuv420p and to yuv444p.
- Fast black_runs on real film grain or noisy black: not tested. The direction is known
  (more candidates), the size is not.
- Blanking fast phase is tied to file frame 0, not to program start.

## Behaviour notes from the first real file (H.264/AAC mp4 from Adobe Media Encoder)
- Untagged sample aspect ratio is treated as square pixels, and an untagged display aspect is width/height x pixel aspect
  (`effective_aspects` in metadata.py); previously these failed as "got None".
- Bit depth is only compared for PCM audio (`bit_depth_comparable`); AAC/AC-3 report none.
- `timing.timecode_start` FAILs when a file has no embedded timecode (mp4 cannot carry a tmcd track). For such files turn
  the check off in the profile (`timing.timecode_start: {enabled: false}`) instead of changing the check.

## Known problems and limits
- audio `merge` (Flame mono-per-channel streams) mentioned in the example spec comment is not
  implemented; layouts other than mono/stereo/5.1/7.1 -> SKIPPED.
- Only the first video stream is checked.
- Decode errors are not located to a frame (ffmpeg gives no frame number).
- UNVERIFIED: whether the ProRes decoder derives frame color_range from the bitstream or
  defaults it; H.264 A53 `closed_captions` detection (no A53 test media).
