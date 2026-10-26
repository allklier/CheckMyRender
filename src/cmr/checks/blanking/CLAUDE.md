# video.blanking

Decisions: ../../../../CLAUDE.md (D9, D12, D13). Check interface: ../../CLAUDE.md.

Check id `video.blanking` (package `src/cmr/checks/blanking/`, tests `tests/blanking/`).

### Milestones
- [x] M1 ffmpeg filters verified on ffmpeg 6.1.1, approach measured
- [x] M2 detector (stage 1) + evaluate, test media generator, recall/FP measurement
- [x] M3 adapted to core check interface (../../CLAUDE.md): Tap / MetaRecord / Candidate / Finding
- [x] M4 partial-line coverage (>= 50% of a row/column), per-pixel lut2; 4 taps x 3 keys
- [x] M5 engine: single-key prints (Tap.keys), exact pts frames, -filter_complex_threads
- [x] Profile entry in profiles/final.yaml (`severity: fail`, `dark_edge_mode: flag`)
- [x] M6 v3: sharp-step `edge` vs `dark_edge` class, run merging, dark_edge_mode (one real file + planted defects on real texture)
- [x] M7 stage 2 temporal context (`context.py`): animation / transition / steady / unknown, may only lower (see "Stage 2")
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

### Stage 2: temporal context (`context.py`, D9 slot, deterministic; 2026-10-26)
Why: a long-form broadcast master (58 min, 1080i MPEG-2, many Ken Burns-animated stills with
pillar/letterbox bars, montages, fades) gave 156 sharp `edge` FAILs; one frame cannot tell a
pan/zoom or a dissolve from a mask error, the difference is temporal.
Flow: `Blanking.evaluate` -> `detector.evaluate` -> `context.analyse(findings, ctx, dp)`. For every
finding with a candidate (not persistent, span <= `context_max_span_frames`, worst first, at
most `max_context_candidates`, within `context_max_seconds`): windows [start-K-1, end+K+1]
(K = `context_frames` 12; fast mode: the possible range) are merged when they overlap and
seek-decoded ONCE each: `ffmpeg -ss (f0-0.5)/fps -i F -map 0:V -frames:v N -vf
scale=out_range=tv,format=yuv420p,extractplanes=y,metadata=mode=add:...,metadata=mode=print:file=T -f rawvideo -`
(8-bit luma with the detector's code values; `format=gray` would expand tv range: Y 30 -> 16,
218 -> 235, measured). Frames come from the exact pts: frame = round((pts x tb + X) x fps),
X = the seek time as passed. Streamed frame by frame (memory = one frame). Pure Python on bytes
(`bytes.translate` + `find`, no numpy): per frame and edge the band depth per line (run of
luma <= black_luma from the edge, up to `context_depth_fraction` 1/8 of the frame), coverage,
median depth (left/right: mean of even/odd-row medians), mean luma of the outer line, and a
sparse luma sample (every 61st byte) for frame differences.
Features per hit (evidence[0], plus `context`, `context_version`, `window`, `hit`):
`depth_track` (window <= 64 frames), `hit_depth_px`, `hit_depth_range_px`, `band_run`,
`context_band_frames`, `depth_min/max`, `depth_motion_px`, `moving_steps`, `monotone`,
`velocity_px`, `trend_r2`, `symmetry` / `opposite_band` (opposite edge banded with similar
depth on the hit frames), `cut_distance`, `dissolve_distance`, `dissolve_share`, `cuts`,
`onset_jump`, `cut_onset`, `cut_end`, `animation_guard`, `reason` (unknown). `finding.verifier` = {name blanking.context, version 2,
decision, p null, model_hash null, severity, lowered}; `Finding.lower_to` only (raw kept).
Rules, in this order:
1. animation: band on >= 3 context frames (run through the hit, stopping at cuts/dissolves),
   depth moves >= 3 px, >= 2 moving steps, >= 80 % of them in one direction (steps < 1 px are
   repeats: 3:2 pulldown gives a zero step every 5th frame), straight-line R^2 >= 0.8, AND no
   guard (context v2, user labels, see "User labels" below): `cut_onset` = the band run starts
   <= `context_cut_onset_frames` (2) frames after a cut and the band is new there (no band on
   the frame before the cut, or a depth differing by > max(2 px, 50 %)); `cut_end` (mirror,
   user decision) = the band run ends <= `context_cut_end_frames` (2) frames before a cut and
   is gone on the cut frame (or its depth differs by > max(2 px, 50 %)); `fast_move` = median
   depth step (`velocity_px`) > `context_max_velocity_px` (8 px/frame). Evidence `cut_onset`
   (frames after the cut or null), `cut_end` (frames before the cut or null) and
   `animation_guard` (list) record it. A guarded hit falls through to the other rules; a hit
   whose band MOVES like an animation and is cut-guarded (`cut_onset`/`cut_end`) skips the
   cut branch of rule 2 (it is not a field-mixed cut frame), the dissolve branch still applies.
2. transition: onset jump <= 8 AND (hit <= 2 frames within 2 frames of a cut, OR >= 50 % of
   the hit frames within 2 frames of a dissolve/fade). Cut = local frame-difference spike
   (>= 12 and >= 4 x the median of +-12 frames). Dissolve = mean |Y(t+4)-2Y(t)+Y(t-4)| /
   mean |Y(t+4)-Y(t-4)| <= 0.35 with mean |Y(t+4)-Y(t-4)| >= 2, runs >= 3 frames.
3. steady: depth range during the hit <= max(2 px, 20 % of the depth) - the mask-error
   signature (stays at its stage-1 severity).
4. unknown: erratic depth (dark picture, montage holds), not reproduced, not analysed (cap,
   time, span, persistent, decode failure; `reason`).
Severity: `context_severity` {animation: info, transition: info, steady: unchanged, unknown:
unchanged}. A later ML verifier (engine.VERIFIERS) runs after evaluate and REPLACES
`finding.verifier` (engine sets it); the context evidence stays (chaining = main-thread item).

Measurements behind the thresholds (the PBS master, 20 windows of 21 s cut by stream copy
(2.5 GB, never the whole file), 203 stage-1 hits re-found, all offsets matched to the earlier
report; visual checks of strips):
- Frame differences: 3:2 pulldown (every 5th diff ~0), held-frame montages (one change every
  6 frames: every change counts as a cut, which is right), fire/flicker graphics (diff 15-30
  every frame: local median prevents false cuts; a whole-file median gave many).
- Dissolve linearity: real cross-dissolve / fades 0.05-0.30 (k = 4); slow Ken Burns of a still
  0.42-0.88 (k = 1 gives ~1.0 everywhere because of the repeats and noise), 1 % of all frames
  <= 0.30, median 1.22.
- Onset jump of the outer line into the hit: real dissolve/fade hits 0-6; masks planted into
  real dissolves and at cuts 19-49 (hence 8).
- Moving-band tracks (real): mid-shot 2 3 3 5 7 9 11 11 12 14 (both bars, no cut); slow
  1 1 2 5 6 6 6 7 7 8 8 8 9 (R^2 0.8-0.99); field split of real ~49 % hits: none (the ~49 %
  coverage is picture content, not one field). The track 33 27 27 21 15 9 5 5 1 0, first read
  as a Ken Burns, starts at a cut and was labelled a DEFECT by the user (see "User labels").
Tests: `tests/unit/test_blanking_context.py` (canned depth tracks: Ken Burns, pulldown,
steady, erratic, dissolve with gradual vs abrupt onset, cut rules, run stops at cuts, symmetry,
fast widening, transitions on synthetic samples, frame features, driver with a faked decode:
lowering, never raising, off, persistent/span/cap/time/decode failure), `tests/blanking/
test_context_media.py` on `tests/blanking/gen_context.py` media (high and fast; also passes with
ffmpeg 5.1.10 on the Rocky machine). Results (`context_analysis` on, default params):

| set | before | after |
|---|---|---|
| synthetic gen_media.py (35 planted) high / fast | 35 found | 35 unchanged (34 edge FAIL steady + the 20 px one matte WARN steady); 0 lowered |
| planted on real texture (6 PBS excerpts x 28: 1 px full 1/3/10 fr, 50 %/70 %, 2 px, 6 px; re-encoded H.264) high | 154 seen, 133 FAIL | 133 FAIL (0 lowered); before the onset rule 3 were lowered (inside a real dissolve / at a cut) |
| same, fast | 160 seen, 138 FAIL | 138 FAIL |
| gen_context.py (Ken Burns zoom + pan, fade, field cut, steady in moving shot / near fade / dark scene) | all FAIL/WARN | Ken Burns 4 edge FAIL + 4 matte WARN -> INFO animation; fade -> INFO transition; 4 planted steady stay FAIL |
| real PBS excerpts, high (203 hits) | edge 124 FAIL, dark_edge 37 FAIL, matte 42 WARN | edge: 14 animation + 8 transition -> INFO, 88 steady + 14 unknown FAIL; dark_edge: 10 -> INFO, 27 FAIL; matte: 5 -> INFO, 37 WARN |
| same, fast (141 hits) | edge 73, dark 28, matte 40 | edge 12 -> INFO (8 anim, 4 trans), 29 steady, 32 unknown |

Steady real hits are mostly constant 1 px lines (a dimmed top line on 49 % of the columns
for seconds, 1 px dark columns in archival shots, one 5-6 px left band in a split-screen,
1 px bands per held image in a step-printed montage): the analyser cannot excuse them and
does not claim they are defects (review sheets: check_my_render_testdata/suite/stills/pbs_blanking).
Cost: 0.4 s per 29-frame seek window on the 26 GB master over the share (long-GOP MPEG-2,
10 windows), ~2.3 ms per frame for the features; ~0.1 s per analysed candidate on excerpts
(windows shared). Seek decode = full decode, sample-identical (8 windows, long-GOP 1080i).
Cannot be separated: a mask error that moves with a Ken Burns (an animated resize mistake) =
animation; a mask switched on at a dark edge during a fade (onset jump small) = transition; a
1-frame band at a cut that switches on abruptly stays FAIL (a field-mixed cut frame would too,
none seen); dark moving picture at an edge with a straight-line depth trend = animation.

### User labels (2026-10-26) and the animation guards (context v2)
The user labelled 30 review sheets of the long-form master (private suite stills/pbs_blanking/
labels.yaml): 4 `defect`, 6 `intended_transition`, 19 `cant_tell` (hard to see, possibly a
single dark line), 1 unlabelled. "Real issues were very obvious failures."
- All 4 defects had been LOWERED FAIL -> INFO as `animation` (the D9 promise broken on real
  material; the synthetic planted masks never moved, so they never tested this). Shapes
  (features only): (a) band 33 px from the frame after a cut, shrinking to 0 in 8 frames
  (onset 1 frame after the cut, R^2 0.96, monotone 1.0); (b) previous shot pillarboxed (~148
  px), cut, band grows 1 -> 36 px on both sides (onset 0); (c, d) mid-shot, no cut detected
  (the move runs through, frame differences ~12 every frame): band appears and grows 4 -> 123
  and 14 -> 108 px in ~9 frames (median step 17 / 14.5 px), outer-line jump ~170, then holds.
- Signature (user insight): an image cut in and animated whose first keyframe was set too loose:
  black shows at or right after the cut for a few frames, then the move covers it (or the
  reverse: a band opens right after the cut). Real Ken Burns moves are long, start smoothly from
  ~0 and are not tied to a cut onset. (c, d) are not tied to a detected cut; their separating
  feature is speed: 14-17 px/frame vs <= 6 for every hit still classed animation.
- Guards (rule 1 above): `cut_onset` (<= 2 frames after a cut, band new there) and
  `fast_move` (> 8 px/frame). Measured on the 20 excerpts (203 stage-1 hits, quality high,
  offline re-classification of the same decoded features): animation 24 -> 13; the 11 that lost
  it: the 4 defects + 3 more hits of the same two events (the matte part of (a), the right side
  and a later dark_edge of (b)), a deep band (108 px) at a cut shrinking to 0 in 15 frames on
  both sides (2 hits, unlabelled; same signature), a matte at a cut shrinking 115 -> 43 px
  (`cant_tell`, WARN stays WARN), a fast erratic shrink ending in a lingering 1 px line
  (unlabelled). Remaining animation: slow mid-shot moves (<= 4 px/frame), bands that shrink to 0
  1-3 frames BEFORE a cut (the end of a move; 2 `cant_tell`, kept INFO), the mid-shot reveal
  2 3 3 5 ... to 77 px pillars. Lowered: edge 22 -> 16, dark_edge 10 -> 7, matte 5 -> 3; FAIL
  129 -> 138, INFO 37 -> 26, WARN 37 -> 39. Transition lowerings are unchanged (the guards
  apply to rule 1 only). (Numbers of this first step; the `cut_end` mirror below supersedes
  the "shrink to 0 before a cut" part and the totals.)
- Labelled outcome: 4/4 defects stay at FAIL (before: 4 INFO); 6/6 `intended_transition` stay
  as before (2 matte WARN, 4 FAIL; none lowered); `cant_tell`: 18 unchanged, 1 (the matte at a
  cut) INFO -> WARN.
- Mirror guard `cut_end` (user decision, same day): a band that ENDS <= 2 frames before a cut
  and is gone after it stays at raw severity, so onset and end at a cut are treated alike.
  Without the cut-branch exclusion above the two `cant_tell` 1-frame hits at the end of a
  shrink (13 -> 1 and 14 -> 1 px, cut 2 / 1 frames later) fell into the cut transition
  rule and stayed INFO; with it they are steady FAIL. Effect on the 203 excerpt hits (vs the
  first guards): 7 more hits lose animation, all `cut_end`: 6 hits of one event (top, bottom
  and right bands shrinking to 0 1-3 frames before a cut: 4 dark_edge/edge INFO -> FAIL
  unknown, the 2 `cant_tell` INFO -> FAIL steady) and one 77 px pillar matte that runs up to a
  cut (INFO -> WARN). No transition changed. Animation 24 -> 6 overall (left: slow mid-shot
  moves and reveals that end well before a cut). Lowered: edge 22 -> 13, dark_edge 10 -> 4,
  matte 5 -> 2; FAIL 129 -> 144, WARN 37 -> 40, INFO 37 -> 19. Labelled: 4/4 defects FAIL,
  `intended_transition` unchanged, `cant_tell` 16 unchanged, 3 raised (the matte at a cut
  INFO -> WARN, the two shrink-to-cut hits INFO -> FAIL). Planted regression identical
  (high 139 raw FAIL -> 138, fast 136 -> 133; same lowered planted hits as before).
- `intended_transition` items were NOT lowered (no principled signal): two are dark picture
  content at the top edge (a dark album page; luma <= black_luma runs follow the picture, not a
  straight line), two are dark title cards with flares (deep, erratic bands), two are a 1 px
  dark line next to dark picture for 25 / 164 frames (constant depth: exactly the mask-error
  signature). A band "straightness" feature (share of banded lines within +-1 px of the
  median depth) was measured: the two dark-page hits 0.12-0.13, defects 0.95-1.0, but the two
  1 px items 0.88-0.90 and dozens of unlabelled steady sharp-edge hits 0.004-0.05, so it does
  not separate the labels and was not adopted. Open: the 1 px dark lines are hard to see
  (low contrast); a visibility measure (step from line to picture) would be a stage-1 rule
  change, not context.
- Planted regression (6 excerpts x 28 planted masks on real texture, re-encoded H.264,
  planted again 2026-10-26 at slightly different start frames than the first study): high 168
  planted, 139 raw FAIL -> 138 final FAIL; fast 136 -> 133; identical before and after the
  guards (one fast hit moved animation -> transition, still INFO). The lowered ones are a known
  gap, unchanged: masks planted inside a fade from black at an already black edge (onset jump
  0-4) read as transition. Synthetic gen_media / gen_context tests unchanged and green.
- Expected effect on the full high run of the master (297 blanking hits: 156 edge FAIL, 51
  dark_edge FAIL, 90 matte WARN before stage 2): the 203 excerpt hits gave +15 FAIL, -18 INFO,
  +3 WARN (both cut guards and fast_move); the 94 hits outside the excerpts were not
  re-measured (estimate: a few more in the same direction).
- Tests: tests/unit/test_blanking_context.py (the four defect shapes, the old "band grows after
  a cut = animation" test inverted, slow mid-shot reveal still animation, a band continuing
  across a held-frame cut is not cut_onset / cut_end, the two shrink-to-cut shapes stay steady,
  a shrink ending well before a cut is still animation, an unmoving 1-frame band at a cut is
  still a transition, thresholds as params).

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
