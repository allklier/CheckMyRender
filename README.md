# CheckMyRender

Video QC suite for Rocky Linux (macOS to follow). Command-line QC for renders out of Flame and other apps. An idiot-check that speeds up delivery
without missing errors: checks settings against a delivery spec, and inspects picture and sound.

> **Status: 0.0.1, work in progress.** Core and first checks work on generated test media only.
> Not yet validated on real renders. Expect changes.

## What it checks (today)
- **Settings vs spec:** container, codec/profile, resolution, pixel format, frame rate, scan type,
  color tags, audio format and layout, filename convention, program frame count, head/tail layout,
  start timecode, embedded captions present, file decodes.
- **Picture:** blanking (1px edge/mask defects, even partial lines), long black runs.
- **Sound:** BS.1770 loudness and true peak over the program range, silence/dead zones,
  silent channels, clipping.
- Every problem is reported with **timecode and frame number**.

Not yet: dialogue-gated loudness, freeze frames, ML false-positive reduction, 2-pop, slate
comparison, caption quality, image sequences, HTML/JUnit reports. See [CLAUDE.md](CLAUDE.md) for the
roadmap and design decisions.

## How it works
- **Profile** (YAML, `profiles/`): the rules - which checks run, tolerances, severities.
  `release` is strict for deliveries, `review` is a fast check for review renders; both extend `final`.
- **Spec sheet** (YAML, `examples/`): the facts about one job - fps, duration, timecode, audio
  layout, head/tail layout. Profiles reference it as `${spec.*}`.
- Checks are plugins; all of them run in one ffmpeg pass per file. See
  [src/cmr/CLAUDE.md](src/cmr/CLAUDE.md).
- Results: console plus JSON report. Exit code 0 pass, 1 warn, 2 fail, 3 tool/config error.

## Quick start
Needs Python 3.12+, ffmpeg and ffprobe (tested with ffmpeg 6.1.1 and 5.1.10). Never change the system
Python on a production machine: use a conda environment (a plain venv also works on a dev machine):

```
conda create -p ./env python=3.12     # then: conda activate ./env
pip install -e .                      # editable install: profile lookup needs the source tree
cmr doctor
cmr check render.mov --profile release --spec examples/spec_hd_broadcast.yaml
cmr check render.mov --profile review --spec examples/spec_review.yaml --report json -o reports/
```

(Without installing, `PYTHONPATH=src python3 -m cmr ...` also works if the dependencies are present.)

Run modes: `--mode foreground` (default) is the fastest; `--mode background` lowers CPU and I/O priority,
uses fewer ffmpeg threads and caps memory, for use while other apps (e.g. Flame) are busy.

Reviewed findings can be waived (`cmr waive`, per finding or per check category with a severity ceiling) and
reports list at most 10 findings per check (`--max-findings`); suppressed ones are counted, not ignored.

Problems? `cmr logs collect` writes a support bundle (no private data by default).

Other commands: `cmr probe FILE`, `cmr profiles list`, `cmr profiles show NAME --spec SPEC`.

## Layout
`src/cmr/` code, `profiles/` rules, `examples/` sample specs, per-folder `CLAUDE.md` design notes, `tests/` tests
(`CMR_TEST_MEDIA=<dir> python3 -m pytest -q`; test media is generated with ffmpeg, not stored).

## Documentation
- [USERGUIDE.md](USERGUIDE.md): platform requirements, three quick starts (any render, a slated ad
  spot, accepting a reviewed finding), then reference: running checks, reading results, profiles, spec
  sheets, every check and its parameters, tuning, waivers, overnight batches, slate checks,
  troubleshooting, glossary; installation on Rocky Linux (conda, Python 3.12) is the last section.
- [templates/](templates/README.md): commented starter spec sheets, profiles, a waiver file, slate job
  files (in development) and an overnight batch script to copy and edit.

License: MPL 2.0 ([LICENSE](LICENSE)).
