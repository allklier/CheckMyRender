# CheckMyRender

Video QC suite for Rocky Linux (macOS on Apple Silicon is planned). Command-line QC for renders out of
Flame and other finishing apps: an idiot-check that speeds up delivery without missing errors. It checks
the settings of a file against a delivery spec, and inspects picture, sound and the slate of ad spots.

> **Status: 0.0.1, work in progress.** Core and checks work on generated test media and on a few real
> files; not yet validated broadly on real renders. Use it as a second pair of eyes. Expect changes.

## What it checks (today)
- **Settings vs spec:** container, codec/profile (ProRes, H.264, DNxHD/HR), resolution, pixel format,
  frame rate (Avid's 2997/125 accepted for 23.976), scan type and field order, colour tags, MXF operational
  pattern, audio format and layout, filename convention, program frame count, head/tail layout, start
  timecode (also from MXF headers), embedded captions present (608, 708, SMPTE 436M), file decodes.
- **Picture:** blanking (1px edge/mask defects, even partial lines), long black runs (a logo or end card on
  black is not a black run).
- **Sound:** BS.1770 loudness and true peak over the program range (also for masters with one mono stream
  per channel), silence/dead zones, silent channels (intended silence can be declared), clipping.
- **Ad spots with a slate:** the slate is read with OCR and compared with the job data (agency, client,
  Ad-ID, title, length, date), the real file and the colour bars.
- Every problem is reported with **timecode and frame number**.

Not built yet: macOS (Apple Silicon only, planned), dialogue-gated loudness, ML false-positive
reduction, image sequences, caption sidecars and caption quality, stems, stills, freeze frames, HTML/JUnit
reports. See [CLAUDE.md](CLAUDE.md) for the roadmap and design decisions.

## How it works
- **Profile** (YAML, `profiles/`): the rules - which checks run, tolerances, severities. 11 stock profiles
  (`release`, `review`, `review_internal`, `web_delivery`, `broadcast_master`, `colour_interim`,
  `log_interim`, `spot_render`, `spot_review`, `spot_delivery`, base `final`); `cmr profiles list`.
- **Spec sheet** (YAML): the facts about one job - fps, duration, timecode, audio layout, head/tail layout.
  Profiles reference it as `${spec.*}`. Commented starting points are in `templates/specs/`.
- **Job file** (`job.yaml`): the entry point of a job. It names the spec, profile, waiver file and report
  folder (and for ad spots the Ad-ID list and slate template). In the job's folder `cmr check RENDER.mov`
  needs no other option.
- Checks are plugins; all of them run in one ffmpeg pass per file. See [src/cmr/CLAUDE.md](src/cmr/CLAUDE.md).
- Results: console plus JSON report (`cmr_report_<date-time>_<id>.json` and `cmr_report_latest.json`).
  Exit code 0 pass, 1 warn, 2 fail, 3 tool/config error or wrong command line.

## Quick start
The easy way on a Rocky workstation is the per-user installer (no sudo; it builds its own conda
environment, uses the system ffmpeg when it is good enough and installs tesseract for the slate checks).
You get a bundle `cmr-<version>-bundle.tar.gz`:

```
tar xzf cmr-0.0.1-bundle.tar.gz
cmr-0.0.1/install.sh --dry-run        # shows every step, changes nothing
cmr-0.0.1/install.sh
cmr doctor
```

(The installer's first real run is still in progress: USERGUIDE.md section 20 has the status.) It also
copies `examples/` and `templates/` to `~/cmr-workspace`. Never change the system Python on a Flame machine.

From a source checkout instead (Python 3.12+ in a conda environment, ffmpeg and ffprobe on the PATH):

```
conda create -p ./env python=3.12     # then: conda activate ./env
pip install .                         # or: pip install -e .  (both carry the stock profiles)
cmr doctor
```

First check, with no real media needed (the demo scripts are in this repo's `tests/` folder):

```
mkdir -p ~/cmr_demo && cd ~/cmr_demo
python3 ~/CheckMyRender/tests/media/make_media.py . one_track        # one demo render
cmr probe one_track/*.mov                                            # what is in it
```

then follow the user guide's quick start A (copy a spec template, edit five lines, run
`cmr check one_track/*.mov --profile review --spec ...`). A slated ad spot: quick start B, which uses
the ready-made job folder `templates/quickstart/` (`cp -r templates/quickstart ~/qc_demo`, make the demo
spot with `tests/slate/make_spot.py spots --short`, then `cmr check spots/clean/*.mov` in that folder).

Run modes: `--mode foreground` (default) is the fastest; `--mode background` lowers CPU and I/O priority,
uses fewer ffmpeg threads and caps memory, for use while other apps (e.g. Flame) are busy.
`--quality fast|high` overrides the profile's quality for the checks that have a fast mode.

Reviewed findings can be waived (`cmr waive`, per finding or per check category with a severity ceiling;
waivers belong to a job) and reports list at most 10 findings per check (`--max-findings`); suppressed
ones are counted, not ignored.

Problems? `cmr logs collect` writes a support bundle (no private data by default).

Other commands: `cmr probe FILE`, `cmr profiles list`, `cmr profiles show NAME --spec SPEC`, `cmr doctor`.

## Layout
`src/cmr/` code, `profiles/` stock rules, `templates/` commented starter spec sheets, profiles, job
folders, waivers and an overnight script (copy and edit), `examples/` sample specs and a fictional ad-spot
job, `installer/` the per-user installer and bundle builder, per-folder `CLAUDE.md` design notes, `tests/`
tests and demo-media generators (`CMR_TEST_MEDIA=<dir> python3 -m pytest -q`; test media is generated with
ffmpeg, not stored).

## Documentation
- [USERGUIDE.md](USERGUIDE.md): platform requirements, three copy-and-run quick starts (any render, a
  slated ad spot, accepting a reviewed finding), then reference: running checks and the job file,
  reading results, profiles, spec sheets, every check and its parameters, tuning, waivers, overnight
  batches, slate checks, the diagnostic log, a QC test suite for your own renders, troubleshooting,
  glossary; installation on Rocky Linux is the last section.
- [templates/](templates/README.md): commented starter spec sheets, profiles, a waiver file, job folders
  for ad spots with slates and an overnight batch script to copy and edit.

License: MPL 2.0 ([LICENSE](LICENSE)).
