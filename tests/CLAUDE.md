# tests

Run: `CMR_TEST_MEDIA=<dir> PYTHONPATH=src python3 -m pytest -q tests` (media is generated with ffmpeg, never stored in the repo).

`python3 tests/media/make_media.py OUTDIR [case...]` - cases good, wrong_meta, wrong_tc, short,
no_captions, corrupt; matches tests/media/spec_test.yaml (48-frame program, 1+23 head, 24 tail).
Case `one_track` = good with ONE stereo track and no captions: the demo for USERGUIDE quick
start A, matching templates/specs/spec_review_render.yaml (one audio track).
`make_media.make(outdir, name, vf_extra=..., af_extra=..., ...)` for planting content defects.
Tests use $CMR_TEST_MEDIA (default <tmp>/cmr_test_media); media is never written to the repo.

Portability (ffmpeg 5.1 on Rocky 9 and 6.1 locally; details src/cmr/CLAUDE.md):
- ProRes generators that need colour tags must use `-bsf:v prores_metadata=...` (5.1 prores_ks
  leaves the frame header unspecified, and 5.1 ffprobe reports the header). make_media does.
- Never cut a file with audio by `-frames:v` (5.1 drops queued audio): use `trim=end_frame=N`
  in the video chain (make_media) or `-t` (test_engine `tiny`). Video-only clips may use `-frames:v`.

## Slate test media (D27, Extreme Reach short form)
`/usr/bin/python3 tests/slate/make_spot.py OUTDIR [--values FILE] [--short] [--defect NAME ...]`
(needs Pillow + numpy: system python3 here). Default writes OUTDIR/clean/ and OUTDIR/errors/
(all defects except `slate_4s`); `--defect` (repeatable) writes one file to OUTDIR/<NAME+NAME>/.
Each run writes OUTDIR/values.yaml (slate text per file: frames 0-119 and the last countdown
second 120-149, render date, measured loudness) and OUTDIR/manifest.yaml (each planted defect:
frames, timecode, checks that should catch it, `detectable_now`). Refuses OUTDIR inside the repo.
- Layout: 1920x1080 ProRes 422 HQ, 30000/1001 NDF, tmcd 00:59:53:00; slate 150 (countdown 5..1)
  + black 60 -> program at frame 210 = 01:00:00:00, 1800 frames, no tail (2010 frames). One
  stereo 48k pcm_s24le stream: silent head except a 2-pop (1 kHz, -20 dBFS, frame 150);
  program noise + chords tuned to -24.0 LUFS (ebur128), true peak ~-9.7 dBTP.
- Pictures are built as 10-bit Y'CbCr and piped as rawvideo: bars/steps decode to the exact
  BT.709 limited-range codes (0 code error after ProRes). TITLE = filename stem; the values
  file's `title` is a template ({seconds} {ad_id} {resolution}); DATE = UTC render date =
  container creation_time.
- `--short`: program 90 frames (3 s; DURATION ":03"); head/slate/bars/2-pop/timecode identical,
  program defects at the same fractions of the program. Spec for it: program_duration_frames 90.
- Values: `tests/slate/values_fictional.yaml` (fictional only, tracked). Real-client values stay
  outside the repo (check_my_render_testdata/private/values_input.yaml on the dev machine).
- Known: audio.clipping at 29.97 reports the clipped frames up to 1 frame early (1601-sample
  chunks spill into the next frame; documented in src/cmr/checks/CLAUDE.md). `slate_4s` is
  caught only by timing.frame_count (layout/timecode checks trust the spec layout).

Waivers (D17): `tests/unit/test_waivers.py` (matching logic with fake fingerprints: inside /
outside / margin / fast uncertainty / locator / changed / incomparable / stale / expiry / file
round trip), `tests/test_waivers_media.py` (black run 30-49 waived -> WAIVED exit 0; re-render
with dim stripes at Y~23, still a black run -> content_changed; clean file -> stale; audio
clipping waived; loudness integrated waived while true peak stays FAIL; metadata not waivable).

Category waivers + findings cap: `tests/unit/test_waivers_category.py` (ceiling on raw severity,
sharp FAIL through a WARN ceiling, class/edge/channel/frame range, metadata never waived,
expiry, stale, per-finding first, file versions 1/2; cap ordering worst-first, FAIL not hidden
behind INFO, counts + exit code include suppressed, per check, 0 = unlimited, profile override,
waived not counted and summarised), `tests/test_waivers_category_media.py` (12 one-frame top
lines + 2 side bars planted via vf_extra: default cap 10 -> 4 suppressed, `--max-findings 0`,
profile `max_findings` and flag override; `--category` WARN ceiling waives the 2 mattes while 12
edge FAILs stay; stale category; edge FAIL ceiling -> exit 0; CLI validation).

## QC test suite (real renders; PROPOSED; runner + comparison built, phase 2)
Real client material never enters the repo: the suite lives OUTSIDE it (path per machine in
CLAUDE.local.md); media stays on the read-only file share and is referenced, never copied.
Only generic profiles (no real names) come into `profiles/`. Layout:

    suite/
      suite.yaml              runs: id, file "<root>:<path>", project, spec, [job], profile, size_mb, tags
      notes.md                inventory, smoke timings, open questions
      projects/<id>/          one folder per project/family (short neutral id)
        spec*.yaml            spec sheet(s) (one per resolution/length variant)
        job.yaml, deliverables.csv, slates/*.yaml   slated jobs only (D27)
        expected.yaml         expectations per run id
        waivers.yaml          reviewed waivers for this project (`cmr waive`, D17), when needed
        notes.md              what the deliverable is, what QC should check, answers to questions
        profile.yaml          optional job profile (run `profile:` = path ending .yaml, suite-relative)
      baselines/<run-id>.json reviewed report the run is compared against (`<id>@fast.json` for smoke)
      runs/<date>_<tier>/     scratch reports of each run
      stills/                 small contact sheets for orientation

`suite.yaml` (`suite_version: 1`): `roots` (name -> directory; the share path is the same on the
dev machine and the Rocky workstation), `defaults` (mode background, max_findings 0, timeout),
`runs[]`. `expected.yaml` (`expected_version: 1`): `runs.<id>`: `exit` (expected exit code or
null = not yet known), `source` (smoke-/meta-<date> | predicted), `findings[]` (`check`,
`severity`, optional `kind`/`edge`/`channel`, `frames: [a, b]`, `count`, `status`, `note`),
`absent[]` (checks that must report no problem), `other: fail|allow` (unlisted problem findings).
`status`: `real_issue` (a defect in the file; the tool must keep finding it), `intended` (picture
or sound meant that way; candidate for a waiver; `intended-tentative` until reviewed), `tool_gap`
(wrong or missing result the tool must fix; the expectation flips when fixed), `unconfirmed` /
`assumed-default` (awaiting the user). The material is past work, not all of it QC'd: real
failures stay `real_issue`, expectations are never bent to make a run pass. Matching: same check +
severity (+ locator when given), frames overlap within 5 frames (or the fast-mode uncertainty).
Kept small on purpose: no per-finding message text (wording changes), no timings.

Runner: `PYTHONPATH=src /usr/bin/python3 tests/suite/run_suite.py [SUITE] --tag smoke|acceptance|meta
[--run ID] [--slow] [--quality fast|high] [--meta] [--update-baseline] [--dry-run]` (SUITE or
`$CMR_SUITE`). One file at a time: `cmr check` via the CLI (`--mode background`, `--max-findings 0`,
JSON) into SUITE/runs/<stamp>_<tier>/<id>/ + <id>.log + summary.json; verdict per run PASS / FAIL
/ ERROR / NEW (no expectation) / SKIP; exit 1 on any FAIL/ERROR. Quality: passed as
`cmr check --quality Q`; with an override the expectation key is `<id>@<quality>` (falls back
to `<id>`). `--meta`: the runner writes a wrapper profile (`extends: <profile file>`, every check
with taps / `needs decode` and the slate checks disabled) into runs/<stamp>/profiles/
(expectations not compared). The runner passes `--job` when the run lists one, else `--no-job`
(a stray ./job.yaml is never used, D31). Comparison logic:
`tests/suite/compare.py`, unit tests `tests/unit/test_suite_compare.py` (synthetic reports only).
Tiers by tag: `meta` (no decode pass: safe on any size), `smoke` (quality fast; the runner skips
files > 500 MB by real size; seconds per file), `acceptance` (all runs, profile quality, compare
with expected.yaml / baseline), `slow` (> 5 GB: only with --slow or --run, on the workstation),
`needs-tesseract` (SKIP without `$CMR_TESSERACT` / tesseract on PATH). Load check for every pair:
`PYTHONPATH=src /usr/bin/python3 -m cmr profiles show PROFILE --spec SPEC [--job JOB]`.
Rules on the shared server: one file at a time, `--mode background`, `timeout`, no full decodes
outside the acceptance tier, stills by seeking only.

## Slate checks (D27) - tests
- Unit (no tesseract, no media): `tests/unit/test_deliverables.py` (job merge + conflicts, paths,
  `${spec.*}` vs `{row.*}` tokens, blank vs `<empty>`, CSV columns, row matching key / generated
  name / both / fields (exactly one row) / `--row` forms, ambiguity = config error),
  `tests/unit/test_slate_ocr.py` (TSV parsing, label pairing incl. countdown/logo/stray label,
  alias, `line: N`, comparison rules + confusables, frames 2nd/2nd-to-last, slate.present,
  slate.consistency changed/uncertain/confusable, tesseract missing -> ERROR, per-file OCR cache),
  `tests/unit/test_slate_content.py` (content: authority TITLE/AD-ID, empty semantics, expect
  overrides, better frame wins, date policies; claims; waiver by exact OCR text + category),
  `tests/unit/test_slate_bars.py` (synthetic planar frames 8/10/12-bit 420/422/444: clean,
  elevated black, wrong hue, tinted grey, tolerance edge, range mismatch, fixed fractions,
  unsupported pix_fmt). Canned TSV builder: `tests/unit/slate_tsv.py` (sample-slate layout).
- Media: `tests/test_slate_media.py` uses `$CMR_SLATE_MEDIA` (a make_spot.py OUTDIR, full or
  `--short`) else generates `--short` into `$CMR_TEST_MEDIA/slate_short` (needs numpy + Pillow;
  skips otherwise). Spec/job from examples/spot_xr (program length + CSV length follow the
  manifest). Without tesseract: bars/filename/ERROR-path tests run, the 3 OCR tests SKIP. With
  tesseract (`CMR_TESSERACT=.../tesseract`): clean -> no problem finding (spot_delivery); errors
  -> every manifest error mapped in `EXPECT` is reported and no other slate finding; waiver of the
  CLIENT typo by text. Run on the Rocky workstation: `CMR_TESSERACT=<env>/bin/tesseract
  CMR_SLATE_MEDIA=<dir> PYTHONPATH=src <py312> -m pytest -q tests/test_slate_media.py`.

## Logging (D29) - tests
`tests/unit/test_log.py` (line format and ids, default level constant + env + flag, off, rotation
at start only, unwritable dir -> stderr, never raises, redaction stable / per salt / private,
scrub, redact_cmd, snapshot with missing ffmpeg/tesseract/nvidia-smi/ionice, two processes
appending, support bundle contents, unhandled exception -> traceback + exit 3);
`tests/test_log_media.py` (privacy grep: a recognisable file/dir name and, with tesseract, the
slate text are absent in default mode and present with --log-private; a crashing check is logged
with traceback while the report keeps its ERROR finding). conftest points CMR_LOG_DIR at a temp
dir so tests never write the user's log.

## Probe/metadata/captions gaps - tests
`tests/unit/test_probe_gaps.py` (canned ffprobe JSON: MXF format timecode + DF, OP UL, DNxHD
class, yuvj, field order, stereo inference; fps equivalence; key_severity; 436M parser on
hand-built bytes with fictional caption bytes, 10-bit coding, hexdump), `tests/test_probe_gaps_media.py`
(generated OP1a DNxHD MXF, interlaced MPEG-2 DF MXF, 2997/125 yuvj420p H.264 MOV; the 436M reader
command on a MOV tmcd stream - ffmpeg cannot write 436M). Pass on ffmpeg 6.1.1 and 5.1.10 (Rocky).

## Content-check gaps (black runs content on black, multi-stream loudness, declared silence) - tests
`tests/unit/test_black_runs_content.py` (run classification, hot pixel, split run, detector off,
bright-tap chain), `tests/test_black_content.py` (make_media vf_extra: logo on black, moving thin
lines = credits, logo then black, fade to black, true black; detector off; fast mode;
`content_min_fraction`). `tests/media/make_media_audio.py`: `make_multi(outdir, name, streams,
container="mov"|"mxf")` writes one MONO pcm_s24le stream per entry (`tone(freq, peak_dbfs)`,
`SILENT`), spec_test layout (program frames 24..71); mxf = OP1a mpeg2video. Unit
`tests/unit/test_audio_programs.py` (program chain amerge + pan without LFE, taps, config errors,
per-program findings, allowed_silence parsing/ranges, silence/silent_channel with declarations,
build_graph `Tap.inputs` routing), media `tests/test_audio_multistream.py` (5.1 + stereo programs in
mov and mxf vs analytic and independent `join` + ebur128; LFE at -1 dBFS kept out of I and true
peak; program_track unchanged; undeclared silent streams FAIL, declared INFO; declared time range;
config error). Note: test module basenames must be unique across tests/ and tests/unit/.

## job.yaml entry point (D31) - tests
`tests/unit/test_job_entry.py` (find_job order and errors, job_settings path resolution, job id
schema, old job files, CLI precedence `apply_job`, missing-key explanations, `split_job` /
foreign summary / job field load + stamp, `_waive_job` defaults, short paths, console JOB and
foreign lines, doctor log + job.yaml lines, suite runner `--quality` / `--meta` wrapper / `--no-job`),
`tests/test_job_media.py` (job folder: `cmr check FILE` with no options, options override,
`--job-dir`, pre-D31 form; waive -> job-stamped entry -> WAIVED; other job id -> foreign; report
of another job refused; `--any-job` category; waiver files without `job`; missing-key message;
quick start A with `one_track`), `tests/test_quickstart_media.py` (templates/quickstart copied
unedited + make_spot `--short` media: without tesseract only the 4 OCR checks ERROR; with
tesseract the clean spot passes, exit 0; verified on the Rocky machine).
