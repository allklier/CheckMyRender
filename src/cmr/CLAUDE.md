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
- `Tap(name, stream, chain, keys=())`: `chain` is an ffmpeg filter chain with NO metadata
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
  `tc(frame) -> "HH:MM:SS:FF"`.
- `ctx.passinfo` (`PassInfo`): `ran`, `returncode`, `stderr` lines, `decoded_frames`
  (engine counter tap on the first video stream), `command`.
- Raise `Skip("reason")` -> SKIPPED. Raise `engine.ConfigError("...")` for unusable
  params/spec values -> ERROR "configuration error: ...". Any other exception -> ERROR
  finding ("check crashed").
- `-filter_complex_threads`: `engine.default_filter_threads()` = min(8, cpu_count//2);
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
  (0 with `--fail-on fail`), 2 fail, 3 ERROR / config error.
- Profile check entry: reserved keys `enabled` (default true), `severity` (default fail),
  `quality` (fast|high, default high), `max_findings` (report cap, see "Findings cap"; not
  passed to the check; `engine.RESERVED_KEYS`); everything else is params. Spec values reach checks only via `${spec.*}` refs in the profile
  (whole-value refs keep type; pass whole sections like `timing: "${spec.timing}"` when a
  check needs optional keys).

## Waivers (D17) - `src/cmr/waivers.py`, report schema 1.1 (1.2: category waivers, cap)
- CLI: `cmr check ... --waivers W.yaml`; `cmr waive REPORT.json` (lists waivable findings with
  their number N = 1-based position over all findings of all files in the report),
  `cmr waive REPORT.json --finding N ... | --all-check ID --reason TEXT [--expires YYYY-MM-DD]
  [-o W.yaml]` (default `./waivers.yaml`; appends, ids W0001...). The media path is the report's
  `files[].path` (relative to the cwd of `cmr check`); its identity (size + head/tail sha) must
  equal the report's, else error. Waivable: `video.*`/`audio.*` findings with a frame range and
  stream, severity INFO/WARN/FAIL. Metadata/file-level findings are not waivable in v1.
- File: `waiver_version: 1`, `waivers: [{id, check_id, stream, [edge, kind, channel],
  frame_start, frame_end, timecode_start, timecode_end, message, fingerprint: {type, algorithm,
  version, frames, values}, reason, created, [expires]}]`. No reviewer identity (v1).
- Fingerprint: up to 8 (video) / 16 (audio) evenly spaced frames of the range incl. first and
  last, one ffmpeg seek per frame AFTER the main pass, only for findings that location-match a
  waiver (cmr check) or are being waived (cmr waive). Video `video_dhash`: `-ss (f-0.5)/fps -i F
  -map 0:S -frames:v 1 -vf scale=9:8:flags=area+accurate_rnd,format=gray -f rawvideo -` -> 72
  bytes -> 64-bit dHash (bit = left > right). Audio `audio_envelope`: `-ss f/fps -i F -map 0:S
  -t 1/fps -f f32le -c:a pcm_f32le -` -> per channel [peak, RMS] dBFS, 0.1 dB, floor -90.
  Distance: max Hamming (video) / max |dB| (audio) over the sampled frames; other
  type/algorithm/version/frames = not comparable = content changed.
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
  start), i.e. ~0.7 s per video finding.
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
  `--category`). Reports 1.0/1.1/1.2 are accepted.

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
