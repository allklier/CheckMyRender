# Starter folder: your first QC run in 5 minutes

This folder is a complete QC setup for one broadcast deliverable: an HD ad spot with a
slate. After the installer, `media/` already holds a clean demo spot, so the first check
works right away. Copy the folder, check the demo, try a spot with errors, then put your own
video in. Everything else (more jobs, other deliverables, waivers, overnight runs) builds on
this folder.

| File | What it is |
|---|---|
| `job.yaml` | The job: names every other file below. `cmr check` in this folder finds it automatically. Reads the slate. |
| `job_no_slate.yaml` | The same job without slate reading (no slate template, no deliverables list, no OCR needed). |
| `spec_starter.yaml` | The spec sheet: what the file must be (codec, size, frame rate, colour, head layout, length, audio). |
| `deliverables.csv` | The list of spots in the job, one row per spot (Ad-ID, title, length, version). |
| `slates/slate_template.yaml` | The slate design: which labels the slate has and what each must read. |
| `media/` | The clean demo spot (made by the installer); later your video. |

All names in these files (agency, client, Ad-IDs) are fictional.
The rules (which checks run, how strict) come from a stock profile named in the job file:
`spot_delivery` (strict delivery QC) in `job.yaml`, `release` in `job_no_slate.yaml`.

## The 5 steps

**1. Install** CheckMyRender (once per machine). From the bundle:

    tar xzf cmr-<version>-bundle.tar.gz
    cmr-<version>/install.sh

The installer puts a copy of this folder in `~/cmr-workspace/templates/starter`, with a clean
demo spot in its `media/`, and spots with planted errors in `~/cmr-workspace/demo_spots/errors/`
(about 60 MB per spot; `install.sh --no-demo` skips them). Check the install with `cmr doctor`.

**2. Copy the starter folder** (one folder per job; the copy carries the demo spot):

    mkdir -p ~/qc
    cp -r ~/cmr-workspace/templates/starter ~/qc/my_first_job
    cd ~/qc/my_first_job

**3. Check the demo spot:**

    cmr check media/*.mov                              # with slate reading (job.yaml)
    cmr check media/*.mov --job job_no_slate.yaml      # without slate reading (no tesseract needed)

Both pass in a few seconds:

    job file: /home/you/qc/my_first_job/job.yaml (job demo_sb_league)
    PASS    media/SB X League 2026_ProRes Generic Submaster_QB Pat_03_ABCD1234567H_1920x1080_Slated.mov
            30000/1001 fps, 300 frames, start TC 00:59:53:00 (file)
      SKIPPED captions.presence: captions not required by spec
    JOB demo_sb_league
    RESULT PASS (exit 0): PASS 27, SKIPPED 1
    report: /home/you/qc/my_first_job/reports/cmr_report_20261026-211543_31f65f.json (also .../reports/cmr_report_latest.json)

cmr reads the file once, prints the result and writes a report to `reports/`. It never changes your video.

**4. Swap in a spot with an error** (the same spot, but its slate has a typo in the client name):

    rm media/*.mov
    cp ~/cmr-workspace/demo_spots/errors/client_typo/*.mov media/
    cmr check media/*.mov

      FAIL    slate.content: slate CLIENT 'Sample Bugrers', expected 'Sample Burgers' @ 00:59:53:01-00:59:57:28 (frames 1-148)
    RESULT FAIL (exit 2): PASS 26, SKIPPED 1, FAIL 1

`~/cmr-workspace/demo_spots/errors/many_errors/` holds a spot with many planted errors (each
listed in the `manifest.yaml` next to it): try it the same way.

**5. Put your own video in `media/`:**

    rm media/*.mov
    cp /path/to/render.mov media/            # or a link: ln -s /path/to/render.mov media/
    cmr check media/*.mov --job job_no_slate.yaml      # start without slate reading
    cmr check media/*.mov                              # with slate reading, once job.yaml fits your job

## What you will see with your own video

**Your own render will almost certainly show findings the first time. That is expected:**
the sample spec describes the demo spot (1920x1080 ProRes 422 HQ, 29.97, a 3-second
program after a 5 s slate and 2 s black). Every value of your file that differs from the
spec is named, with the expected and the actual value. This is the demo spot checked
against a spec for a UHD 23.976 ProRes 4444 :30 (when it is your file that differs, the lines read the same):

      FAIL    timing.frame_count: program frames: expected 900 (+-0), got 90 (file 300 - head 210 - tail 0) ...
      FAIL    video.codec: codec profile: expected '4444', got '422 HQ'
      FAIL    video.fps: frame rate (r_frame_rate): expected 24000/1001, got 30000/1001
      FAIL    video.fps: average frame rate: expected 24000/1001, got 30000/1001
      FAIL    video.resolution: width: expected 3840, got 1920

That list is exactly what to change in the spec (next section) - or what is wrong with the
render. With `job.yaml` a real file also fails the filename and slate checks until
`job.yaml`, `deliverables.csv` and the slate template describe your job; start with
`--job job_no_slate.yaml` to get the technical checks clean first.

## How to read the first report

- One block per file: the file's verdict (PASS / INFO / WARN / FAIL / ERROR), its frame
  rate, frame count and start timecode, then one line per problem. PASS lines are not
  printed; `SKIPPED` means the check did not apply (e.g. captions not required).
- Each problem line: `SEVERITY check.id: what was expected and what was found @ timecode
  (frame numbers)`. Timecodes are the file's own, so you can jump there in Flame or any
  player.
- `RESULT` and the exit code: 0 = pass, 1 = warnings, 2 = failures, 3 = a tool or setup error
  (a wrong value in a YAML file, a missing file, no tesseract for the slate). Scripts use the
  exit code.
- `ERROR` is never a problem in the video: it means cmr could not do a check (read the message).
- The JSON report in `reports/` has everything, incl. all frame ranges and measured values.
  `cmr waive reports/cmr_report_latest.json` lists the picture and sound findings with
  numbers; one you accept (e.g. an intended black frame) can be waived so the next render
  of the job does not flag it again (user guide, "Waivers").
- More than 10 problems of one check are summarised in one line ("... additional findings
  suppressed"); `--max-findings 0` lists them all.

## Your own video: edit the spec

The spec in your copied folder is yours to edit (or copy it first, e.g.
`cp spec_starter.yaml spec_my_spot.yaml`, and change the `spec:` line in both job files).
Every line is commented; these are the fields you usually change (all marked `EDIT`):

| Field | Demo value | Typical change |
|---|---|---|
| `video.codec`, `video.codec_profile` | `prores`, `"422 HQ"` | `"4444"`, `dnxhd`, `h264` |
| `video.width`, `video.height` | 1920, 1080 | 3840, 2160 for UHD |
| `video.fps` | `"30000/1001"` (29.97) | `"24000/1001"` (23.976), `"25"` |
| `video.pixel_format` | `yuv422p10le` | the value the report shows for your codec |
| `timing.timecode_mode` | `non-drop` | `drop` |
| `timing.program_duration_frames` | 90 (3 s demo) | 900 = :30 at 29.97, 450 = :15, 720 = :30 at 23.976 |
| `timing.head_layout` | slate 150 + black 60 | your slate/black lengths; no slate: only `- {type: program}` |
| `audio.tracks`, `audio.loudness.standard` | one stereo track, `"ATSC A/85"` | your tracks; `"EBU R128"` |

Change the values that the findings name, run again, repeat until the known-good render
passes. Then the folder is a reference for every later render of this job.

For the slated job also edit `job.yaml` (lines marked `EDIT`: job id, agency, client,
product, artist, filename pattern), `deliverables.csv` (your spots: one row per spot, the
header names must match `columns:` in job.yaml) and, for a different slate design, the
slate template. A CSV file has no comments: its first line is the column header.

## The demo spots

The installer made them once with the generator in the bundle (`demo/make_spot.py`); one you
delete is not made again. To make them by hand, from a CheckMyRender source folder (a Python
with numpy and Pillow, e.g. the one in cmr's environment, `~/.local/share/cmr/env/bin/python`):

    python3 ~/CheckMyRender/tests/slate/make_spot.py ~/qc/spots --short
    cp ~/qc/spots/clean/*.mov media/

`--short --defect client_typo` (into another folder) makes the spot with the slate typo; the
generator's help lists all planted defects.

## Where to go next

- `../README.md` (the templates folder): which spec and profile to copy for other
  deliverables (long form, review renders, colour interims, overnight batches).
- The user guide `USERGUIDE.md` (in `~/cmr-workspace` or the CheckMyRender folder): every field, every check, waivers, the slate checks,
  profiles, overnight runs.
- `cmr profiles show spot_delivery --spec spec_starter.yaml --job job.yaml`: the rules this job runs with.
- `cmr check --help`: options such as `--quality fast` (quicker), `--mode background`
  (does not slow down other apps), `--profile spot_review` (review pass).
