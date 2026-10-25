# check_my_render — render QC tool

Idiot-check for renders out of Flame and other apps. Goal: faster delivery without missing errors.
Status: round 1 in progress - core + metadata checks implemented (see docs/ for what is verified and the check-plugin interface). Items marked OPEN are undecided.

## Requirements
- CLI-first, scriptable, batch mode with a report. No GUI. Linux primary (Rocky Linux), macOS secondary.
- Checks are defined in a config file (YAML for human-authored config (profiles, specs); JSON for machine-readable reports), as named *profiles* (e.g. `netflix_hd`, `review`).
- Two check families: **consistency** (metadata/settings vs. spec) and **content** (analysis of pixels/audio).
- Initial checks:
  1. codec + resolution (+ pixel format, fps, colour tags)
  2. frame count / duration window
  3. audio channel layout
  4. audio loudness (LUFS, selectable standard, true peak)
  5. audio dead zones + extended black frames
  6. blanking test (black borders / bad blanking on some frames)
  7. filename convention (regex/template)
- Future: slate at frame 0 compared against a reference sheet (OCR + image compare).
- False-positive reduction for blanking and audio-level checks using ML (cheap heuristic first, ML only to confirm/dismiss candidates).

## Architecture (proposed)
Python 3, ffmpeg/ffprobe as media backend (mediainfo optional, not required).

    cli -> config loader -> job planner -> runner -> checks (plugins) -> results -> reporters

- **Probe once**: a single `MediaInfo` object per file (ffprobe JSON) shared by all checks.
- **Check plugin interface**: `Check` class with `id`, `needs` (probe / video frames / audio), `run(media, params) -> list[Finding]`. Registered via a registry/entry points; adding a check = one new file + a config entry.
- **Finding**: severity (PASS/INFO/WARN/FAIL/SKIPPED/ERROR), check id, message, optional time range / frame, evidence (e.g. thumbnail path).
- **Two-stage content checks**: stage 1 cheap detector (ffmpeg blackdetect/silencedetect/cropdetect/ebur128 — flags must be verified against installed ffmpeg before use) emits candidates; stage 2 optional "verifier" (ML) confirms or dismisses each candidate. Verifiers are pluggable and optional so the tool works without GPU/ML.
- **Reporters**: console, JSON (machine), HTML/CSV (humans). Exit code reflects worst severity for CI/pipeline use.
- **Batch**: directory/glob input, parallel per file, optional watch-folder later.

## Decisions
- Config: YAML. Python 3 (ffmpeg/ffprobe backend). Linux (Rocky) first with NVIDIA GPU assumed; macOS follows as fits, must not constrain design.
- Formats: mostly file codecs (ProRes, H.264); EXR/DPX sequences possible -> media abstraction must cover "file" and "sequence".
- Profiles (e.g. intermediate vs final) select/parameterize checks. Profiles may `extends` another profile.
- Scope additions (accepted): frame rate + scan type; colour primaries/transfer/range tags; visual colour-space sanity (log-looking / extreme colours, quantified); duplicate/dropped frames; "doesn't decode" (corruption v1); true peak + clipping; silent channel; sample rate/bit depth; A/V sync (ML lip-sync to be explored); timecode + container checks; watermarks; banding; caption presence; spec-sheet comparison.
- A/V sync v1: container offset/duration check + 2-pop detection only (post/client deliverables). Lip-sync ML = roadmap.
- Watermarks: only detect *accidental* ones (no reference image).
- Sequences (EXR/DPX): checked as a unit (count, gaps, naming), not per frame.
- Spec/profile split confirmed: spec = job data, profile = rules referencing `${spec.*}`. Example: examples/spec_hd_broadcast.yaml.
- mediainfo optional; ffprobe is the baseline. Add mediainfo only if ffprobe lacks something needed (e.g. timecode tracks).
- Loudness: broadcast LUFS is the norm. Mostly stereo; 8-channel layouts testable later.
- Head layout is checked: spec describes an ordered list of segments (`head_layout`: slate, black, bars_tone, two_pop, program...). Typical is a 1-frame slate; must stay flexible for other layouts. Slate *content* comparison remains future.
- Roadmap: stem checks, 8-channel layouts, lip-sync ML, caption quality, slate-vs-sheet.
- Deferred: PSE/flash, caption quality (roadmap), slate vs sheet.
- Spec sheets vs profiles: see "Spec sheets" below (confirmed).

## Review round 1 (Opus agent) — adopted
- **Decode once**: planner merges all video detectors into a single decode/filtergraph pass per file, driven by each check's `needs`; parallelism per file. GPU: ProRes has no NVDEC decoder in this ffmpeg (`ffmpeg -decoders`; NVDEC/NVENC help H.264/HEVC) so ProRes decode is CPU; GPU is for ML and optional custom analysis. Custom decode code only if profiling shows ffmpeg is the bottleneck (UNVERIFIED, measure first).
- **Time model**: fps as rational (24000/1001), all positions integer frames, explicit rounding from ffmpeg `pts_time`, DF/NDF-aware timecode lib. Duration = frame count, not wall clock.
- **head_layout**: strictly sequential video segments from file frame 0; audio events (2-pop) are separate anchored markers. Spec has `file_start_timecode` and `program_start_timecode`.
- **Spec vs profile**: tolerances, max run lengths, severities live in the profile only.
- **Substitution rules**: whole-value refs keep type; missing key = ERROR; no expression language; resolve after `extends`; detect cycles; define list merge.
- **Severity model**: PASS / INFO / WARN / FAIL / SKIPPED / ERROR (tool failure, distinct from FAIL). Profile sets per-check severity so a problem can be reported as info-only without failing QC. Exit codes: 0 pass, 1 warn, 2 fail, 3 tool error; `--fail-on`.
- **Waivers**: file keyed by check id + file identity + frame range, with reason/author/expiry; waived and ML-dismissed findings stay visible in reports.
- **Reproducible reports**: stamp tool/ffmpeg/model versions, hashes of profile/spec/waivers, file identity; stable ordering. Check available ffmpeg filters at startup (verified so far against ffmpeg 6.1.1; other builds, e.g. on Rocky, may differ).
- **Loudness**: measure program range only. **Dialogue-gated loudness is mandatory**; ffmpeg ebur128 has BS.1770 gating only (`ffmpeg -h filter=ebur128`). Needs a dialogue-aware meter: OPEN (licensed Dolby measurement vs. approximate speech-detection gating). Approximate results must not be reported as certified.
- **Blanking**: must detect 1px edge errors on a few frames. ffmpeg cropdetect defaults (round=16, limit=0.094) miss this; use tight settings or own edge row/column statistics. Intentional pillarbox allowance = roadmap.
- **Sequences**: structural checks (count, gaps, naming, header consistency) per unit; content checks still decode all frames. fps/TC from spec; EXR is linear so thresholds differ; audio via sidecar WAV; explicit start number.
- **Two-stage**: verifier can only dismiss, so stage-1 thresholds loose (high recall); Candidate schema (frame range, score, evidence); record verifier decision, confidence, model hash.
- **Reports**: versioned JSON schema, JUnit XML for CI, HTML with thumbnails, batch summary.
- **Added checks (v1)**: freeze frames (freezedetect, alongside duplicate/dropped); timecode track presence, start and continuity; basic levels min/max via signalstats if cheap inside the single pass.
- **Roadmap additions**: proper legal-level/gamut checks, phase/polarity, DC offset, field order/cadence (most deliveries are progressive), pillarbox allowance, delivery-package input (sidecars), dup-track detection, bars/tone level validation, checksum sidecar, act-break black.

## Decisions round 2
- **Dialogue-gated loudness moved to roadmap** (v1 ships BS.1770 relative-gated loudness, labelled as such in reports). Dialog-gating meter question stays open for later.
- **Blanking**: own edge row/column statistics (not cropdetect). Expect ongoing tuning against real false positives/negatives, so: stage 1 is loose/high-recall; ML verifier judges "defect vs intended" per candidate.
- **Freeze frames**: same two-stage pattern. Stage 1 finds frozen runs; ML verifier decides intended still (title card, hold) vs accidental. Verifier returns a **defect probability (0-1)**; the profile sets the threshold(s) per check (e.g. >=0.8 FAIL, 0.4-0.8 WARN, below = dismissed-but-logged).
- **Tuning loop** (to design with milestone 6): reviewers label findings (true defect / intended); labels stored with evidence thumbnails and used to evaluate thresholds and later train/fine-tune the verifier. Until a model exists, verifier is a no-op and stage-1 results report as-is.
- **Timecode**: program normally starts at 01:00:00:00. `file_start_timecode` is optional; if absent it is derived from the head layout.
- **Filename**: an explicit `filename` in the spec wins over `naming_pattern`.

## Decisions round 3 (final pre-coding review; Opus agent #2)
- **Severity rule**: stage 1 + profile set the *maximum* severity; the ML verifier may only lower it (to WARN or dismissed), never raise it. Finding keeps `raw_severity`, `severity`, `verifier{decision,p,model_hash}`, `waiver_ref`.
- **Detection separate from verdict**: each check supplies (a) the ffmpeg filter(s) it needs, (b) a parser for per-frame output, (c) `evaluate(candidates, params)`. Engine merges all video checks into one pass, audio into another. Schemas `Candidate{check_id,stream,frame_start,frame_end,score,evidence[]}` and `Finding` are versioned from day 1, as is the JSON report.
- **Frames and timecode**: detectors print per-frame values (e.g. `metadata=print:file=`), runs are found in Python on integer frames. Every reported problem location is **timecode** (plus frame number). Seconds/minutes appear only for program-length limits (e.g. program < 90 min); ad spots use exact frame counts per spec.
- **Roadmap (moved)**: 2-pop detection and `audio_markers`; caption sidecars (needs delivery package) — v1 checks embedded captions only.
- **Head length** is set in the spec (`head_layout` frames); loader rejects layouts/markers that fall outside the file.
- **Loudness measurement rules** (gating, measure range) live in the profile, not the spec.
- **Value mapping layer** for ffprobe vs spec vocab (e.g. `HQ` vs "422 HQ", color `unknown`).
- **Timecode**: own ~50-line rational DF/NDF implementation with tests, no extra library.
- **Blanking**: crop edge rows/columns + signalstats in the single pass; fall back to raw frames into numpy only if insufficient.
- **Sequences**: media abstraction covers file and sequence, but sequence input is post-v1.
- **Formats**: YAML for authored config, JSON for reports (easiest for pipelines; JUnit XML later for CI). Validation with jsonschema (installed); argparse CLI; PyYAML.
- **Repo layout**: src/cmr/{cli,config,media,timecode,engine,checks/,report/}, profiles/, tests/{unit,media,golden}. Test media generated by script with ffmpeg lavfi and planted defects.
- **CLI**: `cmr check FILE... --profile P --spec S [--report json|html|junit -o DIR] [--fail-on warn|fail]`, `cmr probe`, `cmr profiles list|show`, `cmr doctor`.

## Decisions round 4 (implementation of core + metadata checks)
- **Layout as built**: src/cmr/{cli,config,media,timecode,engine,model,schemas,checks/,report/}; `model.py` (Severity, Candidate, Finding) and `schemas.py` (jsonschema for spec/profile/report) added to the planned layout. Run: `PYTHONPATH=src python3 -m cmr` (pyproject entry point `cmr`). Tests: pytest.
- **Check interface** = `taps()` (plain ffmpeg chains per absolute stream index) + `detect(records) -> Candidates` + `evaluate(candidates) -> Findings`; exact contract in docs/check-interface.md. Plugins are auto-discovered from `cmr/checks/`; a plugin that fails to import becomes ERROR findings, it does not break other checks.
- **One ffmpeg process per file** for all video AND audio taps (one read/decode of the file), replacing "video one pass, audio another". ffmpeg 6.1 rejects graphs whose branches all end in nullsink, so every branch is labelled and mapped to `-f null -`.
- **Profile format**: `checks:` is a mapping keyed by check id; reserved keys `enabled` (default true) and `severity` (default fail), all else = params. Merge: mappings deep-merge, lists/scalars replaced by the child. `extends` resolves next to the file, then in profiles/. Optional spec keys reach checks by referencing whole sections (`timing: "${spec.timing}"`), so no optional-ref syntax is needed.
- **Severity order** PASS < SKIPPED < INFO < WARN < FAIL < ERROR. `--fail-on fail` makes WARN exit 0; ERROR (tool/config) always exits 3.
- **Report timecode** uses the file's embedded start TC when present, else spec `file_start_timecode`, else `program_start_timecode - head frames` (source is stated in the report). DF/NDF from the embedded TC separator (';' = DF), else spec `timecode_mode`.
- **Colour tags**: untagged = `unknown`, reported at the profile's `unknown_severity` (default warn). ffprobe gives no stream-level `color_range` for ProRes-in-MOV, so the single ffprobe call also reads the first packets (`-show_frames -read_intervals %+#10`) and fills missing colour fields from the first decoded frame (marked `color_source`).
- **Filename**: pattern placeholders come from spec `job` fields verbatim, then profile `filename.tags` (literal or `{from, map}`); fps_tag/format/audio_tag mappings live in the profile.
- **head_layout**: segment types slate, black, bars_tone, two_pop, program; `program` exactly once and last; `two_pop` is a positional video frame only (2-pop audio detection stays roadmap). v1 checks layout *positions* (fits in file, program start lands on `program_start_timecode`); segment *content* (black is black, slate not black) belongs with the black detector.
- **Decode errors**: stderr of the single pass at `-v error` + decoded vs container frame count. ffmpeg decoder messages carry no frame number, so decode errors are file-level (no timecode) - documented exception to "every location is a timecode".
- **File identity** in reports = size + sha256 of first and last MiB (full hashes too slow on multi-GB renders).

## v1 cut line / round 1
v1: probe + metadata checks, filename, frame count, audio layout, BS.1770 loudness + true peak, black + silence, decode errors, head/tail layout, JSON + console, exit codes. **Round 1 priority: blanking** (client request) built on top. Then v1.1: freeze, waivers, HTML/JUnit. ML stays roadmap.

## Decisions round 5
- **Blanking** (video.blanking): one tap per edge; each outer pixel is compared with the pixel `max_depth` px further in (`lut2`), giving defect/matte coverage fractions per line. Detects lines >= `coverage` (default 0.5, profile-tunable; a wrong mask can blank only part of a line) black. `crop exact=1` + `format=yuv444p` mandatory (signalstats segfaults on 1px-wide 4:2:2 crops otherwise). Classes edge/matte, `persistent` flag. Filters kept (numpy hybrid measured no faster). Tuned on synthetic media only (35/35 planted defects found, 0 FP); needs real renders. Gaps: defects <50% of a line, non-black bad blanking, near-black vignettes. Knobs: docs/blanking.md.
- **Engine**: frames computed from exact integer pts x time base (6-digit printed seconds were off by one beyond ~1000 s); taps print only needed keys; `-filter_complex_threads` defaults to min(8, cpu_count//2), override `--filter-threads`. Checks raise `ConfigError` for config problems.
- **Stills/thumbnails policy**: users are the people who rendered the file and can jump to the timecode in their app, so timecode is the primary locator. Stills are **off by default**, with one global switch to disable entirely; when on, they are produced only for finding types where a still is faster than seeking (e.g. visual look/colour, slate), capped per file, never for audio, metadata or black runs. Not yet implemented.

## Decisions round 6 (audio content checks)
- **Loudness numbers live in the profile**: spec `audio.loudness` names only `standard` + `program_track`; profile `audio.loudness.standards` holds target/tolerance/true-peak ceiling per standard (EBU R128 -23 +-1 LU / -1 dBTP, ATSC A/85 -24 +-2 / -2 dBTP). This supersedes "loudness target" in "Spec sheets" below.
- Audio checks (loudness, silence, silent channel, clipping) measure the program range only (atrim on sample bounds derived from head/tail layout, rounded half-up); per-frame levels via astats, frames derived from chunk index (pts_time printout has only 6 significant digits). Loudness reports always say "BS.1770 relative-gated, not dialogue-gated".

## Decisions round 7 (quality modes)
- `quality: fast|high` is a reserved per-check profile key (default high). Checks declare `qualities`; an unsupported mode is an ERROR so a report never claims a fast pass that did not happen. Findings carry `quality`; console marks `[fast mode]`. Details: docs/quality-modes.md.
- **Blanking fast** = every `fast_frame_step`-th frame (default 5, per user). Defects >= 5 frames are always seen; shorter may be missed (4-frame defects caught at 4 of 5 phases in tests). Sampled findings report the sampled range plus the possible extent (+-step-1); `min_run_frames` is checked against the possible extent. Measured saving is small (~0.25 s per 1440 frames) because decode dominates; kept as asked.
- **black_runs**: high = `format=yuv420p,signalstats` (same values as yuv444p on all test frames, ~2x faster); fast = quarter-resolution `scale=...:area` every frame (16x faster than the old chain; 1440 frames in ~1.2 s). Equivalence shown on synthetic media only; fast may flag grainy near-black frames (more candidates, not fewer) — acceptable for review renders (assumption).
- Profiles: `review` (fast blanking + black_runs, filename info-only, captions off) and `release` (explicit high, stricter tolerances) both extend `final`; `internal_review` removed.
- Process: agents use .agent-status/ (gitignored, deleted at finish) and write reference to docs/; only the main thread edits CLAUDE.md.

## Milestones
1 core (media abstraction, probe, time/TC lib, config loader, severities, exit codes, JSON+console); 2 metadata checks; 3 single-pass content checks (black, silence, freeze, loudness/true peak, silent channel); 4 head/tail layout, 2-pop, blanking; 5 waivers, HTML/JUnit, batch; 6 ML verifiers, watermark, banding, captions, roadmap.

## Spec sheets (confirmed)
Spec sheet = per-job *data* (what this delivery is: title, fps, duration, TC start, audio layout, loudness target, slate text). Profile = *rules* (which checks run, tolerances, severities). Profile params may reference spec values (`expect: ${spec.fps}`). Same profile reused across jobs; the spec supplies the job-specific numbers.

## Open questions
ML model choice and training data for blanking/freeze verifiers; Rocky ffmpeg build filters (UNVERIFIED); ML model choices and licences (lip-sync, blanking verifier); watermark definition (reference image vs. presence of any).
