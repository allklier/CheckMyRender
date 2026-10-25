# BLANKING_STATUS

Check id `video.blanking` (package `src/cmr/checks/blanking/`, tests `tests/blanking/`).

## Milestones
- [x] M1 ffmpeg filters verified on ffmpeg 6.1.1, approach measured
- [x] M2 detector (stage 1) + evaluate, test media generator, recall/FP measurement
- [x] M3 adapted to core check interface (STATUS.md): Tap / MetaRecord / Candidate / Finding
- [x] M4 partial-line coverage (>= 50% of a row/column), per-pixel lut2; 4 taps x 3 keys
- [x] M5 engine: single-key prints (Tap.keys), exact pts frames, -filter_complex_threads
- [ ] Profile entry in profiles/final.yaml (shared file; `video.blanking: {severity: fail}`)
- [ ] Real-footage tuning (needs labelled renders)

## How it works (v2, per-pixel coverage)
One tap per edge: crop the outer max_depth+1 px strip -> `format=yuv444p` ->
`shuffleplanes=0:0:0` -> split -> outer line / inner line (max_depth px in) -> `lut2`:
Y = defect pixel (outer <= black_luma and inner-outer >= min_contrast), U = matte pixel
(outer <= black_luma and inner <= matte_luma), V = max(outer, inner). signalstats YAVG/255 and
UAVG/255 = defect / matte coverage of the line, VHIGH = edge brightness. Engine prints only
these 3 keys. Classes per frame: `edge` (defect coverage >= coverage - coverage_tolerance),
`matte`, skip (black frame: all VHIGH <= matte_luma; matte in dark frame: all VHIGH <=
dark_frame_luma). Runs -> Candidate; >= persistent_fraction of frames -> `persistent`.

Why: signalstats luma offers only YMIN/YLOW(p10)/YAVG/YHIGH(p90)/YMAX (no median; full key
list printed on 6.1.1). No strip statistic means ">= 50% black"; binarising per pixel makes
YAVG the coverage, and the per-pixel contrast to the inner line rejects flat dark picture.

## Knobs (profile params, 8-bit code values)
| param | default | meaning |
|---|---|---|
| coverage | 0.5 | share of the edge line that must be black |
| coverage_tolerance | 0.02 | slack for codec ringing at mask ends (planted 50% measured 0.498-0.500 at 1080p, 0.486 at bl=24) |
| black_luma | 32 | outer pixel <= this = black (ProRes ringing lifts black pixels to 25-32) |
| matte_luma | 20 | inner pixel <= this = black (matte); black-frame level |
| min_contrast | 4 | inner - outer per pixel for a defect pixel |
| dark_frame_luma | 48 | all edges VHIGH <= this: no `matte` verdict (fades) |
| max_depth | 16 | inner line offset px; deeper bars are `matte` |
| min_run_frames | 1 | shortest run reported |
| persistent_fraction | 0.9 | share of frames for `persistent` |
| class_severity | edge FAIL, edge_persistent FAIL, matte WARN, matte_persistent INFO | capped by profile `severity` |

## Results (generate + measure)
    python3 tests/blanking/gen_media.py <scratch>/media
    PYTHONPATH=src python3 tests/blanking/measure.py <scratch>/media --engine
35/35 planted defects (>= 50% coverage, incl. partial 50/70/100% on each edge, 60% on dark
content) found, all frame-exact; 30% partials missed as designed (below coverage); 0 FP on
clean, fade, grain, dark content, letterbox, pillarbox. Noise floor (coverage of non-defect
frames): clean 0, fade <= 0.06, grain 0.

## Measured cost (1920x1080 ProRes HQ testsrc2, 1440 frames, 16 CPUs, shared box)
Engine decode only 0.79 s; + blanking (4 taps) 1.6-1.9 s at 1-8 threads, 3.6 s at 16.
Metadata output 790 B/frame for all blanking prints (was ~6.7 KB with 8 taps x all keys).
Raw-frame numpy hybrid measured earlier: no speed gain.

## Known gaps
- Partial defects on content that is itself <= black_luma (Y <= 32) without 4 levels of
  contrast to the inner line are missed; near-black vignettes with >= 50% of a line at
  Y <= 32 and a brighter inner line would false-positive (not seen in tests).
- Non-black bad blanking (grey/duplicated line) not detected.
- Matte in dark frames (<= dark_frame_luma) is not judged.
- Inner line is a single row/column (per-pixel compare); a defect exactly max_depth px deep
  or more is reported as matte.
- quality: fast (profile `review`) measures 1 in `fast_frame_step` (5) frames: defects shorter
  than 5 frames can be missed; see docs/quality-modes.md.
