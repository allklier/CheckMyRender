# tests

Run: `CMR_TEST_MEDIA=<dir> PYTHONPATH=src python3 -m pytest -q tests` (media is generated with ffmpeg, never stored in the repo).

`python3 tests/media/make_media.py OUTDIR [case...]` - cases good, wrong_meta, wrong_tc, short,
no_captions, corrupt; matches tests/media/spec_test.yaml (48-frame program, 1+23 head, 24 tail).
`make_media.make(outdir, name, vf_extra=..., af_extra=..., ...)` for planting content defects.
Tests use $CMR_TEST_MEDIA (default <tmp>/cmr_test_media); media is never written to the repo.

Portability (ffmpeg 5.1 on Rocky 9 and 6.1 locally; details src/cmr/CLAUDE.md):
- ProRes generators that need colour tags must use `-bsf:v prores_metadata=...` (5.1 prores_ks
  leaves the frame header unspecified, and 5.1 ffprobe reports the header). make_media does.
- Never cut a file with audio by `-frames:v` (5.1 drops queued audio): use `trim=end_frame=N`
  in the video chain (make_media) or `-t` (test_engine `tiny`). Video-only clips may use `-frames:v`.

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
