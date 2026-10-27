# src/cmr - core (engine, model, config, timecode, media, cli, report)

Design notes for the core package. Overview and numbered decisions: ../../CLAUDE.md.
Run without install: `PYTHONPATH=src python3 -m cmr ...` (entry point `cmr`).

## Check interface (contract for plugin authors) - STABLE v1.0
Adding a check = one new module (or package) under `src/cmr/checks/`; every module there is
auto-imported by `engine.load_checks()` (a module that fails to import becomes ERROR findings
for its check ids and is listed by `cmr doctor`, it does not break other checks).

```python
from cmr.engine import Check, Skip, Tap, MetaRecord, register
from cmr.model import Candidate, Finding, Severity

@register
class Black(Check):
    id = "video.black_runs"                 # profile key under `checks:`
    needs = frozenset({"probe"})            # add "decode" to force the pass without taps
    requires_filters = ("signalstats",)     # checked against `ffmpeg -filters`; missing -> ERROR
    params_schema = None                    # optional jsonschema for the profile params

    def taps(self, ctx) -> list[Tap]:       # (a) what to run in the single pass
        v = ctx.media.video[0]["index"]     # ABSOLUTE ffprobe stream index
        return [Tap(name="full", stream=v, chain="signalstats")]

    def detect(self, ctx, records: dict[str, list[MetaRecord]]) -> list[Candidate]:   # (b) stage 1
        # records["full"] = [MetaRecord(frame=int, pts_time=float, values={"lavfi.signalstats.YAVG": "64", ...})]
        ...

    def evaluate(self, ctx, candidates) -> list[Finding]:   # (c) verdict; [] means PASS
        return [ctx.finding("black run", frame_start=c.frame_start, frame_end=c.frame_end,
                            candidate=c) for c in candidates]
```
- `Tap(name, stream, chain, keys=(), inputs=())`: `inputs` = extra absolute stream indices the
  chain reads as `[in1]`, `[in2]`, ... (audio.loudness merges mono streams); `stream` is the
  main input (prefixed), time base and metadata kind. `chain` is an ffmpeg filter chain with NO metadata
  print and NO output label; it may contain internal `;`-segments whose labels are prefixed
  uniquely (video.blanking uses `bk<edge>...` for split/lut2). The engine appends
  `,metadata=mode=print:file=<tmp>` (`ametadata` for audio streams), or with `keys` one
  `metadata=mode=print:key=K:file=<tmp_i>` per key (merged per frame; prefer this: all ~28
  signalstats keys are ~840 B/frame/tap), splits the input (`split`/`asplit`) when several taps use one stream, labels every
  branch and maps it to `-f null -`. All taps of all checks run in ONE ffmpeg process per file
  (`-v error`; stderr kept in `ctx.passinfo.stderr`).
- `MetaRecord.frame`: t = exact integer `pts` x time base (stream `time_base`, else
  1/sample_rate, whichever matches every printed pts_time; none matches -> check ERROR);
  video = `round(t * fps)`; audio = frame containing the chunk start, `floor(t * fps)`.
  The printed pts_time has only 6 significant digits and is not used for frames.
  Only frames that carry metadata are printed by ffmpeg, so a frame can be absent.
- `ctx` (`CheckContext`): `check_id`, `media` (MediaInfo, mapped probe), `spec` (dict),
  `params` (profile params after ${spec.*} substitution, without `enabled`/`severity`/`quality`),
  `severity` (profile severity for a problem, default FAIL), `timeline`, `passinfo`,
  `quality` (`fast`|`high`, default high; a check lists supported modes in class attr
  `qualities`, default `("high",)`; see src/cmr/checks/CLAUDE.md).
  `ctx.finding(msg, severity=None, **Finding fields)` builds a Finding at the profile severity.
- `ctx.timeline` (`Timeline`): `fps` (Fraction), `drop`, `file_frames`, `start_frame`
  (TC of frame 0 as frame count), `tc_source` (file|spec|none), `head_frames`, `tail_frames`,
  `program_start`, `program_end` (inclusive file frame indices; None if layout does not fit),
  `tc(frame) -> "HH:MM:SS:FF"`, `segments` (head/tail layout segments `{type, start, end}` in
  file frames, inclusive; D27) and `segment(type)` (first segment of a type or None).
  Layout vs file length (D7): the spec loader checks only the layout's SHAPE (one `program`
  segment, last in head_layout; `frames` on every other segment; no program in tail_layout),
  because the file length is unknown at load time. A layout that does not fit the file
  (head + tail >= file frames) is found at run time: `Timeline.problems`, program range None,
  a `timing.layout` FAIL, and SKIPPED for the checks that need the program range (audio
  content incl. loudness, black runs, slate.claims).
- `ctx.passinfo` (`PassInfo`): `ran`, `returncode`, `stderr` lines, `decoded_frames`
  (engine counter tap on the first video stream), `command`.
- Raise `Skip("reason")` -> SKIPPED (from `taps()` too: the check is then SKIPPED without
  running detect/evaluate). Raise `engine.ConfigError("...")` for unusable
  params/spec values -> ERROR "configuration error: ..." (from taps() too). Any other exception -> ERROR
  finding ("check crashed").
- `-filter_complex_threads`: `engine.default_filter_threads()` = min(8, cpu_count//2)
  (macOS: min(8, performance cores), see "macOS" below);
  override `run_file(filter_threads=N)` / `cmr check --filter-threads N`.
  Measured (16 CPUs, 1080p ProRes, 1440 frames, s): blanking only 1:1.8 2:1.6 4:1.7 8:2.1
  16:3.6; blanking+black_runs 1:65 2:39 4:25 8:21.6 16:20.2 (decode only 0.8). 16 (= ffmpeg
  default, all CPUs) hurts small-strip taps, 1-2 starve full-frame taps; 8 is the compromise.
  black_runs dominated with full-frame `format=yuv444p,signalstats` (~19 s/1440 frames);
  now `format=yuv420p,signalstats` (high, ~9 s) or quarter-res (fast, ~1.2 s), see
  src/cmr/checks/CLAUDE.md.
- Engine fills `timecode_start/end` from `frame_start/end` for every finding; reports show
  TC + frame. Frame ranges are INCLUSIVE.
- Candidate: `{check_id, stream, frame_start, frame_end, score, evidence[]}`.
- Finding: `{check_id, severity, raw_severity, message, stream, frame_start, frame_end,
  timecode_start, timecode_end, expected, actual, candidate, evidence[], verifier, waiver_ref,
  quality, waiver}` (`quality` filled by the engine with the check's mode; null for engine errors;
  `waiver_ref`/`waiver` see Waivers below).
- Waiver location: evidence[0] keys `edge`, `kind`, `channel` (finding.evidence, else
  candidate.evidence) are part of a waiver's location. A check that emits several finding types
  for one stream and range must tell them apart with `kind` (blanking classes; audio.loudness
  `integrated`/`true_peak`/`lra`), or a waiver of one would cover the others.
- Verifier hook: `engine.VERIFIERS[check_id] = fn(candidate, ctx) -> {"decision", "p",
  "model_hash", "severity"}`; applied to findings that carry a `candidate`; the engine calls
  `Finding.lower_to()` so a verifier can only LOWER severity. Empty in v1.
- Severities: PASS < SKIPPED < INFO < WARN < FAIL < ERROR. Exit: 0 pass, 1 warn
  (0 with `--fail-on fail`), 2 fail, 3 ERROR / config error / command-line usage error
  (`cli._Parser`; argparse's own 2 would read as FAIL).
- File and run verdict (`model.file_verdict`): the worst severity, except that SKIPPED alone is
  PASS (a file whose only non-PASS results are SKIPPED shows PASS; user decision). SKIPPED
  findings stay in `summary.counts` and are listed (console without -v too). Exit code unchanged.
- `params_schema`: build it with `schemas.check_params(required, **props)` (additionalProperties
  false). `engine.validate_profile_params(profile, substituted)` runs at config time (`cmr check`
  after substitution, `cmr profiles show`): unknown param names for every check entry (enabled
  or not), values only for enabled checks once substituted; errors = ConfigError, exit 3,
  message `checks.<id>: unknown parameter(s) 'x' (allowed: ...)`. run_file still validates
  (ERROR finding) for library callers. Strict: every check (the four audio content checks
  joined in the D31 round; every stock profile, template and example pair still loads).
- Profile check entry: reserved keys `enabled` (default true), `severity` (default fail),
  `quality` (fast|high, default high), `max_findings` (report cap, see "Findings cap"; not
  passed to the check; `engine.RESERVED_KEYS`); everything else is params. Spec values reach checks only via `${spec.*}` refs in the profile
  (whole-value refs keep type; pass whole sections like `timing: "${spec.timing}"` when a
  check needs optional keys). Substitution is LAZY for checks with `enabled: false`
  (`config.resolve_profile`): their entries keep the refs verbatim, so a spec without e.g. an
  audio section loads a profile whose audio checks are off; an enabled check with a missing key
  is still a ConfigError (D4). Spec scalars that checks treat as "not specified" may be null:
  video width/height, timing file_start_timecode / program_start_timecode /
  program_duration_frames, audio sample_rate / bit_depth (`schemas._opt_int`).

## job.yaml as the entry point (D31; report schema 1.4)
- Job file (`config.JOB_SCHEMA`, `job_version: 1`): sections `job` / `deliverables` / `slate`
  (merged into the spec as before, D27) plus top-level run keys `spec`, `profile`, `waivers`,
  `output`; `job.id` = short job id (`^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`; `*` reserved). All
  paths relative to the job file (`config.job_settings`); `profile` stays a stock name unless
  it has a `/` or ends in .yaml/.yml. A job file without the new keys works as before.
- Which job file (`config.find_job`): `--job PATH`, else `--job-dir DIR` (DIR/job.yaml), else
  `./job.yaml` when it exists, unless `--no-job` (the three are mutually exclusive; on `cmr
  check` and `cmr waive`). An explicit one that is missing = config error (exit 3). When
  ./job.yaml is picked up by itself, `cmr check` prints `job file: PATH (job ID)` to stderr.
- `cmr check` (`cli.apply_job`): options win; the job fills only what the command line left
  out: spec, profile, waivers (the job's `waivers`, default `waivers.yaml` next to job.yaml,
  used only when the file exists: no waiver file yet = no waivers, logged), `output` (report
  folder; it also turns `--report json` on, also when `-o` moves the folder). Without spec or
  profile from either side: `error: --spec and --profile required: ...` exit 3. `--spec` and
  `--profile` are no longer argparse-required.
- Report 1.4 (additive): `summary.job_id` (job file `job.id`, null without one; a spec sheet's
  own `job.id` is not used), `waivers.foreign`; console `JOB <id>` line before `RESULT`, and
  `foreign N` + `foreign (job X, not applied): Wnnnn ...` lines. Log `check:` line: job file
  (redacted path), job id (redacted `name` token in default mode) and how it was found.
- Waivers carry the job (`waivers.split_job`): an entry with `job: <id>` applies only to runs
  of that job id; `job: "*"` to every job; an entry WITHOUT `job` (files from before D31)
  applies to every run (backward compatible); entries of another job (or any `job:` entry in
  a run without job id) are skipped and listed in `waivers.foreign` (with `job`). Summary
  entries carry `job` when set. Waiver file version unchanged (1/2): `job` is an optional key.
- `cmr waive` (`cli._waive_job`): in a job (same three options) the default `-o` is the job's
  waiver file (else ./waivers.yaml) and new entries get `job: <job.id>`; without a job file the
  report's `summary.job_id` is stamped; a report of another job than the job file = error;
  `--any-job` stamps `job: "*"`. Listings show the media path relative to the job folder (else
  the cwd), or the base name when outside it (`cli._short`, symlinks not resolved).
- Missing `${spec.*}` keys raise `config.MissingSpecKey` (a ConfigError; `str()` unchanged:
  `missing spec key ${spec.x} (no 'x')`) with `where` (`profile check ID (profile 'P')`, or the
  job section); `explain(spec_path, job_path)` is what `cmr check` / `cmr profiles show` print:
  the field as YAML path (`audio: loudness:`), the spec sheet path, for job sections
  (job/deliverables/slate) the job file (or "none is in use"), "switch that check off" for
  profile checks, and the guide section "Spec sheets: the job facts".
- `cmr doctor` also prints `log: PATH` and `job.yaml in the current folder: none | PATH (job
  ID) | PATH - INVALID: ...` (informational, never a doctor failure).

## Job `input:` + spec `job.filename_regex` (report schema 1.5; 2026-10-27)
- Job file `input: {folder (required), pattern (default "*"), pick (only `latest`)}`
  (`JOB_SCHEMA`, additionalProperties false; pattern must not contain `/`). `folder` resolves
  like spec/profile/output (`config.job_settings`: absolute, `~`, else relative to the job
  file's folder) -> `js["input"]` (None without the block).
- Spec `job.filename_regex` (`SPEC_SCHEMA`; may also come from the job file's `job:` section,
  merged as usual): Python regex, `re.fullmatch` on the file NAME (`MediaInfo.name`, NFC).
  `config.filename_regex(job)` compiles it; invalid regex, or given together with
  `job.filename` / `job.naming_pattern` = ConfigError "give only one" (checked in `build_spec`,
  so exit 3 before anything runs; the filename check calls it too -> ERROR "configuration
  error" for library callers). Filename check: mismatch = `filename: expected a name matching
  '<regex>', got '<name>'` (expected = regex, actual = name) at the check severity; no tags used.
- `cmr check` FILE is `nargs="*"`. `cli.apply_job`: no FILE and no job `input` = usage error
  `no file to check: ...` (exit 3). `cli.pick_job_input` (after build_spec + profile, inside the
  config-error block): FILE arguments win (log INFO "job file's `input:` is ignored"); else
  `config.pick_input(js["input"], spec["job"])`: candidates = `os.listdir(folder)` names that
  are files, do not start with `.` (AppleDouble `._*`), `fnmatch.fnmatchcase(nfc(name),
  pattern)`; each candidate `fullmatch`ed (NFC) against the regex; group `version`
  (`config.VERSION_GROUP`) must be ASCII digits -> int. latest = highest int (NOT mtime).
  ConfigError (exit 3): folder missing; no regex or no `version` group (message shows
  `(?P<version>\d+)` and the example `^Color_6K_v(?P<version>\d+)\.mov$`); no conforming
  candidate (lists what the glob found, or "nothing"); ANY two conforming candidates with the
  same version number (both listed, e.g. `v4` + `v04` with `\d+`). Non-conforming candidates
  (regex mismatch, non-numeric version) = notices `{severity: WARN, file, message}`, never
  silent. stderr `input: NAME (version N) from FOLDER[; K file(s) not considered, see NOTICE]`;
  log INFO pick (redacted) + WARN per notice.
- Report 1.5 (additive; `model.SCHEMA_VERSION`, `cmr waive` accepts 1.0-1.5): top-level `input`
  (only when picked: folder, pattern, pick, picked, version, conforming, notices[]) and
  `summary.notices` (count, always present). The notices' severity raises `summary.worst` and so
  the exit code (WARN -> 1, 0 with `--fail-on fail`, D8), but NOT the file verdict
  `files[].worst`; `summary.counts` stay finding counts. Console: `INPUT NAME (version N,
  highest of K matching job.filename_regex) from FOLDER, pattern 'P'`, one `  NOTICE  WARN
  FILE: message` line each, and `; NOTICE K (see INPUT)` on the RESULT line. This is the
  run-level notice mechanism (none existed); reuse it for other run-level notices.
- Lazy params (`Check.lazy_params`, `config._substitute_check`): mapping params a check lists
  there are substituted entry by entry; an entry with a missing `${spec.*}` key becomes
  `{config.MISSING ("$missing"): "missing spec key ..."}` instead of a load error. filename
  declares `("tags",)`; `filename._tag` raises it as KeyError only when the naming_pattern uses
  that tag -> ERROR `cannot build expected filename: tag audio_tag: missing spec key ...` (exit 3
  as before, but at run time; a pattern without `{audio_tag}`, or filename_regex, needs no
  audio). So a picture-only spec (`audio: {tracks: []}`) loads final/review/release-based
  profiles with the filename check ON, once the audio content checks are off (they still need
  `${spec.audio.codec}` etc.). `config._lazy_params` imports engine late (engine imports config).
- Known limits: a render still being written is picked like any other file (no size/mtime
  stability test); the glob is case-sensitive (also on case-insensitive APFS); one file per run.

## Packaging, report files, `--quality` (report schema 1.3)
- pyproject has an explicit `[build-system]` (setuptools>=61, `setuptools.build_meta`); the
  offline wheel build (`installer/build_bundle.sh --python /usr/bin/python3`, setuptools 78.1)
  still works.
- Install: `pip install .` ships the stock profiles as package data: pyproject maps the repo's
  top-level `profiles/` to package `cmr.profiles` (`package-dir`, `*.yaml`; explicit package
  list, guarded by tests/unit/test_cli_config.py). `config.stock_profiles_dir()`:
  `importlib.resources.files("cmr")/profiles` when it exists (installed), else `<repo>/profiles`
  (source tree, `PYTHONPATH=src`, and `pip install -e .`, which keeps editing the repo's files).
  `--profile PATH.yaml` and `extends` next to the extending file work unchanged. Templates,
  examples and slate templates are not packaged (no code path loads them by default).
  Verified: `pip install --target` (dev machine, Python 3.12.3) and a venv on the Rocky machine (Python
  3.12.15): `cmr profiles list` from /tmp lists all stock profiles from site-packages; editable
  install resolves to the repo's profiles/. Version single source: `cmr.__version__`
  (pyproject `dynamic = ["version"]`, `attr`). A non-editable `pip install .` leaves `build/` in
  the source folder (not in .gitignore).
- `cmr check --report json [-o DIR]`: DIR defaults to the current directory. Each run writes
  `DIR/cmr_report_<YYYYmmdd-HHMMSS>_<run id>.json` (run id = the log's `run=`, `log.run_id()`)
  and replaces `DIR/cmr_report_latest.json` (a copy, written to a temp file + `os.replace`; a
  copy rather than a symlink so it works on any file share). Scripts read the latest copy;
  `cmr waive` takes any report path. A failed write exits 3.
- `cmr check --quality fast|high` (`cli.apply_quality`, after substitution and param checks):
  sets `quality` on every ENABLED check whose class implements that mode; checks without it
  keep the profile's (i.e. high). Precedence: `--quality` > profile `quality` > `high`. Logged
  (`quality fast (--quality ...): set on [...]; ... kept as profiled: [...]`). Report 1.3
  (additive): `summary.mode` (foreground|background, D26) and `summary.quality` (fast|high,
  or `profile` without the flag); per-finding `quality` stays the mode actually used.
  `cmr waive` accepts reports 1.0-1.4.

## Waivers (D17) - `src/cmr/waivers.py`, report schema 1.1 (1.2: category waivers, cap)
- CLI: `cmr check ... --waivers W.yaml`; `cmr waive REPORT.json` (lists waivable findings with
  their number N = 1-based position over all findings of all files in the report),
  `cmr waive REPORT.json --finding N ... | --all-check ID --reason TEXT [--expires YYYY-MM-DD]
  [-o W.yaml]` (default `./waivers.yaml`, in a job the job's waiver file, D31; appends, ids W0001...). The media path is the report's
  `files[].path` (relative to the cwd of `cmr check`); its identity (size + head/tail sha) must
  equal the report's, else error. Waivable: `video.*`/`audio.*` findings with a frame range and
  stream, severity INFO/WARN/FAIL. Metadata/file-level findings are not waivable in v1.
- File: `waiver_version: 1`, `waivers: [{id, check_id, stream, [edge, kind, channel],
  frame_start, frame_end, timecode_start, timecode_end, message, fingerprint: {type, algorithm,
  version, frames, values}, reason, created, [expires]}]`. No reviewer identity (v1).
- Fingerprint: up to 8 (video) / 16 (audio) evenly spaced frames of the range incl. first and
  last, one ffmpeg seek per frame AFTER the main pass, only for findings that location-match a
  waiver (cmr check) or are being waived (cmr waive). Video `video_dhash` v2 (algorithm
  `dhash64-luma9x8-box`, written since 2026-10-26; ffmpeg is NOT in the reduction): `-ss
  (f-0.5)/fps -i F -map 0:S -frames:v 1 -vf extractplanes=y -f rawvideo -` = the decoder's luma
  plane at native size and bit depth (gray / gray10le / gray12le / gray16le; no scale, no
  format conversion: verified byte-identical to the Y part of a native rawvideo decode, and
  `ffmpeg -h filter=extractplanes`: planes y,u,v,r,g,b,a). RGB sources (pix_fmt rgb*/bgr*/gbr*/
  argb/abgr/0rgb/x2rgb...): `extractplanes=r+g+b[r][g][b];[r][g][b]vstack=inputs=3` (`ffmpeg -h
  filter=vstack`: inputs), luma = BT.709 integer weights 2126/7152/722 applied to the CELL SUMS
  (a box mean is linear: no per-pixel work). Bit depth = first component of the stream pix_fmt
  in `ffmpeg -pix_fmts` (BIT_DEPTHS column; same layout on 6.1.1/7.1.1/9.0.2), byte order from
  the pix_fmt suffix (`be` -> swapped). Reduction in pure Python (`waivers.luma_cells`, stdlib
  `memoryview`/`array`, per-row `sum()` over slices): cells = column edges `c*W//9` (c=0..9) x row
  edges `r*H//8` (r=0..8), integer floor, every cell non-empty (W >= 9, H >= 8); cell value =
  round-half-up(sum / (count * 2^(depth-8))) in integers = an 8-bit code, so 8/10/12/16-bit
  encodes of one picture give the same codes, and the 8-bit step keeps the v1 dead zone (grain
  on flat areas does not flip bits; the 10-bit tolerance stays calibrated). Range: values are
  NOT range-converted (no swscale); a full-range (yuvj/pc) and a limited-range encode of the
  same picture are an order-preserving linear map apart, which dHash ignores (measured 0-1 bit).
  Fingerprint keys: `plane` (y | rgb) and `ffmpeg` (`ffmpeg -version` first line) are
  INFORMATIONAL (not compared). A pix_fmt without an 8..16-bit integer plane (float EXR-style,
  bayer, unknown) gets a v1 fingerprint (WARN logged); a v2 waiver cannot be matched on such a
  file (fingerprint failure = content changed).
  v1 (`dhash64-gray9x8-area`, waivers written before): `-vf
  scale=9:8:flags=area+accurate_rnd,format=gray` -> 72 bytes, swscale does the averaging, so it
  DEPENDS ON THE FFMPEG BUILD (Y-23 dim stripes read 0/8 on 6.1.1 but all 0 on 7.1.1/9.0.2).
  Kept, unchanged, only to match v1 waiver files: `cmr check` recomputes a v1 waiver with the v1
  chain (`Fingerprinter.fingerprint(..., version=1)`); the finding's `waiver` then carries
  `fingerprint_version: 1` and `fingerprint_note` ("fingerprint v1 (depends on the ffmpeg
  build); re-create the waiver to upgrade it to v2"), printed on the console under the finding.
  Re-creating the waiver (`cmr waive` again) writes v2. A `format=yuv444p` variant of the v1
  chain (tried by the ffmpeg 7.1 agent) reproduced 6.1.1 on 10-bit dim stripes under 7.1/9.0
  but read 8-bit dim stripes as black on EVERY build and moved dark pictures by 3-4 bits, so the
  original chain is kept. Audio `audio_envelope`: `-ss f/fps -i F -map 0:S
  -t 1/fps -f f32le -c:a pcm_f32le -` -> per channel [peak, RMS] dBFS, 0.1 dB, floor -90.
  Distance: max Hamming (video) / max |dB| (audio) over the sampled frames; other
  type/algorithm/version/frames = not comparable = content changed.
- Cross-build (picture set made once with 6.1.1, byte-identical inputs; 8 frames each; Linux
  6.1.1 dev machine vs conda-forge 7.1.1 and 9.0.2 on the Rocky machine): v2 hash bits AND all 72
  cell codes identical on all 19 files (ProRes 422 10-bit, 4444 12-bit + alpha, H.264 8-bit crf
  18/28, H.264 10-bit, yuvj420p full range, ffv1 1001x563 12-bit, gray16le, PNG rgb24/rgb48be,
  dark low-contrast ProRes + H.264, Y-23 stripes 10-bit ProRes + 8-bit H.264, black, testsrc2,
  the waiver test files). Same picture across encodes (v2): ProRes 10 -> H.264 crf 18/28 1 bit,
  -> 10-bit H.264 / 4444 / yuvj 0, -> rgb24/rgb48 4, dark ProRes -> H.264 1; stripes vs black 32.
  Audio envelope: PCM identical on all three builds; AAC differs (6.1.1 = 7.1.1, 9.0.2 up to 1.6
  dB: the seek decodes AAC without pre-roll, 9.0 differs in how it handles that; all within the
  2 dB tolerance). Not fixed (would need audio v2; PCM, the delivery format, is unaffected).
- Cost v2 (dev machine, 1080p, one sampled frame incl. ffmpeg start): 8-bit H.264 ~90 ms, ProRes 10-bit
  ~115 ms, 4444 12-bit ~160 ms, RGB 3 planes ~90-145 ms; of that the Python reduction is 8.5 ms
  (8-bit) / 15-21 ms (10-16 bit). ~0.8-1.3 s per video finding (8 frames; v1 was ~0.7 s).
  UHD/6K planes scale with the pixel count (UNVERIFIED, estimated 4-10x).
- Matching (`waivers.apply`, pure, unit-tested with fake fingerprints): same check_id, stream and
  edge/kind/channel; finding range inside [frame_start - m, frame_end + m], m =
  max(profile `waivers.margin_frames` (5), evidence `boundary_uncertainty_frames`); the CURRENT
  file is fingerprinted at the WAIVER's frames. Distance <= tolerance (profile `waivers:`
  video_max_bits 10, audio_max_db 2.0) -> WAIVED: `lower_to(PASS)`, raw_severity unchanged,
  `waiver_ref` = id, `waiver` = {id, status: waived, reason, distance, tolerance}. Else the finding
  keeps its severity with `waiver` = {status: content_changed, ..., note "previously waived (id),
  content changed - re-review"}. Expired waivers (expires < today) are ignored. Applied in
  cli.cmd_check after engine.run_file (engine unchanged), file `worst` recomputed.
- Report 1.1 (additive): `finding.waiver`, `summary.counts.WAIVED` (waived findings are counted
  there, not as PASS), top-level `waivers: {file, applied, content_changed, stale, expired}`
  (only with --waivers). Console: `WAIVED` line + "was FAIL; waiver W0001: reason" (shown
  without -v), content-changed note line, `WAIVERS ...` counts, stale/expired listed.
- Measured (ffmpeg 6.1.1, the dev machine): seek fingerprints equal full-decode fingerprints on
  every frame of a 96-frame clip with 73 distinct dHashes (ProRes HQ and H.264 with B-frames,
  start_time 0) and on audio (per-frame peak/RMS, 0 dB difference); same result on Rocky 9
  (ffmpeg 5.1.10, full suite 123 passed under Python 3.12). dHash distance, same frames:
  ProRes HQ -> H.264 crf 18/28 max 7/8 bits; ProRes HQ -> Proxy 0; regrained noise 0; brightness
  +0.06 (eq) 0 (dHash ignores level shifts); dark low-contrast picture -> 8-bit H.264 max 12
  (would read as content changed: safe direction); white box over the central quarter max 16;
  different picture max 42. Cost ~70-85 ms per sampled frame (1080p ProRes, includes ffmpeg
  start), i.e. ~0.7 s per video finding. (These figures are v1; v2 see above.)
- Category waivers (blanket decision for a check class; NO fingerprint, so a changed picture
  is not noticed - that is the point of a blanket waiver): `cmr waive --category CHECK_ID
  [--class KIND] [--edge top|bottom|left|right | --channel C] [--frames A-B] --max-severity
  info|warn|fail --reason TEXT [--expires YYYY-MM-DD] [-o W.yaml]`. No REPORT argument and no
  media; `--category` excludes REPORT/--finding/--all-check, the scope flags are rejected
  without `--category`. CHECK_ID must be a registered `video.*`/`audio.*` check; `--edge` only
  for video, `--channel` (1-based) only for audio. Ids share the W sequence.
  Entry: `{id, type: category, check_id, [kind], [edge | channel], [frame_start, frame_end],
  max_severity, reason, created, [expires]}`. Matching (`waivers.category_match`): same
  check_id; finding has a frame range (metadata findings are never waived, as D17);
  INFO <= RAW severity <= max_severity (raw, so a verifier-lowered FAIL is still a FAIL, and a
  sharp blanking `edge` FAIL passes a dark_edge WARN ceiling); evidence[0] `kind`/`edge` equal
  when set; `channel` equals evidence `channel` or is in evidence `channels` (audio.clipping);
  with frames, the finding range inside [A-m, B+m] (same margin rule as per-finding). Order:
  per-finding waivers first, then category waivers on findings not WAIVED (a per-finding
  `content_changed` finding can be waived by a category; the per-finding waiver is still
  listed as content_changed). Waived finding: `lower_to(PASS)`, `waiver_ref`, `waiver` =
  {id, status: waived, type: category, reason, max_severity}. Counted under WAIVED, excluded
  from the exit code; matched nothing = stale; past expires = ignored + listed expired.
- Waiver file: `waiver_version: 2` adds `type: category` entries; `load` reads 1 and 2
  (category entries require 2); `save` writes 1 while the file has no category waiver, so older
  builds keep reading per-finding files. `waivers` summary entries (1.2) gain `type`
  (finding|category) and `count` (findings waived in the run); category entries also carry
  `kind`/`edge`/`channel`/`max_severity`. Console: one `WAIVED ... N finding(s) by category
  waiver Wnnnn (worst was X)` line per check and waiver, and one `category Wnnnn <scope>: N
  finding(s) waived: reason` line under WAIVERS; stale/expired category waivers print their scope.
- Slate findings (D27): `media_kind` `slate` for `slate.*`; fingerprint `slate_text` =
  [evidence kind, exact OCR text] (frames [], tolerance 0), computed from the finding itself (no
  decode) in `cmr waive` and in `apply`; see checks/slate/CLAUDE.md.
- Limits: a whole-frame dHash does not see a change of the defect itself (e.g. a 1 px blanking
  line becoming 4 px) when the picture stays the same; black/silent evidence fingerprints are
  constant (black is black). Audio envelope does not see timbre changes at equal level. Files
  with non-zero start_time, long-GOP real files and 5.1 seek accuracy: UNVERIFIED. Roadmap:
  moved content, reviewer identity, exact-hash mode, metadata waivers.

## Findings cap (report schema 1.2) - `report.cap_findings`, `cmr check --max-findings N`
- Per check id: after waivers, the problem findings (INFO..ERROR, not WAIVED) are ranked by
  severity (worst first), then frame; the first N are listed (in the usual report order: check,
  frame); the rest become ONE entry in `files[].suppressed`: `{check_id, count, limit,
  worst_severity, by_severity {SEV: n}, frame_start/frame_end/timecode_start/timecode_end}`
  (span of the suppressed findings). Console: `(75 additional video.blanking findings
  suppressed; worst: WARN)` after that check's lines; RESULT line adds "(N not listed but
  counted; --max-findings 0 lists all)".
- NOT ignored: `summary.counts`, `summary.worst`, file `worst` and the exit code are computed
  from ALL findings before the cap. `summary.suppressed` = total suppressed, `summary.max_findings`
  = cap in force (0 = unlimited), `summary.check_limits` = per-check profile overrides used.
- Always listed: PASS/SKIPPED results and per-finding WAIVED findings (each is a reviewed
  item). Findings WAIVED by a category waiver are never listed one by one (unless the check is
  unlimited): `files[].waived_by_category: [{check_id, waiver_id, count, worst_raw_severity,
  span}]`. Waived findings never count against the cap.
- Limit: CLI `--max-findings N` (explicit value wins for every check, 0 = all, the way to get the
  full list in JSON); otherwise profile per-check `max_findings`, else 10 (`cli.DEFAULT_MAX_FINDINGS`).
  `build_report(..., max_findings=0)` (library default) lists everything.
- `cmr waive REPORT.json` numbers only listed findings; suppressed findings cannot be waived
  per finding from a capped report (it prints a note: re-run with `--max-findings 0`, or use
  `--category`). Reports 1.0-1.4 are accepted.

## Verified ffmpeg/ffprobe facts (ffmpeg 6.1.1; other builds, e.g. Rocky, UNVERIFIED - run `cmr doctor`)
- `-filter_complex` with all branches ending in `nullsink` is rejected ("A filtergraph has
  zero outputs") -> engine labels and maps every branch to `-f null -`.
- `metadata=mode=print:file=F` format, `metadata=mode=add` (frame counter), signalstats in
  native bit depth (10-bit), split/asplit/ametadata options: `ffmpeg -h filter=...`.
- prores profile names (Proxy/LT/Standard/HQ/4444/XQ), `field_order=progressive`, missing
  `color_range` for ProRes-in-MOV, tmcd `timecode` tag, `eia_608` subtitle stream from SCC
  stream-copied into MOV: generated media + ffprobe JSON.
- Decode errors (corrupted ProRes) print on stderr at `-v error` with exit code 0 and no frame
  number.
- ProRes-in-MOV: no stream-level `color_range`, but `-show_frames -read_intervals %+#10` in the
  same ffprobe call reports `tv` on the first decoded frame (H.264 untagged reports nothing).
- SCC timestamps are taken as file time when stream-copied: a caption at 00:59:59:xx is beyond
  the file end and gets dropped (generator uses 00:00:00:01).
- libx264 `-flags +ildct+ilme -x264opts tff=1` -> ffprobe field_order interlaced (tt).
- MXF (also on 5.1.10; ffmpeg mxf muxer output + two real masters): timecode only as FORMAT tag
  `timecode` (media falls back to it, `MediaInfo.timecode_source`); `operational_pattern_ul`
  format tag (-> `MediaInfo.mxf_op`); no video `nb_frames` (frames from duration); PCM
  `channel_layout` absent; DNxHD `profile` 'DNXHD', no `bit_rate` -> the first-frames ffprobe
  call also reads `frame=pkt_size` (DNxHD class, see checks/CLAUDE.md). `-timecode` and
  `-c:v dnxhd -b:v 175M` / `mpeg2video -flags +ilme+ildct -top 1` write OP1a MXF test media.
- 436M VANC data stream: tag `data_type: vbi_vanc_smpte_436M`; `-select_streams N -show_packets
  -show_data -read_intervals %+#K` returns exactly K packets as a hexdump (`cmr/vanc.py`); real
  26 GB master 0.3 s (6.1.1) / 0.5 s (5.1.10), identical counts. A real master's first video
  frame also carried an MPEG-2 GOP timecode that differed from the format tag (not used).
- Pixel formats `yuvj*` -> base format + range pc in media.py (`pixel_format_raw` keeps the name).

## ffmpeg 5.1.10 (Rocky 9.7) vs 6.1.1 - measured differences
- Full test suite passes on both (97) after the test-media fixes below; engine command and
  metadata print format identical (full/edge/audio taps give the same records).
- ProRes colour: ffprobe 5.1 reports stream AND frame colour from the ProRes frame header,
  even when the header says unspecified (2/2/2) and the MOV `colr` atom says bt709; ffprobe
  6.1 falls back to the `colr` atom in that case (same file: 5.1 unknown, 6.1 bt709).
  So a render with tagged `colr` but untagged ProRes header gives video.color WARNs on 5.1
  only (whether Flame renders ever look like that: UNVERIFIED). Header bytes: `icpf` + 18.
- prores_ks on 5.1 writes header colour 2/2/2 despite `-color_primaries/-color_trc/-colorspace`
  (the `colr` atom does get them); `-bsf:v prores_metadata=color_primaries=..:color_trc=..:colorspace=..`
  sets the header on both versions (`ffmpeg -h bsf=prores_metadata` on both).
- Encoding with `-frames:v N` plus audio: 5.1 finalises the output when N is reached and drops
  queued audio (nondeterministic: 3072/7168/... of 19200 samples); 6.1 keeps it. Use `-t` or
  `trim=end_frame=N` instead (`ffmpeg -h filter=trim` on both). The engine never limits frames,
  so checks are not affected.

## Logging (D29) - `src/cmr/log.py`
- One file, LTX_OFX conventions: `$CMR_LOG_DIR/cmr.log`, else `$XDG_CACHE_HOME/cmr/logs/cmr.log`,
  else `~/.cache/cmr/logs/cmr.log`; `--log-file F` overrides. Rotated only at start (`log.setup`)
  when >= 10 MB, 5 old files (`cmr.log.1..5`, flock on `.rotate.lock`); never mid-run. Every line
  is one `os.write` on an O_APPEND fd, so concurrent `cmr` processes interleave whole lines.
- Line: `YYYY-MM-DD HH:MM:SS.mmm LEVEL comp[pid/tid] run=<6 hex> file=<8 hex|-> | msg` (local
  time, LEVEL padded to 5: `DEBUG INFO  WARN  ERROR`; comp = module (`cli`, `engine`, `media`,
  `waivers`, `report`, `deliverables`, `runmode`, `slate`) or the check id; run = random per
  invocation; file = salted hash of the resolved path of the file being checked). Tracebacks
  follow on the next lines.
- Levels `--log-level debug|info|warn|error|off`, env `CMR_LOG_LEVEL`; default from ONE constant
  `log.LOG_DEFAULT_ENABLED` (True = INFO now; planned flip to OFF by default). The options are
  accepted before or after the subcommand. `cmr logs path|tail` do not write to the log.
- Never breaks a run: an unwritable log dir prints ONE stderr line and logs WARN+ to stderr;
  handler/formatting errors are swallowed; without `setup()` (library use, tests) the `cmr`
  logger is silent (NullHandler, no propagation). Tests set `CMR_LOG_DIR` to a temp dir (conftest).
- Privacy, two modes. Default: `log.redact(kind, value)` (THE helper; use it for every private
  value) returns a token = salted sha256 prefix + non-private facts: `<path#1a2b3c4d.mov>`,
  `<text#.. len=14>`, `<row#..>`, tool paths `<dir#..>/ffmpeg` (system bin dirs verbatim). The
  salt is per installation (`<logdir>/salt`, 0600, never bundled), so tokens are stable across
  runs on one machine and differ between machines. Every redacted path/name/text/arg/user/host
  value (and a path's base name; >= 4 chars, not digits only; `log.SCRUB_KINDS`) is registered
  and scrubbed from the message part of every later line (stderr, tracebacks); `value`/`row`
  tokens are not (ordinary words, numbers); `$HOME` becomes `~`. `log.redact_quoted(msg)` redacts quoted values in finding/config-error
  messages and exception lines. Commands: `log.redact_cmd` (inputs, `-i`, `file=` in graphs).
  Private mode (`--log-private`, env `CMR_LOG_PRIVATE=1`): everything verbatim. The mode is the
  `env privacy:` line of every snapshot. Image/audio data is never logged. Limit: a value that
  was never redacted and is not quoted (e.g. an unquoted CSV value inside some exception text)
  would pass through; profile string params are therefore redacted wholesale at DEBUG.
- What is logged: `start` (redacted argv) and `end` (exit code, time, memory); environment
  snapshot (`env ...` lines: cmr version + git, privacy, log path/level, host, user, OS/kernel/
  glibc, Python, ffmpeg/ffprobe path + version, filter count + sha256 digest + missing required
  filters + plugin import errors, tesseract, CPUs, RAM, GPU via nvidia-smi, nice/ionice/
  RLIMIT_AS) for `check` and `doctor`; `check:` inputs line, `inputs:` profile/spec/job/waiver
  sha256, resolved profile per check at DEBUG, run mode/threads/limits; per file: identity/probe,
  media summary (extension, size, codecs, fps, streams, TC), timeline, timeline problems (WARN),
  checks enabled, tap layout (DEBUG: every tap chain), main pass short form (INFO) and full command
  incl. filtergraph (DEBUG), records parsed, decoded frames, memory (RSS/peak of cmr, peak of
  children via `/proc` + getrusage); per check `done in X s (records, detect, evaluate)`, candidates,
  findings by severity, finding detail (DEBUG, messages redacted), Skip, ConfigError, crashes with
  full traceback (ERROR; the report keeps the short "check crashed" finding); every external
  command via `log.run` (exit code, duration, first/last 5 stderr lines; WARN on failure or stderr
  output; full command at DEBUG): ffprobe x2, ffmpeg main pass, slate/bars seeks, waiver
  fingerprint seeks (DEBUG), tesseract, ionice, `-filters`/`-version` (DEBUG); slate OCR per
  frame (words, mean/min confidence, labels found/missing), per field text/length/confidence
  (DEBUG), uncertain reads (WARN); deliverables row selection (how, row number, filename groups,
  row values at DEBUG); waivers (loaded, per waiver at DEBUG, content changed INFO, fingerprint
  failures WARN, summary counts); cap decisions per check; report build/write; summary; an
  unhandled exception in any command (traceback, exit 3).
- Support bundle: `cmr logs collect [-o FILE] [--reports DIR [--last N]]` -> tar.gz (default
  `./cmr-support-YYYYmmdd-HHMMSS.tar.gz`): `logs/cmr.log*`, `environment.txt` (snapshot now,
  same privacy mode), `reports/NN_<name>.json` (newest N `*.json` under DIR, verbatim: reports
  hold paths and slate text). `cmr logs path`, `cmr logs tail [-n N]`.
- Debugging a slate problem: ask for `cmr check ... --log-level debug --log-private` then
  `cmr logs collect`; the log then shows the OCR text per field and frame, confidences, the
  selected CSV row and the compared values. In default mode the same lines show tokens and
  lengths only (equal tokens = equal text on that machine).
- Measured overhead (dev machine, 5 runs each, median): 96-frame test file 1.255 s off / 1.329 s
  info / 1.339 s debug; slate spot (short, OCR skipped) 2.984 / 3.063 / 3.077 s. INFO costs a
  fixed ~75 ms per invocation (snapshot subprocesses: ffmpeg/ffprobe -version, nvidia-smi, git,
  ionice), independent of file length; per-file logging is a few ms.

## macOS (D32) - Apple silicon; first real session 2026-10-26
Machine: Mac Studio M1 Ultra (16 performance + 4 efficiency cores, 128 GB), macOS 26.7.1, arm64,
/bin/bash 3.2.57. Everything in ONE folder (`~/cmr-mac`: Miniforge, env, repo copy, media), no
sudo, no shell rc changes; uninstall = delete the folder.
- Setup used for development (conda-forge only): Miniforge 26.9.4-0 `Miniforge3-MacOSX-arm64.sh`
  (sha256 85512f54...c760 = the versioned `Miniforge3-26.9.4-0-MacOSX-arm64.sh.sha256`; the
  unversioned `releases/latest/download/...sh.sha256` is a 404), `bash Miniforge3-...sh -b -p DIR`
  (`-h` lists -b -f -p -s -u -t -c; batch mode changes no shell files), then `conda create -p ENV
  --override-channels -c conda-forge python=3.12 pip pyyaml jsonschema pytest numpy pillow ffmpeg
  tesseract=5.5.2` -> python 3.12.16, ffmpeg 9.0.2 (gpl, x264), tesseract 5.5.2 / leptonica
  1.87.0, numpy 2.5.4, pillow 13.0.0. `CONDA_REGISTER_ENVS=false` keeps the env out of
  `~/.conda/environments.txt` (conda still rewrote that file's mtime once). ffmpeg 6.1 + tesseract
  5.5.2 cannot share one env (unsolvable on osx-arm64 and linux-64; 7.1 and 8.0 solve); the
  installer's separate ffmpeg-env gave conda-forge ffmpeg 6.1.2 on the Mac.
- Installer (bash 3.2 port, real run): see installer/CLAUDE.md.
- ffmpeg 9.0.2 osx-arm64: encoders prores_ks, prores_videotoolbox, libx264, h264_videotoolbox,
  dnxhd, mpeg2video, pcm_s24le; hwaccels videotoolbox, vulkan; 506 filters (Linux conda-forge
  9.0.2: 533), none of the 15 required ones missing. VideoToolbox is not used (D32 scout: decode
  is a small part of the pass).
- Platform branches (all guarded, Linux unchanged; tests fake `sys.platform`):
  `runmode.apply` background = nice 15 + 2 filter threads, NO RLIMIT_AS, no ionice, log
  `background mode applied: nice 15, 2 filter threads; no memory cap on macOS (RLIMIT_AS is not
  enforced there), I/O priority unchanged`; `log.log_dir()` = `$CMR_LOG_DIR`, else
  `$XDG_CACHE_HOME/cmr/logs`, else `~/Library/Logs/cmr` (Linux `~/.cache/cmr/logs`);
  `log.memory()` without /proc = `peak N MB` from `getrusage` (ru_maxrss in BYTES on macOS);
  snapshot line `env macos: 26.7.1 arm64, chip Apple M1 Ultra, 16 performance + 4 efficiency
  cores, Rosetta no` (`sysctl -n hw.memsize | machdep.cpu.brand_string |
  hw.perflevel0.physicalcpu | hw.perflevel1.physicalcpu | sysctl.proc_translated`,
  `platform.mac_ver()`; all verified on the Mac, `log.sysctl()` falls back to /usr/sbin/sysctl),
  `env limits: ... no memory cap on macOS (RLIMIT_AS not enforced)`. Engine thread default on
  macOS = min(8, `hw.perflevel0.physicalcpu`) (no SMT, so P cores take the place of "half the
  logical CPUs"); 8 on the M1 Ultra.
- Thread timing (`cmr check`, best of 2, 1440-frame 1080p ProRes HQ testsrc2, s; same clip and
  script on both machines; Linux = the 16-CPU dev machine):

  | profile | threads | Mac 9.0.2 | Mac 6.1.2 | Linux 6.1.1 | Linux 9.0.2 |
  |---|---|---|---|---|---|
  | blanking only | 1 / 2 / 4 / 8 / 16 | 4.1 / 4.2 / 4.6 / 5.7 / 7.3 | 4.5 / 4.6 / 5.2 / 6.3 / 8.4 | 4.5 / 3.5 / 3.3 / 3.6 / 6.1 | 3.3 / 3.5 / 3.8 / 3.2 / 5.6 |
  | blanking + black_runs | 1 / 2 / 4 / 8 / 16 | 22.3 / 18.0 / 15.8 / 15.7 / 17.1 | 24.7 / 19.4 / 16.8 / 16.6 / 18.7 | 29.8 / 22.5 / 14.6 / 13.3 / 14.1 | 27.7 / 20.8 / 18.2 / 14.7 / 14.8 |

  The default (8 on both) is within 1% of the best for the heavy case; small-strip taps prefer
  fewer threads on the Mac too. The Mac is ~15-20% slower than the dev machine at 8 threads.
- Cross-platform parity (`tests/suite/compare.py --strict`, 29 report cases: make_media cases,
  quality fast/high, MXF/Avid probe cases, slated spots clean/errors x review/delivery, inputs
  byte-identical, copied from Linux): Linux conda-forge 9.0.2 vs Mac 9.0.2 (both tesseract
  5.5.2) = ALL equivalent, slate OCR included; Linux 6.1.1 vs Mac 6.1.2 = equivalent except the
  ProRes decoder's corrupt-file messages (`ac tex damaged 2048, 2048` vs `3818, 1024`) and the
  slate cases (no tesseract on the dev machine); Mac 6.1.2 vs Mac 9.0.2 = equivalent except the
  slate TITLE on the errors spot (uncertain-read WARN vs FAIL: the OCR frame is converted to
  8-bit gray by ffmpeg, so OCR confidences depend on the ffmpeg build as well as on tesseract).
  Share meta tier (4 real files, background mode): Linux 6.1.1 vs Mac 9.0.2 all equivalent.
  Decoded video is bit-identical across all four builds (md5 of the decoded stream).
- ffmpeg >= 7 differences found (fixed): `ffmpeg -filters` has 2 flag columns from 8.0 (parser
  accepts both; before the fix every filter read as missing -> all content checks ERROR);
  ffmpeg 9 removed `-top` (test generator uses `setfield=tff`); ffmpeg >= 7 converts untagged
  frames to the output's `-colorspace` (test generators now tag frames with `setparams`; decoded
  media identical to 6.1 output). The waiver dHash v1 chain
  (`scale=9:8:flags=area+accurate_rnd,format=gray`) gives different gray values per build: dim
  stripes at Y 23 read 0/8 on Linux 6.1.1 and Mac 9.0.2 but all 0 on Linux 7.1.1/8.0.1/9.0.2.
  FIXED by the v2 video fingerprint (luma plane via `extractplanes`, reduction in Python; see
  Waivers): identical bits on 6.1.1 / 7.1.1 / 9.0.2; v1 is kept only to match old waiver files.
- File names: APFS keeps names as created (an NFD name lists as NFD) and opens NFC and NFD
  spellings alike; case-insensitive by default. `MediaInfo.name`/`.stem` = the stored folder
  entry (`media.disk_name`: exact, else the single entry equal ignoring case and normalisation)
  in NFC: `cmr check showname_101...mov` on a file stored as `SHOWNAME_101...` gave a false
  filename FAIL before. NFC (`cmr.textnorm.nfc`) at ingestion: spec/job YAML, slate templates,
  deliverables CSV headers/cells, OCR words, waiver text fingerprints. Paths used to open files
  are never changed.
- TMPDIR: macOS temp is `/var/folders/../T/` (safe). A temp folder whose path is unsafe for a
  filtergraph `file=` (e.g. a space) now falls back to /tmp (was: every content check ERROR).
- Log: rotation at start + flock and two concurrent processes verified on APFS; default log
  `~/Library/Logs/cmr/cmr.log`; `cmr doctor` OK. The `gpu:` line says "none found
  (nvidia-smi absent)" on a Mac (cosmetic).
- The media share (SMB, mounted by the user): readable over ssh without a TCC prompt;
  `cmr probe` 1 s. Whether a LaunchAgent/Terminal run needs a Network Volumes permission: UNVERIFIED.
- Run tests on the Mac: `PATH=<env>/bin:/usr/bin:/bin:/usr/sbin:/sbin CMR_TESSERACT=<env>/bin/tesseract
  CMR_TEST_MEDIA=<dir> PYTHONPATH=src <env>/bin/python -m pytest -q tests`; installer tests with
  the real bash 3.2: add `CMR_TEST_BASH=/bin/bash`. Results 2026-10-26: 479 passed, 6 skipped
  (shellcheck, 5 bundle builds that need /usr/bin/python3 with setuptools) on ffmpeg 9.0.2 with
  Mac-made and with Linux-made media, and on ffmpeg 6.1.2; ~5.6 min.
