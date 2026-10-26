# examples/spot_xr - :60 spot for Extreme Reach (fictional job, D27)

Matches the generated test spot (tests/slate/make_spot.py with tests/slate/values_fictional.yaml).
- `spec_spot_xr.yaml` technical spec: 1920x1080 ProRes 422 HQ yuv422p10le, 30000/1001 NDF,
  head_layout slate 150 + black 60 + program 1800 (program at 01:00:00:00), no tail, one stereo
  48 kHz 24-bit track, loudness standard ATSC A/85.
- `job.yaml` job file (D31 entry point: `cmr check SPOT.mov` in this folder needs no other
  options): run keys `spec` (spec_spot_xr.yaml), `profile` (spot_review), `waivers`
  (waivers.yaml), `output` (reports), `job.id` (nw_sb_league); job constants (agency, client, product `<empty>`, artist),
  `naming_pattern` from CSV row fields + the profile tag `resolution`, deliverables (CSV, column
  map, key `ad_id`, match `both`, filename regex with named groups), slate template.
- `deliverables.csv` one row per spot, no filenames.
- `slates/xr_standard.yaml` slate template (labels, types, default expectations, countdown box,
  bars + grey-step targets 235 204 172 141 110 79 47 16).

"Label: value" variant (real XR slates look like this; fictional values):
- `slates/xr_label_colon.yaml` template `layout: label_colon`, labels with colon, no bars or
  countdown, `date_formats` MM/DD/YYYY + M/D/YYYY, aliases for both label sets seen in practice
  (Ad-ID/AdID, Advertiser/Brand, Length `15s` or `30`); AD-ID expects the filename's Ad-ID BASE
  (`{file.ad_base}`), LENGTH the filename length part (`{file.length}`).
- `job_label_colon.yaml` composite key `[ad_base, length]` from filenames like `SBX01760H.mov`
  (base + length + H); `deliverables_label_colon.csv` (Length as `:60` / `30s`, both = seconds).
- Matches `tests/slate/make_spot.py --layout label_colon` (slate 145 + black 65 against the
  spec's 150 + 60: set `split_tolerance_frames: 5` on the slate OCR checks).

Run: `cd examples/spot_xr && cmr check SPOT.mov` (or from elsewhere `cmr check SPOT.mov
--job-dir examples/spot_xr`; the old form `--profile spot_review --spec ... --job ...` still
works; profiles spot_render / spot_review / spot_delivery). `job_label_colon.yaml` names only
its spec and job id (`--job examples/spot_xr/job_label_colon.yaml --profile ...`).
The :60 spec does not fit the `--short` demo: templates/starter/ is the unedited
`--short` set (program 90 frames, CSV length 03, job id demo_sb_league).
OCR needs tesseract (`CMR_TESSERACT`). Rules: ../../src/cmr/checks/slate/CLAUDE.md.
