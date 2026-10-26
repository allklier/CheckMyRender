# CheckMyRender user guide

CheckMyRender (`cmr`) is a command-line QC tool for renders out of Flame and other
finishing apps (Baselight, Resolve, Premiere, Avid and the like). It checks a rendered file
against a **spec sheet** (what the delivery must be) using a **profile** (how strict to be),
looks at every frame and every audio sample, and reports each problem with **timecode and
frame number**.

> **Status: 0.0.1, early.** The checks pass their test suite on generated media and on a few
> real files (see the "Known limits" of each check). Results on your renders still need a
> human look: use cmr as a second pair of eyes, not as the final authority.

Written for operators and technical leads at finishing / post houses who are at home on a
Linux shell but are not software developers.

**New here?** Check section 1 (what the machine needs; installing is section 20), then do
**one** quick start: A for any render, B for an ad spot with a slate. Everything after that is
reference; come back to it when you need it.

Contents

1. [Platform requirements](#1-platform-requirements)
2. [Quick start A: any render, no slate](#2-quick-start-a-any-render-no-slate)
3. [Quick start B: an ad spot with a slate](#3-quick-start-b-an-ad-spot-with-a-slate)
4. [Quick start C: accept a finding you have reviewed](#4-quick-start-c-accept-a-finding-you-have-reviewed)
5. [Running a check](#5-running-a-check)
6. [Reading the results](#6-reading-the-results)
7. [Profiles: the rules](#7-profiles-the-rules)
8. [Spec sheets: the job facts](#8-spec-sheets-the-job-facts)
9. [Quality modes: fast and high](#9-quality-modes-fast-and-high)
10. [The checks, one by one](#10-the-checks-one-by-one)
11. [Tuning](#11-tuning)
12. [Waivers: accepted findings](#12-waivers-accepted-findings)
13. [Batch and overnight runs](#13-batch-and-overnight-runs)
14. [Slate checks](#14-slate-checks)
15. [Diagnostic log](#15-diagnostic-log)
16. [QC test suite for your own renders](#16-qc-test-suite-for-your-own-renders)
17. [Troubleshooting](#17-troubleshooting)
18. [Glossary](#18-glossary)
19. [Not built yet](#19-not-built-yet)
- [Appendix A: demo media used in this guide](#appendix-a-demo-media-used-in-this-guide)
20. [Installation (Rocky Linux)](#20-installation-rocky-linux)

Starter files to copy: [templates/](templates/README.md) and [examples/spot_xr/](examples/spot_xr/).

Conventions: commands are meant to be copied. `$` is not shown. Output shown under a command
was produced by that command on generated test media (Appendix A), unless marked
**UNVERIFIED**. All names in the examples (shows, clients, people, Ad-IDs) are fictional.

Two folders are named in the commands. `TEMPLATES` is the folder with the starter files:
`~/cmr-workspace/templates` after the installer (section 20), or `templates/` in the
CheckMyRender folder you received (`~/CheckMyRender` in this guide). The **demo-media scripts**
(`tests/media/make_media.py`, `tests/slate/make_spot.py`) are in the `tests/` folder of that
CheckMyRender folder; the installer bundle does not contain them.

---

## 1. Platform requirements

- **Linux**, tested on Rocky Linux 9. macOS (Apple Silicon only) is planned, not available
  (section 19).
- **Python 3.12 in its own environment** (conda). Never update or replace the system Python
  on a Flame workstation (Rocky 9 ships 3.9, which Flame and the OS rely on). The installer
  (section 20) creates the environment for you.
- **ffmpeg and ffprobe**, version 5.1 or newer with the filters cmr uses (`cmr doctor` checks
  them). Tested: 6.1.1 on Ubuntu, 5.1.10 on Rocky 9. The installer uses the ones the OS
  provides when they are good enough, else installs ffmpeg 6.1 into cmr's own environment.
- **tesseract 5** reads the slate of ad spots (section 14). The installer installs it by
  default (`--no-slate` leaves it out); without it everything except the slate text checks works.
- Disk: about 3 GB for the installer's environment (1.5 GB without tesseract). Network access
  to conda-forge during installation only; cmr itself never needs the network.
- `cmr doctor` tells you whether the machine is ready.

---

## 2. Quick start A: any render, no slate

Goal: check one render against a spec of its own. About five minutes.

You need a render and `cmr` working (`cmr doctor` says `OK`). Here we use a generated demo
render (one ProRes file, one stereo audio track); with your own render skip the first block
and use your file instead. The demo script is in the `tests/` folder of the CheckMyRender
folder ([Appendix A](#appendix-a-demo-media-used-in-this-guide)).

**1. Make the demo render and copy a spec template.** The templates are never edited in
place. (For real jobs keep specs in `~/qc/specs/`, see [templates/README.md](templates/README.md).)

```
TEMPLATES=~/cmr-workspace/templates          # or ~/CheckMyRender/templates (see the top of this guide)
mkdir -p ~/cmr_demo && cd ~/cmr_demo
python3 ~/CheckMyRender/tests/media/make_media.py . one_track     # needs ffmpeg only
mkdir -p specs
cp $TEMPLATES/specs/spec_review_render.yaml specs/spec_showname_101.yaml
```

**2. Look at what is really in a known-good render**, then edit the few lines marked `# EDIT`
in the copy. Everything else in the template is a normal HD ProRes review render:

```
cmr probe one_track/*.mov
```

| field in the spec | set it to |
|---|---|
| `job.title_code` | the show code in the file name, e.g. `"SHOWNAME"` |
| `job.episode`, `job.version` | as in the file name (`"101"`, `"v03"`; the template already has these) |
| `video.codec_profile` | the ProRes flavour you render, e.g. `"422 HQ"` |
| `timing.program_duration_frames` | frames of the program only, not the head or tail (here `48`) |
| `audio.tracks` | one line per audio stream in the file (`cmr probe` lists them; the demo has one) |

For the demo file the three edits are:

```
sed -i -e 's/title_code: "EXFEAT"/title_code: "SHOWNAME"/' \
       -e 's/codec_profile: "422" /codec_profile: "422 HQ" /' \
       -e 's/program_duration_frames: 2880/program_duration_frames: 48/' specs/spec_showname_101.yaml
```

Also compare `video.width/height/fps` and `head_layout` / `tail_layout` with your render
(the template has a 1-frame slate + 23 black head and a 24-frame black tail).

**3. Check that spec and profile load**, then run the check with a stock profile:

```
cmr profiles show review --spec specs/spec_showname_101.yaml
cmr check one_track/*.mov --profile review --spec specs/spec_showname_101.yaml
```

`profiles show` prints the merged profile and stops with `config error: ...` if the spec or
profile does not load. Choose the profile by purpose: `review` (review renders, fast),
`release` (final deliveries, strict, needs the spec's captions/loudness keys), or a copy of a
`templates/profiles/client_*` file given by path (`--profile ~/qc/profiles/hl_review.yaml`).
`cmr profiles list` shows the stock ones; section 7 explains them.

**4. Read the result.** With the edits above:

```
PASS    one_track/SHOWNAME_101_HD_2398_ST_v03.mov
        24000/1001 fps, 96 frames, start TC 00:59:59:00 (file)
RESULT PASS (exit 0): PASS 22
```

If you forget the edits (run the unedited template copy), the problems are named
one by one. This is how a wrong spec looks:

```
FAIL    one_track/SHOWNAME_101_HD_2398_ST_v03.mov
        24000/1001 fps, 96 frames, start TC 00:59:59:00 (file)
  INFO    filename: filename: expected 'EXFEAT_101_HD_2398_ST_v03.mov', got 'SHOWNAME_101_HD_2398_ST_v03.mov'
  FAIL    timing.frame_count: program frames: expected 2880 (+-0), got 48 (file 96 - head 24 - tail 24); program range shown @ 01:00:00:00-01:00:01:23 (frames 24-71)
  FAIL    video.codec: codec profile: expected '422', got '422 HQ'
RESULT FAIL (exit 2): PASS 19, INFO 1, FAIL 2
```

Each line: severity, which check, what is wrong, then `@ timecode (frame)`: type that
timecode into Flame or your player to see the spot. The last line is the verdict.

A render with a real problem looks the same. Here a demo file whose timecode starts one second
late (`make_media.py . wrong_tc`; this demo has two audio tracks, so the one-track spec also
reports `audio.layout`):

```
FAIL    wrong_tc/SHOWNAME_101_HD_2398_ST_v03.mov
        24000/1001 fps, 96 frames, start TC 01:00:00:00 (file)
  FAIL    audio.layout: audio streams: expected 1, got 2
  FAIL    timing.layout: first program frame (file frame 24, after 24 head frames) has timecode 01:00:01:00, expected 01:00:00:00; 01:00:00:00 is file frame 0 @ 01:00:01:00 (frame 24)
  FAIL    timing.timecode_start: start timecode: expected 00:59:59:00 (program_start_timecode - 24 head frames), got 01:00:00:00 from stream 0 (off by +24 frames) @ 01:00:00:00 (frame 0)
RESULT FAIL (exit 2): PASS 19, FAIL 3
```

**The exit code** (for scripts and cron) is the worst result of all files:

| code | meaning |
|---|---|
| 0 | pass (also: only INFO or SKIPPED) |
| 1 | worst result is WARN: look at it |
| 2 | worst result is FAIL: do not deliver as is |
| 3 | ERROR: the tool could not check (bad config, missing file or filter, wrong command line) |

**What next:** for a check in seconds right after Flame writes the file, use a copy of
`templates/profiles/client_render_check.yaml` (settings only, no picture or sound measurements).
Details: options (section 5), reading results (section 6), profiles (section 7), spec fields
(section 8). A spot with a slate: quick start B. A problem you have looked at and accept:
quick start C.

---

## 3. Quick start B: an ad spot with a slate

Goal: check a slated spot against the job data (agency, client, Ad-ID, title, length) and the
slate text. The slate is read with OCR, so this needs **tesseract** (sections 1 and 14).
About ten minutes. The same job folder works for your real spots: only the values change.

**1. Copy the starter job folder and make the demo spot.** `templates/quickstart/` is a
complete job for the generated demo spot: it works unedited.

```
TEMPLATES=~/cmr-workspace/templates          # or ~/CheckMyRender/templates
cp -r $TEMPLATES/quickstart ~/qc_demo && cd ~/qc_demo
python3 ~/CheckMyRender/tests/slate/make_spot.py spots --short
```

`make_spot.py` is the demo-media generator from the `tests/` folder (not part of the installer
bundle). It needs Python with **Pillow, numpy and PyYAML**; the cmr environment made by the
installer does not contain Pillow and numpy, so use another Python that has them (the system
`python3` on many machines), or add them to an environment of your own
(`conda install pillow numpy`; **UNVERIFIED** for the installer environment). It refuses an
output folder inside the CheckMyRender folder. It writes `spots/clean/` (a correct :03 spot)
and `spots/errors/` (the same spot with planted errors, listed in `spots/manifest.yaml`).

What the job folder contains:

| file | what it is |
|---|---|
| `job.yaml` | the entry point: names the spec, profile, waiver file and report folder, holds the job constants (job id, agency, client, artist), the expected filename pattern and where the CSV and slate template are |
| `spec_spot_short.yaml` | the technical spec: codec, frame rate, head layout, program length, audio |
| `deliverables.csv` | one row per spot of the job: Ad-ID, title, length (no filenames) |
| `slates/slate_template.yaml` | the design of the slate: which labels it has and what each must say |
| profile `spot_review` | the rules (a stock profile named in `job.yaml`, nothing to copy; `spot_render` and `spot_delivery` also exist) |

**2. Run it.** `job.yaml` in the current folder is picked up by itself, so no other option is
needed:

```
cmr check spots/clean/*.mov
```

**3. Read the result.** A good spot:

```
job file: ~/qc_demo/job.yaml (job demo_sb_league)
PASS    spots/clean/SB X League 2026_ProRes Generic Submaster_QB Pat_03_ABCD1234567H_1920x1080_Slated.mov
        30000/1001 fps, 300 frames, start TC 00:59:53:00 (file)
  SKIPPED captions.presence: captions not required by spec
JOB demo_sb_league
RESULT PASS (exit 0): PASS 27, SKIPPED 1
report: ~/qc_demo/reports/cmr_report_20261026-163051_bcc1c4.json (also ~/qc_demo/reports/cmr_report_latest.json)
```

(Paths shortened. SKIPPED = one check did not apply; everything that ran passed, so the file
is PASS, exit 0.) The first line says which job file was used (it goes to stderr), `JOB` names
the job, and because `job.yaml` has an `output:` folder the JSON report is written as well.

The `errors` spot carries the planted errors. Abridged output (audio, colour-bar and some long lines left out or shortened):

```
cmr check spots/errors/*.mov
FAIL    spots/errors/SB X League 2026_ProRes Generic Submaster_QB Pat_03_ABCD1234567H_1280x720_Slated.mov
        30000/1001 fps, 299 frames, start TC 00:59:53:00 (file)
  FAIL    filename: filename: expected 'SB X League 2026_ProRes Generic Submaster_QB Pat_03_ABCD1234567H_1920x1080_Slated.mov', got '...1280x720_Slated.mov'
  FAIL    slate.claims: slate DURATION ':03' = 90 frames (3 s x 30 timecode frames/s) but the program is 89 frames (-1) @ 00:59:53:01-00:59:57:28 (frames 1-148)
  FAIL    slate.claims: slate AUDIO 'Mono' (1 ch) but the file's first audio stream is stereo (2 ch) @ 00:59:53:01-00:59:57:28 (frames 1-148)
  FAIL    slate.consistency: slate ARTIST changes during the slate: frame 1 'Alex Example', frame 148 'Alex Exampe' @ 00:59:53:01-00:59:57:28 (frames 1-148)
  WARN    slate.content: slate CLIENT 'Sample Bugrers', expected 'Sample Burgers' @ 00:59:53:01-00:59:57:28 (frames 1-148)
  FAIL    slate.content: slate does not match the file: slate AD-ID 'ABCD1234568H', expected 'ABCD1234567H' @ 00:59:53:01-00:59:57:28 (frames 1-148)
  WARN    slate.content: slate DATE 20250921 is 400 days before the file date 20261026 (creation_time); allowed +-30 days @ 00:59:53:01-00:59:57:28 (frames 1-148)
  FAIL    timing.frame_count: program frames: expected 90 (+-0), got 89 (file 299 - head 210 - tail 0); program range shown @ 01:00:00:00-01:00:02:28 (frames 210-298)
  FAIL    video.black_runs [fast mode]: 20 consecutive black frames in program @ 01:00:01:05-01:00:01:24 (frames 245-264)
  ...
RESULT FAIL (exit 2): PASS 17, SKIPPED 1, WARN 6, FAIL 13
```

**When a slate field is wrong.** A spot whose slate spells the client `Sample Bugrers`
(make it with `make_spot.py spots_typo --short --defect client_typo`; use a separate output
folder for each defect):

```
cmr check spots_typo/client_typo/*.mov
WARN    spots_typo/client_typo/SB X League 2026_ProRes Generic Submaster_QB Pat_03_ABCD1234567H_1920x1080_Slated.mov
        30000/1001 fps, 300 frames, start TC 00:59:53:00 (file)
  SKIPPED captions.presence: captions not required by spec
  WARN    slate.content: slate CLIENT 'Sample Bugrers', expected 'Sample Burgers' @ 00:59:53:01-00:59:57:28 (frames 1-148)
RESULT WARN (exit 1): PASS 26, SKIPPED 1, WARN 1
```

The message names the slate field, what the slate says and what the job expects. Decide which
side is wrong:

- **the slate is wrong:** fix it in the slate graphic and re-render;
- **the job data is wrong** (typo in `job.yaml` or the CSV, wrong row): fix that file and run again;
- **both are as intended:** accept the finding (quick start C; the slate version is shown there too).

The OCR can misread; a result marked `check by eye` is uncertain: look at the slate frame
(the timecode is in the message).

**Using it for a real spot.** Keep one job folder per job (`~/qc/jobs/<job>/`, copy of
`quickstart/` or of `slate/`, which has more comments and a :30 spot) and edit:

| file | edit |
|---|---|
| `job.yaml` | `job.id`, `agency`, `client`, `artist`; `product: "<empty>"` if your slates leave PRODUCT blank (a blank value means "not checked"); `naming_pattern`/`filename_regex` if your file names differ |
| `deliverables.csv` | your rows: `Ad-ID`, `Title`, `Length` (seconds) |
| `spec_spot_short.yaml` | `program_duration_frames` (:30 at 29.97 = `900`, :60 = `1800`), and codec / fps / audio if your delivery differs |

Leave the slate template alone unless your slate has different labels (section 14).

**What next:** all slate rules, the other profiles, the job file keys and the `--row` option:
sections 5 and 14. Quality of the picture and sound checks, tuning: sections 9 to 11.

---

## 4. Quick start C: accept a finding you have reviewed

Goal: a problem was looked at and is fine (a deliberate fade to black, intentional distortion,
a client name spelt the client's way). Waive it so the next render of the same job does not
flag it again. Waived findings stay visible in the report.

We use the demo render of quick start A with planted defects (a 1-pixel black line on frames
40-44, black frames, audio pushed into clipping). They are made with a few lines of Python
(the generator is in `tests/`, see Appendix A); run in `~/cmr_demo`:

```
python3 - <<'PY'
import sys, os; sys.path.insert(0, os.path.expanduser("~/CheckMyRender/tests/media"))
import make_media as m
m.make(".", "defects", audio_tracks=1, captions=False,
       vf_extra="drawbox=x=0:y=0:w=iw:h=1:color=black:t=fill:enable='between(n,40,44)',"
                "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(n,50,65)'",
       af_extra="volume=volume=30dB:enable='between(t,2.9,3.0)'")
PY
```

Two commands. First write a JSON report and list the findings (each gets a number):

```
cmr check defects/*.mov --profile review --spec specs/spec_showname_101.yaml --report json -o reports/
cmr waive reports/cmr_report_latest.json
   1 FAIL  audio.clipping: stream 1: sample peak 0.00 dBFS >= -0.1 dBFS on channel(s) 1,2 @ 01:00:01:21-01:00:01:23 (defects/SHOWNAME_101_HD_2398_ST_v03.mov)
   4 WARN  audio.loudness: integrated loudness outside -23 +-1 LU; ...
   5 WARN  audio.loudness: true peak above -1 dBTP (first in this 100 ms block); ...
  16 WARN  video.blanking: top: black edge (non-picture, sharp step), 100% of line, seen on 1 sampled frame(s) ...
```

`cmr_report_latest.json` is always the newest report in that folder (every run also keeps its
own `cmr_report_<date-time>_<run id>.json`). Then waive the one you accepted, with a reason:

```
cmr waive reports/cmr_report_latest.json --finding 1 --reason "Intentional distortion effect in the mix" --expires 2027-06-30
W0001: audio.clipping @ 01:00:01:21-01:00:01:23 (finding 1)
waivers: waivers.yaml (1 added, 1 total)
```

Run the next render with the waiver file:

```
cmr check defects/*.mov --profile review --spec specs/spec_showname_101.yaml --waivers waivers.yaml
WARN    defects/SHOWNAME_101_HD_2398_ST_v03.mov
        24000/1001 fps, 96 frames, start TC 00:59:59:00 (file)
  WAIVED  audio.clipping: stream 1: sample peak 0.00 dBFS >= -0.1 dBFS on channel(s) 1,2 @ 01:00:01:21-01:00:01:23 (frames 69-71)
          was FAIL; waiver W0001: Intentional distortion effect in the mix
  WARN    audio.loudness: integrated loudness outside -23 +-1 LU; ...
  ...
RESULT WARN (exit 1): PASS 19, WARN 3, WAIVED 1
WAIVERS waivers.yaml: applied 1, content changed 0, stale 0, expired 0
```

The clipping no longer counts; the WARNs are still there for you to look at.

**In a job folder** (quick start B) it is shorter: `cmr check` finds `job.yaml`, writes the
report into its `output:` folder and uses its `waivers.yaml` by itself, and `cmr waive` writes
into that file and stamps each waiver with the job id, so it only applies to this job. Run in
the `~/qc_demo` folder of quick start B, with the `Sample Bugrers` spot:

```
cmr check spots_typo/client_typo/*.mov                      # WARN, exit 1
cmr waive reports/cmr_report_latest.json                    # lists the findings
cmr waive reports/cmr_report_latest.json --finding 15 --reason "Client spelling is intended in the slate"
W0001: slate.content @ 00:59:53:01-00:59:57:28 (finding 15)
waivers: ~/qc_demo/waivers.yaml (job demo_sb_league) (1 added, 1 total)
cmr check spots_typo/client_typo/*.mov
PASS    spots_typo/client_typo/SB X League 2026_ProRes Generic Submaster_QB Pat_03_ABCD1234567H_1920x1080_Slated.mov
        30000/1001 fps, 300 frames, start TC 00:59:53:00 (file)
  SKIPPED captions.presence: captions not required by spec
  WAIVED  slate.content: slate CLIENT 'Sample Bugrers', expected 'Sample Burgers' @ 00:59:53:01-00:59:57:28 (frames 1-148)
          was WARN; waiver W0001: Client spelling is intended in the slate
RESULT PASS (exit 0): PASS 26, SKIPPED 1, WAIVED 1
```

(The finding number, 15 here, is whatever the listing shows for your run.) Run `cmr waive`
from the folder you ran `cmr check` in, with the same media file in place. A waiver remembers
what the picture or sound (for a slate: the exact slate text) looked like: if the content
changes, the finding comes back for a new review.

**What next:** category waivers ("all dark-edge findings on the left"), expiry, stale waivers,
job-scoped waivers: section 12.

---

## 5. Running a check

```
cmr check FILE [FILE ...] --profile PROFILE --spec SPEC.yaml [options]
cmr check FILE [FILE ...]                       # in a folder with a job.yaml (below)
```

- `--profile`: a stock profile name (`release`, `review`, ...; list them with
  `cmr profiles list`) or the path to a profile `.yaml` file.
- `--spec`: the spec sheet of the job.
- Both are optional when a job file names them (next sub-section). Without a job file, a
  missing `--spec` or `--profile` is an error (exit 3).
- Several files: list them, or use a shell wildcard. All files are checked against the same
  spec, and you get one combined report. Give only media files: `good/*` would also pick up a
  `captions.scc` sidecar, which is reported as `ERROR   probe: no video stream`.

```
cmr check one_track/*.mov defects/*.mov --profile release --spec specs/spec_showname_101.yaml
```

A spec describes ONE deliverable (its filename, length and timecode). For a folder of
different deliverables run one `cmr check` per file with its own spec (section 13).

### The job file: job.yaml

A **job file** is the entry point for a job. It names everything the job needs, so that
`cmr check RENDER.mov`, run in the job's folder, needs no other option. cmr uses a
`job.yaml` in the current folder by itself (a line `job file: PATH (job ID)` on stderr says
which); `--job PATH/job.yaml` or `--job-dir DIR` use another from anywhere; `--no-job`
ignores `./job.yaml`. An option on the command line always wins over the job file. `cmr waive`
takes the same three options.

```
job_version: 1                 # always 1
spec: spec_spot_short.yaml     # the spec sheet (paths are relative to the job file; ~ is allowed)
profile: spot_review           # a stock profile name, or a path to your own .yaml
waivers: waivers.yaml          # the waiver file (default: waivers.yaml next to the job file; may not exist yet)
output: reports                # report folder; also switches the JSON report on
job:
  id: demo_sb_league           # short job id: letters, digits, _ - .  (stamped on waivers, shown in reports)
  # ...job constants for slated spots: agency, client, naming_pattern ... (section 14)
deliverables: {...}            # slated spots only: the CSV (section 14)
slate: {...}                   # slated spots only: template and expectations (section 14)
```

A job without a slate only needs the first lines, e.g. `job_version`, `spec`, `profile`,
`waivers`, `output`, `job: {id: ...}`. Examples: `templates/quickstart/job.yaml` (unedited
demo), `templates/slate/job.yaml` (commented), `examples/spot_xr/`. See also
[templates/README.md](templates/README.md). `cmr doctor` shows which job file the current
folder has (and says `INVALID` with the reason if it does not load).

### Options of `cmr check`

| option | what it does |
|---|---|
| `-v`, `--verbose` | also list the checks that passed |
| `--report json -o DIR` | also write the JSON report into `DIR` (created; the current folder without `-o`): `cmr_report_<date-time>_<run id>.json` plus a copy `cmr_report_latest.json`. Runs never overwrite each other's reports; `latest` is always the newest. A job file's `output:` switches this on by itself |
| `--fail-on warn\|fail` | lowest severity that makes the exit code non-zero (default `warn`) |
| `--max-findings N` | list at most N problems per check, summarise the rest (default 10; `0` = list all). Overrides the profile's `max_findings` |
| `--mode foreground\|background` | `foreground` (default): as fast as possible. `background`: do not disturb other work (see below) |
| `--quality fast\|high` | run every check that has a fast mode (today `video.blanking` and `video.black_runs`) in this mode; other checks stay high. The flag beats the profile; the report records it (section 9) |
| `--filter-threads N` | ffmpeg filter threads (default: half the CPUs, at most 8). Overrides the mode's value |
| `--waivers FILE` | apply a waiver file (section 12; default: the job file's) |
| `--job`, `--job-dir`, `--no-job` | which job file (above) |
| `--deliverables CSV`, `--row VALUE` | slate checks: another CSV, or pick its row by hand (section 14) |

Global options go BEFORE the sub-command: `cmr --ffmpeg /opt/ffmpeg/bin/ffmpeg --ffprobe
/opt/ffmpeg/bin/ffprobe check ...` uses another ffmpeg build. The logging options
(`--log-level`, `--log-file`, `--log-private`, section 15) are accepted before or after the
sub-command.

### Exit codes

| code | meaning |
|---|---|
| 0 | passed (or only INFO / SKIPPED; also WARN with `--fail-on fail`) |
| 1 | worst result is WARN |
| 2 | worst result is FAIL |
| 3 | ERROR: the tool could not check something (bad config, missing file, missing filter), **or a wrong command line** |

So exit 2 always means "a FAIL finding", never a typing mistake. The exit code is computed
from all findings, including findings not listed because of the `--max-findings` cap. Example
of `--fail-on fail` (the demo file of quick start C, whose FAIL is waived, so only warnings
are left):

```
cmr check defects/*.mov --profile review --spec specs/spec_showname_101.yaml --waivers waivers.yaml --fail-on fail
...
RESULT WARN (exit 0): PASS 19, WARN 3, WAIVED 1
```

(A wrong command line, e.g. an unknown option, is printed by the option parser as
`cmr: error: unrecognized arguments: --bogus` and exits 3; there is then no `RESULT` line.)

### Run modes: foreground and background

| | foreground (default) | background |
|---|---|---|
| CPU priority | normal | nice 15 (lower) |
| disk priority | normal | idle (`ionice -c 3`, Linux) |
| ffmpeg filter threads | min(8, CPUs/2) | 2 |
| memory | no limit | 16 GB virtual-memory cap for cmr and its ffmpeg |

Use `--mode background` on a Flame workstation while Flame is in use, and for overnight
batches. It takes longer, mostly because of the 2 filter threads (the blanking and black-run
measurements are the expensive part). How much longer on a real render: UNVERIFIED. The mode
is recorded in the report (`summary.mode`).

### Other commands

| command | what it does |
|---|---|
| `cmr probe FILE` | prints what cmr sees in a file (codec, profile, pixel format, colour tags, audio streams, timecode) in the words a spec uses. Run it on a known-good render when writing a spec |
| `cmr profiles list` | stock profiles |
| `cmr profiles show PROFILE [--spec SPEC] [--job JOB]` | the merged profile; with `--spec`, filled in with the spec's values. The quickest way to see whether a spec/profile pair loads, and it reports a misspelt parameter |
| `cmr waive ...` | create waivers (section 12) |
| `cmr doctor` | installation check (section 20) |
| `cmr logs path\|tail\|collect` | diagnostic log (section 15) |
| `cmr --version` | prints `cmr 0.0.1` |

`cmr probe` (shortened and some lines joined):

```
cmr probe one_track/*.mov
{
  "path": "one_track/SHOWNAME_101_HD_2398_ST_v03.mov",
  "kind": "file",
  "container": "mov",
  "timecode": "00:59:59:00",
  "timecode_source": "stream 0",
  "video": [
    {
      "index": 0,
      "codec": "prores",
      "codec_profile": "422 HQ",
      "width": 1920,
      "height": 1080,
      "pixel_format": "yuv422p10le",
      "sample_aspect_ratio": "1:1",
      "display_aspect_ratio": "16:9",
      "fps": "24000/1001",
      "scan": "progressive",
      "field_order": "progressive",
      "color": {"primaries": "bt709", "transfer": "bt709", "matrix": "bt709", "range": "tv"},
      ...
  "audio": [
    {"index": 1, "codec": "pcm_s24le", "sample_rate": 48000, "bit_depth": 24,
     "channels": 2, "channel_layout": "stereo", ...},
  ...
}
```

---

## 6. Reading the results

### The console report

```
cmr check defects/*.mov --profile release --spec specs/spec_showname_101.yaml
FAIL    defects/SHOWNAME_101_HD_2398_ST_v03.mov
        24000/1001 fps, 96 frames, start TC 00:59:59:00 (file)
  FAIL    audio.clipping: stream 1: sample peak 0.00 dBFS >= -0.1 dBFS on channel(s) 1,2 @ 01:00:01:21-01:00:01:23 (frames 69-71)
  FAIL    audio.loudness: integrated loudness outside -23 +-0.5 LU; EBU R128: I -15.2 LUFS, LRA 0.0 LU, true peak 1.0 dBTP [BS.1770 relative-gated (-70 LUFS absolute, -10 LU relative); not dialogue-gated] @ 01:00:00:00-01:00:01:23 (frames 24-71)
  FAIL    audio.loudness: true peak above -1 dBTP (first in this 100 ms block); EBU R128: I -15.2 LUFS, LRA 0.0 LU, true peak 1.0 dBTP [BS.1770 relative-gated (-70 LUFS absolute, -10 LU relative); not dialogue-gated] @ 01:00:01:21-01:00:01:23 (frames 69-71)
  SKIPPED captions.presence: captions not required by spec
  FAIL    video.black_runs: 16 consecutive black frames in program @ 01:00:01:02-01:00:01:17 (frames 50-65)
  FAIL    video.blanking: top: black edge (non-picture, sharp step), 100% of line, 5 frame(s) @ 01:00:00:16-01:00:00:20 (frames 40-44)
RESULT FAIL (exit 2): PASS 18, SKIPPED 1, FAIL 5
```

Line by line:

- `FAIL    defects/...mov`: the file's verdict = its worst finding.
- `24000/1001 fps, 96 frames, start TC 00:59:59:00 (file)`: what the timeline is built on.
  `(file)` = timecode read from the file; `(spec)` = no timecode in the file, derived from the
  spec's `program_start_timecode` minus the head.
- One line per problem: severity, check id, message, `@ timecode (frame)`. Ranges are
  inclusive: `frames 50-65` is 16 frames.
- `RESULT ...`: worst severity over all files, the exit code, and counts per severity.
  `-v` also prints the PASS lines.
- With a job file there are three more lines: `job file: PATH (job ID)` before the result
  (stderr), `JOB <id>` before `RESULT`, and `report: PATH` when a JSON report was written.

**Timecode and frame number.** The timecode is the file's own timecode (what you type into
Flame or your player to jump there). The frame number counts from the first frame of the file,
starting at 0. Both point to the same frame. Audio findings are placed on the video frame
that contains the audio.

### Severities

| severity | meaning | exit code |
|---|---|---|
| PASS | check ran, nothing found | 0 |
| SKIPPED | check did not apply (e.g. captions not required, no audio stream, head/tail layout does not fit the file) | 0 |
| INFO | worth knowing, not a problem by the profile's rules | 0 |
| WARN | probable problem, look at it | 1 |
| FAIL | problem by the profile's rules | 2 |
| ERROR | the tool could not do the check (config error, crash, missing filter) | 3 |
| WAIVED | a reviewed and accepted finding (section 12); counted, not in the exit code | 0 |

The **profile** decides what is FAIL, WARN or INFO for each check.

A file whose only non-PASS results are SKIPPED is **PASS** (exit 0): "everything that ran passed;
something did not apply". The SKIPPED lines are still listed and counted:

```
cmr check one_track/*.mov --profile release --spec specs/spec_showname_101.yaml
PASS    one_track/SHOWNAME_101_HD_2398_ST_v03.mov
        24000/1001 fps, 96 frames, start TC 00:59:59:00 (file)
  SKIPPED captions.presence: captions not required by spec
RESULT PASS (exit 0): PASS 22, SKIPPED 1
```

`[fast mode]` after a check id means it ran in fast mode (section 9):

```
  WARN    video.blanking [fast mode]: top: black edge (non-picture, sharp step), 100% of line, seen on 1 sampled frame(s) (fast: 1 in 5 frames; true start/end up to 4 frame(s) outside the range shown) @ 01:00:00:06 (frame 30)
```

A different spec value of the same kind that is accepted but not identical (an Avid file whose
frame rate is written 2997/125 for 23.976) shows as an INFO line naming both values (section 10).

### The findings cap

A check never lists more than 10 problems per file by default. The rest is summarised in
one line, and still counted and in the exit code:

```
cmr check many/*.mov --profile release --spec specs/spec_showname_101.yaml --max-findings 3
FAIL    many/SHOWNAME_101_HD_2398_ST_v03.mov
        24000/1001 fps, 96 frames, start TC 00:59:59:00 (file)
  SKIPPED captions.presence: captions not required by spec
  FAIL    video.blanking: top: black edge (non-picture, sharp step), 100% of line, 1 frame(s) @ 01:00:00:00 (frame 24)
  FAIL    video.blanking: top: black edge (non-picture, sharp step), 100% of line, 1 frame(s) @ 01:00:00:03 (frame 27)
  FAIL    video.blanking: top: black edge (non-picture, sharp step), 100% of line, 1 frame(s) @ 01:00:00:06 (frame 30)
  (11 additional video.blanking findings suppressed; worst: FAIL)
RESULT FAIL (exit 2): PASS 21, SKIPPED 1, FAIL 14 (11 not listed but counted; --max-findings 0 lists all)
```

The worst findings are listed first, so a FAIL is never hidden behind INFO lines. Use
`--max-findings 0` to see (and waive) every one.

### WAIVED lines

```
  WAIVED  video.black_runs: 16 consecutive black frames in program @ 01:00:01:02-01:00:01:17 (frames 50-65)
          was FAIL; waiver W0001: Fade to black in the edit, approved by the director
  WAIVED  audio.clipping: 2 finding(s) by category waiver W0002 (worst was FAIL) @ 01:00:01:21-01:00:01:23 (frames 69-71)
WAIVERS waivers.yaml: applied 2, content changed 0, stale 1, expired 0
```

See section 12.

### The JSON report

`--report json -o DIR` writes `DIR/cmr_report_<date-time>_<run id>.json` (schema version 1.4)
and refreshes `DIR/cmr_report_latest.json`, a copy of the newest run. Runs never overwrite each
other's reports. The console prints `report: PATH`. Use `latest` for `cmr waive` and scripts,
the dated files as the archive. Main fields:

| field | content |
|---|---|
| `schema_version` | `"1.4"` |
| `tool` | `name`, `version`, `ffmpeg` (version line), `python` |
| `profile` | `name`, `sha256` (one hash per profile file in the `extends` chain) |
| `spec` | `path`, `sha256` (and `job` with the job file's path and hash) |
| `files[]` | one entry per checked file, below |
| `summary` | `worst`, `exit_code`, `fail_on`, `counts` (per severity, incl. `WAIVED`), `mode` (`foreground`/`background`), `quality` (`profile`, or `fast`/`high` from `--quality`), `job_id`, `max_findings`, `suppressed` |
| `waivers` | only with waivers: `file`, `applied`, `content_changed`, `stale`, `expired`, `foreign` (waivers of another job, not applied) |

Per file: `path`, `identity` (size + checksum of the first and last MiB: proves which file
was checked), `timeline` (`fps`, `drop_frame`, `file_frames`, `start_timecode`,
`timecode_source`, `head_frames`, `tail_frames`, `program_start`, `program_end`, `segments`),
`worst`, `findings[]`, `suppressed[]`, `waived_by_category[]`.

Per finding:

```
{
 "check_id": "video.blanking",
 "severity": "FAIL",
 "message": "top: black edge (non-picture, sharp step), 100% of line, 5 frame(s)",
 "raw_severity": "FAIL",          <- before any waiver
 "stream": 0,
 "frame_start": 40, "frame_end": 44,
 "timecode_start": "01:00:00:16", "timecode_end": "01:00:00:20",
 "expected": null, "actual": null, <- filled by the settings checks
 "evidence": [{"edge": "top", "kind": "edge", "persistent": false, "frames": 5,
               "coverage_mean": 0.999, "coverage_max": 0.999, "depth_px": "1-16"}],
 "candidate": {...},              <- raw detector output (same evidence)
 "verifier": null,
 "waiver_ref": null, "waiver": null,
 "quality": "high"
}
```

A morning summary of a folder of reports: `TEMPLATES/automation/summarize_reports.py`
(section 13).

---

## 7. Profiles: the rules

A profile says which checks run, how strict they are and how fast. It never contains job
facts; those come from the spec through `${spec....}` references.

### Stock profiles (in `profiles/`, do not edit)

`cmr profiles list` prints these 11 names. They extend each other, so a line below only says
what is different from its parent.

| profile | for | in short |
|---|---|---|
| `final` | base of all others | every check, high quality, everything FAIL (colour/scan untagged: WARN; blanking mattes: WARN) |
| `release` | deliveries | `final` + EBU R128 +-0.5 LU, black runs and silences max 6 frames, captions FAIL |
| `review` | review renders | `final` + blanking and black runs FAST, blanking and loudness WARN, dark picture edges INFO, filename INFO, captions off, black runs / silences up to 24 frames |
| `review_internal` | internal reviews with temp audio | `review` + loudness off; colour tag mismatches and a wrong colour range INFO |
| `web_delivery` | online / platform uploads | `release` + a "Web -16" loudness preset (-16 LUFS +-1 LU, -1 dBTP; the spec names it); colour mismatches WARN |
| `broadcast_master` | long-form masters | `release` + silent channels INFO, 25 findings per check for black runs and blanking, colour mismatches WARN |
| `colour_interim` | graded / comped interims, picture only | `review` + timing, naming, black runs INFO; loudness, all audio checks, captions and filename off; sharp blanking FAIL; colour mismatches WARN. Works with a spec that has no `audio:` section |
| `log_interim` | log-encoded interims | `colour_interim` + colour tags INFO, black runs off, dark edges hidden |
| `spot_render` | a slated spot right after the render | settings, layout, filename, decode errors and "is there a slate" only (section 14) |
| `spot_review` | a slated spot before agency/client review | `final` + the slate checks; picture checks fast; loudness and bars WARN; AD-ID, title and duration mismatches FAIL (section 14) |
| `spot_delivery` | a slated spot for delivery | `spot_review`, everything high and FAIL (dates and uncertain OCR reads stay WARN) (section 14) |

Colour tags: in `final`, `release` and `spot_delivery` every colour mismatch (primaries,
transfer, matrix, range) is a FAIL; in `review_internal` a range mismatch and the others are
INFO; in `colour_interim`, `broadcast_master` and `web_delivery` they are WARN. A file tagged
`yuvj420p` (full range) is reported once, as a range problem.

Templates to copy: `templates/profiles/` (`client_render_check`, `client_review`,
`client_delivery`, `client_colour_interim`), see [templates/README.md](templates/README.md).

### Format

```
profile_version: 1                 # always 1
name: hl_delivery                  # = file name without .yaml (never a stock profile's name)
extends: release                   # optional: start from this profile
description: House delivery rules  # optional
checks:
  CHECK_ID:
    enabled: true                  # reserved key: run this check (default true)
    severity: fail                 # reserved: severity of a problem: info | warn | fail (default fail)
    quality: high                  # reserved: fast | high (default high; section 9)
    max_findings: 10               # reserved: problems listed per check (0 = all)
    some_param: 12                 # everything else = check parameters (section 10)
waivers:                           # optional, section 12
  video_max_bits: 10
  audio_max_db: 2.0
  margin_frames: 5
```

Only these top-level keys are allowed; anything else is a config error.

### extends and merging

- `extends: NAME` loads that profile first. It looks for `NAME.yaml` next to your file, then in
  the CheckMyRender `profiles/` folder. Chains are fine (`log_interim` -> `colour_interim` ->
  `review` -> `final`).
- Your settings are merged into it **per key**: write only what you change.
  `video.blanking: {severity: warn}` keeps every other blanking setting.
- Lists and single values are **replaced**, mappings (key: value blocks) are merged.
- A check in the parent that you want off: `CHECK_ID: {enabled: false}`.

### ${spec....} references

`expect: "${spec.video.codec}"` is replaced by the spec's value when the run starts.
`${spec.audio.tracks.0.layout}` = first entry of a list. A reference to a key the spec does
not have stops the run (exit 3) and says what to do:

```
config error: missing spec key ${spec.video.codec_profile}, used by profile check video.codec (profile 'review')
  the spec has no 'video.codec_profile'.
  Fill in `video: codec_profile:` in the spec sheet specs/spec_nokey.yaml
  or switch that check off in the profile (`enabled: false`).
  Spec fields are explained in USERGUIDE.md, "Spec sheets: the job facts".
```

- **Checks you switched off (`enabled: false`) are not filled in**, so their spec keys are not
  needed: switching a check off is enough to drop its spec keys.
- Optional values may be left out of the spec or set to `null`; that means "not checked"
  (`video.width`, `video.height`, `audio.sample_rate`, `audio.bit_depth`,
  `timing.program_start_timecode`, `timing.file_start_timecode`,
  `timing.program_duration_frames`).
- The stock profiles still refer to most spec sections (`video`, `timing`, `audio.codec`,
  `audio.tracks`, `audio.loudness`, `content.captions`, `job`), so a spec for them needs those
  keys, unless the check is off. The `colour_interim` and `log_interim` profiles are the
  picture-only exception: they work without an `audio:` section.

### Writing your own profile

1. Copy a template from `templates/profiles/` to e.g. `~/qc/profiles/hl_delivery.yaml`.
2. Change `name:`, adjust the checks.
3. Check it loads and see the result: `cmr profiles show ~/qc/profiles/hl_delivery.yaml --spec SPEC.yaml`
4. Run it on a known-good render (expect PASS) and on a known-bad one.

Watch out:

- **A misspelt parameter name stops the run** (exit 3), for every check, also for checks that
  are switched off; `cmr profiles show PROFILE` shows it without running a file:

```
config error: profile 'typo' params invalid:
  checks.video.blanking: unknown parameter(s) 'coverge' (allowed: black_luma, class_severity, coverage, coverage_tolerance, ...)
```

- A misspelt check id is an ERROR: `ERROR   video.blackruns: unknown check id`.
- A misspelt `quality` stops the run: `checks/video.blanking/quality: 'fats' is not one of ['fast', 'high']`.
- Top-level keys other than the ones listed above are a config error.

---

## 8. Spec sheets: the job facts

One spec sheet per deliverable. Templates: `templates/specs/` (generic master, HD long form,
spot short form, review render, colour interim without audio), `templates/quickstart/` and
`templates/slate/` (ad spots). Examples: `examples/`.

### All fields

| field | example | used by | notes |
|---|---|---|---|
| `spec_version` | `1` | loader | required, always 1 |
| `job.title`, `job.title_code`, `job.episode`, `job.version`, `job.client` | `"EXFEAT"` | filename | free fields; any `job.x` can be a `{x}` placeholder in `naming_pattern` |
| `job.filename` | `"EXFEAT_101_HD_2398_ST_v01.mov"` | filename | exact expected name; wins over the pattern |
| `job.naming_pattern` | `"{title_code}_{episode}_{format}_{fps_tag}_{audio_tag}_{version}.mov"` | filename | see "Filename" below |
| `video.container` | `mov` | container.format | as `cmr probe` prints it (`mov`, `mp4`, ...) |
| `video.codec` | `prores` | video.codec | ffprobe codec name (`prores`, `h264`, `dnxhd`) |
| `video.codec_profile` | `"422 HQ"` | video.codec | ProRes: `"422 Proxy"`, `"422 LT"`, `"422"`, `"422 HQ"`, `"4444"`, `"4444 XQ"`; H.264: `"High"`, `"Main"` ...; DNxHR: `"DNxHR HQ"` (LB, SQ, HQ, HQX, 444); DNxHD: the class as `cmr probe` shows it, e.g. `"DNxHD 175x"` (the `x` = 10-bit) |
| `video.width`, `video.height` | `1920`, `1080` | video.resolution | |
| `video.pixel_aspect` | `1.0` | video.resolution | untagged files count as square |
| `video.aspect_ratio` | `"16:9"` | video.resolution | display aspect |
| `video.pixel_format` | `yuv422p10le` | video.pixel_format | ProRes 4444 decodes as `yuv444p12le`; `yuvj420p` etc. count as `yuv420p` with full range |
| `video.fps` | `"24000/1001"` | video.fps, timing | **exact fraction in quotes**; `23.976` is rejected |
| `video.scan` | `progressive` | video.scan | `progressive` or `interlaced` |
| `video.field_order` | `tt` | video.field_order | optional: `progressive`, `tt` (top field first), `bb` (bottom first), `tb`, `bt`; absent = not checked |
| `video.operational_pattern` | `OP1a` | container.op | optional, MXF only (`OP1a`, `OP1b`, `OPAtom` ...; case and `-` ignored); absent = not checked; not MXF = SKIPPED |
| `video.color` | `{primaries: bt709, transfer: bt709, matrix: bt709, range: tv}` | video.color | `tv` = limited/legal, `pc` = full; the profile can give each key its own severity (`key_severity`, section 10) |
| `timing.timecode_mode` | `non-drop` | timing | `non-drop` or `drop` |
| `timing.program_start_timecode` | `"01:00:00:00"` | timing | timecode of the first program frame (FFOA). DF: `"01:00:00;00"` |
| `timing.file_start_timecode` | `"00:59:59:00"` | timing.timecode_start | optional; derived from the head if omitted |
| `timing.program_duration_frames` | `2880` | timing.frame_count | program only, in frames |
| `timing.head_layout` | list | timing, blanking, black runs, audio | below |
| `timing.tail_layout` | list | same | below; `[]` = no tail |
| `audio.codec` | `pcm_s24le` | audio.format | `pcm_s16le`, `aac`, ... |
| `audio.sample_rate` | `48000` | audio.format | |
| `audio.bit_depth` | `24` | audio.format | compared for PCM only |
| `audio.tracks` | list | audio.layout | below |
| `audio.loudness.standard` | `"EBU R128"` | audio.loudness | must be a name in the profile's `standards` |
| `audio.loudness.program_track` | `0` | audio.loudness | which audio stream carries the main mix (0 = first) |
| `audio.loudness.programs` | list | audio.loudness | instead of `program_track`, for masters with one mono stream per channel: see "Loudness of multi-stream masters" below |
| `audio.allowed_silence` | list | audio.silence, audio.silent_channel | channels that are meant to be silent: see below |
| `content.captions` | `{required: true, formats: [embedded_608]}` | captions.presence | `embedded_608`, `embedded_708`, `vanc_436m` (SMPTE 436M caption data in an MXF data track) |

A value that is **optional** (`video.width`, `video.height`, `audio.sample_rate`,
`audio.bit_depth`, `timing.program_start_timecode`, `timing.file_start_timecode`,
`timing.program_duration_frames`, `video.field_order`, `video.operational_pattern`) may be left
out or set to `null`: that check is then "not checked". A key the profile refers to that is
neither optional nor present is a config error that names the key and the spec file (section 7);
checks that are switched off need no keys.

### Frame rate as a fraction

| rate | write |
|---|---|
| 23.976 | `"24000/1001"` |
| 24 | `"24"` |
| 25 | `"25"` |
| 29.97 | `"30000/1001"` |
| 50 | `"50"` |
| 59.94 | `"60000/1001"` |

`fps: 23.976` stops with `config error: spec ... invalid: video/fps: 23.976 is not of type 'string', 'integer'`.

**Avid files.** Avid writes 23.976 as `2997/125` (and 29.97 as `2997/100`). A spec of
`"24000/1001"` accepts such a file: `video.fps` reports one INFO line per frame-rate field
naming both values, and all frame numbers are computed from the file's own rate (the
difference is below one frame per hour). Only these NTSC-equivalent pairs are accepted: 24 and
23.976 stay different, as do 30 and 29.97. UNVERIFIED on a real Avid file (tested on generated
rates and by code review).

### Timecode, drop-frame and durations

- All positions are frames. A duration is a frame count: count it in Flame or your NLE.
- Drop-frame (29.97/59.94 only; also accepted for Avid's 2997/100) is written with `;` before the frames: `01:00:00;00`. In DF,
  a duration of `00:44:26;00` is 79,900 frames, not 44 x 60 x 30 + 26 x 30.
- Spots count timecode seconds: :30 at 29.97 = 30 x 30 = 900 frames; at 23.976 = 30 x 24 = 720.
- Where the file's start timecode is read from: a timecode track (MOV `tmcd`), else for MXF the
  timecode tag in the file header (most MXF masters have no timecode track). A `;` in it means
  drop-frame. The `timing.timecode_start` message names the source (`from stream 0` / `from format`).
- `program_start_timecode` is the timecode the first program frame must carry. cmr derives the
  file start timecode from it and the head length (01:00:00:00 minus 24 head frames =
  00:59:59:00) and compares it with the file.

### Head and tail layout

The frames before the program (head) and after it (tail), in order from file frame 0:

```
  head_layout:
    - {type: bars_tone, frames: 900}
    - {type: black, frames: 150}
    - {type: slate, frames: 300}
    - {type: black, frames: 150}
    - {type: program}               # always last, no frames
  tail_layout:
    - {type: black, frames: 150}    # [] = nothing after the program
```

Types: `slate`, `black`, `bars_tone`, `two_pop`, `program`. cmr uses the frame counts to find
the program range: black runs, loudness, silence and clipping are measured **inside the program
only**, and the blanking check skips the `slate` segment (and others you list in its
`exclude_segments`). cmr does not yet check what is inside each head segment (is the black
black, are the bars bars); that is the roadmap (the slate checks of section 14 read the slate).

If the head plus tail is longer than the file, `timing.layout` FAILs with
`head (231) + tail (24) frames fall outside the file (96 frames)` and the content checks are
SKIPPED (see Troubleshooting).

### Audio tracks

```
  tracks:
    - {index: 0, layout: stereo, content: "Stereo full mix L/R"}
    - {index: 1, layout: "5.1", content: "5.1 full mix"}
```

- One entry per **audio stream** of the file, in file order. `index` 0 = first audio stream.
- `layout`: `mono`, `stereo`, `"5.1"` (quoted), `"7.1"`. Other layouts SKIP the layout check.
- A file with one mono stream per channel (typical for MXF masters) must be listed as that many
  `mono` tracks; `audio.layout` counts streams and channels per stream. The streams are merged
  into a surround or stereo program for the loudness measurement only (below). An MXF stream
  with 2 channels and no layout tag counts as `stereo`.
- In messages, streams are counted from 1 (`stream 1` = `index: 0`), while the spec counts from 0.
- `content` is a free description.
- `tracks: []` = the file must have no audio (use with a profile that does not need the other
  audio keys, `templates/profiles/client_colour_interim.yaml`).

### Loudness of multi-stream masters

When the main mix is spread over mono streams (for example 8 streams: 5.1 on 1-6, stereo on
7-8), name the groups in the spec instead of `program_track`:

```
  audio:
    loudness:
      standard: "ATSC A/85"
      programs:
        - {name: "5.1", streams: [0, 1, 2, 3, 4, 5], channels: [L, R, C, LFE, Ls, Rs]}
        - {name: stereo, streams: [6, 7]}
```

`streams` are positions among the audio streams (from 0), merged in this order. `channels`
names each merged channel (`L R C LFE Ls Rs`, `M` for mono, `-` to leave a channel out); if it
is left out, 2 streams mean `L R` and 6 streams `L R C LFE Ls Rs`. **The LFE (and `-`) is
dropped before loudness and true peak are measured**, as BS.1770 defines. Each program gets its
own findings. `program_track` still works for a mix on one stream; do not give both.
Real output (demo master with the streams above):

```
  PASS    audio.loudness: 5.1 (streams 1,2,3,4,5,6, measured L R C Ls Rs): ATSC A/85: I -24.0 LUFS, LRA 0.0 LU, true peak -28.6 dBTP [BS.1770 relative-gated ...] @ 01:00:00:00-01:00:01:23 (frames 24-71)
  PASS    audio.loudness: stereo (streams 7,8, measured L R): ATSC A/85: I -24.0 LUFS, LRA 0.0 LU, true peak -24.0 dBTP [BS.1770 relative-gated ...] @ 01:00:00:00-01:00:01:23 (frames 24-71)
```

### Channels that are meant to be silent

A master may carry channels that are silent on purpose (an empty LFE, a music-and-effects
stream with gaps). Declare them in the spec (or as the profile parameter `allowed_silence`;
both lists are used):

```
  audio:
    allowed_silence:
      - {stream: 2}                                                   # the whole stream, whole program
      - {stream: 0, channels: [2], start: "01:00:00:00", end: "01:00:10:00"}   # one channel, one range
```

`stream` counts from 0; `channels` count from 1 (omitted = all); `start`/`end` are timecodes
(omitted = the whole program). Declared silence is shown as INFO (`silent ... as declared`)
and not counted as a problem. A channel that is silent outside its declared range is still
reported. A declared channel that carries sound is not reported.
Real output of the demo master (LFE stream declared):

```
  INFO    audio.silence: stream 4: 48 silent frames, 48 of them on channels declared silent (allowed_silence) - not counted @ 01:00:00:00-01:00:01:23 (frames 24-71)
  INFO    audio.silent_channel: stream 4 channel(s) 1: silent for the whole program as declared (allowed_silence) @ 01:00:00:00-01:00:01:23 (frames 24-71)
```

### Loudness standards live in the profile

The spec only names the standard (`audio.loudness.standard: "ATSC A/85"`). Target, tolerance
and true-peak ceiling are in the profile (`audio.loudness.standards`, section 11). A spec naming
a standard the profile does not know gives
`ERROR   audio.loudness: configuration error: standard 'EBU R-128' has no preset in the profile (known: EBU R128, ATSC A/85)`.

### Filename

- `job.filename: "EXACT_NAME.mov"` = this exact name.
- `job.naming_pattern`: placeholders in `{}`. `{title_code}`, `{episode}`, `{version}` (any
  `job.x`) come from the spec verbatim; `{format}`, `{fps_tag}`, `{audio_tag}` are tags the
  profile builds (`profiles/final.yaml`):

| tag | from | mapping in `final` |
|---|---|---|
| `format` | `video.height` | 720 -> HD, 1080 -> HD, 2160 -> UHD |
| `fps_tag` | `video.fps` | 24000/1001 -> 2398, 24 -> 24, 25 -> 25, 30000/1001 -> 2997, 60000/1001 -> 5994 |
| `audio_tag` | first track's layout | mono -> MO, stereo -> ST, 5.1 -> 51 |

Add or change tags in your profile under `filename: tags:` (a literal `name: "TEXT"` or
`name: {from: "${spec....}", map: {value: TAG}}`). The result is compared with the file's
name, case-sensitive:
`FAIL    filename: filename: expected 'SHOWNAME_101_HD_2398_ST_v03.mov', got 'show_101.mp4'`.

---

## 9. Quality modes: fast and high

Two checks have a fast mode. Set it per check in the profile (`quality: fast`), or for one run
on the command line: `cmr check --quality fast` / `--quality high` runs every check that has
the mode in that mode (the flag beats the profile; checks without a fast mode stay high). The
report records it (`summary.quality`).

| check | high (default) | fast | what fast can miss |
|---|---|---|---|
| `video.blanking` | every frame | every 5th file frame (`fast_frame_step`) | defects shorter than 5 frames may be missed; ranges are approximate (+-4 frames) |
| `video.black_runs` | every frame, full resolution | every frame, quarter resolution | on grainy near-black, fast tends to call MORE frames black (more findings, not fewer) |
| slate checks | 2 frames | same (both modes identical) | |

Every other check has only `high`; `quality: fast` there is an ERROR
(`bad profile params: quality 'fast' not implemented (supports: high)`).

Example: the `many` demo file (Appendix A) has 14 one-frame blanking defects. `release` (high) finds all
14; `review` (fast) finds 3 of them (frames 30, 45, 60: the multiples of 5):

```
cmr check many/*.mov --profile review --spec specs/spec_showname_101.yaml
WARN    many/SHOWNAME_101_HD_2398_ST_v03.mov
        24000/1001 fps, 96 frames, start TC 00:59:59:00 (file)
  WARN    video.blanking [fast mode]: top: black edge (non-picture, sharp step), 100% of line, seen on 1 sampled frame(s) (fast: 1 in 5 frames; true start/end up to 4 frame(s) outside the range shown) @ 01:00:00:06 (frame 30)
  WARN    video.blanking [fast mode]: top: black edge (non-picture, sharp step), 100% of line, seen on 1 sampled frame(s) (fast: 1 in 5 frames; true start/end up to 4 frame(s) outside the range shown) @ 01:00:00:21 (frame 45)
  WARN    video.blanking [fast mode]: top: black edge (non-picture, sharp step), 100% of line, seen on 1 sampled frame(s) (fast: 1 in 5 frames; true start/end up to 4 frame(s) outside the range shown) @ 01:00:01:12 (frame 60)
RESULT WARN (exit 1): PASS 21, WARN 3
```

So: **review renders fast, deliveries high.** Measured on a 1-minute 1080p ProRes test clip
(16 CPUs, ffmpeg 6.1.1): the whole `release` run took 10.4 s, `review` 2.1 s. Most of the
saving comes from black runs; fast blanking saves only about 20% of the blanking time,
because every frame must be decoded anyway. Real renders decode slower than test pictures,
so expect a smaller relative saving (UNVERIFIED on real renders).

---

## 10. The checks, one by one

Defaults below are the values in `profiles/final.yaml`, or the check's built-in default when
the profile does not set it. Check ids are what you write under `checks:`.

### Settings checks (metadata vs spec)

Read from the file header (ffprobe); fast, no decoding.

| check id | compares | parameters (`final`) |
|---|---|---|
| `container.format` | container | `expect: ${spec.video.container}` |
| `video.codec` | codec and profile | `codec`, `profile` |
| `video.resolution` | width, height, pixel aspect, display aspect | `width`, `height`, `pixel_aspect`, `aspect_ratio` |
| `video.pixel_format` | pixel format | `expect` |
| `video.fps` | frame rate and average frame rate, exactly (Avid's 2997/125 for 23.976 passes with an INFO line, section 8) | `expect` |
| `video.scan` | progressive / interlaced | `expect`, `unknown_severity: warn` (not signalled) |
| `video.color` | primaries, transfer, matrix, range | `expect`, `unknown_severity: warn` (untagged), optional `key_severity` |
| `video.field_order` | field order, only when the spec has `video.field_order` | `video`, `unknown_severity: warn` (not signalled) |
| `container.op` | MXF operational pattern, only when the spec has `video.operational_pattern`; SKIPPED for other containers | `video` |
| `audio.format` | codec, sample rate, bit depth of every audio stream | `codec`, `sample_rate`, `bit_depth` |
| `audio.layout` | number of audio streams and channels per stream | `tracks` |
| `filename` | file name | `job`, `tags` |
| `captions.presence` | embedded captions present | `captions` |

**Per-key colour severity.** `key_severity: {range: info}` in your profile sets the severity of
a mismatch of one key (`primaries`, `transfer`, `matrix`, `range`); the others keep the check's
`severity`. The stock profiles use it (section 7): a full-range encode is INFO in
`review_internal` and WARN in `colour_interim`, `broadcast_master` and `web_delivery`.

Known limits: only the first video stream is checked. Untagged colour is reported at
`unknown_severity`. ffprobe 5.1 (Rocky 9) and 6.1 read ProRes colour tags differently: a file
whose ProRes frame header is untagged while the MOV `colr` atom says bt709 is "unknown" on 5.1
and bt709 on 6.1 (whether Flame renders look like that: UNVERIFIED). H.264 caption (A53)
detection: UNVERIFIED. Captions: presence only, not content or timing (see below); sidecar files are not
read. Bit depth is compared for PCM audio only.

### Captions

`captions.presence` looks for embedded captions in the formats the spec lists
(`content.captions.formats`): `embedded_608` (MOV caption track, H.264 A53 data, or 608 inside
SMPTE 436M), `embedded_708` (A53, or a 436M caption packet with 708 data) and `vanc_436m`
(any SMPTE 436M caption data track). For MXF masters with 436M caption data (public-television
style) cmr reads the **first 30 packets (about 1 second)** of the data track. Caption text is
never read into reports or logs. A 436M track that cannot be read is an ERROR ("not verified"),
not a FAIL. Limits: captions that only start later than the first second, or tracks that carry
only padding there, read as missing; 10-bit 436M sample coding is UNVERIFIED (no test file);
the 436M reader was tested on hand-built data (ffmpeg cannot make such a file), not on a real
master. Caption quality and sidecar files (SCC/SRT) are not checked (section 19).

### Timing checks

| check id | what | parameters |
|---|---|---|
| `timing.frame_count` | program frames = file frames - head - tail, vs `program_duration_frames` | `timing`, `tolerance_frames: 0` |
| `timing.layout` | head + tail fit in the file; first program frame carries `program_start_timecode` | `timing` |
| `timing.timecode_start` | embedded start timecode and DF/NDF vs the spec (or the derived value) | `timing` |

Examples:

```
  FAIL    timing.frame_count: program frames: expected 48 (+-0), got 40 (file 88 - head 24 - tail 24); program range shown @ 01:00:00:00-01:00:01:15 (frames 24-63)
  FAIL    timing.layout: first program frame (file frame 24, after 24 head frames) has timecode 01:00:01:00, expected 01:00:00:00; 01:00:00:00 is file frame 0 @ 01:00:01:00 (frame 24)
  FAIL    timing.timecode_start: start timecode: expected 00:59:59:00 (program_start_timecode - 24 head frames), got 01:00:00:00 (off by +24 frames) @ 00:59:59:00 (frame 0)
```

For MXF files the start timecode is read from the file header when there is no timecode track
(the message says `from format`). Files without any timecode (MP4) FAIL `timing.timecode_start` with
`no embedded timecode (no tmcd track / timecode tag)`; switch it off for such deliverables:
`timing.timecode_start: {enabled: false}`.

### decode.errors

Decodes the whole file once and reports ffmpeg decoder errors and a frame count that differs
from the container's. Parameter: `max_lines: 20` (decoder messages kept in the report).

```
  FAIL    decode.errors: 6 decoder error line(s), ffmpeg exit 0 (ffmpeg does not report the frame)
```

ffmpeg does not say which frame was damaged, so this finding has no timecode.

### video.black_runs

Runs of black frames inside the program (head/tail black is ignored). A frame is black when
90% of its pixels are at or below `black_luma`.

| parameter | default | meaning |
|---|---|---|
| `max_black_run_frames` | 12 (`final`), 6 (`release`), 24 (`review`) | longest allowed run; required |
| `black_luma` | 24 | 8-bit luma; legal black is 16 |

```
  FAIL    video.black_runs: 16 consecutive black frames in program @ 01:00:01:02-01:00:01:17 (frames 50-65)
```

**Content on black.** A frame that is black except for a logo, end card or credits is **not**
black: when the share of pixels above `content_luma` reaches `content_min_fraction`, the frame
is "content on black". Such runs are not black runs; they are listed once as INFO, and real
black before or after them is still reported:

```
  INFO    video.black_runs: 16 dark frames in program not counted as a black run: 16 frame(s) carry content on black (logo/text/credits, up to 8.71% of the picture above luma 100) @ 01:00:01:02-01:00:01:17 (frames 50-65)
```

| parameter | default | meaning |
|---|---|---|
| `content_on_black` | true | `false` = old behaviour: such frames count as black |
| `content_luma` | 100 | 8-bit video level a pixel must exceed to count as content; lower it for a dim grey logo |
| `content_min_fraction` | 0.0001 (0.01% of the frame) | how much content makes a frame "content on black"; raise it (e.g. `0.005`) to make credits count as black again |

Limits: noisy or grainy black is not tuned on real footage yet (UNVERIFIED); a logo whose
every pixel is below `content_luma` still counts as black; a cluster of hot pixels of 0.01% can
excuse a black frame.

### video.blanking

Black or non-picture lines at the four picture edges: a bad reposition, a scale that leaves a
1-pixel line, a wrong mask, letterbox where none should be. Finds defects as thin as 1 pixel
and as short as 1 frame, also when only part of the line is black. It sorts what it finds
into three classes:

| class | looks like | default severity | message |
|---|---|---|---|
| `edge` | black line with a sharp step to the picture (the typical render error) | FAIL | `top: black edge (non-picture, sharp step), 100% of line, 5 frame(s)` |
| `dark_edge` | dark picture or soft black at the edge, no sharp step (curtains, dark floors, night scenes) | FAIL, see `dark_edge_mode` | `dark edge (dark picture or soft black edge? review)` |
| `matte` | black bar several pixels deep (letterbox / pillarbox / matte) | WARN; INFO if it lasts the whole file (persistent) | `bottom: black bar (matte/letterbox/pillarbox?), 50% of line, 900 frame(s)` |

The profile's `severity` caps all classes (`review`: WARN).

| parameter | default | meaning |
|---|---|---|
| `dark_edge_mode` | `flag` (`review`: `info`) | `flag` = report dark edges at their class severity; `info` = as INFO; `hide` = not listed, only counted in one summary line |
| `coverage` | 0.5 | share of an edge line that must be black (0.5 = half the line) |
| `coverage_tolerance` | 0.02 | slack for compression ringing |
| `black_luma` | 32 | outer pixel at or below this counts as black (8-bit; ProRes ringing lifts black to 25-32) |
| `matte_luma` | 20 | "true black" for mattes and deeper bands |
| `min_step` | 6 | brightness step from black line to picture for a sharp `edge` |
| `min_contrast` | 4 | step for a `dark_edge` |
| `dark_frame_luma` | 48 | frames darker than this everywhere are not judged for mattes (fades) |
| `max_depth` | 16 | pixels searched inwards; black deeper than this is a matte |
| `min_run_frames` | 1 | shortest run reported |
| `persistent_fraction` | 0.9 | a run covering 90% of the frames is "persistent" |
| `merge_gap_frames` | 250 | `dark_edge` / `matte` runs of one edge closer than this become one finding (sharp `edge` findings never merge) |
| `fast_frame_step` | 5 | fast mode: 1 in N frames |
| `exclude_segments` | `[slate, bars_tone, two_pop]` | head/tail segments not judged; set your own list in the profile to change it |
| `class_severity` | edge, edge_persistent, dark_edge, dark_edge_persistent: fail; matte: warn; matte_persistent: info | per-class severity (`pass`/`info`/`warn`/`fail`), capped by `severity` |

Known limits: tuned on one real H.264 file and planted defects; ProRes deliverables from Flame
still UNVERIFIED. A real 1-pixel black line next to very dark picture (luma up to ~22) cannot be
told from dark picture: it is reported as `dark_edge` (or `matte`), not lost - unless you set
`dark_edge_mode: hide`. Grey or duplicated edge lines (not black) are not detected. The head's colour bars have black areas at the bottom edge, and a vignetted slate looks like
a dark edge, so the segments `slate`, `bars_tone` and `two_pop` of the head layout are not
judged by default (`exclude_segments`). If you list your own `exclude_segments`, include them.

### Audio checks

All four measure the **program range only** (head and tail excluded), every frame.

| check id | what | parameters (`final`) | finding |
|---|---|---|---|
| `audio.loudness` | BS.1770 integrated loudness, loudness range and true peak of the main mix (`program_track`) | `standards` (per standard: `target_lufs`, `tolerance_lu`, `true_peak_max_dbtp`), optional `lra_max_lu` | integrated / true peak / LRA, each separately |
| `audio.silence` | runs where every channel of a stream is below `threshold_dbfs` | `threshold_dbfs: -60`, `max_run_frames: 12` | per run |
| `audio.silent_channel` | a channel below `threshold_dbfs` for the whole program | `threshold_dbfs: -60` | per channel |
| `audio.clipping` | frames where any channel's sample peak reaches `clip_dbfs` | `clip_dbfs: -0.1` | per run |

Stock loudness presets: EBU R128 -23 LUFS +-1 LU (release: +-0.5), true peak max -1 dBTP;
ATSC A/85 -24 LKFS +-2 LU, -2 dBTP. Every loudness message says
`[BS.1770 relative-gated (-70 LUFS absolute, -10 LU relative); not dialogue-gated]`: this is
**not** a dialogue-gated measurement (as some US specs require; roadmap) and not a certified
meter reading.

```
  FAIL    audio.loudness: true peak above -1 dBTP (first in this 100 ms block); EBU R128: I -15.2 LUFS, LRA 0.0 LU, true peak 1.0 dBTP [...] @ 01:00:01:21-01:00:01:23 (frames 69-71)
  FAIL    audio.silent_channel: stream 2 channel 1: silent for the whole program (peak -inf dBFS < -60 dBFS) @ 01:00:00;00-01:00:03;29 (frames 1500-1619)
```

Masters with one mono stream per channel get one loudness result per program (section 8,
"Loudness of multi-stream masters"). Channels that are silent on purpose are declared with
`allowed_silence` (section 8) and then reported as INFO.

Known limits: the true-peak location is the first 100 ms block over the ceiling; 5.1/7.1
loudness weighting is UNVERIFIED; at 29.97 a clipped frame can be reported 1 frame early;
a loudness check on a file whose head/tail layout does not fit the file is SKIPPED, not an
ERROR.

---

## 11. Tuning

Change these in YOUR profile (section 7), never in the stock profiles. A misspelt parameter name
is an error (exit 3), so a typo cannot silently do nothing.

**Loudness standard numbers.** Override the whole `standards` block; the names must match
what specs use:

```
checks:
  audio.loudness:
    standards:
      "EBU R128": {target_lufs: -23, tolerance_lu: 0.5, true_peak_max_dbtp: -1}
      "ATSC A/85": {target_lufs: -24, tolerance_lu: 2, true_peak_max_dbtp: -2}
      "ATSC A/85 tight": {target_lufs: -24, tolerance_lu: 1, true_peak_max_dbtp: -2}
    lra_max_lu: 20
```

For temp mixes: `audio.loudness: {severity: info}` keeps the measurement in the report without
gating.

**Blanking.**

- Dark scenes produce many `dark_edge` findings: review them once, then either set
  `dark_edge_mode: info` (listed, not gating), `hide` (only a count), or clear the reviewed ones
  with waivers (section 12). Sharp `edge` defects are not affected by `dark_edge_mode`.
- Bars, slate and 2-pop in the head are not judged by default (`exclude_segments: [slate, bars_tone, two_pop]`); list your own segments only to change that.
- Only full-line defects: `coverage: 0.9`. Partial masks matter: keep 0.5.
- Lifted black on a log render: the sharp-edge rule still works (it looks for the step);
  use `dark_edge_mode: hide` there (`log_interim` does).
- Too many lines in a long report: `max_findings: 25`.

**Black runs.** `max_black_run_frames` (allowed run length inside the program) and
`black_luma` (raise it to 28-32 if grainy black is not caught; lower it if very dark scenes are
called black).
End cards and logos on black are INFO, not black runs (`content_on_black`, `content_luma`,
`content_min_fraction`; section 10).

**Silence and clipping.** `audio.silence: {threshold_dbfs: -60, max_run_frames: 12}`,
`audio.clipping: {clip_dbfs: -0.1}` (-0.5 for a stricter house rule).

**Intentionally silent channels.** Declare them in the spec (`audio.allowed_silence`, section 8) instead of setting `audio.silent_channel` to info.

**Waiver tolerances.** Top-level `waivers:` in the profile, section 12.

After every change: `cmr profiles show YOUR_PROFILE --spec SPEC` (loads?) and one run on a
known-good and a known-bad render.

---

## 12. Waivers: accepted findings

A finding that was looked at and is fine (a fade to black, a dark curtain, a deliberate
distortion) can be waived, so the next render of the same job is not flagged again. A waived
finding stays in the report as `WAIVED` with its reason, and does not count for the exit code.

Two kinds:

| | per finding | category |
|---|---|---|
| made with | `cmr waive REPORT.json --finding N` | `cmr waive --category CHECK_ID ...` |
| covers | that place in the file (check, stream, edge/channel/class, frame range) | every finding of a check (optionally narrowed by class, edge/channel, frame range) up to a severity ceiling |
| re-checks the content | **yes**: a fingerprint of the picture/sound; re-flagged if it changed | **no**: a blanket decision |
| typical use | a specific fade, a specific clipped moment | "dark curtain on the left in all stage scenes" |

Only picture, sound and slate findings with a frame range can be waived (not settings, filename,
timing or decode findings).

### Per finding

```
cmr check defects/*.mov --profile release --spec specs/spec_showname_101.yaml --report json -o reports/
cmr waive reports/cmr_report_latest.json
   1 FAIL  audio.clipping: stream 1: sample peak 0.00 dBFS >= -0.1 dBFS on channel(s) 1,2 @ 01:00:01:21-01:00:01:23 (defects/SHOWNAME_101_HD_2398_ST_v03.mov)
   4 FAIL  audio.loudness: integrated loudness outside -23 +-0.5 LU; ...
   5 FAIL  audio.loudness: true peak above -1 dBTP (first in this 100 ms block); ...
  16 FAIL  video.black_runs: 16 consecutive black frames in program @ 01:00:01:02-01:00:01:17 (defects/SHOWNAME_101_HD_2398_ST_v03.mov)
  17 FAIL  video.blanking: top: black edge (non-picture, sharp step), 100% of line, 5 frame(s) @ 01:00:00:16-01:00:00:20 (defects/SHOWNAME_101_HD_2398_ST_v03.mov)

cmr waive reports/cmr_report_latest.json --finding 16 --reason "Fade to black in the edit, approved by the director" --expires 2027-06-30
W0001: video.black_runs @ 01:00:01:02-01:00:01:17 (finding 16)
waivers: waivers.yaml (1 added, 1 total)
```

- Without `--finding` it only lists the numbered findings that can be waived. (The numbers
  are positions in the report's list of findings, so they are not consecutive.)
- `--finding 16 17` waives several; `--all-check video.black_runs` waives every listed finding of
  that check. A finding that cannot be waived gives `error: finding(s) [6] are not waivable`.
- `--reason` is required. `--expires YYYY-MM-DD` is optional (last day it applies).
- `-o FILE` chooses the waiver file (default `./waivers.yaml`, or the job's waiver file, below);
  new waivers are appended.
- Run it from the folder you ran `cmr check` in: it reads the media file again (the path in the
  report) to fingerprint it, and refuses if the file changed.
- From a capped report, findings that were not listed cannot be waived one by one: re-run with
  `--max-findings 0`, or use a category waiver.
- `cmr check --waivers FILE` with a file that does not exist is a config error (exit 3). Inside
  a job folder the job's `waivers.yaml` may not exist yet; that is fine.

Next render:

```
cmr check defects/*.mov --profile release --spec specs/spec_showname_101.yaml --waivers waivers.yaml
  ...
  WAIVED  video.black_runs: 16 consecutive black frames in program @ 01:00:01:02-01:00:01:17 (frames 50-65)
          was FAIL; waiver W0001: Fade to black in the edit, approved by the director
  ...
RESULT FAIL (exit 2): PASS 18, SKIPPED 1, FAIL 4, WAIVED 1
WAIVERS waivers.yaml: applied 1, content changed 0, stale 0, expired 0
```

### What "content changed", "stale" and "expired" mean

(The "content changed" example below is from the project's waiver tests; the stale example is from the demo files of this guide.)

- **content changed**: a finding at the waived place, but the picture or sound there is
  different from when it was waived. It is flagged again for a new review:

```
  FAIL    video.black_runs: 16 consecutive black frames in program @ 01:00:01:02-01:00:01:17 (frames 50-65)
          previously waived (W0001), content changed - re-review (reason was: Fade to black in the edit, approved by the director)
WAIVERS waivers.yaml: applied 0, content changed 1, stale 0, expired 0
```

- **stale**: the waiver matched nothing in this run (the problem is gone, or it moved). Listed,
  harmless; delete it when the job is done:

```
WAIVERS waivers.yaml: applied 2, content changed 0, stale 1, expired 0
  stale: W0003 category video.blanking kind=dark_edge edge=left <= WARN: Dark curtain on the left in the stage scenes
```

- **expired**: past its `expires` date; ignored and listed.

How "same content" is decided: for picture, a 64-bit fingerprint of up to 8 frames of the
range; re-encodes (ProRes to H.264) and regrain stay within the tolerance, a changed picture does
not. For sound, the peak and RMS level of up to 16 frames per channel. Limits: a defect that
grows on an unchanged picture (a 1-pixel line becoming 4 pixels) is not noticed; a change of
sound at the same level is not noticed; content that moved in time is not matched (roadmap).

Tolerances (profile, top level; defaults):

| key | default | meaning |
|---|---|---|
| `video_max_bits` | 10 | how many of the 64 fingerprint bits may differ on any sampled frame |
| `audio_max_db` | 2.0 | level difference allowed on any sampled frame |
| `margin_frames` | 5 | how far a finding may extend outside the waived range |

### Category waivers

```
cmr waive --category audio.clipping --max-severity fail --reason "Intentional distortion effect in the mix" -o waivers.yaml
W0002: category audio.clipping <= FAIL
waivers: waivers.yaml (1 added, 2 total)

cmr waive --category video.blanking --class dark_edge --edge left --max-severity warn --reason "Dark curtain on the left in the stage scenes" -o waivers.yaml
W0003: category video.blanking kind=dark_edge edge=left <= WARN
waivers: waivers.yaml (1 added, 3 total)
```

| option | meaning |
|---|---|
| `--category CHECK_ID` | a `video.*` or `audio.*` check |
| `--max-severity info\|warn\|fail` | required ceiling, compared with the ORIGINAL severity: a sharp blanking FAIL is not waived by a `warn` ceiling |
| `--class KIND` | only this class: `edge`, `dark_edge`, `matte` (blanking); `integrated`, `true_peak`, `lra` (loudness) |
| `--edge top\|bottom\|left\|right` | video checks |
| `--channel C` | audio checks, 1-based |
| `--frames A-B` | only inside this file frame range |
| `--reason`, `--expires`, `-o` | as above |

In the report, category-waived findings are one line per waiver:

```
  WAIVED  audio.clipping: 1 finding(s) by category waiver W0002 (worst was FAIL) @ 01:00:01:21-01:00:01:23 (frames 69-71)
WAIVERS waivers.yaml: applied 2, content changed 0, stale 1, expired 0
  category W0002 audio.clipping <= FAIL: 1 finding(s) waived: Intentional distortion effect in the mix
  stale: W0003 category video.blanking kind=dark_edge edge=left <= WARN: Dark curtain on the left in the stage scenes
```

### The waiver file

Commented example: `templates/waivers/waivers_example.yaml`. Edit only `reason`, `expires` and
`job` by hand. Waivers apply to every file of a run (they are not tied to a file name). There is
no reviewer name in a waiver yet; put it in the reason if you need it.

### Waivers belong to a job

Inside a job folder (section 5) `cmr waive` stamps every waiver with the job id (`job:
show_a`) and writes to the job's waiver file. A waiver applies only to runs of the same job. A
waiver of another job in the same file is listed, not applied, and also counted in the report
(`waivers.foreign`); a waiver with `job: "*"` applies to every job (`cmr waive --any-job`); a
waiver without a `job` line (a file from before job ids) applies everywhere. Real output, a
file with one waiver of job `show_a`, read by job `show_b`:

```
WAIVERS waivers.yaml: applied 0, content changed 0, stale 0, expired 0, foreign 1
  foreign (job show_a, not applied): W0001 audio.clipping: Intentional distortion
```

and a waiver made with `--any-job` (`W0002 ... (job *)`) applies in both jobs. Without a job
file the job id comes from the report, or the waiver has no job.

---

## 13. Batch and overnight runs

`TEMPLATES/automation/overnight_qc.sh` checks every `.mov`, `.mxf` and `.mp4` in a folder (not
recursive), one file after the other, in background mode, with one report folder per file.
Copy it (and `summarize_reports.py`) to `~/qc/` first. Two ways to say what to check against:

```
overnight_qc.sh RENDER_DIR REPORT_DIR PROFILE SPEC [WAIVERS]            # explicit
CMR_JOB=~/qc/jobs/exco_rush/job.yaml overnight_qc.sh RENDER_DIR REPORT_DIR   # the job file names them
```

(Cron starts in your home folder, so give the job file's full path in `CMR_JOB`, or run the
script from the job's folder. The job file's `output:` is not used here: every file gets its own
folder in REPORT_DIR.) Example run on two demo renders (renamed copies, so the filename check
fails):

```
overnight_qc.sh renders reports release specs/spec_showname_101.yaml waivers.yaml
2026-10-26 16:31:52 2 file(s) in renders -> reports
2026-10-26 16:31:52 checking A_good.mov
2026-10-26 16:31:54 checking B_defects.mov
2026-10-26 16:31:56 done. Summary: reports/summary.tsv
exit  verdict  file           started
2     FAIL     A_good.mov     2026-10-26 16:31:52
2     FAIL     B_defects.mov  2026-10-26 16:31:54
```

- Output: `reports/<file name>/cmr_report_<date-time>_<run id>.json` with its copy
  `cmr_report_latest.json`, `reports/<file name>/console.txt`, and `reports/summary.tsv`.
- A file that already has a report is skipped on the next run (`REDO=1` checks it again), so an
  interrupted night can simply be restarted.
- Settings: `CMR` (how to call cmr), `CMR_JOB` (job file), `CMR_TIMEOUT` (per file, default
  `6h`; exit 124 = timed out), `CMR_EXTRA` (more options, e.g. `--max-findings 0`).
- All files of one call use one spec. Different deliverables: one call (or one folder) per spec.

Morning summary:

```
python3 summarize_reports.py reports
FAIL    exit 2  renders/A_good.mov
        profile release, PASS 21, SKIPPED 1, FAIL 1
  SKIPPED captions.presence: captions not required by spec
  FAIL    filename: filename: expected 'SHOWNAME_101_HD_2398_ST_v03.mov', got 'A_good.mov'

FAIL    exit 2  renders/B_defects.mov
        profile release, PASS 17, SKIPPED 1, FAIL 5, WAIVED 1
  FAIL    audio.loudness: integrated loudness outside -23 +-0.5 LU; EBU R128: I -15.2 LUFS, ... @ 01:00:00:00 (frame 24)
  ...
2 report(s); worst exit code 2
```

`--all` also lists WAIVED findings. Any Python 3 runs it (also the system Python). It reads the
`cmr_report_latest.json` of each folder.

From cron, without activating conda: call the program by its full path: `~/.local/bin/cmr` is the launcher the installer writes (built for cron; verified on Rocky with an empty environment, section 20), `~/envs/cmr/bin/cmr` is a manual install (tested on Rocky 9 with a minimal environment):

```
# m h dom mon dow   command
30 20 * * 1-5  CMR=$HOME/.local/bin/cmr $HOME/qc/overnight_qc.sh /mnt/renders/tonight $HOME/qc/reports/$(date +\%F) release $HOME/qc/specs/spec_example.yaml >> $HOME/qc/overnight.log 2>&1
```

(The crontab line itself: UNVERIFIED as a cron entry; the same kind of command was run from a
shell with an empty environment.)

On a busy workstation run batches one file at a time (as the script does), in background
mode, and keep at least the 16 GB the background cap allows free.

---

## 14. Slate checks

The slate checks read the slate of a spot with OCR (tesseract), compare it with the job data
and the actual file, and measure the colour bars on the slate. They are built and verified on
generated slated spots (Rocky 9, ffmpeg 5.1.10, tesseract 5.5) and on frames of real
"Label: value" spots from one delivery house; other real slate designs are untested (limits
at the end of this section). Use the results as a second pair of eyes.

| check id | what | default severity |
|---|---|---|
| `slate.present` | the spec's head layout has a `slate` segment, and every required slate label is found on both frames read | FAIL |
| `slate.consistency` | the 2nd frame and the 2nd-to-last frame of the slate agree (countdown ignored) | FAIL |
| `slate.content` | each field vs what the job expects: AGENCY, CLIENT, TITLE (= filename), AD-ID (= CSV row and filename), DURATION, DATE rule ... | per field |
| `slate.claims` | what the slate and filename CLAIM vs the actual media: DURATION/LENGTH vs the real program length, AUDIO vs the audio layout, resolution/codec/fps words in the title | FAIL |
| `slate.bars` | colour bars and grey steps on the slate vs BT.709 values (+-2 code values); SKIPPED when the template has no `bars:` section | WARN in `spot_review` |

### What you need

- tesseract 5. The installer installs it (section 20). Otherwise
  `conda install -y -p ~/envs/cmr tesseract`, or set `CMR_TESSERACT=/path/to/tesseract`;
  `cmr doctor` shows which one is used. Without it every OCR check is an ERROR
  `slate not read: tesseract not found ...`; `slate.bars` and everything else still run.
- A **job folder** with `job.yaml` (section 5) and the files it points to; starter sets are
  `templates/quickstart/` (the unedited demo), `templates/slate/` (commented, :30 spot) and
  `examples/spot_xr/` (:60 spot, plus a "Label: value" variant):
  - the spec sheet: technical spec WITHOUT a `job:` section (the job constants live in `job.yaml`),
  - `job.yaml`: job id, job constants (agency, client, artist ...), the filename pattern, where
    the CSV and slate template are, how a CSV row is matched to a file,
  - `deliverables.csv`: one row per spot (Ad-ID, title, length, ...), no filenames,
  - `slates/<template>.yaml`: the slate design (labels, field types, expectations, bars).
- Two slate layouts are read: `aligned` (labels right-aligned in a column, values left-aligned
  next to them, colour bars on top, grey steps at the bottom, a countdown that is ignored) and
  `label_colon` ("Label: value" lines, no bars). The template's `layout:` key selects it.

### Run

```
cmr check "SPOT.mov"                          # in the job folder: job.yaml names profile, spec, CSV, template
cmr check "SPOT.mov" --profile spot_delivery  # an option beats the job file
```

Profiles: `spot_render` (right after render: settings, layout, filename, decode, slate present),
`spot_review` (slate read and verified; AD-ID, TITLE, DURATION mismatches FAIL; loudness and bars
WARN; picture checks fast), `spot_delivery` (everything high and FAIL). `--deliverables CSV` uses
another CSV; `--row VALUE` picks the CSV row by hand (`--row ABCD1234567H`, `--row "#2"`,
`--row Title=...`). Real output for the clean demo spot and the spot with planted errors is in
quick start B (section 3).

### Rules worth knowing

- **The filename is authoritative** once the slate TITLE matches it. A TITLE that does not
  match the filename is "wrong slate or renamed file" (FAIL); the other fields are still compared
  with the CSV row picked by the filename. Every row value comes from that one row.
- **Ad-ID**: the row, the filename and the slate must agree. A trailing H/D is ignored for the
  row match. Look-alike characters (0/O, 1/I/l, 5/S, 2/Z, 8/B) are reported as INFO, not as a
  mismatch.
- **Blank = not checked.** A field with no expectation (blank in job.yaml or CSV) is not
  checked; `"<empty>"` means the slate field must be empty.
- **Uncertain reads**: OCR words below confidence 60 give a WARN "check by eye", not a content
  error. On the real slates tried so far the Ad-ID line was read at confidence 37-57, so a wrong
  Ad-ID there shows as an uncertain-read WARN rather than a FAIL; look at the slate frame.
- **DATE** (default `spot_review` rule): format and within 30 days of the file's own date
  (container `creation_time`, else modification time); WARN, because file dates change when
  files are copied.
- **Bars** are measured in the file's own Y'CbCr values, so they assume the slate graphic
  went through the same colour management as the picture. If your slates are not, every bars
  check fails: switch `slate.bars` off in your profile.
- **A slate shorter or longer than the spec.** Only the program start really matters. A slate
  that ends a few frames earlier than the spec's `slate` segment (real example: spec 120 + 48,
  file 119 + 49) is tolerated with the profile parameter `split_tolerance_frames: N` (default 0,
  strict) on `slate.present`, `slate.consistency`, `slate.content` and `slate.claims`.
- Slate findings can be waived like others; the waiver stores the exact OCR text, so a waived
  CLIENT spelling is flagged again when the text changes (section 12).
- Cost: about 1 s of one CPU core per slate. Slate frames are decoded as 8-bit grey for OCR
  (10-bit sources were misread otherwise).

### "Label: value" slates and Ad-ID with the length in the filename

Many real slates are left-aligned `Label: value` lines. Use a template with `layout: label_colon`
(example: `examples/spot_xr/slates/xr_label_colon.yaml`). Useful keys in the template:
`aliases` for every spelling of a label (`Ad-ID` / `AdID`, `Advertiser` / `Brand`), and
`date_formats: [MM/DD/YYYY, M/D/YYYY]` (the slate's own date style). A `Length` value may be
`15s`, `15` or `:15`: all mean 15 seconds. If there is no `bars:` section, `slate.bars` is
SKIPPED.

Some delivery houses put the Ad-ID base and the length in the file name (`SBX01703H.mov` =
base `SBX017` + length `03` + `H`). The CSV row is then chosen by two columns, in `job.yaml`:

```
deliverables:
  csv: deliverables_label_colon.csv
  columns: {ad_base: "Ad-ID", title: "Title", length: "Length"}
  key: [ad_base, length]          # both must match the filename's regex groups of the same names
  match: key
  filename_regex: '(?P<ad_base>[A-Z]{2,4}\d{3})(?P<length>\d{2})?(?P<hd>[HD])\.mov'
```

`AD-ID` is then expected as `{file.ad_base}` and the length as `{file.length}` or `{row.length}`
in the template. If the 2-digit code in the name is not a length (for example `31` for a second
:30), give the CSV an extra `code` column and key on it. A file whose name has no length code
is matched by the base alone, as long as that selects one row. `slate.claims` compares the
slate's length with the real program length: a :30 file whose slate says `15s` FAILs naming both
numbers. A complete demo (generated media; the generator's slate is 145 + black 65 frames
against the spec's 150 + 60, hence the tolerance):

```
cp -r ~/CheckMyRender/examples/spot_xr ~/lc_demo && cd ~/lc_demo
python3 ~/CheckMyRender/tests/slate/make_spot.py spots --short --layout label_colon
sed -i 's/program_duration_frames: 1800/program_duration_frames: 90/' spec_spot_xr.yaml
sed -i 's/,:60,/,:03,/' deliverables_label_colon.csv
```

with a profile `profile_lc.yaml`:

```
profile_version: 1
name: xr_label_colon
extends: spot_review
checks:
  slate.present:     {split_tolerance_frames: 5}
  slate.consistency: {split_tolerance_frames: 5}
  slate.content:     {split_tolerance_frames: 5}
  slate.claims:      {split_tolerance_frames: 5}
```

```
cmr check spots/clean/*.mov --job job_label_colon.yaml --profile profile_lc.yaml
PASS    spots/clean/SBX01703H.mov
        30000/1001 fps, 300 frames, start TC 00:59:53:00 (file)
  SKIPPED captions.presence: captions not required by spec
  SKIPPED filename: spec has neither job.filename nor job.naming_pattern
  SKIPPED slate.bars: slate template 'xr_label_colon' defines no bars
JOB nw_sb_label_colon
RESULT PASS (exit 0): PASS 25, SKIPPED 3
```

(`--job job_label_colon.yaml` is needed because the folder also holds the other job file,
`job.yaml`.) The same spot with planted errors (`spots/errors/`), abridged:

```
  FAIL    slate.claims: slate LENGTH '15s' = 450 frames (15 s x 30 timecode frames/s) but the program is 89 frames (-361) @ 00:59:53:01-00:59:57:23 (frames 1-143)
  FAIL    slate.claims: slate AUDIO 'Mono' (1 ch) but the file's first audio stream is stereo (2 ch) @ 00:59:53:01-00:59:57:23 (frames 1-143)
  FAIL    slate.consistency: slate AGENCY changes during the slate: frame 1 'Northwind Advertising', frame 143 'Northwind Advertisig' @ 00:59:53:01-00:59:57:23 (frames 1-143)
  FAIL    slate.content: slate does not match the file: slate AD-ID 'SBX018', expected 'SBX017' @ 00:59:53:01-00:59:57:23 (frames 1-143)
  WARN    slate.content: slate ADVERTISER 'Sample Bugrers', expected 'Sample Burgers' @ 00:59:53:01-00:59:57:23 (frames 1-143)
RESULT FAIL (exit 2): PASS 17, SKIPPED 3, WARN 5, FAIL 10
```

### Profile parameters of the slate checks

Set them under the check id in your profile (a misspelt name is an error, section 7). Common:
`slate` (`"${spec.slate}"`, required), `tesseract` (path), `min_confidence` (60),
`uncertain_severity` (warn), `confusable_severity` (info), `field_severity` ({FIELD: severity}),
`split_tolerance_frames` (0), `psm` (3), `lang` (eng), `ocr_timeout` (60 s). `slate.content`:
`job`, `deliverables`, `tags`, `date` ({mode: ignore|format|window|equals_row, format,
window_days, severity}). `slate.claims`: `tolerance_frames` (0), `check_filename` (true).
`slate.bars`: `tolerance` (2), `chroma_tolerance`, `missing_severity` (warn).

### Limits (what is and is not verified)

- Verified: generated spots (both layouts, every planted error found, no extra slate
  finding), and OCR of frames from 5 real "Label: value" spots from one delivery house (all
  labels read on both frames, every value read exactly, Ad-ID at low confidence).
- **Not handled:** wrapped values (a value that continues on a second line), centred values,
  a value separated from its label by a gap wider than 1.5 line heights, a label whose colon
  was not read and whose value follows in the same text segment (`Ad-ID AB002`). No automatic
  choice between slate designs: the template says which layout to expect.
- Only the first video stream and the first audio stream are claimed against.
- Other real slates and fonts, the `aligned` layout on real renders, and bars on real
  H.264 4:2:0 renders: UNVERIFIED.
- Not built: slate audio (silent slate, 2-pop), countdown continuity, an unslated variant
  having no slate, Ad-ID structure and SD/HD pairing, safe-area checks.

---

## 15. Diagnostic log

cmr writes a diagnostic log for support, separate from the reports: what ran, with which
versions and settings, every ffmpeg/ffprobe/tesseract command with its timing, errors with
details. It never contains picture or sound data. By default **private data is not logged**:
file and project names, paths and slate text are replaced by short codes, so a log can be sent
to support. `--log-private` (or `CMR_LOG_PRIVATE=1`) logs them in full, e.g. to debug a slate.

| option (before the sub-command) | meaning |
|---|---|
| `--log-level debug\|info\|warn\|error\|off` | default `info` (env `CMR_LOG_LEVEL`) |
| `--log-file FILE` | default `$CMR_LOG_DIR/cmr.log`, else `~/.cache/cmr/logs/cmr.log` (Linux). On macOS the log will live in `~/Library/Logs/cmr` (planned, with the Mac version) |
| `--log-private` | log names, paths and slate text verbatim |

```
cmr logs path            # where the log is
cmr logs tail -n 40      # last lines
cmr logs collect -o support.tar.gz --reports reports/ --last 5   # support bundle
```

`cmr doctor` also prints the log location (`log: PATH`). The bundle contains `logs/cmr.log`,
`environment.txt` (versions of cmr, Python, ffmpeg and tesseract; OS, CPU, RAM, GPU and limits; host and user names hashed by default) and, with `--reports DIR`,
the newest JSON reports; it says `note: the reports are included verbatim (file paths, slate
text)`. The installer writes its own log, `~/.cache/cmr/logs/install.log`, in the same style.

A log line looks like:

```
2026-10-26 16:38:21.208 INFO  cli[3312657/3312657] run=a96529 file=- | end: exit 2 in 2.152 s; rss 30 MB peak 30 MB, children peak 404 MB
```

**What the default log hides.** File and project names, paths, user and host names, slate text
and CSV/job values appear as short codes such as `<path#1a2b3c4d.mov>` or `<text#87623dee len=14>`.
The same value always gets the same code on your machine, so a problem can still be traced
through the log. Known gap: a slate DATE appears in the clear in finding messages.

**Rotation:** at start, when the file is 10 MB or more; 5 old files are kept. If the log folder
cannot be written, cmr prints one notice and carries on, warnings go to the screen.

**Sending us a support bundle:** run `cmr logs collect` (add `--reports DIR` to include your last JSON
reports; those contain file paths and slate text, so only add them if you may share them) and send
the `.tar.gz` it writes. For slate or OCR problems we may ask you to re-run with
`--log-level debug --log-private`, which logs names and slate text verbatim: share that bundle only
if you are allowed to.

The log is on by default while the tool is young; this default will change to off later.

---

## 16. QC test suite for your own renders

A set of your own real renders (known-good ones and ones with known problems), each with the
result you expect, that you re-run after every CheckMyRender update to see that nothing got
worse. The runner is `tests/suite/run_suite.py` in the CheckMyRender folder (a source
checkout; it is not part of the installer bundle) and is an advanced, optional tool.

**Rules:** real client media never goes into the CheckMyRender folder. The suite is a folder of
its own that **points at** the media where it lives (read-only); only the suite's spec sheets,
job files, expectations and reports are stored in it. Keep it private (it names your jobs).

### Layout

```
suite/
  suite.yaml                 the list of runs
  projects/<id>/             one folder per project or family of deliverables
    spec.yaml ...            spec sheet(s); job.yaml + CSV + slates for slated spots
    expected.yaml            what each run should report
    waivers.yaml             reviewed waivers of this project (optional)
  baselines/                 reviewed JSON reports to compare with (create this folder yourself)
  runs/<date>_<tier>/        written by the runner: one folder per run + summary.json
```

`suite.yaml`:

```
suite_version: 1
roots:                       # short name -> folder (a run's file is "<root>:<path>")
  renders: /mnt/renders
defaults: {timeout_s: 600}   # per file
runs:
  - id: demo_good
    file: "renders:one_track/SHOWNAME_101_HD_2398_ST_v03.mov"
    project: demo            # folder under projects/
    spec: projects/demo/spec.yaml
    profile: review          # stock name, or a path ending .yaml (relative to the suite)
    job: projects/demo/job.yaml   # optional; without it the runner passes --no-job
    tags: [smoke, acceptance]
```

`projects/<id>/expected.yaml`:

```
expected_version: 1
runs:
  demo_good:
    exit: 0                  # expected exit code (null = not yet known)
    source: predicted        # where the expectation comes from (smoke-/meta-<date> | predicted)
    other: fail              # an unlisted problem finding makes the run FAIL (allow = ignore)
  demo_defects:
    exit: 2
    findings:
      - {check: audio.clipping, severity: FAIL, frames: [69, 71], status: real_issue}
      - {check: video.blanking, severity: WARN, frames: [40, 44], status: real_issue}
    absent: []               # checks that must report no problem
    other: fail
```

`status` says why a finding is expected: `real_issue` (a defect in the file; the tool must keep
finding it), `intended` (meant that way; candidate for a waiver), `tool_gap` (a wrong or
missing result the tool should fix), `unconfirmed`. A finding matches on check and severity
(and edge/channel/kind when given) with frames overlapping within 5 frames. Expectations are
never bent to make a run pass.

### Running it

```
python3 ~/CheckMyRender/tests/suite/run_suite.py ~/qc_suite --tag smoke
```

(with PyYAML available: the cmr environment's Python is fine; the runner runs `python -m cmr`
from its own folder's `src/`). The suite folder can also be given by `$CMR_SUITE`. One file at
a time, `--mode background` and `--max-findings 0` by default, with a timeout. Output, real
(two demo renders, an expected blanking finding left out on purpose to show a failure):

```
FAIL  demo_defects             exit=2        0.9s
        - unexpected: video.blanking WARN kind=edge edge=top frames 40-40
2 runs: PASS 1, FAIL 1; reports in ~/qc_suite/runs/20261026_163233_smoke
```

| option | meaning |
|---|---|
| `--tag T` (repeatable) | run the runs with this tag (OR): `smoke`, `acceptance`, `meta` ... |
| `--run ID` (repeatable) | run exactly these ids (ignores tags) |
| `--meta` | only the settings checks (no decoding): safe on any size and on a busy server |
| `--quality fast\|high` | `cmr check --quality`; `--tag smoke` implies `fast`. The expectation key is then `<id>@fast` (falls back to `<id>`) |
| `--slow` | include runs tagged `slow` (very large files; on request) |
| `--max-size-mb N` | skip larger files (default 500 for smoke) |
| `--update-baseline` | store this run's report as the baseline (`baselines/<id>.json`; the `baselines/` folder must exist) |
| `--timeout S`, `--mode`, `--dry-run` | seconds per run; foreground/background; only print the commands |

Verdict per run: PASS, FAIL (a difference from the expectation or the baseline, listed under
the line), ERROR (no report, media missing, timeout), NEW (no expectation yet), SKIP (a run
tagged `needs-tesseract` without tesseract). Exit code 1 on any FAIL or ERROR.

Tiers by tag: `meta` (settings only), `smoke` (fast quality, small files, seconds each),
`acceptance` (every run at the profile's quality, compared with the expectations and baselines),
`slow` (huge files, only on request). On a shared file server: one file at a time, background
mode, `timeout`, no full-decode run outside the acceptance tier.

The suite is for the people who maintain cmr or a house's profiles; for a plain nightly
folder check use section 13.

---

## 17. Troubleshooting

Start with `cmr doctor`, then `cmr profiles show PROFILE --spec SPEC`.

| message | cause | fix |
|---|---|---|
| `cmr: command not found` | the installer's launcher is not on the PATH, or (manual install) the environment is not active | add `~/.local/bin` to the PATH (the installer prints the line to add), or call `~/.local/bin/cmr` by its full path; manual install: `conda activate ~/envs/cmr` or call `~/envs/cmr/bin/cmr` |
| `config error: profile 'relase' not found (looked in .../profiles)` | typo | check `cmr profiles list` (11 stock profiles). If the list is empty the install is broken: rerun the installer (profiles ship inside the package; `pip install .` and `pip install -e .` both carry them) |
| `config error: missing spec key ${spec.video.codec_profile}, used by profile check video.codec (profile 'review')` plus the spec file to fill in | spec lacks a key the profile refers to | add the key (optional keys may be `null`), or switch that check off in your profile (`enabled: false`) |
| `config error: spec ... invalid: video/fps: 23.976 is not of type 'string', 'integer'` | fps as a decimal | `fps: "24000/1001"` |
| `config error: profile ... invalid: checks/video.blanking/quality: 'fats' is not one of ['fast', 'high']` | typo | `fast` or `high` |
| `ERROR   video.blackruns: unknown check id` | misspelt check id | see section 10 |
| `config error: profile 'x' params invalid:` / `checks.video.blanking: unknown parameter(s) 'coverge' (allowed: ...)` (exit 3) | misspelt parameter in a profile | fix the name; the message lists the allowed ones |
| `ERROR   decode.errors: bad profile params: quality 'fast' not implemented (supports: high)` | fast mode on a check without one | remove `quality` |
| `ERROR   audio.loudness: configuration error: standard 'EBU R-128' has no preset in the profile (known: EBU R128, ATSC A/85)` | spec names a standard the profile lacks | fix the name, or add it to `standards` |
| `ERROR   probe: [Errno 2] No such file or directory: 'missing.mov'` | wrong path | |
| `cmr: internal error (exit 3); details in the log: PATH` | a bug in cmr | `cmr logs collect` and send the bundle (section 15) |
| `ERROR   probe: no video stream` | not a video file (e.g. a `.scc` picked up by `*`) | use `*.mov` |
| `error: --spec and --profile required: no job.yaml in the current folder (or use --job / --job-dir)` (exit 3) | no job file found and no options | give `--profile` and `--spec`, or run in the job's folder / use `--job-dir` |
| `config error: job file .../job.yaml invalid:` (exit 3) | a `job.yaml` that is in the current folder does not load (`cmr doctor` also shows `INVALID`) | fix it, or `--no-job` to ignore it |
| `cmr: error: unrecognized arguments: ...` (exit 3) | wrong command line | `cmr check --help` |
| `config error: waiver file waivers.yaml: [Errno 2] No such file or directory` | `--waivers` names a file that does not exist | check the path; `cmr waive` creates the file |
| `timing.layout: head (231) + tail (24) frames fall outside the file (96 frames)` plus SKIPPED content checks (also `audio.loudness`) | head/tail in the spec longer than the file (wrong spec or very short file) | fix the spec layout |
| `FAIL    timing.timecode_start: no embedded timecode (no tmcd track / timecode tag)` | MP4 or a file without timecode | `timing.timecode_start: {enabled: false}` for such deliverables |
| `slate not read: tesseract not found ...` | slate checks without tesseract | install it (the installer does), set `CMR_TESSERACT`, section 14 |
| `FAIL    slate.claims: slate LENGTH '15s' = 450 frames ... but the program is ...` | the slate says a different length than the file has | the slate or the file is wrong |
| `WARN ... uncertain read ... check by eye` | OCR confidence under 60 | look at the slate frame at the timecode shown |
| colour WARNs `(untagged)` on ProRes on Rocky only | ffprobe 5.1 reads the ProRes frame header, 6.1 falls back to the MOV tag | check the render's colour tagging; compare `cmr probe` on both machines |
| `error: reports/... differs from the file in the report (identity)` from `cmr waive` | the media changed or moved since the report | re-run `cmr check`, then `cmr waive` from the same folder |

### Other things that go wrong

- A slate result that looks wrong: run again with `--log-level debug --log-private` and look at
  `cmr logs tail`; the OCR text is in the log then (section 15).
- A `job.yaml` in the current folder you did not mean to use: `--no-job`, or name the one you
  want with `--job`. The `job file: PATH` line on stderr always says which one was used.
- The demo-media scripts need Python modules the cmr environment may lack (`make_spot.py`:
  Pillow, numpy, PyYAML): use another Python for them, section 3.

### ffmpeg versions

Tested: 6.1.1 (Ubuntu) and 5.1.10 (Rocky 9.7), full test suite passing on both. Known
difference: ProRes colour tags (above). Another ffmpeg build: point cmr at it with
`cmr --ffmpeg PATH --ffprobe PATH ...` and run `cmr doctor` with the same options.

### Memory and CPU on a busy workstation

- `--mode background`: nice 15, idle disk priority, 2 filter threads, 16 GB memory cap. On the
  demo files ffmpeg peaked at about 340 MB; long UHD renders need more (UNVERIFIED how much).
- If background mode is still too heavy, also pass `--filter-threads 1`.
- If a run dies with a memory error in background mode, the 16 GB cap was hit: run in
  foreground at a quiet time (UNVERIFIED message text).
- Several renders: one `cmr check` after the other (as `overnight_qc.sh` does), not in parallel.
- `--filter-threads` above 8 does not help: many small picture measurements run slower with more
  threads (measured).

---

## 18. Glossary

| term | meaning here |
|---|---|
| frame number | position of a frame in the file, counted from 0 at the first frame. Unambiguous; independent of timecode |
| timecode (TC) | `HH:MM:SS:FF` label of a frame, from the file's timecode track (or derived from the spec) |
| DF / NDF | drop-frame / non-drop-frame timecode. At 29.97 and 59.94 DF skips frame numbers so the timecode keeps up with the clock; written `01:00:00;00`. NDF counts every frame; `01:00:00:00` |
| program | the part of the file that is the actual show/spot, between head and tail |
| head / tail | material before / after the program: bars and tone, black, slate, 2-pop |
| FFOA | first frame of action: the first program frame, usually 01:00:00:00 |
| slate | the information card before the program (title, Ad-ID, duration, date ...) |
| 2-pop | one frame of tone (and often a picture flash) exactly 2 s before the program, for sync |
| Ad-ID | the industry identifier of a commercial (11 characters, +H for HD / D for SD) |
| spec sheet | YAML file with the facts of one deliverable |
| profile | YAML file with the rules: which checks, how strict, how fast |
| finding | one result line: a problem, or a PASS |
| severity | PASS, SKIPPED, INFO, WARN, FAIL, ERROR (section 6) |
| waiver | a reviewed finding that is accepted (section 12) |
| fingerprint | a small signature of a waived range (picture or sound) used to notice changes |
| LUFS / LKFS | loudness units relative to full scale (the same unit; LKFS is the US name) |
| LU | loudness unit, a difference in loudness (1 LU = 1 dB) |
| integrated loudness | loudness of the whole program, gated (BS.1770) |
| dialogue-gated | loudness measured only where there is speech (some US specs); NOT what cmr measures |
| true peak (dBTP) | highest level of the reconstructed analogue signal, can be above the sample peak |
| sample peak (dBFS) | highest sample value; 0 dBFS = digital full scale |
| LRA | loudness range: how much the loudness varies over the program |
| clipping | samples at or near full scale (distortion) |
| blanking | black or non-picture lines at the picture edges |
| matte | a black bar several pixels deep: letterbox (top/bottom), pillarbox (left/right) |
| dark edge | dark PICTURE at a frame edge (curtain, night sky): not a render error by itself |
| sharp edge | a black line with a sharp step to the picture: the typical render error |
| black run | consecutive black frames inside the program |
| legal range (tv) / full range (pc) | 8-bit black 16, white 235 vs 0-255 |
| ProRes profiles | Proxy, LT, 422 (Standard), HQ, 4444, 4444 XQ |
| fast / high | quality modes (section 9) |
| exit code | the number a command returns to the shell: 0 pass, 1 warn, 2 fail, 3 error (including a wrong command line) |
| job file (`job.yaml`) | the entry point of a job: names its spec, profile, waiver file, report folder and (for ad spots) CSV and slate template; section 5 |
| job id | the short name of a job (`job: id:` in job.yaml); stamped on waivers so they only apply to that job |

---

## 19. Not built yet

Roadmap, not available today:

- **macOS.** Planned for Apple Silicon (M-series) Macs only; Intel Macs are not planned. The
  code is Linux-first; the installer prints a polite "planned" message on a Mac, and the
  diagnostic log will live in `~/Library/Logs/cmr` there.
- **Dialogue-gated loudness** (as some US specs require). cmr measures BS.1770 relative-gated
  loudness and says so in every message; it is not a certified meter reading.
- **Machine-learning review** of blanking and freeze-frame findings (fewer false alarms).
- **Image sequences** (EXR/DPX).
- **Caption sidecars** (SCC/SRT) and **caption quality** (only the presence of embedded
  captions is checked).
- **Stems** and other multi-stream layouts beyond the loudness programs of section 8
  (merging mono streams into one layout for `audio.layout`).
- **Stills** of findings (the timecode is the locator).
- Freeze frames, dropped/duplicated frames, timecode track continuity, legal levels / gamut,
  banding, A/V sync offset, accidental watermarks, 2-pop detection, slate audio and countdown
  checks, phase and DC offset, field cadence, pillarbox allowance, HTML/JUnit reports, watch
  folders, waivers for settings findings and for content that moved in time.

---

## Appendix A: demo media used in this guide

The repository can generate small test renders with ffmpeg (no real media needed). The
scripts are in the `tests/` folder of the CheckMyRender folder (not in the installer bundle).
This is the media all examples in this guide were made with:

```
mkdir -p ~/cmr_demo && cd ~/cmr_demo
python3 ~/CheckMyRender/tests/media/make_media.py . one_track
```

`make_media.py` needs only Python 3 and ffmpeg. It writes one folder per case, each with a
4-second 1080p ProRes 422 HQ 23.976 file `SHOWNAME_101_HD_2398_ST_v03.mov` (1 slate frame, 23
black, 48 program frames, 24 black tail, timecode 00:59:59:00). Without a case name it writes
all cases:

| case (folder) | what it is |
|---|---|
| `one_track/` | nothing wrong: ONE stereo track, no captions. The file of quick start A; matches `templates/specs/spec_review_render.yaml` after the edits of the quick start |
| `good/` | nothing wrong: two stereo tracks, embedded 608 captions (matches `tests/media/spec_test.yaml`, a spec that requires both) |
| `wrong_meta/` | H.264 MP4, 720p, 25 fps, interlaced, mono AAC, no timecode, no captions |
| `wrong_tc/` | timecode starts 24 frames late |
| `short/` | program 8 frames short |
| `no_captions/` | captions missing |
| `corrupt/` | damaged bytes in the middle |

Slated spots for quick start B (needs Python with Pillow, numpy and PyYAML; writes to a folder
outside the CheckMyRender folder): `python3 ~/CheckMyRender/tests/slate/make_spot.py OUTDIR
--short` (clean + errors), `--defect NAME` (one planted error per folder, e.g. `client_typo`),
`--layout label_colon` (the "Label: value" slate). Each run also writes `values.yaml` (the slate
text) and `manifest.yaml` (every planted error with frames and the checks that should catch it).

```
cmr check one_track/*.mov --profile release --spec specs/spec_showname_101.yaml
PASS    one_track/SHOWNAME_101_HD_2398_ST_v03.mov
        24000/1001 fps, 96 frames, start TC 00:59:59:00 (file)
  SKIPPED captions.presence: captions not required by spec
RESULT PASS (exit 0): PASS 22, SKIPPED 1
```

### Extra demo files with planted defects

The `defects/` and `many/` files used in sections 4, 6, 9 and 12 are the `one_track` case plus
defects, made with the generator's Python function (run in `~/cmr_demo`):

```
python3 - <<'PY'
import sys, os; sys.path.insert(0, os.path.expanduser("~/CheckMyRender/tests/media"))
import make_media as m
m.make(".", "defects", audio_tracks=1, captions=False,
       vf_extra="drawbox=x=0:y=0:w=iw:h=1:color=black:t=fill:enable='between(n,40,44)',"
                "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(n,50,65)'",
       af_extra="volume=volume=30dB:enable='between(t,2.9,3.0)'")
m.make(".", "many", audio_tracks=1, captions=False, vf_extra=",".join(
       f"drawbox=x=0:y=0:w=iw:h=1:color=black:t=fill:enable='eq(n,{24 + 3 * i})'" for i in range(14)))
PY
```

`defects/`: 1-pixel black top line on frames 40-44, black frames 50-65, audio pushed into
clipping near frame 70. `many/`: 14 one-frame top-line defects, every 3rd frame from 24.
All examples with these files use the spec of quick start A (`specs/spec_showname_101.yaml`).

---

## 20. Installation (Rocky Linux)

Requirements: section 1. The installer works per user (no sudo, never as root), needs network
access to conda.anaconda.org (conda-forge packages only; no PyPI, no Anaconda `defaults`) and
about 3 GB free disk space (1.5 GB with `--no-slate`). It never touches the system Python or the
system ffmpeg, and never changes conda's base or other environments.

    tar xzf cmr-<version>-bundle.tar.gz
    cmr-<version>/install.sh --dry-run     # shows every step, changes nothing
    cmr-<version>/install.sh               # installs (about 1 minute)

What it does:
- uses your conda (Anaconda/Miniconda/Miniforge on PATH or in ~/anaconda3, ~/miniconda3,
  ~/miniforge3), or offers to install Miniforge privately (`--bootstrap-conda`; not yet verified on a
  machine without conda);
- creates its own environment ~/.local/share/cmr/env (Python 3.12, PyYAML, jsonschema, the cmr
  package, tesseract 5.5.2 for the slate checks; `--no-slate` skips tesseract, about 0.9 GB less);
- uses the system ffmpeg/ffprobe when they are 5.1 or newer and have every filter cmr needs
  (Rocky 9: 5.1.10, accepted); otherwise it installs conda-forge ffmpeg 6.1 into a separate
  environment ~/.local/share/cmr/ffmpeg-env;
- writes the launcher ~/.local/bin/cmr (`--bin-dir DIR` for another folder) and copies examples/,
  templates/ and this guide to ~/cmr-workspace (`--workspace DIR`); files you edited there are never
  overwritten, unedited ones are updated on upgrade;
- ends with `cmr doctor` and a self-test on a generated clip. Log: ~/.cache/cmr/logs/install.log.

If ~/.local/bin is not on your PATH the installer says so; add
`export PATH="$HOME/.local/bin:$PATH"` to ~/.bashrc, or call ~/.local/bin/cmr by its full path.
No `conda activate` is needed, also not in scripts or cron (verified with `env -i`).

Other options: `--prefix DIR` (install location), `--upgrade` (refresh packages and reinstall cmr),
`--uninstall` (asks; removes the environment and launcher, keeps your workspace and logs), `-y`.
Exit codes: 0 done, 1 failed (log kept), 2 usage/unsupported platform, 3 cancelled.
The demo-media scripts (tests/) are not in the bundle: the quick starts that generate media need a
checkout of the repository.

### Updating
Unpack the new bundle and run its install.sh: it upgrades in place, updates the workspace files you
did not edit and keeps the ones you did. Same version again: re-checks only (seconds).

### cmr doctor

`cmr doctor` checks ffmpeg, ffprobe, tesseract and every ffmpeg filter the checks need, and
prints the log location and whether the current folder holds a `job.yaml`. On the Rocky 9 test
machine (28 check lines, most left out here; paths shortened):

```
cmr --version
cmr 0.0.1
cmr doctor
ffmpeg: /usr/bin/ffmpeg
ffprobe: /usr/bin/ffprobe
ffmpeg version: ffmpeg version 5.1.10 Copyright (c) 2000-2026 the FFmpeg developers
tesseract (slate checks): /home/USER/.../env/bin/tesseract
check audio.clipping: ok
check audio.format: ok
...                                   (one line per check)
check video.scan: ok
log: /home/USER/.cache/cmr/logs/cmr.log
job.yaml in the current folder: none
OK
```

`OK` and exit code 0 = ready. `PROBLEMS FOUND` (exit 3) lists what is missing, e.g.
`check video.blanking: MISSING filters lut2`. Without tesseract the line reads
`tesseract (slate checks): NOT FOUND - ...`; that does not stop `OK` (only slate checks need it).
In a folder with a `job.yaml`, the last line shows `job.yaml in the current folder: PATH (job
ID)`, or `- INVALID: ...` with the reason.

