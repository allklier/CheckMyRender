# CheckMyRender - render QC tool

Idiot-check for renders out of Flame and other apps: speeds up delivery while not missing errors.
CLI-first, scriptable, batch mode with a report. Python, ffmpeg/ffprobe backend. Linux (Rocky) first,
macOS follows as fits.

This file is the overview and the **decision index**. Numbered decisions (D1, D2, ...) are referenced
from code comments and docs; keep the numbers stable (add new ones, supersede old ones, never renumber).
Detail lives next to the code (loaded by Claude Code when files in that folder are touched):

| File | Covers |
|---|---|
| `src/cmr/CLAUDE.md` | core: check-plugin interface, engine, time model, verified ffmpeg facts |
| `src/cmr/checks/CLAUDE.md` | the checks: metadata, black runs, audio, quality modes, known limits |
| `src/cmr/checks/blanking/CLAUDE.md` | the blanking detector: method, tuning knobs, measurements |
| `profiles/CLAUDE.md` | profiles (rules), review vs release |
| `examples/CLAUDE.md` | spec sheets (job data) |
| `tests/CLAUDE.md` | test media generation and how to run tests |

Machine-specific notes (hosts, paths, accounts, status) go in the git-ignored `CLAUDE.local.md`.
Tags: **DECIDED** (user approved), **PROPOSED** (recommended, not approved), **OPEN** (needs a decision),
**UNVERIFIED** (not confirmed against a real run). Only the main thread edits this file.

## Status
**0.0.1 (in progress).** Core and first checks exist and pass the test suite on *generated* media only;
nothing is validated on real renders yet. See D22 for versioning.

## Requirements
- Configurable checks (YAML), two families: **consistency** (metadata vs spec) and **content** (pixels/audio).
- Today: codec/resolution/pixel format/fps/scan/colour tags, frame count, head/tail layout, start timecode,
  audio format and layout, filename convention, captions present, decode errors, blanking, black runs,
  loudness + true peak, silence, silent channel, clipping.
- Every problem is reported with timecode and frame number.
- False-positive reduction with ML for blanking and freeze/audio-level checks (D9) - not built yet.

## Architecture
    cli -> config loader -> planner -> one ffmpeg pass per file -> checks (plugins) -> findings -> reporters
Probe once per file; checks declare filter taps, parse per-frame output, then judge candidates.
Reporters: console and JSON today; HTML/JUnit later. Details: `src/cmr/CLAUDE.md`.

## Decisions
**D1 Platform [DECIDED].** CLI-first, scriptable, batch mode with report, no GUI. Linux (Rocky) first with an NVIDIA GPU assumed; macOS follows as fits and must not constrain the design.

**D2 Stack [DECIDED].** Python (pyproject requires >=3.12; Rocky's Python version UNVERIFIED), ffmpeg/ffprobe as media backend, PyYAML + jsonschema, argparse, pytest, installed in a venv. **Production Python policy:** never update or replace the system Python on production machines; use Anaconda environments for other Python versions (conda-based Python 3.12 env, not a system package). mediainfo optional, add only if ffprobe lacks something. Custom decode code only if profiling shows ffmpeg is the bottleneck. GPU: ProRes has no NVDEC decoder in this ffmpeg, so ProRes decode is CPU; GPU is for ML.

**D3 Profiles and spec sheets [DECIDED].** Profile = rules (which checks, tolerances, severities, quality); spec sheet = data for one job. Profiles reference spec values as `${spec.*}`. Profiles may `extends` another. Profiles: `final` (base), `review`, `release`. YAML for authored config, JSON for reports.

**D4 Substitution rules [DECIDED].** Whole-value refs keep their type; a missing spec key is a config error (exit 3), never an empty string; no expression language; resolve after `extends`; detect cycles; mappings deep-merge, lists/scalars replaced by the child. Optional spec keys reach checks by passing whole sections.

**D5 Formats [DECIDED].** Mostly file codecs (ProRes, H.264); EXR/DPX sequences possible: media abstraction covers file and sequence, sequences are checked as a unit (count, gaps, naming, header consistency), content checks still decode all frames. Sequence input is post-v1.

**D6 Time model [DECIDED].** fps is an exact rational (`24000/1001`), all positions are integer frames, own DF/NDF timecode code. Duration is frame count, not wall clock. Problems are reported as timecode + frame; minutes/seconds only for program-length limits (e.g. program < 90 min); ad spots use exact frame counts. Exception: decode errors carry no frame number.

**D7 Head/tail layout [DECIDED].** The spec lists strictly sequential video segments from file frame 0 (`head_layout`, `tail_layout`: slate, black, bars_tone, two_pop, program); head length is set in the spec; the loader rejects layouts outside the file. Program normally starts at 01:00:00:00; `file_start_timecode` is optional (derived). v1 checks layout positions, not segment content. 2-pop audio detection and slate content comparison: roadmap.

**D8 Severity and exit codes [DECIDED].** PASS < SKIPPED < INFO < WARN < FAIL < ERROR (ERROR = tool/config failure, distinct from FAIL). The profile sets severity per check, so a problem can be info-only. Exit 0 pass, 1 warn, 2 fail, 3 error; `--fail-on`.

**D9 Two-stage content checks and ML [PROPOSED; ML not built].** Stage 1 is a loose, high-recall detector emitting Candidates; detection is separate from the verdict. An optional ML verifier judges defect vs intended (blanking, freeze frames) and returns a defect probability, with thresholds in the profile. The verifier may only LOWER severity, never raise it; the finding keeps `raw_severity` and the verifier decision/model hash. Tuning loop: reviewers label findings; labels guide thresholds and later training. Until a model exists the verifier is a no-op. ML choice and licences: OPEN.

**D10 Engine [DECIDED].** One ffmpeg process per file for all video and audio taps; parallelism per file. Frame numbers come from exact integer pts x time base (printed seconds have only 6 digits). Taps print only the keys they need. `-filter_complex_threads` defaults to min(8, cpus/2), override `--filter-threads`. Checks raise `ConfigError` for config problems.

**D11 Check plugin interface [DECIDED].** `taps()` + `detect(records) -> Candidates` + `evaluate(candidates) -> Findings`; plugins auto-discovered from `cmr/checks/`, a plugin that fails to import becomes ERROR findings. Candidate/Finding/report schemas are versioned. Contract: `src/cmr/CLAUDE.md`.

**D12 Blanking method [DECIDED, tuned on one real H.264 file; ProRes UNVERIFIED].** Own edge statistics, not cropdetect; one filter chain per edge in the single pass (`crop exact=1` + `format=yuv444p` mandatory: signalstats segfaults on 1px-wide 4:2:2 crops). Classes: sharp **`edge`** (a black outer line with a sharp step to picture: 1px if the next pixel is >= `min_step` 6 brighter; 2-16px bands if outer <= `matte_luma` 20 with a sharp inner step), **`dark_edge`** (the old rule: dark outer line, picture brighter inward by `min_contrast` 4; this is what dark curtains/floors/audiences produce, see D25) and `matte` (outer and inner both black: letterbox/pillarbox, persistent flag). Priority per frame edge > dark_edge > matte. `dark_edge` and `matte` hits on one edge merge when closer than `merge_gap_frames` (250); sharp `edge` hits never merge so they stay frame-exact. Real file (97 min stage play): 302 hits in v1 fast became 0 sharp edges + 70 dark_edge + 28 matte after merging; planted 1px/partial/band defects on real texture: 126/129 found as sharp `edge`, 129/129 found in some class; synthetic 35/35. Cannot be separated: a true black 1px line next to picture at luma <= ~22 (step under 6) shows as `dark_edge`/`matte`. Runtime is higher than v1 (blanking+black_runs 13 -> 19 s per 1750 frames high, 2.1 -> 3.2 s fast). Detail: `src/cmr/checks/blanking/CLAUDE.md`.

**D13 Blanking coverage [DECIDED].** A defect covering >= `coverage` (default 0.5, profile-tunable) of an edge line is detected, because a wrong mask can blank only part of a line.

**D14 Quality modes [DECIDED].** `quality: fast|high` per check (default high); unsupported mode is an ERROR; findings record the mode. Blanking fast samples every `fast_frame_step`-th frame (default 5; defects >= 5 frames are always seen, shorter may be missed; sampled ranges are approximate). black_runs fast measures a quarter-resolution picture of every frame. `review` runs fast, `release` runs high. Detail: `src/cmr/checks/CLAUDE.md`.

**D15 Loudness [DECIDED].** BS.1770 relative-gated loudness and true peak over the program range only; reports say "not dialogue-gated". Standard presets (EBU R128 -23 +-1 LU / -1 dBTP, ATSC A/85 -24 +-2 / -2 dBTP) live in the profile; the spec only names the standard and program track. Dialogue-gated loudness is roadmap (meter OPEN: licensed Dolby measurement vs speech-detection approximation, which must not be reported as certified).

**D16 Other audio checks [DECIDED].** Silence/dead zones, silent channel and clipping are measured per frame over the program range; thresholds in the profile.

**D17 Waivers [DECIDED; v1 built].** Reviewed findings can be waived so later renders do not re-flag them. A waiver records check id, stream, evidence `edge`/`kind`/`channel`, frame range (+ timecodes), a **perceptual fingerprint** and a reason (no reviewer identity). Fingerprints: video = 64-bit dHash of up to 8 evenly spaced frames in the range (distance = largest Hamming distance, tolerance `waivers.video_max_bits` 10); audio = per-channel peak/RMS level envelope over up to 16 frames (tolerance `waivers.audio_max_db` 2.0). Tolerances and `margin_frames` (5) live in the profile (`waivers:`). Matching (`cmr check --waivers FILE`): same check/stream/locator, finding range inside the waiver range +- margin (or the fast-mode boundary uncertainty), new file fingerprinted at the waiver's frames. Within tolerance = WAIVED (severity lowered to PASS, `raw_severity` kept, `waiver_ref` and `waiver{}` set, counted under `counts.WAIVED`, shown, excluded from the exit code); same place but changed content = stays flagged with "previously waived (Wnnnn), content changed - re-review"; unmatched = listed stale; past `expires` = ignored and listed. Create with `cmr waive REPORT.json [--finding N | --all-check ID] --reason TEXT [--expires DATE] [-o FILE]` (default ./waivers.yaml). Report schema 1.1 (additive). Only video/audio findings with a frame range can be waived (metadata/file-level waivers: roadmap); waivers apply to every file in a run (not tied to a filename). **Category waivers:** `cmr waive --category CHECK_ID [--class KIND] [--edge E | --channel C] [--frames A-B] --max-severity info|warn|fail --reason TEXT [--expires DATE]`. A blanket decision with NO fingerprint (a changed picture is not detected); the REQUIRED ceiling compares against the raw severity, so a sharp-edge FAIL still gets through a WARN ceiling. The check still runs; covered findings are WAIVED and shown as ONE line per waiver with a count; a waiver that matches nothing is stale. Applied after per-finding waivers; only findings with a frame range (never metadata). Waiver file version 2 only when a category waiver exists. Known limits: whole-frame dHash, so a defect that grows on an unchanged picture stays waived; a timbre change at the same level is not noticed; cost ~0.7 s per video finding. Roadmap: matching content that moved in time, reviewer identities, exact-hash mode, metadata waivers.

**D18 Reproducible reports [DECIDED, partly built].** Stamp tool/ffmpeg versions, profile hash and file identity (size + sha256 of first and last MiB); stable ordering. `cmr doctor` checks the installed ffmpeg and filters (Rocky build UNVERIFIED).

**D19 Stills [DECIDED; not built].** Timecode is the primary locator (users can jump to it in their app). Stills are off by default with one global switch to disable entirely; when on, only for findings where a still is faster than seeking (e.g. visual look/colour, slate), capped per file; never for audio, metadata or black runs.

**D20 Filename [DECIDED].** An explicit `job.filename` wins over `job.naming_pattern`. Placeholders come from spec job fields verbatim (use a filename-safe `title_code`), then profile `filename.tags` (`fps_tag`, `format`, `audio_tag` mappings).

**D21 Captions, watermarks, A/V sync [DECIDED].** v1 checks embedded captions only; sidecars (SCC/SRT) need a delivery-package input: roadmap, as is caption quality. Watermark check detects accidental watermarks only (no reference image): not built. A/V sync v1 = container offset/duration check; ML lip sync is roadmap.

**D22 Versioning and releases [DECIDED].** 0.0.1 = design shared, code not yet tested on real renders (that is the correct label for now). 0.1 = first verified code. Annotated tags `vX.Y.Z`; GitHub releases are marked pre-release until then. Remote: SSH (`git@github.com:allklier/CheckMyRender.git`).

**D23 Colour tags and value mapping [DECIDED].** Untagged colour is `unknown`, reported at the profile's `unknown_severity` (default warn). ffprobe gives no stream-level `color_range` for ProRes-in-MOV, so the first decoded frame fills missing fields (marked `color_source`). A value-mapping layer translates ffprobe vocabulary to spec vocabulary (e.g. `HQ` -> "422 HQ").

**D24 Repo documentation and process [DECIDED].** Per-folder CLAUDE.md files hold the detail (table above); no separate reference docs/ folder (docs/ holds design drafts and sample material only). Local machine details go in git-ignored `CLAUDE.local.md` (verified in the official memory docs: it loads alongside CLAUDE.md, appended after it, and must be added to .gitignore by hand). Agents keep a live status file in `.agent-status/` (gitignored, deleted at finish); lasting findings go in the folder CLAUDE.md of their area; only the main thread edits the root CLAUDE.md. TODO: split `src/cmr/checks/CLAUDE.md` (~240 lines) per check or check group so no folder file gets too long.

**D25 Dark picture edges [DECIDED].** Edges that are dark because the picture is dark (curtains, black floors, dark houses) are a separate blanking class (`dark_edge`), not a sharp digital-blanking defect. Whether they are reported is a rule, so it is a PROFILE param of `video.blanking`, not a spec key: `dark_edge_mode: flag | info | hide` (default `flag`; `review` profile sets `info`; `hide` lists nothing but keeps a count and frame ranges in one summary finding). Reviewed hits are cleared with waivers (D17). First real-file test (stage-play recording, 97 min): 302 blanking WARNs, all dark picture content that the user judged fair to flag. (An earlier draft put this setting in the spec; reversed to keep rules in profiles, per D3.)

**D26 Run modes [DECIDED].** `cmr check --mode foreground|background`, default **foreground**. Foreground = shortest time to QC, no limits (engine default threads). Background = does not disturb other apps: CPU nice 15, idle I/O priority (`ionice -c 3`, Linux only), 2 ffmpeg filter threads, 16 GB virtual-memory cap (`RLIMIT_AS`, not enforced on macOS); inherited by the ffmpeg/ffprobe children (verified). An explicit `--filter-threads` overrides the mode's thread count. Constants live in `src/cmr/runmode.py`. Not recorded in reports yet.

**D28 Findings cap [DECIDED].** Noisy reports do not help, nothing is ignored: after N findings of one check the rest are suppressed from the body. Default 10 per check; `--max-findings N` (0 = unlimited; overrides every check) or per-check profile key `max_findings`. Order: waivers first (waived findings do not count against the cap), then per check the worst severity first then by time; the remainder is one summary entry `(75 additional video.blanking findings suppressed; worst: WARN)`. Summary counts, worst severity and the exit code are computed from ALL findings before the cap; PASS/SKIPPED and per-finding-waived findings are always listed. Report schema 1.2 (`files[].suppressed`, `waived_by_category`). A capped report cannot waive suppressed findings one by one (use `--max-findings 0` or a category waiver).

**D27 Slate check [PROPOSED; design agreed in discussion, not built].** Full design in `docs/slate-design.md` (docs/ holds design drafts and sample material only; sample: docs/sample_slate.png). Summary: OCR two frames of the slate segment (2nd and 2nd-to-last, countdown ignored; they must agree); rows keyed by LABEL (line count as alternative); expectations are tokens over `job.yaml`, a CSV deliverables row (selected by the actual filename; every row value from the SAME row) and filename parts; the filename is authoritative once the slate TITLE matches it, and AD-ID in row/filename/slate must agree (wrong slate is the likelier error); values the slate claims (duration, audio, resolution, codec ...) are verified against the actual media; colour and grey bars are measured for elevated black / wrong hue; templates per user/job; fields that may be empty must be declared; date policy flexible. OCR engine to be chosen by testing on the sample.

## Roadmap
Freeze frames (two-stage with ML, D9), timecode track continuity, basic legal levels / full gamut, duplicate/dropped frames, visual colour-space sanity (log-looking footage), banding, A/V sync offset, watermarks (accidental), 2-pop detection, slate vs reference sheet, caption sidecars and caption quality, dialogue-gated loudness, stems and 8-channel layouts, lip-sync ML, phase/polarity, DC offset, field order/cadence, pillarbox allowance, dual-track detection, bars/tone level, checksum sidecar, act-break black, waiver matching of moved content + reviewer identities (D17), stills (D19), HTML/JUnit reports, image sequences (D5), batch/watch-folder. Deferred/out: PSE/flash.

## Open questions
- ML model choice, training data and licences (blanking/freeze verifiers, lip sync).
- Dialogue-gated loudness meter.
- Rocky: Python version and ffmpeg build/filters (UNVERIFIED; run `cmr doctor`).
- Real-render tuning of blanking and black-run thresholds.
