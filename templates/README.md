# Templates

Starting points for your own QC setup. **Copy, then edit the copy**; do not edit the files
here or in `profiles/` (an update of CheckMyRender would overwrite them). Every file is
commented line by line. The full explanation of every field is in [../USERGUIDE.md](../USERGUIDE.md).
The guide's quick starts (A: any render, B: slated spot, C: waivers) use these files.

## What to copy for which situation

| Situation | Spec sheet (job facts) | Profile (rules) | Extra |
|---|---|---|---|
| First try, nothing to edit: the generated demo spot (`tests/slate/make_spot.py spots --short`) | `quickstart/spec_spot_short.yaml` | stock `spot_review` (named in `quickstart/job.yaml`) | the whole `quickstart/` folder: copy it, add the demo media, run `cmr check spots/clean/*.mov` in it |
| Check a render right after it comes out of Flame (settings only, fast) | any spec below | `profiles/client_render_check.yaml` | |
| Client / internal review render | `specs/spec_review_render.yaml` | `profiles/client_review.yaml` (or stock `review`) | |
| Final master or deliverable, file-based | `specs/spec_master_generic.yaml` | `profiles/client_delivery.yaml` (or stock `release`) | `waivers/waivers_example.yaml` |
| HD broadcast long form (bars, slate, DF timecode, 2 audio streams, captions) | `specs/spec_broadcast_hd_longform.yaml` | `profiles/client_delivery.yaml` (or stock `release` / `broadcast_master`) | |
| Ad spot, short form (5 s slate + 2 s black, no tail) | `specs/spec_spot_short_form.yaml` | `profiles/client_delivery.yaml` | |
| Ad spot with slate reading (**in development**) | `slate/spec_spot_slated.yaml` | stock `spot_render`, `spot_review`, `spot_delivery` | `slate/job.yaml`, `slate/deliverables.csv`, `slate/slates/slate_template.yaml` |
| Colour / comp interim, picture only (graded or log, no audio) | `specs/spec_colour_interim_noaudio.yaml` | `profiles/client_colour_interim.yaml` (only this one: see the file header) | |
| Folder of renders overnight | (one of the above) | (one of the above) | `automation/overnight_qc.sh`, `automation/summarize_reports.py` |

## Suggested folder layout and names

Keep your QC setup outside the CheckMyRender folder, e.g. `~/qc/`:

    ~/qc/
      profiles/   <house>_<purpose>.yaml            e.g. hl_review.yaml, hl_delivery.yaml
      specs/      spec_<client>_<job>_<deliverable>.yaml   e.g. spec_excoffee_rush30_master.yaml
      waivers/    <client>_<job>.yaml               one waiver file per job
      jobs/       <client>_<job>/  job.yaml (+ deliverables.csv, slates/ for slated spots,
                                   waivers.yaml, reports/)   one folder per job
      reports/    <job>_<date>/                     written by overnight_qc.sh

- Lower case, no spaces, `_` between parts. The profile's `name:` = its file name without `.yaml`.
- Do not reuse a stock profile name (`final`, `release`, `review`, `broadcast_master`,
  `colour_interim`, `log_interim`, `spot_render`, `spot_review`, `spot_delivery`): use a prefix.
- A profile is used by path (`--profile ~/qc/profiles/hl_delivery.yaml`). Its `extends: release`
  finds the stock profile automatically (a file of that name next to yours wins).

## One folder per job: job.yaml

A `job.yaml` names everything a job needs: the job id, the spec sheet, the profile, the
deliverables CSV and slate template (slated spots), the waiver file and the report folder
(paths relative to the job.yaml). Run `cmr check RENDER.mov` in that folder and no other
option is needed; `--job PATH/job.yaml` or `--job-dir PATH` work from anywhere, and an option
on the command line always wins. Examples: `quickstart/job.yaml` (unedited demo),
`slate/job.yaml` (commented, :30 spot). For a job without a slate the job file only needs:

    job_version: 1
    spec: spec_excoffee_rush30_master.yaml
    profile: ~/qc/profiles/hl_delivery.yaml     # or a stock name: release
    waivers: waivers.yaml
    output: reports
    job: {id: excoffee_rush30}

Waivers made with `cmr waive` in the folder go into its waivers.yaml and carry the job id,
so they are only applied to renders of this job.

## First steps with a copied pair

    cp templates/specs/spec_master_generic.yaml ~/qc/specs/spec_excoffee_rush30_master.yaml
    cp templates/profiles/client_delivery.yaml  ~/qc/profiles/hl_delivery.yaml
    # edit both (change `name: client_delivery` to `name: hl_delivery`)
    cmr profiles show ~/qc/profiles/hl_delivery.yaml --spec ~/qc/specs/spec_excoffee_rush30_master.yaml
    cmr check RENDER.mov --profile ~/qc/profiles/hl_delivery.yaml --spec ~/qc/specs/spec_excoffee_rush30_master.yaml

`cmr profiles show` must print the merged profile without `config error`. Then run one known-good
render: it should come out PASS (or SKIPPED, see the guide). If it does not, fix the spec first.

## Automation (overnight runs)

    templates/automation/overnight_qc.sh RENDER_DIR REPORT_DIR PROFILE SPEC [WAIVERS]
    CMR_JOB=~/qc/jobs/<job>/job.yaml templates/automation/overnight_qc.sh RENDER_DIR REPORT_DIR
    python3 templates/automation/summarize_reports.py REPORT_DIR

One `cmr check` per file in `--mode background`, one report folder per file (with
`cmr_report_latest.json`), a `summary.tsv` with the exit code of each file. Details and a cron example: USERGUIDE.md, "Batch and overnight runs".

## How these templates were checked

Every spec + profile pair was loaded with `cmr profiles show PROFILE --spec SPEC` and run with
`cmr check` on generated media of the same layout (program length shortened to keep the files
small): master, review, spot, long form (with real SMPTE bars in the head), interim (with and
without a planted blanking defect), slated spot (with tesseract, on Rocky Linux 9 / ffmpeg 5.1.10).
Parameters used here exist in the code: stock profiles in `profiles/`, check parameter schemas in
`src/cmr/checks/` (`params_schema`), profile/spec/job schemas in `src/cmr/schemas.py`, slate
template schema in `src/cmr/checks/slate/template.py`.
