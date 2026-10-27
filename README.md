# CheckMyRender

Video QC suite for Rocky Linux and macOS on Apple Silicon. Command-line QC for renders out of
Flame and other finishing apps: an idiot-check that speeds up delivery without missing errors. It checks
the settings of a file against a delivery spec, and inspects picture, sound and the slate of ad spots.

> **Status: 0.1.1 (installer release on top of the verified 0.1.0).** Tested on generated media and on real renders (Rocky Linux 9 and
> Apple Silicon macOS, ffmpeg 7.1.1). Use it as a second pair of eyes, not as the final authority: the blanking
> check in particular is noisy on long-form material until the planned ML verifier. Known limits and the next
> steps are in [TODO.md](TODO.md).

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

Not built yet: dialogue-gated loudness, ML false-positive
reduction, image sequences, caption sidecars and caption quality, stems, stills, freeze frames, HTML/JUnit
reports. See [TODO.md](TODO.md) for the roadmap and [CLAUDE.md](CLAUDE.md) for the design decisions.

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

### Install
The easy way on a Rocky workstation is the per-user installer (no sudo; it builds its own conda
environment with the pinned, tested ffmpeg 7.1.1 (the same on every machine, so results are comparable)
and tesseract for the slate checks; `--use-system-ffmpeg` uses the system ffmpeg instead, with a warning
that results may differ).
You get a bundle `cmr-<version>-bundle.tar.gz`:

```
tar xzf cmr-0.1.1-bundle.tar.gz
cmr-0.1.1/install.sh --dry-run        # shows every step, changes nothing
cmr-0.1.1/install.sh
cmr doctor
```

(The installer has been run for real on Rocky Linux and on Apple Silicon macOS: USERGUIDE.md section 20.) It also
puts everything into one folder, `~/cmr` (`--root DIR` for another): `bin/` (cmr and its own environment;
`~/.local/bin/cmr` links to it), `demo/` (examples, templates, demo spots; `--no-demo` skips the spots) and
`work/` (your own specs, profiles, jobs and reports). The folder can be moved later (USERGUIDE.md section 20).
Never change the system Python on a Flame machine.

### First check: the starter folder
The starter folder is a complete job folder for an HD ad spot, and after the installer its `media/`
already holds a clean demo spot. Copy it, check the demo (PASS in seconds), swap in a spot with
errors, then use your own video:

```
mkdir -p ~/qc && cp -r ~/cmr/demo/templates/starter ~/qc/my_first_job && cd ~/qc/my_first_job
cmr check media/*.mov                         # PASS (exit 0); reads the slate (tesseract)
cmr check media/*.mov --job job_no_slate.yaml # PASS without slate reading
rm media/*.mov && cp ~/cmr/demo/demo_spots/errors/client_typo/*.mov media/
cmr check media/*.mov                         # FAIL slate.content: slate CLIENT 'Sample Bugrers' (exit 2)
rm media/*.mov && cp /path/to/your_render.mov media/
cmr check media/*.mov --job job_no_slate.yaml # your render (no slate reading yet)
```

`~/cmr/demo/demo_spots/errors/many_errors/` has a spot with many planted errors. Steps and how to
read the result: [templates/starter/README.md](templates/starter/README.md), USERGUIDE.md quick start A.

From a source checkout instead (Python 3.12+ in a conda environment, ffmpeg and ffprobe on the PATH):

```
conda create -p ./env python=3.12     # then: conda activate ./env
pip install .                         # or: pip install -e .  (both carry the stock profiles)
cmr doctor
```

### Run a first check on your own render
Copy a sample set, edit it for your job, and point `cmr` at your file. Samples (all fictional values):

| What | Sample |
|---|---|
| Spec sheets (job data) | [examples/spec_review.yaml](examples/spec_review.yaml), [examples/spec_hd_broadcast.yaml](examples/spec_hd_broadcast.yaml), [examples/spec_spot_30s.yaml](examples/spec_spot_30s.yaml), more in [templates/specs/](templates/specs/) |
| Profiles (rules) | stock ones ship with the tool (`cmr profiles list`: `review`, `release`, `spot_review`, ...); client starters in [templates/profiles/](templates/profiles/) |
| job.yaml (one file that names spec, profile, CSV, waivers) | [templates/starter/job.yaml](templates/starter/job.yaml), a slated-spot job in [examples/spot_xr/job.yaml](examples/spot_xr/job.yaml) |

Without a job file (spec + stock profile):

```
cmr check /path/to/render.mov --profile review --spec my_spec.yaml
```

With a job file (run it in the folder that holds `job.yaml`; it needs no other options):

```
cd ~/qc_job && cmr check /path/to/render.mov
cmr check /path/to/render.mov --job /path/to/job.yaml     # or from anywhere
```

The console shows the verdict. For a JSON report as well, add `--report json` (written to the current folder, or `-o DIR`), or set `output:` in a job file.
The user guide's quick starts A-C walk through this step by step.

### Test media (optional, if you have no render at hand)
Most people will use real media and can skip this. The demo scripts are in this repo's `tests/` folder
and generate small clips with ffmpeg:

```
mkdir -p ~/cmr_demo && cd ~/cmr_demo
python3 ~/CheckMyRender/tests/media/make_media.py . one_track        # one demo render
cmr probe one_track/*.mov                                            # what is in it
```

From a source checkout (no installer, so no demo spots in the workspace), make the slated demo spots
with `tests/slate/make_spot.py` (needs a Python with Pillow and numpy; output outside the repo):
`python3 tests/slate/make_spot.py ~/qc/spots --short` writes `spots/clean/` and `spots/errors/`
(`--defect client_typo` into another folder: the slate-typo spot). Then `cp -r templates/starter ~/qc/my_first_job`,
copy `spots/clean/*.mov` into its `media/` folder and run `cmr check media/*.mov` there.

### More options
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
