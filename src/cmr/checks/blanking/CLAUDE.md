# video.blanking

Decisions: ../../../../CLAUDE.md (D12, D13). Check interface: ../../CLAUDE.md.

Check id `video.blanking` (package `src/cmr/checks/blanking/`, tests `tests/blanking/`).

### Milestones
- [x] M1 ffmpeg filters verified on ffmpeg 6.1.1, approach measured
- [x] M2 detector (stage 1) + evaluate, test media generator, recall/FP measurement
- [x] M3 adapted to core check interface (../../CLAUDE.md): Tap / MetaRecord / Candidate / Finding
- [x] M4 partial-line coverage (>= 50% of a row/column), per-pixel lut2; 4 taps x 3 keys
- [x] M5 engine: single-key prints (Tap.keys), exact pts frames, -filter_complex_threads
- [x] Profile entry in profiles/final.yaml (`severity: fail`, `dark_edge_mode: flag`)
- [x] M6 v3: sharp-step `edge` vs `dark_edge` class, run merging, dark_edge_mode (one real file + planted defects on real texture)
- [ ] Real-footage tuning on more renders (Flame ProRes deliverables; only one H.264 stage play so far)

### How it works (v3, sharp step + dark_edge class)
Per edge two taps (`<edge>`: YAVG/UAVG/VHIGH, `<edge>_dark`: YAVG), strip of max_depth+1 px ->
`format=yuv444p` -> `shuffleplanes=0:0:0` -> crops -> `lut2` stages -> signalstats.
Per pixel along the edge line (o = outer, a = adjacent 1 px in, i = inner max_depth px in,
s[k] = pixel k px in); exact graph in `detector.chains()`:
- sharp defect (Y of `<edge>`): `o <= black_luma and a - o >= min_step` (1 px deep) OR
  `o <= matte_luma and s[k] <= matte_luma and s[k+1]-s[k] >= min_step` for some k < max_depth
  (2..max_depth px; step flags of all neighbour pairs max-reduced to one line by a lut2 tree of
  overlapping halves, `_max_reduce`; `scale flags=area` was rejected: 16->1 gave 0 for a flag in
  the first/last column, measured).
- matte (U): `o <= black_luma and i <= matte_luma` (v1 rule, unchanged). Level (V) = max(o, i).
- soft/dark (Y of `<edge>_dark`): `o <= black_luma and i - o >= min_contrast` (the v1 defect rule).
Classes per frame and edge (coverage >= coverage - coverage_tolerance), in this order:
`edge` (sharp) > `dark_edge` (soft only) > `matte`; skip: black frame (all VHIGH <= matte_luma),
matte in dark frame (all VHIGH <= dark_frame_luma). Runs -> Candidate; persistent as before.
Every frame v1 flagged is still flagged (as `edge` or `dark_edge`); v1 matte is unchanged.
Consolidation: `dark_edge` and `matte` runs of one edge whose gap is <= merge_gap_frames merge
into one Candidate (evidence `runs`, summed `frames`/`frames_sampled`, max/weighted-mean
coverage); `edge` runs never merge (frame-exact).
Profile param `dark_edge_mode` (final: flag, review: info): flag = class severity (FAIL, capped
by the profile `severity`), info = INFO, hide = not listed; a hidden count + frame ranges goes
into one PASS finding (no other findings) or one INFO finding. Waivers (another agent) will
clear reviewed hits.

Why (measured on a 97-min 1080p25 H.264 stage play, 302 v1 hits, all dark picture): curtain /
floor / audience pixels at the edge have neighbour differences of 0-2 code values (median 0,
p90 1-2) but rise >= 4 over 16 px, which v1 counted. Planted 1px black next to picture keeps a
neighbour step >= 7 (synthetic dark content) up to ~150. Deeper planted bands are true black
(outer 13-18 measured); dark picture bands at the edge were 20-29, hence matte_luma for 2+ px.

### Knobs (profile params, 8-bit code values)
| param | default | meaning |
|---|---|---|
| coverage | 0.5 | share of the edge line that must be flagged |
| coverage_tolerance | 0.02 | slack for codec ringing at mask ends (planted 50% measured 0.498-0.500 at 1080p, 0.486 at bl=24) |
| black_luma | 32 | outer pixel <= this = black (ProRes ringing lifts black pixels to 25-32) |
| matte_luma | 20 | true black: inner matte pixel, 2+ px deep defect pixels; black-frame level |
| min_step | 6 | neighbour step black -> picture for a sharp `edge` pixel (dark picture: 0-2; real max coverage 0.13) |
| min_contrast | 4 | inner - outer for a `dark_edge` pixel (v1 rule) |
| dark_frame_luma | 48 | all edges VHIGH <= this: no `matte` verdict (fades) |
| max_depth | 16 | px searched for the step / inner line; deeper black = `matte` |
| min_run_frames | 1 | shortest run reported |
| persistent_fraction | 0.9 | share of frames for `persistent` |
| fast_frame_step | 5 | quality fast: 1 in N frames |
| merge_gap_frames | 250 | dark_edge / matte runs of one edge closer than this merge (10 s at 25p) |
| dark_edge_mode | flag | flag / info / hide for `dark_edge` findings |
| exclude_segments | [slate, bars_tone, two_pop] | head/tail layout segments (spec, D7) not judged: a vignetted slate would read as dark_edge (D27); bars and 2-pop cards have black edge areas by design (user decision; was [slate]) |
| class_severity | edge, edge_persistent, dark_edge, dark_edge_persistent FAIL; matte WARN; matte_persistent INFO | capped by profile `severity` |

### Results
Synthetic (`python3 tests/blanking/gen_media.py <scratch>/media`;
`PYTHONPATH=src python3 tests/blanking/measure.py <scratch>/media --engine`): 35/35 planted
(all frame-exact, all class `edge`), 30% partials missed as designed, 0 FP; letterbox/pillarbox
persistent matte. Tests: tests/blanking/test_blanking.py (incl. dark gradient -> dark_edge,
dark_edge_mode flag/info/hide through the engine, merging, odd max_depth reduction).

Real file (a real H.264 file, 145,562 frames, 1080p25 H.264, stage play; stats from one ffmpeg pass,
classified in Python with detector.detect; v1 fast count reproduces the 302 of the real run):

| | v1 high | v1 fast | v3 high | v3 fast |
|---|---|---|---|---|
| sharp `edge` | 632 | 265 | 0 | 0 |
| `dark_edge` (was v1 edge) | - | - | 632 -> 102 merged | 265 -> 70 merged |
| `matte` | 63 | 37 | 63 -> 31 merged | 37 -> 28 merged |
| findings total | 695 | 302 | 133 | 98 |
| max sharp coverage of any frame (top/bottom/left/right) | 0.60/0.83/1.00/0.67 | | 0.13/0.07/0.03/0.06 | |
merge_gap 0/125/250/750 frames: fast 302/112/98/79 findings. Review (cap warn, info): 70 INFO
+ 28 WARN; hide: 28 WARN + 1 INFO count.

Planted defects on real texture (70 s excerpts re-encoded, libx264 crf 18 and one ProRes HQ,
drawbox 1px full line 1/3/10 frames, 50%/70% partial, 2 px and 6 px deep, on every edge; 28 per
excerpt; scripts in the agent scratch, not in the repo). Class of the overlapping candidate:

| excerpt | planted | `edge` | other class | sharp-edge FP | other candidates (picture) |
|---|---|---|---|---|---|
| A dark curtain left (H.264 / ProRes) | 28 / 28 | 27 / 27 | 1 dark_edge (left 50% on curtain) | 0 | 1-2 dark_edge |
| B curtain call, dark theatre | 17 (file ends) | 17 | - | 0 | 6 matte, 1-2 dark_edge |
| C dark stage floor | 28 | 27 | 1 matte (bottom 50% on floor at Y 19-24) | 0 | 1-2 matte, 1-2 dark_edge |
| D bright scene | 28 | 28 | - | 0 | 2-3 dark_edge |
Same with quality fast (defects containing a sampled frame). v1 found all as `edge` but also
flagged the curtain itself as `edge` (e.g. A clean: 6 edge runs, one 1467 frames long).

### Measured cost v1 (1920x1080 ProRes HQ testsrc2, 1440 frames, 16 CPUs, shared box)
Engine decode only 0.79 s; + blanking (4 taps) 1.6-1.9 s at 1-8 threads, 3.6 s at 16.
Metadata output 790 B/frame for all blanking prints (was ~6.7 KB with 8 taps x all keys).
Raw-frame numpy hybrid measured earlier: no speed gain.

### Measured cost (Rocky 9, ffmpeg 5.1.10, 1750-frame 1080p25 H.264 excerpt, s)
| | v1 | v3 |
|---|---|---|
| blanking high, 4 filter threads | 2.9 | 8.2 (1 thread: 4.6; 2/8/16: 8.1/7.8/8.2) |
| blanking fast, 4 threads | 1.3 | 2.1 |
| blanking + black_runs high, 4 / 1 threads | 13.4 / 21.3 | 18.7 / 24.2 |
| blanking + black_runs fast, 4 threads | 2.1 | 3.2 |
The many small lut2/crop filters prefer few threads; the engine-wide thread default is shared
with black_runs (not changed here).

### Known gaps
- Hard case, cannot be separated by a per-pixel rule: a real 1px black line (Y 16) next to
  picture at Y <= ~22 (neighbour step < min_step), e.g. a 50% partial on the dark curtain / floor
  in the excerpts. It is NOT lost: it lands in `dark_edge` (or `matte` when the picture there is
  <= matte_luma) instead of `edge`. With dark_edge_mode hide it is only counted.
- Deep (2+ px) bands brighter than matte_luma (e.g. lifted black at 22) are not sharp `edge`
  (they are `dark_edge` when inner - outer >= 4).
- Dark-scene mattes (audience, fade-in at file start) remain `matte` WARN: inseparable by level
  (picture at 17-20); requiring outer AND inner <= 18 would cut real-file mattes 63 -> 5 (high)
  but was not adopted (recall; user wants hits reviewed, not hidden).
- Non-black bad blanking (grey/duplicated line) not detected.
- Matte in dark frames (<= dark_frame_luma) is not judged.
- A defect max_depth px deep or more is reported as matte.
- quality: fast (profile `review`) measures 1 in `fast_frame_step` (5) frames: defects shorter
  than 5 frames can be missed; see src/cmr/checks/CLAUDE.md.
