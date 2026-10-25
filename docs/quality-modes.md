# Quality modes (`quality: fast | high`)

## Design
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

## video.blanking
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

## video.black_runs
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

## Measured timings
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

## Verified ffmpeg facts (ffmpeg 6.1.1)
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

## Limitations / not verified
- Equivalence of the high chain was shown on synthetic media (testsrc2, flat levels, noise,
  lines), not on real renders. It relies on swscale producing the same luma for 10->8-bit
  conversion to yuv420p and to yuv444p.
- Fast black_runs on real film grain or noisy black: not tested. The direction is known
  (more candidates), the size is not.
- Blanking fast phase is tied to file frame 0, not to program start.
