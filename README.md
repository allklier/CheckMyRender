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
  [docs/check-interface.md](docs/check-interface.md).
- Results: console plus JSON report. Exit code 0 pass, 1 warn, 2 fail, 3 tool/config error.

## Quick start
Needs Python 3.12+, ffmpeg and ffprobe (developed against ffmpeg 6.1.1). Python dependencies
(PyYAML, jsonschema) go into a virtual environment:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cmr doctor
cmr check render.mov --profile release --spec examples/spec_hd_broadcast.yaml
cmr check render.mov --profile review --spec examples/spec_review.yaml --report json -o reports/
```

(Without installing, `PYTHONPATH=src python3 -m cmr ...` also works if the dependencies are present.)

Other commands: `cmr probe FILE`, `cmr profiles list`, `cmr profiles show NAME --spec SPEC`.

## Layout
`src/cmr/` code, `profiles/` rules, `examples/` sample specs, `docs/` reference, `tests/` tests
(`CMR_TEST_MEDIA=<dir> python3 -m pytest -q`; test media is generated with ffmpeg, not stored).

More detail: [USERGUIDE.md](USERGUIDE.md) (stub). License: MPL 2.0 ([LICENSE](LICENSE)).
