# profiles - rules

A profile is the *rules*: which checks run, tolerances, severities, quality mode. Job facts come from a
spec sheet via `${spec.*}` (see ../examples/CLAUDE.md). Decisions: ../CLAUDE.md (D3, D4, D8, D14).

- `final.yaml` - base rules, all checks.
- `review.yaml` - extends final: fast blanking and black_runs (`quality: fast`), filename info-only,
  captions off, blanking and loudness are warnings, blanking `dark_edge_mode: info`, looser
  black-run and silence limits (24 frames).
- Spot profiles (D27; need `--spec` + `--job`, they read `${spec.slate}`, `${spec.job}`,
  `${spec.deliverables}`; slate rules: ../src/cmr/checks/slate/CLAUDE.md):
  - `spot_review.yaml` - extends final: before agency/client review. Adds slate.present /
    consistency / content / claims / bars; filename verifies the name generated from the CSV row
    (`deliverables` param + tag `resolution`); blanking and black_runs `quality: fast`, blanking
    dark_edge info; loudness and slate.bars WARN; slate.content WARN except
    `field_severity` AD-ID / TITLE / row FAIL; slate.claims (duration, audio, resolution) FAIL;
    DATE policy window 30 days WARN.
  - `spot_delivery.yaml` - extends spot_review: Extreme Reach delivery, strict: every check
    quality high and FAIL (blanking dark_edge flag, slate.content/bars FAIL), ATSC A/85 -24 +-2
    LKFS, true peak <= -2 dBTP, frame count tolerance 0 (with the spec's empty tail_layout: no
    tail black / extra frames), head layout per spec. Kept WARN on purpose: DATE (file dates are
    unreliable after copies) and uncertain OCR reads (check by eye). Waivers apply as usual.
  - `spot_render.yaml` - extends spot_review: right after a render. Technical metadata,
    timing/layout, filename, decode errors and slate.present (quality fast) only; content checks
    (blanking, black runs, audio content, captions, other slate checks) off; slate.present and
    decode.errors list at most 5 findings.
- `colour_interim.yaml` (extends review; picture only: filename, captions and every audio check off, so it loads with a spec that has no audio section; to gate an unexpected
  audio stream, extend it with `audio.layout: {enabled: true}` and give the spec `audio: {tracks: []}`),
  `log_interim.yaml`, `broadcast_master.yaml` (extends release; a silent
  channel is info only), `review_internal.yaml` (loudness off, colour mismatches INFO), `web_delivery.yaml` (adds a
  "Web -16" loudness preset: -16 LUFS +-1, -1 dBTP; colour WARN). `video.color` severities (user
  decision): a range mismatch (`key_severity: {range: ...}`) is INFO in review_internal and WARN in
  colour_interim, broadcast_master and web_delivery; primaries/transfer/matrix keep the profile's
  `severity` for the check (info in review_internal, warn in the other three; release,
  spot_delivery and final keep fail for every key). final.yaml shows `key_severity` commented out.
- final.yaml `video.field_order` (`video: "${spec.video}"`, `unknown_severity`) and `container.op`
  (`video: "${spec.video}"`): checked only when the spec sets `video.field_order` /
  `video.operational_pattern` (see ../src/cmr/checks/CLAUDE.md).
- `release.yaml` - extends final: `quality: high` explicit, R128 tolerance +-0.5 LU, black-run and
  silence limit 6 frames, blanking and captions gate (FAIL).
- Format: `checks:` is a mapping keyed by check id; reserved keys `enabled` (default true),
  `severity` (default fail), `quality` (fast|high, default high), `max_findings` (integer >= 0:
  problem findings listed per check before the rest is summarised as one "N additional ...
  suppressed; worst: X" entry; 0 = all; default 10 from `cmr check --max-findings`, and an
  explicit `--max-findings` overrides this key); everything else is check params. Suppressed
  findings still count in the summary and exit code (../src/cmr/CLAUDE.md "Findings cap").
  Merge on `extends`: mappings deep-merge, lists/scalars are replaced by the child.
  Params are strict (every check, incl. the audio content checks): a param name the check does
  not know is a config error (exit 3) naming the key, also in disabled checks (`cmr profiles show NAME` shows it). `${spec.*}` refs of checks
  with `enabled: false` are not looked up (lazy), so switching a check off is enough to drop its
  spec keys; no placeholder overrides needed. `cmr check --quality fast|high` overrides `quality`
  of every check that implements the mode (precedence: flag > profile > high).
- Installed copies (`pip install .`) carry these files as package data `cmr/profiles/`; edit
  them in the repo (an editable install uses the repo files directly).
- `video.blanking` `dark_edge_mode: flag | info | hide` (final: flag): how `dark_edge` hits
  (dark picture at a frame edge, no sharp step; see ../src/cmr/checks/blanking/CLAUDE.md) are
  reported: flag = at the check's severity, info = INFO, hide = not listed, only a count + frame
  ranges in one PASS/INFO finding. Sharp `edge` defects and `matte` are not affected.
- `video.blanking` stage 2, temporal context (D9 slot, deterministic; details and the
  measurements: ../src/cmr/checks/blanking/CLAUDE.md "Stage 2"): `context_analysis` (true;
  final.yaml states it), `context_frames` (12), `context_severity` ({animation: info,
  transition: info, steady: unchanged, unknown: unchanged}; values unchanged|pass|info|warn|fail,
  can only LOWER a finding, raw_severity kept), `max_context_candidates` (300 per file),
  `context_max_seconds` (600 per file), `context_max_span_frames` (300), thresholds
  `context_min_band_frames` 3, `context_min_motion_px` 3, `context_monotone` 0.8,
  `context_min_r2` 0.8, `context_transition_gap` 2, `cut_max_hit_frames` 2,
  `transition_max_jump` 8, `steady_max_range_px` 2, `cut_min_diff` 12, `cut_ratio` 4,
  `dissolve_min_diff` 2, `dissolve_max_linearity` 0.35, `dissolve_min_frames` 3,
  `context_depth_fraction` 0.125. No stock profile overrides them (review/release inherit).
- Loudness numbers per standard live here (`audio.loudness.standards`), not in the spec. What is
  measured (program_track or merged `programs`, LFE not measured) is spec data.
- `video.black_runs` content on black (defaults in the check, none set in profiles):
  `content_on_black` (true), `content_luma` (100, 8-bit tv code), `content_min_fraction`
  (0.0001): a logo/end card/credits on black is reported as one INFO (`content_on_black`), not
  a black run. Set `content_on_black: false` for the old behaviour.
- `audio.silence` / `audio.silent_channel`: final.yaml passes `audio: "${spec.audio}"` so a
  spec's `audio.allowed_silence` (declared intended silence) reaches them; a profile can also
  set `allowed_silence:` directly (same entry format; both lists apply). See
  ../src/cmr/checks/CLAUDE.md "Audio content checks".
- Audio programs: final.yaml also passes `audio: "${spec.audio}"` to `audio.loudness` and
  `audio.clipping`, so the spec's `audio.programs` reaches all four audio content checks
  (silence/silent_channel judged per program, clipping names program + role, loudness per
  program). New params: `audio.loudness.undeclared_programs_severity` (info|warn, final: warn;
  several audio streams and no programs or program_track declared = one notice that only one stream was
  measured), `audio.silent_channel.include_lfe` (final: false; true = a program's LFE channel
  is judged too).
- `audio.silence` `warn_run_frames` (optional, integer >= 0, must be < `max_run_frames` to take
  effect): two-tier silence. A run > warn and <= max is WARN, a run > max is the check's
  `severity` (FAIL). `broadcast_master.yaml`: `warn_run_frames: 6`, `max_run_frames: 30`
  (long-form masters); `release` keeps the single 6-frame limit. `audio.loudness`: an explicit
  spec `program_track` counts as declaring the program, so `undeclared_programs_severity` only
  applies when neither `audio.programs` nor `program_track` is given.
- `video.blanking` `exclude_segments` default is now [slate, bars_tone, two_pop].
- `waivers:` (top level, D17; tolerances for `cmr check --waivers`): `video_max_bits` (10),
  `audio_max_db` (2.0), `margin_frames` (5). Defaults live in `src/cmr/waivers.py` DEFAULTS and
  are repeated in final.yaml; see ../src/cmr/CLAUDE.md "Waivers".
