# slate checks (D27)

Package `src/cmr/checks/slate/`. Design and user decisions: docs/slate-design.md (untracked
draft) and the root CLAUDE.md D27; OCR spike: docs/slate-ocr-spike.md. Check interface:
../../CLAUDE.md. Examples: ../../../../examples/spot_xr/ (fictional job), profiles `spot_*`.

| check | what | default severity |
|---|---|---|
| `slate.present` | head_layout has a `slate` segment (else SKIPPED = unslated variant); every required template label found on both frames read | check severity |
| `slate.consistency` | both frames agree per field (countdown ignored); low-confidence side = uncertain | check severity / uncertain |
| `slate.content` | each field vs its expectation (job, CSV row, filename); TITLE vs actual filename; AD-ID row/filename/slate; DATE policy | check severity, `field_severity`, date severity |
| `slate.claims` | DURATION/LENGTH (first `duration` field) vs program frames, AUDIO vs the audio stream, resolution/codec/fps tokens in TITLE and filename vs probe | check severity |
| `slate.bars` | 8 colour bars + 8 grey steps in native Y'CbCr vs BT.709 targets | check severity |

All declare `qualities = ("fast", "high")` (D14) with identical behaviour (they read 2 frames).
No taps: everything runs in `evaluate()` after the main pass by seeking (`reader.py`), so a
profile with only slate/metadata checks decodes 2 frames, not the file.

## Files
- `template.py` slate template YAML (schema `template.SCHEMA`, cached by path+mtime, `${spec.*}`
  substituted with the spec at load time).
- `ocr.py` tesseract adapter (`tesseract IMG stdout --psm 3 -l eng tsv`, env OMP_THREAD_LIMIT=1,
  timeout `ocr_timeout` 60 s), TSV parser, `pair()` -> `pair_aligned()` (port of the spike's
  pairing) or `pair_colon()` (template `layout: label_colon`).
- `reader.py` frame choice, PNG decode by seeking, OCR + pairing, per-file cache (shared by the
  checks: key = path, size, mtime, frames, tesseract binary, psm, lang, template).
- `compare.py` field-type-aware comparison. `base.py` shared params schema + finding helper.
- `present.py`, `content.py`, `claims.py`, `bars.py` the checks.

## Inputs (job file, CSV, template)
- `cmr check FILE --spec S --job job.yaml [--deliverables CSV] [--row VALUE]`. `config.build_spec`
  merges the job file (sections `job`, `deliverables`, `slate`; schema `schemas.JOB_SCHEMA`) into
  the spec; a key set in both with different values is a config error (never typed twice).
  `deliverables.csv` and `slate.template` are relative to the file that sets them. `${spec.*}`
  in the job sections is resolved at load time (D4); `{row.*}`, `{file.*}`, `{job.*}` stay for
  run time (D20 style). The report's `spec` gets `job: {path, sha256}`.
- Blank value (job field or CSV cell) = not specified = not checked. `"<empty>"`
  (`deliverables.EMPTY`) = the slate field must be empty.
- Deliverables CSV (`cmr/deliverables.py`): one row per spot, no filename column;
  composite key `key: [ad_base, length]` (each column = the same-named `filename_regex` group,
  all must equal; for filenames like base + length + H, `AB002`+`15`+`H`). Key types per part
  (`deliverables.key_parts`): `key_type` (a type for a single key, or a mapping column -> type;
  a single type with a composite key = config error), else by name: `ad_id`/`adid`/`ad_*` ->
  ad_id, `length`/`duration` -> duration (`:15` = `15` = `15s`), else text. ad_id drops a trailing
  H/D from a 12-char Ad-ID and from a short house id (< 11 chars, digit before the H/D, e.g.
  `AB00215H`); an 11-char Ad-ID is kept whole. An optional key group that did not match the
  filename (`AB003H` has no length) is not compared; the other parts must then select one row
  (else config error naming --row). `--row VALUE` without `=` needs a single key (composite ->
  config error: use COLUMN=VALUE or #N). Composite key parts are each compared row vs filename
  (`kind: row`). A 2-digit filename code that is NOT the length (31 = second :30) needs its own
  CSV column as key part; compare the slate length with `{row.length}` then. `columns`
  maps canonical names to headers (every header is also available normalised: `Air Date` ->
  `air_date`). Row matching per job, `deliverables.match`:
  `key` (A: regex group named like the `key` column; `key_type: ad_id` (default when the key is
  `ad_id`) ignores a trailing H/D), `generated_name` (B: the row whose `job.naming_pattern`
  renders to the actual filename), `both` (key selects, the `filename` check verifies the
  generated name), `fields` (C, default without a key: `match_fields` or all regex groups that
  are columns, compared loosely; must select exactly ONE row else config error naming
  `--row`). `--row`: key value, `COLUMN=VALUE`, or `#N` (1-based). Ambiguous key/name = config
  error. No row for a key / name / regex mismatch = FAIL finding (`kind: row`), row fields then
  not checked.
- `filename` check (D20 extended): `naming_pattern` accepts `{row.x}`, `{file.x}`, `{job.x}`
  (bare `{x}` = job field, then profile tag, as before). Needs profile param
  `deliverables: "${spec.deliverables}"` when the pattern uses row/file tokens.
- Template: `layout` (`aligned` default | `label_colon`), `date_formats` (list of
  `compare.DATE_FORMATS`; wins over the profile `date.format`: the slate style knows its
  format), `fields` {NAME: {aliases, type, optional, line, expect}}, `countdown` box
  (fractions; words centred inside are dropped before pairing), `bars` {colour, grey}. Job
  `slate.expect` overrides `expect` per field (null/blank = not checked; unknown field = config
  error).

## Reading and pairing
- Frames: `slate` segment of `timeline.segments` (new: Timeline carries head/tail segments with
  file frame ranges); 2nd and 2nd-to-last frame (one frame for a 1- or 3-frame segment).
  Profile param `split_tolerance_frames` N (all OCR checks; default 0 = strict): the real
  slate/black split may be up to N frames EARLIER than the spec says (real XR: spec 120 + 48,
  file 119 + 49; only the program start at frame 168 matters, which timing.layout/frame_count
  and slate.claims see through the spec program range); the last frame read moves N frames
  earlier (never before the first). A longer real slate needs no tolerance. Set it on every
  slate.* OCR check (same frames = one OCR via the cache).
- Decode: `ffmpeg -ss (f-0.5)/fps -i FILE -map 0:S -frames:v 1 -pix_fmt gray OUT.png` (the
  waiver seek; verified locally that frame 148 of the test spot decodes with countdown 1 and
  the late text). `-pix_fmt gray` (8-bit, `ffmpeg -h long`: `-pix_fmt format`; `ffmpeg -h
  encoder=png` lists gray on 6.1.1 and 5.1.10): the default for 10-bit sources is a 16-bit
  rgb48be PNG, on which tesseract misread the first slate line of 4 of 5 real DNxHD slates
  (`mereley`, conf 0-19, and `LGO004` for the base Ad-ID); from 8-bit gray all 5 read right.
- tesseract binary: profile param `tesseract`, else env `CMR_TESSERACT`, else `tesseract` on
  PATH. Missing -> ERROR "slate not read: tesseract not found ..." on each OCR check (never a
  crash); `slate.bars` does not need OCR and still runs. `cmr doctor` prints the binary.
- Pairing (spike algorithm): junk words (no letter/digit, conf < 50) dropped; lines by vertical
  centre (0.5 x height); segments split at gaps > 1.5 x line height; labels = segments whose
  normalised text (A-Z0-9) is a template alias AND whose right edge is on the median label right
  edge (+-1 label height); value column = median left edge of the nearest segment right of the
  label-column lines; a value is the segment on the label's line (+-0.6 h) starting within
  1.5 h of the value column, else "" (label found, value empty). Absent key = label not read.
  `line: N` = N-th line of the label column (needs other labels to locate the column).
- `layout: label_colon` (`pair_colon`, "Label: value" slates; real XR deliveries): lines and
  segments as above; a label = the text of a segment up to its FIRST colon (up to 4 words,
  e.g. `Air Date:`) that is a template alias; the value = the rest of that segment after the
  colon (tight `Ad-ID: X`, or `Ad-ID:X` read as one word) or else the NEXT segment on the same
  line (tab-stop value column). Values keep colons and slashes (`Kickoff: Night 1/2`). A label
  followed by nothing or by another label -> "" (empty value). A whole segment equal to an alias
  without a colon (colon not read) is a label too, value = next segment. An unknown `X:` label
  owns its next segment, so a value like `Date: x` there is never taken for a label. No
  alignment rule (left- or right-aligned labels, two label columns side by side). First
  (top-most) occurrence of a field wins. `line: N` = N-th line whose first segment starts on
  the label column's left edge (median x0 of line-leading labels +- 1 label height); value =
  text after the colon, else the next segment. Not handled: a label whose colon was not read
  AND whose value follows in the same segment (`Ad-ID AB002`); a value with a gap wider than
  1.5 x line height inside it (cut at the gap).
- Content comparisons use, per field, the frame read with the higher minimum word confidence.

## Comparison rules (`compare.py`)
- All types: case-folded, whitespace collapsed. `title`: `_`, space, `-` one separator class,
  plus confusable classes. `id`: spaces removed, plus confusable classes. `duration`: leading `:`
  ignored, then seconds when it parses (`15s` = `15` = `:15`; `1:00` = `60`; `compare.seconds`).
  `date`: digit groups, one-digit groups zero-padded (`5/7/2026` = `05/07/2026`).
  `text`/`audio`: case and spacing only.
- Confusable classes {0,O,o}, {1,I,l,|,i}, {5,S,s}, {2,Z,z}, {8,B}, applied before case folding
  (so upper `L` is not `1`). Equal only under the classes -> `confusable_severity` (INFO) naming
  the positions; never a mismatch.
- Uncertain: a mismatch where a word of the read has confidence < `min_confidence` (60) ->
  `uncertain_severity` (WARN) "uncertain read ... check by eye", not a content error. Same for
  a two-frame disagreement.
- TITLE mismatch with the default expectation `{file.stem}` -> "wrong slate or renamed file";
  the remaining fields are still compared against the row picked from the actual filename.
  `id` mismatches read "slate does not match the file". AD-ID row vs filename disagreement
  (e.g. `--row` picked another spot) -> FAIL `kind: row`.

## Params (profile)
Common: `slate` ("${spec.slate}", required), `tesseract`, `psm` (3), `lang` (eng),
`ocr_timeout` (60), `split_tolerance_frames` (0, see Reading), `min_confidence` (60), `uncertain_severity` (warn), `confusable_severity`
(info), `field_severity` {FIELD|row: sev} (replaces the check severity for that evidence kind).
- slate.content: `job` ("${spec.job}"), `deliverables` ("${spec.deliverables}"), `tags`
  (filename tags, only for match generated_name), `date` {mode ignore|format|window|equals_row
  (default window), format YYYYMMDD|YYYY-MM-DD|MM/DD/YYYY|M/D/YYYY|DD.MM.YYYY|MM/DD/YY|M/D/YY,
  window_days (30), severity (warn)}. Formats: the template `date_formats` when set (any one
  that parses), else `date.format`; MM/DD/YYYY needs two digits, M/D accepts 1 or 2
  (`compare.parse_date`). The DATE field = the field named DATE, else the first `date` field.
  File date = container `creation_time`, else file mtime (UTC). `equals_row` compares with the
  row column `date` as dates (parsed with the same formats; else digits). An uncertain DATE read
  uses the lower severity.
- slate.claims: `tolerance_frames` (0), `check_filename` (true: tokens in the filename stem too;
  a filename-only claim mismatch has no frame location and is not waivable).
- slate.bars: `tolerance` (2, 8-bit code values), `chroma_tolerance` (= tolerance), `band`
  (0.12 of the height searched for auto strips), `inner` (0.55 of each segment measured),
  `step_threshold` (20: |dY|+|dCb|+|dCr| over +-2 samples), `missing_severity` (warn: strip not
  found as N equal segments).

## slate.claims
- Fields by TYPE: the first `duration` field (DURATION, LENGTH, ...) and the first `audio`
  field; messages and evidence `kind` use the template field name.
- DURATION `:60`/`60`/`1:00`/`60s` -> seconds x round(fps) timecode frames (:60 at 29.97 =
  1800; :15 at 23.976 = 360) vs program frames (file - head - tail), to the frame. Message names
  both values (real case: `slate LENGTH '15s' = 360 frames (15 s x 24 timecode frames/s) but the
  program is 720 frames (+360)`).
- AUDIO `Mono|Stereo|5.1|7.1|Surround` -> channels of the FIRST audio stream. Unknown word ->
  uncertain severity "not verified". The template has no AUDIO `expect` on purpose: the media
  is the single source (slate.content would repeat the same error).
- Tokens (`claims.tokens`): `WxH`, `720p/1080i/...` (height), codec words `ProRes [HQ|LT|Proxy|
  4444|XQ]`, `H264/H.264/AVC`, `HEVC/H265`, `DNxHD/DNxHR`, fps as `NNfps`/`NN.NNfps` or
  `23.976|23.98|29.97|59.94`. Bare numbers are NOT fps (`_60_` is the duration, `2997` a tag).
  Slate TITLE and filename claims are merged per (kind, value); the message names the sources.

## slate.bars
- One frame (2nd of the slate) decoded `-f rawvideo` in the NATIVE pixel format (no -pix_fmt:
  no swscale matrix/range/dither); planar yuv(a)420/422/444p at 8..16 bit parsed in pure Python
  (`array`), values compared as 8-bit equivalents (10-bit / 4). Other formats -> SKIPPED.
  Measured: 1920x1080 yuv422p10le = 8,294,400 bytes; ~0.15 s per frame incl. ffmpeg (dev machine).
- Strips: template fractions (`y0 y1 x0 x1`, equal segments) or auto (spike bars.py on
  Y'CbCr: rows of the top/bottom `band`, cuts at steps, >= 4 equal-width segments covering > 70%
  of the width, longest run of such rows). Median Y/Cb/Cr over the inner half of the rows and the
  inner 55% of each segment.
- Targets: BT.709 100% bars limited range (white 235/128/128 ... black 16/128/128; full-range
  scaled when the file is tagged `pc`); grey steps: template `y_targets`, Cb/Cr 128.
- Findings per segment (`kind` `colour:<name>` / `grey:step N`, `text` "Y=.. Cb=.. Cr=.."):
  elevated/crushed black (black bar or last grey step), wrong hue (Cb/Cr, colour), tinted grey,
  wrong level. Range mismatch (black and white match the OTHER range) -> one `kind: range`
  finding instead of 16.
- Generated media (make_spot.py): exact codes on every segment after ProRes; planted black
  Y 32 and red Cr 228 measured exactly. Real renders, H.264 4:2:0 tolerance: UNVERIFIED.
- Not built (optional): learning grey targets from a known-good render.

## Waivers (D17 extension)
Slate findings carry stream + the slate frame range, so `cmr waive REPORT --finding N` works.
Fingerprint type `slate_text` (`waivers.text_fingerprint`): values [evidence kind, exact OCR
text], frames [] (not tied to frames), tolerance 0, computed from the finding (no media
decode). Same text = WAIVED; another text (e.g. the AD-ID changed) = content changed.
`waivers.media_kind` returns `slate` for `slate.*`; category waivers (`--category slate.content
--class CLIENT`) work as for video/audio. Old waiver files read unchanged.

## Other changes made for D27 (outside this package)
- `engine.Timeline.segments` / `segment(type)` (head/tail segments in file frames; also in the
  report timeline).
- `video.blanking` param `exclude_segments` (default `[slate, bars_tone, two_pop]`): records of
  those segments are dropped before detection (a vignetted slate would read as dark_edge).
  black_runs was already program-only.
- `config.load_job/merge_job/build_spec`, `schemas.JOB_SCHEMA`, CLI `--job/--deliverables/--row`
  (`cmr check`, `--job` also on `cmr profiles show`), `cmr doctor` tesseract line.

## Verified / measured (2026-10-26)
- Dev box (ffmpeg 6.1.1, no tesseract): unit tests with canned TSV; bars and filename on the
  generated spots; tesseract-missing path.
- Production machine (Rocky 9, ffmpeg 5.1.10, tesseract 5.5.2, Python 3.12): generated :60
  spot clean -> no problem finding with `spot_delivery`; errors spot -> every planted error of
  the manifest reported (except the roadmap slate-tone item) and no other slate finding;
  `tests/test_slate_media.py` 5/5 on the full and the `--short` media. Whole `cmr check
  spot_delivery` on the 2010-frame spot: ~22-26 s wall under nice/ionice (dominated by
  black_runs high, not the slate).

## Real "Label: value" slates (2026-10-26; 5 real XR spots, private suite xr_spots)
DNxHD 10-bit 1080p 2997/125, slate 0-118 + black 119-167, program 168; white on black, no
bars. Frames 1 and 118 per file extracted by seeking (8-bit gray PNG), tesseract 5.5.2 `--psm 3`
on the production machine, checks run on the dev machine with those TSVs (reader seek/OCR
replaced) and the real header probe: 7/7 labels read on both frames of all 5 spots (both label
sets); every value read exactly; AD-ID confidence 37-57 (< 60: a mismatch there would be
"uncertain", matches are fine), other fields 91-97. Findings: the :30 spot whose slate says 15s
-> slate.claims FAIL (360 vs 720 frames) + slate.content LENGTH FAIL (row length 30); the
other 4 spots: no slate finding; slate.bars SKIPPED (no bars). psm 4/6/11 were no better on the
16-bit PNG; 8-bit gray fixed it.

## Limits / not handled
- Two slate styles: `aligned` (right-aligned labels, left-aligned values) and `label_colon`;
  wrapped values and centred values are not read. Best-fit template auto-detection: not built
  (the template's `layout` selects the pairing).
- Only the first video stream; AUDIO claim = first audio stream only.
- The slate is assumed colour-managed in the render (user decision), else every bars check fails.
- Real slates: one family verified (5 XR "Label: value" slates, Arial-like font, see above);
  other real slates/fonts and the aligned layout on real renders: UNVERIFIED.
- Roadmap (design): slate audio silent + 2-pop, countdown continuity, unslated variant has no
  slate, Ad-ID structure / SD-HD pairing, safe-area check.
