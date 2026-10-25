# Check interface (contract for plugin authors)

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
  `params` (profile params after ${spec.*} substitution, without `enabled`/`severity`),
  `severity` (profile severity for a problem, default FAIL), `timeline`, `passinfo`.
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
  black_runs dominates: full-frame `format=yuv444p,signalstats` ~19 s/1440 frames standalone
  (signalstats alone 11.9 s) - owner may want a cheaper chain.
- Engine fills `timecode_start/end` from `frame_start/end` for every finding; reports show
  TC + frame. Frame ranges are INCLUSIVE.
- Candidate: `{check_id, stream, frame_start, frame_end, score, evidence[]}`.
- Finding: `{check_id, severity, raw_severity, message, stream, frame_start, frame_end,
  timecode_start, timecode_end, expected, actual, candidate, evidence[], verifier, waiver_ref}`.
- Verifier hook: `engine.VERIFIERS[check_id] = fn(candidate, ctx) -> {"decision", "p",
  "model_hash", "severity"}`; applied to findings that carry a `candidate`; the engine calls
  `Finding.lower_to()` so a verifier can only LOWER severity. Empty in v1.
- Severities: PASS < SKIPPED < INFO < WARN < FAIL < ERROR. Exit: 0 pass, 1 warn
  (0 with `--fail-on fail`), 2 fail, 3 ERROR / config error.
- Profile check entry: reserved keys `enabled` (default true), `severity` (default fail);
  everything else is params. Spec values reach checks only via `${spec.*}` refs in the profile
  (whole-value refs keep type; pass whole sections like `timing: "${spec.timing}"` when a
  check needs optional keys).
