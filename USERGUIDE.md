# CheckMyRender user guide

CheckMyRender (`cmr`) is a command-line QC tool for renders out of Flame and other
finishing apps. It checks a rendered file against a **spec sheet** (what the delivery must
be) using a **profile** (how strict to be), looks at every frame and every audio sample, and
reports each problem with **timecode and frame number**.

> **Status: 0.0.1, early.** The checks pass their test suite on generated media and a few
> real files. Results on your renders still need a human look. Sections marked
> **IN DEVELOPMENT** describe features that are being built now; their details can change.

Written for operators and technical leads at finishing / post houses (Flame artists, QC and
delivery staff) who are at home on a Linux shell but are not software developers.

**New here?** Check section 1 (what the machine needs), then do **one** quick start:
A for any render, B for an ad spot with a slate. Everything after that is reference; come
back to it when you need it. Installing cmr is the last section (20).

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
14. [Slate checks (IN DEVELOPMENT)](#14-slate-checks-in-development)
15. [Diagnostic log](#15-diagnostic-log)
16. [QC test suite for your own renders (IN DEVELOPMENT)](#16-qc-test-suite-for-your-own-renders-in-development)
17. [Troubleshooting](#17-troubleshooting)
18. [Glossary](#18-glossary)
19. [Not built yet](#19-not-built-yet)
- [Appendix A: demo media used in this guide](#appendix-a-demo-media-used-in-this-guide)
20. [Installation (Rocky Linux)](#20-installation-rocky-linux)

Starter files to copy: [templates/](templates/README.md) and [examples/spot_xr/](examples/spot_xr/).

Conventions: commands are meant to be copied. `$` is not shown. Output shown under a command
was produced by that command on generated test media (Appendix A), unless marked
**UNVERIFIED**.

---

## 1. Platform requirements

- **Linux**, tested on Rocky Linux 9 (macOS will follow later).
- **Python 3.12 in its own conda environment.** Never update or replace the system Python on
  a Flame workstation (Rocky 9 ships 3.9, which Flame and the OS rely on).
- **ffmpeg and ffprobe** on the PATH (tested: 6.1.1 on Ubuntu, 5.1.10 on Rocky 9.7; the system
  packages are fine).
- **tesseract 5** only if you use the slate checks (section 14); optional.
- How to install all of this: section 20. `cmr doctor` tells you whether the machine is ready.

---

## 2. Quick start A: any render, no slate

Goal: check one render against a spec of its own. About five minutes.

You need the render (here the generated test file from [Appendix A](#appendix-a-demo-media-used-in-this-guide)),
and `cmr` working (`cmr doctor` says `OK`).

**1. Copy a spec template.** The templates are never edited in place. (Here we work in the
demo folder of Appendix A; for real jobs keep specs in `~/qc/specs/`, see [templates/README.md](templates/README.md).)

```
cd ~/cmr_demo && mkdir -p specs
cp ~/CheckMyRender/templates/specs/spec_review_render.yaml specs/spec_showname_101.yaml
```

**2. Look at what is really in a known-good render**, then edit the 5-6 lines marked `# EDIT`
in the copy. Everything else in the template is a normal HD ProRes review render:

```
cmr probe good/*.mov
```

| field in the spec | set it to |
|---|---|
| `job.title_code` | the show code in the file name, e.g. `"SHOWNAME"` |
| `job.episode`, `job.version` | as in the file name (`"101"`, `"v03"`) |
| `video.codec_profile` | the ProRes flavour you render, e.g. `"422 HQ"` |
| `timing.program_duration_frames` | frames of the program only, not the head or tail (here `48`) |
| `audio.tracks` | one line per audio stream in the file (`cmr probe` lists them) |

Also compare `video.width/height/fps` and `head_layout` / `tail_layout` with your render
(the template has a 1-frame slate + 23 black head and a 24-frame black tail).

**3. Check that spec and profile load**, then run the check with a stock profile:

```
cmr profiles show review --spec specs/spec_showname_101.yaml
cmr check good/*.mov --profile review --spec specs/spec_showname_101.yaml
```

Choose the profile by purpose: `review` (review renders, fast), `release` (final deliveries,
strict, needs the spec's captions/loudness keys), or a copy of a `templates/profiles/client_*`
file given by path (`--profile ~/qc/profiles/hl_review.yaml`). `cmr profiles list` shows the
stock ones; section 7 explains them.

**4. Read the result.** The first run fails, which is typical: the template lists one audio
stream and this file has two.

```
FAIL    good/SHOWNAME_101_HD_2398_ST_v03.mov
        24000/1001 fps, 96 frames, start TC 00:59:59:00 (file)
  FAIL    audio.layout: audio streams: expected 1, got 2
RESULT FAIL (exit 2): PASS 19, FAIL 1
```

Add the second `- {index: 1, layout: stereo, content: "M&E"}` line under `tracks:` and run again:

```
PASS    good/SHOWNAME_101_HD_2398_ST_v03.mov
        24000/1001 fps, 96 frames, start TC 00:59:59:00 (file)
RESULT PASS (exit 0): PASS 20
```

A render with a real problem (here a file 8 frames short, and one that starts 1 s late):

```
cmr check short/*.mov wrong_tc/*.mov --profile review --spec specs/spec_showname_101.yaml
FAIL    short/SHOWNAME_101_HD_2398_ST_v03.mov
        24000/1001 fps, 88 frames, start TC 00:59:59:00 (file)
  FAIL    timing.frame_count: program frames: expected 48 (+-0), got 40 (file 88 - head 24 - tail 24); program range shown @ 01:00:00:00-01:00:01:15 (frames 24-63)
FAIL    wrong_tc/SHOWNAME_101_HD_2398_ST_v03.mov
        24000/1001 fps, 96 frames, start TC 01:00:00:00 (file)
  FAIL    timing.layout: first program frame (file frame 24, after 24 head frames) has timecode 01:00:01:00, expected 01:00:00:00; 01:00:00:00 is file frame 0 @ 01:00:01:00 (frame 24)
  FAIL    timing.timecode_start: start timecode: expected 00:59:59:00 (program_start_timecode - 24 head frames), got 01:00:00:00 (off by +24 frames) @ 01:00:00:00 (frame 0)
RESULT FAIL (exit 2): PASS 37, FAIL 3
```

Each line: severity, which check, what is wrong, then `@ timecode (frame)`: type that
timecode into Flame or your player to see the spot. The last line is the verdict.

**The exit code** (for scripts and cron) is the worst result of all files:

| code | meaning |
|---|---|
| 0 | pass (also: only INFO or SKIPPED) |
| 1 | worst result is WARN: look at it |
| 2 | worst result is FAIL: do not deliver as is |
| 3 | ERROR: the tool could not check (bad config, missing file or filter) |

**What next:** for a check in seconds right after Flame writes the file, use a copy of
`templates/profiles/client_render_check.yaml` (settings only, no picture or sound measurements).
Details: options (section 5), reading results (section 6), profiles (section 7), spec fields
(section 8). A spot with a slate: quick start B. A problem you have looked at and accept:
quick start C.

---

## 3. Quick start B: an ad spot with a slate

Goal: check a slated spot against the job data (agency, client, Ad-ID, title, length) and the slate
text. The slate is read with OCR, so this needs **tesseract** (sections 1 and 14). About ten minutes.

**1. Copy the sample job files** into a folder per job:

```
mkdir -p ~/qc/jobs/sb_league && cd ~/qc/jobs/sb_league
S=~/CheckMyRender/examples/spot_xr
cp $S/job.yaml $S/deliverables.csv $S/spec_spot_xr.yaml .
cp -r $S/slates .
```

What each file is for:

| file | what it is |
|---|---|
| `spec_spot_xr.yaml` | the technical spec: codec, frame rate, head layout, program length, audio |
| `job.yaml` | the job constants (agency, client, artist), the expected filename pattern, where the CSV and slate template are |
| `deliverables.csv` | one row per spot of the job: Ad-ID, title, length (no filenames) |
| `slates/xr_standard.yaml` | the design of the slate: which labels it has and what each must say |
| profile `spot_review` | the rules (a stock profile, nothing to copy; `spot_render` and `spot_delivery` also exist) |

(`templates/slate/` holds the same set with extra comments, for a :30 spot.)

**2. Edit these values** (open the files in your editor):

| file | edit |
|---|---|
| `job.yaml` | `agency`, `client`, `artist`; `product: "<empty>"` if your slates leave PRODUCT blank. A blank value means "not checked" |
| `deliverables.csv` | your rows: `Ad-ID`, `Title`, `Length` (seconds: `30`) |
| `spec_spot_xr.yaml` | `program_duration_frames` (:30 at 29.97 = `900`, :60 = `1800`), and codec / fps / audio if your delivery differs |

Leave `slates/xr_standard.yaml` alone unless your slate has different labels.

*Trying it without a real spot:* the generated demo spot is 3 s long, so set the length to match:
`sed -i 's/,60,v01,/,03,v01,/' deliverables.csv` and set `program_duration_frames: 90`.
Make the clip with `/usr/bin/python3 ~/CheckMyRender/tests/slate/make_spot.py ~/spots --short`.
The clip with the misspelt client below: the same command with `--defect client_typo` added (it lands in `spots/client_typo/`).

**3. Run it** from the job folder (`--deliverables` is optional: `job.yaml` already names the CSV):

```
cmr check "spots/clean/SB X League 2026_ProRes Generic Submaster_QB Pat_03_ABCD1234567H_1920x1080_Slated.mov" \
    --profile spot_review --spec spec_spot_xr.yaml --job job.yaml --deliverables deliverables.csv
```

**4. Read the result.** A good spot:

```
SKIPPED spots/clean/SB X League 2026_ProRes Generic Submaster_QB Pat_03_ABCD1234567H_1920x1080_Slated.mov
        30000/1001 fps, 300 frames, start TC 00:59:53:00 (file)
  SKIPPED captions.presence: captions not required by spec
RESULT SKIPPED (exit 0): PASS 25, SKIPPED 1
```

(SKIPPED = everything that ran passed; one check did not apply. Exit 0.) The same job with the
edits of step 2 forgotten (the CSV still says 60 s, the spec 1800 frames) looks like this:

```
  FAIL    filename: filename: expected 'SB X League 2026_ProRes Generic Submaster_QB Pat_60_ABCD1234567H_1920x1080_Slated.mov', got 'SB X League 2026_ProRes Generic Submaster_QB Pat_03_ABCD1234567H_1920x1080_Slated.mov'
  WARN    slate.content: slate DURATION ':03', expected ':60' @ 00:59:53:01-00:59:57:28 (frames 1-148)
  FAIL    timing.frame_count: program frames: expected 1800 (+-0), got 90 (file 300 - head 210 - tail 0); program range shown @ 01:00:00:00-01:00:02:29 (frames 210-299)
RESULT FAIL (exit 2): PASS 22, SKIPPED 1, WARN 1, FAIL 2
```

**When a slate field is wrong.** A spot whose slate spells the client `Sample Bugrers`:

```
WARN    spots/client_typo/SB X League 2026_ProRes Generic Submaster_QB Pat_03_ABCD1234567H_1920x1080_Slated.mov
        30000/1001 fps, 300 frames, start TC 00:59:53:00 (file)
  SKIPPED captions.presence: captions not required by spec
  WARN    slate.content: slate CLIENT 'Sample Bugrers', expected 'Sample Burgers' @ 00:59:53:01-00:59:57:28 (frames 1-148)
RESULT WARN (exit 1): PASS 24, SKIPPED 1, WARN 1
```

The message names the slate field, what the slate says and what the job expects. Decide which side is wrong:

- **the slate is wrong:** fix it in the slate graphic and re-render;
- **the job data is wrong** (typo in `job.yaml` or the CSV, wrong row): fix that file and run again;
- **both are as intended:** accept the finding (quick start C).

The OCR can misread; a result marked `check by eye` is uncertain: look at the slate frame
(the timecode is in the message).

**What next:** all slate rules, the other profiles and the `--row` option: section 14.
Quality of the picture and sound checks, tuning: sections 9 to 11.

---

## 4. Quick start C: accept a finding you have reviewed

Goal: a problem was looked at and is fine (a deliberate fade to black, intentional distortion). Waive it
so the next render of the same job does not flag it again. Waived findings stay visible in the report.

Two commands. First write a JSON report and list the findings (each gets a number):

```
cmr check defects/*.mov --profile review --spec specs/spec_showname_101.yaml --report json -o reports/
cmr waive reports/cmr_report.json
   1 FAIL  audio.clipping: stream 1: sample peak 0.00 dBFS >= -0.1 dBFS on channel(s) 1,2 @ 01:00:01:21-01:00:01:23 (defects/SHOWNAME_101_HD_2398_ST_v03.mov)
   2 FAIL  audio.clipping: stream 2: sample peak 0.00 dBFS >= -0.1 dBFS on channel(s) 1,2 @ 01:00:01:21-01:00:01:23 (defects/SHOWNAME_101_HD_2398_ST_v03.mov)
   5 WARN  audio.loudness: integrated loudness outside -23 +-1 LU; ...
   6 WARN  audio.loudness: true peak above -1 dBTP (first in this 100 ms block); ...
  16 WARN  video.blanking: top: black edge (non-picture, sharp step), 100% of line, seen on 1 sampled frame(s) ...
```

Then waive the ones you accepted, with a reason:

```
cmr waive reports/cmr_report.json --finding 1 2 --reason "Intentional distortion effect in the mix" --expires 2027-06-30
W0001: audio.clipping @ 01:00:01:21-01:00:01:23 (finding 1)
W0002: audio.clipping @ 01:00:01:21-01:00:01:23 (finding 2)
waivers: waivers.yaml (2 added, 2 total)
```

Run the next render with the waiver file:

```
cmr check defects/*.mov --profile review --spec specs/spec_showname_101.yaml --waivers waivers.yaml
WARN    defects/SHOWNAME_101_HD_2398_ST_v03.mov
        24000/1001 fps, 96 frames, start TC 00:59:59:00 (file)
  WAIVED  audio.clipping: stream 1: sample peak 0.00 dBFS >= -0.1 dBFS on channel(s) 1,2 @ 01:00:01:21-01:00:01:23 (frames 69-71)
          was FAIL; waiver W0001: Intentional distortion effect in the mix
  ...
RESULT WARN (exit 1): PASS 17, WARN 3, WAIVED 2
WAIVERS waivers.yaml: applied 2, content changed 0, stale 0, expired 0
```

The same works for slate findings (verified with the `Sample Bugrers` slate of quick start B: exit 1 became exit 0,
`WAIVED slate.content`). Run `cmr waive` from the folder you ran `cmr check` in, with the same media file in place.
A waiver remembers what the picture or sound looked like: if the content changes, the finding comes back for a new review.

**What next:** category waivers ("all dark-edge findings on the left"), expiry, stale waivers: section 12.

---
## 5. Running a check

```
cmr check FILE [FILE ...] --profile PROFILE --spec SPEC.yaml [options]
```

- `--profile`: a stock profile name (`release`, `review`, ...; list them with
  `cmr profiles list`) or the path to a profile `.yaml` file.
- `--spec`: the spec sheet of the job.
- Several files: list them, or use a shell wildcard. All files are checked against the same
  spec, and you get one combined report. Give only media files: `good/*` would also pick up the
  `captions.scc` sidecar, which is reported as `ERROR   probe: no video stream`.

```
cmr check good/*.mov wrong_tc/*.mov --profile release --spec spec_test.yaml
```

A spec describes ONE deliverable (its filename, length and timecode). For a folder of
different deliverables run one `cmr check` per file with its own spec (section 13).

### Options of `cmr check`

| option | what it does |
|---|---|
| `-v`, `--verbose` | also list the checks that passed |
| `--report json -o DIR` | also write `DIR/cmr_report.json` (the directory is created). The name is fixed: a second run into the same DIR overwrites it |
| `--fail-on warn\|fail` | lowest severity that makes the exit code non-zero (default `warn`) |
| `--max-findings N` | list at most N problems per check, summarise the rest (default 10; `0` = list all). Overrides the profile's `max_findings` |
| `--mode foreground\|background` | `foreground` (default): as fast as possible. `background`: do not disturb other work (see below) |
| `--filter-threads N` | ffmpeg filter threads (default: half the CPUs, at most 8). Overrides the mode's value |
| `--waivers FILE` | apply a waiver file (section 12) |
| `--job`, `--deliverables`, `--row` | slate checks, section 14 |

Global options go BEFORE the sub-command: `cmr --ffmpeg /opt/ffmpeg/bin/ffmpeg --ffprobe
/opt/ffmpeg/bin/ffprobe check ...` uses another ffmpeg build.

### Exit codes

| code | meaning |
|---|---|
| 0 | passed (or only INFO / SKIPPED; also WARN with `--fail-on fail`) |
| 1 | worst result is WARN |
| 2 | worst result is FAIL |
| 3 | ERROR: the tool could not check something (bad config, missing file, missing filter) |

The exit code is computed from all findings, including findings not listed because of the
`--max-findings` cap. Example of `--fail-on fail` (a review with warnings only):

```
cmr check many/*.mov --profile review --spec spec_test.yaml --fail-on fail
...
RESULT WARN (exit 0): PASS 19, WARN 3
```

Note: a wrong command line (e.g. `--report json` without `-o`) also exits with 2, printed
by the option parser as `cmr: error: --report needs -o DIR`. Scripts should check that a
`RESULT` line exists before trusting exit code 2.

### Run modes: foreground and background

| | foreground (default) | background |
|---|---|---|
| CPU priority | normal | nice 15 (lower) |
| disk priority | normal | idle (`ionice -c 3`, Linux) |
| ffmpeg filter threads | min(8, CPUs/2) | 2 |
| memory | no limit | 16 GB virtual-memory cap for cmr and its ffmpeg |

Use `--mode background` on a Flame workstation while Flame is in use, and for overnight
batches. It takes longer, mostly because of the 2 filter threads (the blanking and black-run
measurements are the expensive part). How much longer on a real render: UNVERIFIED.

### Other commands

| command | what it does |
|---|---|
| `cmr probe FILE` | prints what cmr sees in a file (codec, profile, pixel format, colour tags, audio streams, timecode) in the words a spec uses. Run it on a known-good render when writing a spec |
| `cmr profiles list` | stock profiles |
| `cmr profiles show PROFILE [--spec SPEC]` | the merged profile; with `--spec`, filled in with the spec's values. The quickest way to see whether a spec/profile pair loads |
| `cmr waive ...` | create waivers (section 12) |
| `cmr doctor` | installation check (section 20) |
| `cmr logs ...` | diagnostic log (section 15, in development) |

`cmr probe` (shortened and some lines joined):

```
cmr probe good/*.mov
{
  "path": "good/SHOWNAME_101_HD_2398_ST_v03.mov",
  "kind": "file",
  "container": "mov",
  "timecode": "00:59:59:00",
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
      "avg_fps": "24000/1001",
      "scan": "progressive",
      "color": {"primaries": "bt709", "transfer": "bt709", "matrix": "bt709", "range": "tv"},
      ...
  "audio": [
    {"index": 1, "codec": "pcm_s24le", "sample_rate": 48000, "bit_depth": 24,
     "channels": 2, "channel_layout": "stereo"},
  ...
  "subtitles": [{"index": 3, "codec": "eia_608"}]
}
```

---

## 6. Reading the results

### The console report

```
cmr check defects/*.mov --profile release --spec spec_test.yaml
FAIL    defects/SHOWNAME_101_HD_2398_ST_v03.mov
        24000/1001 fps, 96 frames, start TC 00:59:59:00 (file)
  FAIL    audio.clipping: stream 1: sample peak 0.00 dBFS >= -0.1 dBFS on channel(s) 1,2 @ 01:00:01:21-01:00:01:23 (frames 69-71)
  FAIL    audio.clipping: stream 2: sample peak 0.00 dBFS >= -0.1 dBFS on channel(s) 1,2 @ 01:00:01:21-01:00:01:23 (frames 69-71)
  FAIL    audio.loudness: integrated loudness outside -23 +-0.5 LU; EBU R128: I -15.2 LUFS, LRA 0.0 LU, true peak 1.0 dBTP [BS.1770 relative-gated (-70 LUFS absolute, -10 LU relative); not dialogue-gated] @ 01:00:00:00-01:00:01:23 (frames 24-71)
  FAIL    audio.loudness: true peak above -1 dBTP (first in this 100 ms block); EBU R128: I -15.2 LUFS, LRA 0.0 LU, true peak 1.0 dBTP [BS.1770 relative-gated (-70 LUFS absolute, -10 LU relative); not dialogue-gated] @ 01:00:01:21-01:00:01:23 (frames 69-71)
  FAIL    video.black_runs: 16 consecutive black frames in program @ 01:00:01:02-01:00:01:17 (frames 50-65)
  FAIL    video.blanking: top: black edge (non-picture, sharp step), 100% of line, 5 frame(s) @ 01:00:00:16-01:00:00:20 (frames 40-44)
RESULT FAIL (exit 2): PASS 17, FAIL 6
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

**Timecode and frame number.** The timecode is the file's own timecode (what you type into
Flame or your player to jump there). The frame number counts from the first frame of the file,
starting at 0. Both point to the same frame. Audio findings are placed on the video frame
that contains the audio.

### Severities

| severity | meaning | exit code |
|---|---|---|
| PASS | check ran, nothing found | 0 |
| SKIPPED | check did not apply (e.g. captions not required, no audio stream) | 0 |
| INFO | worth knowing, not a problem by the profile's rules | 0 |
| WARN | probable problem, look at it | 1 |
| FAIL | problem by the profile's rules | 2 |
| ERROR | the tool could not do the check (config error, crash, missing filter) | 3 |
| WAIVED | a reviewed and accepted finding (section 12); counted, not in the exit code | 0 |

The **profile** decides what is FAIL, WARN or INFO for each check.

A file whose only non-PASS result is SKIPPED shows **SKIPPED** as its verdict, with exit 0.
That means "everything that ran passed; something did not apply":

```
SKIPPED master/EXFEAT_101_HD_2398_ST_v01.mov
        24000/1001 fps, 96 frames, start TC 00:59:59:00 (file)
  SKIPPED captions.presence: captions not required by spec
RESULT SKIPPED (exit 0): PASS 20, SKIPPED 1
```

`[fast mode]` after a check id means it ran in fast mode (section 9):

```
  WARN    video.blanking [fast mode]: top: black edge (non-picture, sharp step), 100% of line, seen on 1 sampled frame(s) (fast: 1 in 5 frames; true start/end up to 4 frame(s) outside the range shown) @ 01:00:00:06 (frame 30)
```

### The findings cap

A check never lists more than 10 problems per file by default. The rest is summarised in
one line, and still counted and in the exit code:

```
cmr check many/*.mov --profile release --spec spec_test.yaml --max-findings 3
FAIL    many/SHOWNAME_101_HD_2398_ST_v03.mov
        24000/1001 fps, 96 frames, start TC 00:59:59:00 (file)
  FAIL    video.blanking: top: black edge (non-picture, sharp step), 100% of line, 1 frame(s) @ 01:00:00:00 (frame 24)
  FAIL    video.blanking: top: black edge (non-picture, sharp step), 100% of line, 1 frame(s) @ 01:00:00:03 (frame 27)
  FAIL    video.blanking: top: black edge (non-picture, sharp step), 100% of line, 1 frame(s) @ 01:00:00:06 (frame 30)
  (11 additional video.blanking findings suppressed; worst: FAIL)
RESULT FAIL (exit 2): PASS 20, FAIL 14 (11 not listed but counted; --max-findings 0 lists all)
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

`--report json -o DIR` writes `DIR/cmr_report.json` (schema version 1.2) for scripts,
archives and support. Main fields:

| field | content |
|---|---|
| `schema_version` | `"1.2"` |
| `tool` | `name`, `version`, `ffmpeg` (version line), `python` |
| `profile` | `name`, `sha256` (one hash per profile file in the `extends` chain) |
| `spec` | `path`, `sha256` (and `job` with the slate checks) |
| `files[]` | one entry per checked file, below |
| `summary` | `worst`, `exit_code`, `fail_on`, `counts` (per severity, incl. `WAIVED`), `max_findings`, `suppressed` |
| `waivers` | only with `--waivers`: `file`, `applied`, `content_changed`, `stale`, `expired` |

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

A morning summary of a folder of reports: `templates/automation/summarize_reports.py`
(section 13).

---

## 7. Profiles: the rules

A profile says which checks run, how strict they are and how fast. It never contains job
facts; those come from the spec through `${spec....}` references.

### Stock profiles (in `profiles/`, do not edit)

| profile | for | in short |
|---|---|---|
| `final` | base of all others | every check, high quality, everything FAIL (colour/scan untagged: WARN; blanking mattes: WARN) |
| `release` | deliveries | `final` + EBU R128 +-0.5 LU, black runs and silences max 6 frames, captions FAIL |
| `review` | review renders | `final` + blanking and black runs FAST, blanking and loudness WARN, dark picture edges INFO, filename INFO, captions off, black runs / silences up to 24 frames |
| `broadcast_master` | long-form masters | `release` + silent channels INFO, 25 findings per check for black runs and blanking |
| `colour_interim` | graded/comped interims | `review` + timing, naming, black runs, silence INFO; loudness, captions, filename off; sharp blanking FAIL. Needs a spec with audio values |
| `log_interim` | log interims | `colour_interim` + colour tags INFO, black runs off, dark edges hidden |
| `spot_render`, `spot_review`, `spot_delivery` | slated spots | section 14 (in development) |

Templates to copy: `templates/profiles/` (`client_render_check`, `client_review`,
`client_delivery`, `client_colour_interim`), see [templates/README.md](templates/README.md).

### Format

```
profile_version: 1                 # always 1
name: hl_delivery                  # = file name without .yaml
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
not have stops the run: `config error: missing spec key ${spec.audio.sample_rate} (no
'spec.audio.sample_rate')` (exit 3). This happens even for checks you switched off: the whole
profile is filled in before it runs. The stock profiles reference `audio.codec`,
`audio.sample_rate`, `audio.bit_depth`, `audio.tracks`, `audio.loudness`, `content.captions`,
`job`, `timing` and the `video` keys, so a spec needs all of them, or your profile must replace
those references (as `templates/profiles/client_colour_interim.yaml` does for no-audio specs).

### Writing your own profile

1. Copy a template from `templates/profiles/` to e.g. `~/qc/profiles/hl_delivery.yaml`.
2. Change `name:`, adjust the checks.
3. Check it loads and see the result: `cmr profiles show ~/qc/profiles/hl_delivery.yaml --spec SPEC.yaml`
4. Run it on a known-good render (expect PASS) and on a known-bad one.

Watch out:

- `profiles show` does not test check parameters; `cmr check` does. A misspelt parameter of
  `video.blanking`, `video.black_runs` or the audio content checks is an ERROR:
  `ERROR   video.blanking: bad profile params: Additional properties are not allowed ('coverge' was unexpected)`.
- A misspelt parameter of a settings check (`container.format`, `video.codec`, `video.resolution`,
  `video.fps`, ...) is **silently ignored**: `video.codec: {codek: prores}` passes without a
  word. Compare with `cmr profiles show` and this guide.
- A misspelt check id is an ERROR: `ERROR   video.blackruns: unknown check id`.
- A misspelt `quality` stops the run: `checks/video.blanking/quality: 'fats' is not one of ['fast', 'high']`.

---

## 8. Spec sheets: the job facts

One spec sheet per deliverable. Templates: `templates/specs/` (generic master, HD long form,
spot short form, review render, colour interim without audio). Examples: `examples/`.

### All fields

| field | example | used by | notes |
|---|---|---|---|
| `spec_version` | `1` | loader | required, always 1 |
| `job.title`, `job.title_code`, `job.episode`, `job.version`, `job.client` | `"EXFEAT"` | filename | free fields; any `job.x` can be a `{x}` placeholder in `naming_pattern` |
| `job.filename` | `"EXFEAT_101_HD_2398_ST_v01.mov"` | filename | exact expected name; wins over the pattern |
| `job.naming_pattern` | `"{title_code}_{episode}_{format}_{fps_tag}_{audio_tag}_{version}.mov"` | filename | see "Filename" below |
| `video.container` | `mov` | container.format | as `cmr probe` prints it (`mov`, `mp4`, ...) |
| `video.codec` | `prores` | video.codec | ffprobe codec name (`prores`, `h264`, `dnxhd`) |
| `video.codec_profile` | `"422 HQ"` | video.codec | ProRes: `"422 Proxy"`, `"422 LT"`, `"422"`, `"422 HQ"`, `"4444"`, `"4444 XQ"`; H.264: `"High"`, `"Main"` ... |
| `video.width`, `video.height` | `1920`, `1080` | video.resolution | |
| `video.pixel_aspect` | `1.0` | video.resolution | untagged files count as square |
| `video.aspect_ratio` | `"16:9"` | video.resolution | display aspect |
| `video.pixel_format` | `yuv422p10le` | video.pixel_format | ProRes 4444 decodes as `yuv444p12le` |
| `video.fps` | `"24000/1001"` | video.fps, timing | **exact fraction in quotes**; `23.976` is rejected |
| `video.scan` | `progressive` | video.scan | `progressive` or `interlaced` |
| `video.color` | `{primaries: bt709, transfer: bt709, matrix: bt709, range: tv}` | video.color | `tv` = limited/legal, `pc` = full |
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
| `content.captions` | `{required: true, formats: [embedded_608]}` | captions.presence | `embedded_608`, `embedded_708` |

A value left out of a settings check is "not checked" only if the profile does not refer to
it; with the stock profiles every key above is needed (section 7).

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

### Timecode, drop-frame and durations

- All positions are frames. A duration is a frame count: count it in Flame or your NLE.
- Drop-frame (29.97/59.94 only) is written with `;` before the frames: `01:00:00;00`. In DF,
  a duration of `00:44:26;00` is 79,900 frames, not 44 x 60 x 30 + 26 x 30.
- Spots count timecode seconds: :30 at 29.97 = 30 x 30 = 900 frames; at 23.976 = 30 x 24 = 720.
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
- A Flame export with one mono stream per channel must be listed as that many `mono` tracks;
  merging them into one layout is not supported yet.
- `content` is a free description.
- `tracks: []` = the file must have no audio (use with a profile that does not need the other
  audio keys, `templates/profiles/client_colour_interim.yaml`).

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

Two checks have a fast mode. Set it per check in the profile: `quality: fast`.

| check | high (default) | fast | what fast can miss |
|---|---|---|---|
| `video.blanking` | every frame | every 5th file frame (`fast_frame_step`) | defects shorter than 5 frames may be missed; ranges are approximate (+-4 frames) |
| `video.black_runs` | every frame, full resolution | every frame, quarter resolution | on grainy near-black, fast tends to call MORE frames black (more findings, not fewer) |
| slate checks | 2 frames | same (both modes identical) | |

Every other check has only `high`; `quality: fast` there is an ERROR
(`bad profile params: quality 'fast' not implemented (supports: high)`).

Example: the `many` demo file has 14 one-frame blanking defects. `release` (high) finds all
14; `review` (fast) finds 3 of them (frames 30, 45, 60: the multiples of 5):

```
cmr check many/*.mov --profile review --spec spec_test.yaml
WARN    many/SHOWNAME_101_HD_2398_ST_v03.mov
        24000/1001 fps, 96 frames, start TC 00:59:59:00 (file)
  WARN    video.blanking [fast mode]: top: black edge (non-picture, sharp step), 100% of line, seen on 1 sampled frame(s) (fast: 1 in 5 frames; true start/end up to 4 frame(s) outside the range shown) @ 01:00:00:06 (frame 30)
  WARN    video.blanking [fast mode]: top: black edge (non-picture, sharp step), 100% of line, seen on 1 sampled frame(s) (fast: 1 in 5 frames; true start/end up to 4 frame(s) outside the range shown) @ 01:00:00:21 (frame 45)
  WARN    video.blanking [fast mode]: top: black edge (non-picture, sharp step), 100% of line, seen on 1 sampled frame(s) (fast: 1 in 5 frames; true start/end up to 4 frame(s) outside the range shown) @ 01:00:01:12 (frame 60)
RESULT WARN (exit 1): PASS 19, WARN 3
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
| `video.fps` | frame rate and average frame rate, exactly | `expect` |
| `video.scan` | progressive / interlaced | `expect`, `unknown_severity: warn` (not signalled) |
| `video.color` | primaries, transfer, matrix, range | `expect`, `unknown_severity: warn` (untagged) |
| `audio.format` | codec, sample rate, bit depth of every audio stream | `codec`, `sample_rate`, `bit_depth` |
| `audio.layout` | number of audio streams and channels per stream | `tracks` |
| `filename` | file name | `job`, `tags` |
| `captions.presence` | embedded captions present | `captions` |

Known limits: only the first video stream is checked. Untagged colour is reported at
`unknown_severity`. ffprobe 5.1 (Rocky 9) and 6.1 read ProRes colour tags differently: a file
whose ProRes frame header is untagged while the MOV `colr` atom says bt709 is "unknown" on 5.1
and bt709 on 6.1 (whether Flame renders look like that: UNVERIFIED). H.264 caption (A53)
detection: UNVERIFIED. Captions: presence only, not content or timing; sidecar files are not
read. Bit depth is compared for PCM audio only.

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

Files without a timecode track (MP4) FAIL `timing.timecode_start` with
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

Limits: noisy or grainy black and logos over black are not tuned on real footage yet
(UNVERIFIED); a small bright logo (under 10% of the picture) does not stop a frame counting as
black.

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
| `exclude_segments` | `[slate]` | head/tail segments not judged |
| `class_severity` | edge, edge_persistent, dark_edge, dark_edge_persistent: fail; matte: warn; matte_persistent: info | per-class severity (`pass`/`info`/`warn`/`fail`), capped by `severity` |

Known limits: tuned on one real H.264 file and planted defects; ProRes deliverables from Flame
still UNVERIFIED. A real 1-pixel black line next to very dark picture (luma up to ~22) cannot be
told from dark picture: it is reported as `dark_edge` (or `matte`), not lost - unless you set
`dark_edge_mode: hide`. Grey or duplicated edge lines (not black) are not detected. Colour bars
in the head have black areas at the bottom edge and are reported as a matte unless `bars_tone`
is in `exclude_segments`:

```
  WARN    video.blanking: bottom: black bar (matte/letterbox/pillarbox?), 50% of line, 900 frame(s) @ 00:59:10;00-00:59:39;29 (frames 0-899)
```

(stock `release` on a long-form file with 30 s of SMPTE bars; `templates/profiles/client_delivery.yaml`
excludes `bars_tone` and reports nothing).

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

Known limits: the true-peak location is the first 100 ms block over the ceiling; 5.1/7.1
loudness weighting is UNVERIFIED; at 29.97 a clipped frame can be reported 1 frame early;
channel maps with intentionally silent channels need `audio.silent_channel` at `info`
(cmr cannot be told which channels are meant to be empty).

---

## 11. Tuning

Change these in YOUR profile (section 7), never in the stock profiles.

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
- Bars or other graphics in the head: `exclude_segments: [slate, bars_tone]`.
- Only full-line defects: `coverage: 0.9`. Partial masks matter: keep 0.5.
- Lifted black on a log render: the sharp-edge rule still works (it looks for the step);
  use `dark_edge_mode: hide` there (`log_interim` does).
- Too many lines in a long report: `max_findings: 25`.

**Black runs.** `max_black_run_frames` (allowed run length inside the program) and
`black_luma` (raise it to 28-32 if grainy black is not caught; lower it if very dark scenes are
called black).

**Silence and clipping.** `audio.silence: {threshold_dbfs: -60, max_run_frames: 12}`,
`audio.clipping: {clip_dbfs: -0.1}` (-0.5 for a stricter house rule).

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

Only picture and sound findings with a frame range can be waived (not settings, filename,
timing or decode findings).

### Per finding

```
cmr check defects/*.mov --profile release --spec spec_test.yaml --report json -o reports/
cmr waive reports/cmr_report.json
  22 FAIL  audio.clipping: stream 1: sample peak 0.00 dBFS >= -0.1 dBFS on channel(s) 1,2 @ 01:00:01:21-01:00:01:23 (defects/SHOWNAME_101_HD_2398_ST_v03.mov)
  ...
  37 FAIL  video.black_runs: 16 consecutive black frames in program @ 01:00:01:02-01:00:01:17 (defects/SHOWNAME_101_HD_2398_ST_v03.mov)
  38 FAIL  video.blanking: top: black edge (non-picture, sharp step), 100% of line, 5 frame(s) @ 01:00:00:16-01:00:00:20 (defects/SHOWNAME_101_HD_2398_ST_v03.mov)

cmr waive reports/cmr_report.json --finding 37 --reason "Fade to black in the edit, approved by the director" --expires 2027-06-30
W0001: video.black_runs @ 01:00:01:02-01:00:01:17 (finding 37)
waivers: waivers.yaml (1 added, 1 total)
```

- Without `--finding` it only lists the numbered findings that can be waived.
- `--finding 37 38` waives several; `--all-check video.black_runs` waives every listed finding of
  that check.
- `--reason` is required. `--expires YYYY-MM-DD` is optional (last day it applies).
- `-o FILE` chooses the waiver file (default `./waivers.yaml`); new waivers are appended.
- Run it from the folder you ran `cmr check` in: it reads the media file again (the path in the
  report) to fingerprint it, and refuses if the file changed.
- From a capped report, findings that were not listed cannot be waived one by one: re-run with
  `--max-findings 0`, or use a category waiver.

Next render:

```
cmr check defects/*.mov --profile release --spec spec_test.yaml --waivers waivers.yaml
  ...
  WAIVED  video.black_runs: 16 consecutive black frames in program @ 01:00:01:02-01:00:01:17 (frames 50-65)
          was FAIL; waiver W0001: Fade to black in the edit, approved by the director
RESULT FAIL (exit 2): PASS 17, FAIL 5, WAIVED 1
WAIVERS waivers.yaml: applied 1, content changed 0, stale 0, expired 0
```

### What "content changed", "stale" and "expired" mean

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
WAIVERS waivers.yaml: applied 0, content changed 0, stale 1, expired 0
  stale: W0001 video.black_runs @ 01:00:01:02-01:00:01:17 (frames 50-65): Fade to black in the edit, approved by the director
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
  WAIVED  audio.clipping: 2 finding(s) by category waiver W0002 (worst was FAIL) @ 01:00:01:21-01:00:01:23 (frames 69-71)
WAIVERS waivers.yaml: applied 2, content changed 0, stale 1, expired 0
  category W0002 audio.clipping <= FAIL: 2 finding(s) waived: Intentional distortion effect in the mix
  stale: W0003 category video.blanking kind=dark_edge edge=left <= WARN: Dark curtain on the left in the stage scenes
```

### The waiver file

Commented example: `templates/waivers/waivers_example.yaml`. Edit only `reason` and
`expires` by hand. Waivers apply to every file of a run (they are not tied to a file name):
keep one waiver file per job. There is no reviewer name in a waiver yet; put it in the reason
if you need it.

---

## 13. Batch and overnight runs

`templates/automation/overnight_qc.sh` checks every `.mov`, `.mxf` and `.mp4` in a folder (not
recursive), one file after the other, in background mode, with one report folder per file:

```
templates/automation/overnight_qc.sh renders reports release ~/cmr_demo/spec_test.yaml ~/cmr_demo/waivers.yaml
2026-10-26 14:44:08 3 file(s) in renders -> reports
2026-10-26 14:44:08 checking A_good.mov
2026-10-26 14:44:09 checking B_defects.mov
2026-10-26 14:44:11 checking C wrong meta.mp4
2026-10-26 14:44:12 done. Summary: reports/summary.tsv
exit  verdict  file              started
2     FAIL     A_good.mov        2026-10-26 14:44:08
2     FAIL     B_defects.mov     2026-10-26 14:44:09
2     FAIL     C wrong meta.mp4  2026-10-26 14:44:11
```

(All three FAIL here because the copies were renamed and the spec expects one exact filename.)

- Output: `reports/<file name>/cmr_report.json`, `reports/<file name>/console.txt`,
  `reports/summary.tsv`.
- A file that already has a report is skipped on the next run (`REDO=1` checks it again), so an
  interrupted night can simply be restarted.
- Settings: `CMR` (how to call cmr), `CMR_TIMEOUT` (per file, default `6h`; exit 124 = timed
  out), `CMR_EXTRA` (more options, e.g. `--max-findings 0`).
- All files of one call use one spec. Different deliverables: one call (or one folder) per spec.

Morning summary:

```
python3 templates/automation/summarize_reports.py reports
FAIL    exit 2  renders/B_defects.mov
        profile release, PASS 16, FAIL 4, WAIVED 3
  FAIL    audio.loudness: integrated loudness outside -23 +-0.5 LU; EBU R128: I -15.2 LUFS, ... @ 01:00:00:00 (frame 24)
  FAIL    filename: filename: expected 'SHOWNAME_101_HD_2398_ST_v03.mov', got 'B_defects.mov'
  FAIL    video.blanking: top: black edge (non-picture, sharp step), 100% of line, 5 frame(s) @ 01:00:00:16 (frame 40)
  ...
3 report(s); worst exit code 2
```

`--all` also lists WAIVED findings. Any Python 3 runs it (also the system Python).

From cron, without activating conda (tested on Rocky 9 with a minimal environment):

```
# m h dom mon dow   command
30 20 * * 1-5  CMR=$HOME/envs/cmr/bin/cmr $HOME/qc/overnight_qc.sh /mnt/renders/tonight $HOME/qc/reports/$(date +\%F) release $HOME/qc/specs/spec_example.yaml >> $HOME/qc/overnight.log 2>&1
```

(The crontab line itself: UNVERIFIED as a cron entry; the same command was run from a shell
with an empty environment.)

On a busy workstation run batches one file at a time (as the script does), in background
mode, and keep at least the 16 GB the background cap allows free.

---

## 14. Slate checks (IN DEVELOPMENT)

> Built and tested on generated slated spots (Rocky 9, ffmpeg 5.1.10, tesseract 5.5.2) only;
> no real slates yet. Details may change. Use the results as a second pair of eyes.

The slate checks read the slate of a spot with OCR (tesseract), compare it with the job data
and the actual file, and measure the colour bars on the slate.

| check id | what | default severity |
|---|---|---|
| `slate.present` | the spec's head layout has a `slate` segment, and every required slate label is found on both frames read | FAIL |
| `slate.consistency` | the 2nd frame and the 2nd-to-last frame of the slate agree (countdown ignored) | FAIL |
| `slate.content` | each field vs what the job expects: AGENCY, CLIENT, TITLE (= filename), AD-ID (= CSV row and filename), DURATION, DATE rule ... | per field |
| `slate.claims` | what the slate and filename CLAIM vs the actual media: DURATION vs program frames, AUDIO vs the audio layout, resolution/codec/fps words in the title | FAIL |
| `slate.bars` | colour bars and grey steps on the slate vs BT.709 values (+-2 code values) | WARN in `spot_review` |

### What you need

- tesseract 5: `conda install -y -p ~/envs/cmr tesseract`, then `cmr doctor` shows it. Or set
  `CMR_TESSERACT=/path/to/tesseract`. Without it every OCR check is an ERROR
  `slate not read: tesseract not found ...`; `slate.bars` and everything else still run.
- Four files per job (templates in `templates/slate/`, example job in `examples/spot_xr/`):
  - `spec_spot_slated.yaml`: technical spec WITHOUT a `job:` section,
  - `job.yaml`: job constants (agency, client, artist ...), the filename pattern, where the CSV
    and slate template are, how a CSV row is matched to a file,
  - `deliverables.csv`: one row per spot (Ad-ID, title, length, ...), no filenames,
  - `slates/<template>.yaml`: the slate design (labels, field types, expectations, bars).
- One slate design: labels right-aligned in a column, values left-aligned next to them, colour
  bars on top, grey steps at the bottom, a countdown that is ignored. Centred values,
  `LABEL: value` slates and wrapped values are not read yet.

### Run

```
cmr check "SPOT.mov" --profile spot_review --spec spec_spot_slated.yaml --job job.yaml
```

Profiles: `spot_render` (right after render: settings, layout, filename, decode, slate present),
`spot_review` (slate read and verified; AD-ID, TITLE, DURATION mismatches FAIL; loudness and bars
WARN; picture checks fast), `spot_delivery` (everything high and FAIL).
`--deliverables CSV` uses another CSV; `--row VALUE` picks the CSV row by hand
(`--row EXCO2700001H`, `--row "#2"`, `--row Title=...`).

A clean generated spot with the templates (program shortened to 3 s):

```
cmr check clean/*.mov --profile spot_review --spec spec.yaml --job job.yaml
SKIPPED clean/EC Morning Rush_Hero_03_EXCO2700001H_1920x1080_Slated.mov
        30000/1001 fps, 300 frames, start TC 00:59:53:00 (file)
  SKIPPED captions.presence: captions not required by spec
RESULT SKIPPED (exit 0): PASS 25, SKIPPED 1
```

The same spot with planted errors (shortened):

```
  WARN    slate.bars: slate black bar: elevated black: measured 32.0/128.0/128.0, expected Y=16 Cb=128 Cr=128 (Y/Cb/Cr, 8-bit, +-2) @ 00:59:53:01 (frame 1)
  FAIL    slate.claims: slate DURATION ':03' = 90 frames (3 s x 30 timecode frames/s) but the program is 89 frames (-1) @ 00:59:53:01-00:59:57:28 (frames 1-148)
  FAIL    slate.claims: slate AUDIO 'Mono' (1 ch) but the file's first audio stream is stereo (2 ch) @ 00:59:53:01-00:59:57:28 (frames 1-148)
  FAIL    slate.consistency: slate ARTIST changes during the slate: frame 1 'Sam Placeholder', frame 148 'Sam Placeholdr' @ 00:59:53:01-00:59:57:28 (frames 1-148)
  WARN    slate.content: slate CLIENT 'Example Cofefe Co', expected 'Example Coffee Co' @ 00:59:53:01-00:59:57:28 (frames 1-148)
  FAIL    slate.content: wrong slate or renamed file: slate TITLE 'EC Morning Rush_Hero_03_EXCO2700002H_1280x720_Slated' does not match the filename 'EC Morning Rush_Hero_03_EXCO2700001H_1280x720_Slated.mov' @ 00:59:53:01-00:59:57:28 (frames 1-148)
  FAIL    slate.content: slate does not match the file: slate AD-ID 'EXCO2700002H', expected 'EXCO2700001H' @ 00:59:53:01-00:59:57:28 (frames 1-148)
  WARN    slate.content: slate DATE 20250921 is 400 days before the file date 20261026 (creation_time); allowed +-30 days @ 00:59:53:01-00:59:57:28 (frames 1-148)
```

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
  error.
- **DATE** (default `spot_review` rule): format YYYYMMDD and within 30 days of the file's own
  date (container `creation_time`, else modification time); WARN, because file dates change
  when files are copied.
- **Bars** are measured in the file's own Y'CbCr values, so they assume the slate graphic
  went through the same colour management as the picture. If your slates are not, every bars
  check fails: switch `slate.bars` off in your profile.
- Slate findings can be waived like others; the waiver stores the exact OCR text, so a waived
  CLIENT spelling is flagged again when the text changes.
- Cost: about 1 s of one CPU core per slate.

Profile parameters of the slate checks (from the implementation notes; may change): `slate`
(`"${spec.slate}"`, required), `tesseract`, `min_confidence` (60), `uncertain_severity` (warn),
`confusable_severity` (info), `field_severity` ({FIELD: severity}); `slate.content`: `date`
({mode: ignore|format|window|equals_row, format, window_days, severity}); `slate.claims`:
`tolerance_frames` (0), `check_filename` (true); `slate.bars`: `tolerance` (2),
`chroma_tolerance`, `missing_severity` (warn).

**To be revisited when the slate build is final:** parameter list, profile defaults, the
template format, messages.

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
| `--log-file FILE` | default `$CMR_LOG_DIR/cmr.log`, else `~/.cache/cmr/logs/cmr.log` |
| `--log-private` | log names, paths and slate text verbatim |

```
cmr logs path            # where the log is
cmr logs tail -n 40      # last lines
cmr logs collect -o support.tar.gz --reports reports/ --last 5   # support bundle
```

A log line looks like:

```
2026-10-26 14:47:00.341 INFO  engine[1367340/1367340] run=91871b file=a0509361 | file done in 0.543 s: worst PASS, 20 finding(s) {'PASS': 20}; rss 30 MB peak 30 MB, children peak 338 MB
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

## 16. QC test suite for your own renders (IN DEVELOPMENT)

> Proposed; the structure is drafted, the runner is not built.

Idea: keep a set of your own real renders with known problems and known-good ones, with the
expected result for each, and re-run them after every CheckMyRender update to see that nothing
got worse. Real client media never goes into the CheckMyRender folder: the suite lives in its
own folder and points at the media on your file server (read-only).

    suite/
      suite.yaml              list of runs: file, spec, (job), profile, tags
      projects/<id>/          spec(s), job.yaml + CSV + slates (slated jobs), expected.yaml, waivers.yaml
      baselines/<run>.json    reviewed reports to compare with
      runs/<date>_<tier>/     reports of each run

`expected.yaml` lists, per run, the expected exit code and the findings (check, severity,
frames, and whether each is a real issue, intended, or a tool gap). Tiers: `meta` (settings
only, safe on any size), `smoke` (small files, fast mode), `acceptance` (everything, high
quality), `slow` (very large files, on request). On a shared server: one file at a time,
`--mode background`, with `timeout`.

Until the runner exists, `templates/automation/overnight_qc.sh` plus a reviewed
`summary.tsv` per folder is the practical substitute.

**To be revisited when the suite runner is built.**

---

## 17. Troubleshooting

Start with `cmr doctor`, then `cmr profiles show PROFILE --spec SPEC`.

| message | cause | fix |
|---|---|---|
| `cmr: command not found` | environment not active | `conda activate ~/envs/cmr`, or call `~/envs/cmr/bin/cmr` |
| `config error: profile 'relase' not found (looked in .../profiles)` | typo, or cmr installed without `-e` | check `cmr profiles list`; reinstall with `pip install -e .` |
| `config error: missing spec key ${spec.audio.sample_rate} (no 'spec.audio.sample_rate')` | spec lacks a key the profile refers to | add the key, or replace the reference in your profile |
| `config error: spec ... invalid: video/fps: 23.976 is not of type 'string', 'integer'` | fps as a decimal | `fps: "24000/1001"` |
| `config error: profile ... invalid: checks/video.blanking/quality: 'fats' is not one of ['fast', 'high']` | typo | `fast` or `high` |
| `ERROR   video.blackruns: unknown check id` | misspelt check id | see section 10 |
| `ERROR   video.blanking: bad profile params: Additional properties are not allowed ('coverge' was unexpected)` | misspelt parameter | section 10 |
| `ERROR   decode.errors: bad profile params: quality 'fast' not implemented (supports: high)` | fast mode on a check without one | remove `quality` |
| `ERROR   audio.loudness: configuration error: standard 'EBU R-128' has no preset in the profile (known: EBU R128, ATSC A/85)` | spec names a standard the profile lacks | fix the name, or add it to `standards` |
| `ERROR   probe: [Errno 2] No such file or directory: 'missing.mov'` | wrong path | |
| `ERROR   probe: no video stream` | not a video file (e.g. a `.scc` picked up by `*`) | use `*.mov` |
| `cmr: error: --report needs -o DIR` (exit 2) | `--report json` without `-o` | add `-o reports/` |
| `timing.layout: head (231) + tail (24) frames fall outside the file (96 frames)` plus SKIPPED content checks and `ERROR   audio.loudness: check crashed: RuntimeError: ebur128 produced no measurement` | head/tail in the spec longer than the file (wrong spec or very short file) | fix the spec layout; the loudness ERROR follows from it |
| `FAIL    timing.timecode_start: no embedded timecode (no tmcd track / timecode tag)` | MP4 or a file without timecode | `timing.timecode_start: {enabled: false}` for such deliverables |
| `slate not read: tesseract not found ...` | slate checks without tesseract | section 14 |
| colour WARNs `(untagged)` on ProRes on Rocky only | ffprobe 5.1 reads the ProRes frame header, 6.1 falls back to the MOV tag | check the render's colour tagging; compare `cmr probe` on both machines |
| `error: reports/... differs from the file in the report (identity)` from `cmr waive` | the media changed or moved since the report | re-run `cmr check`, then `cmr waive` from the same folder |

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
| exit code | the number a command returns to the shell: 0 pass, 1 warn, 2 fail, 3 error |

---

## 19. Not built yet

Roadmap, not available: freeze frames, dropped/duplicated frames, timecode track continuity,
legal levels / gamut, banding, A/V sync offset, accidental watermarks, 2-pop detection, slate
audio and countdown checks, caption sidecars (SCC/SRT) and caption content, dialogue-gated
loudness, merging mono streams into one layout, phase and DC offset, field order/cadence,
pillarbox allowance, image sequences (EXR/DPX), HTML/JUnit reports, stills of findings,
watch folders, machine-learning review of blanking/freeze findings, waivers for settings
findings and for content that moved in time, macOS support.

---

## Appendix A: demo media used in this guide

The repository can generate small test renders with ffmpeg (no real media needed). This is the
media all examples in this guide were made with:

```
mkdir ~/cmr_demo && cd ~/cmr_demo
python ~/CheckMyRender/tests/media/make_media.py .
cp ~/CheckMyRender/tests/media/spec_test.yaml .
```

(The quick starts use a spec made from `templates/specs/spec_review_render.yaml`; the other
sections use this `spec_test.yaml`, which also requires captions and two audio streams.)

This writes one folder per case, each with a 4-second 1080p ProRes 422 HQ 23.976 file
`SHOWNAME_101_HD_2398_ST_v03.mov` (1 slate frame, 23 black, 48 program frames, 24 black tail,
two stereo tracks, 608 captions, timecode 00:59:59:00):

| folder | what is wrong |
|---|---|
| `good/` | nothing |
| `wrong_meta/` | H.264 MP4, 720p, 25 fps, interlaced, mono AAC, no timecode, no captions |
| `wrong_tc/` | timecode starts 24 frames late |
| `short/` | program 8 frames short |
| `no_captions/` | captions missing |
| `corrupt/` | damaged bytes in the middle |

Two more files with planted picture/sound defects (`defects/`, `many/`) are used in sections 6
and 12; how they were made is at the end of this appendix.

```
cmr check good/*.mov --profile release --spec spec_test.yaml
PASS    good/SHOWNAME_101_HD_2398_ST_v03.mov
        24000/1001 fps, 96 frames, start TC 00:59:59:00 (file)
RESULT PASS (exit 0): PASS 21
```

### Extra demo files with planted defects

The `defects/` and `many/` files used in sections 6, 9 and 12 were made with the test-media
generator's Python function (run in `~/cmr_demo`):

```
python - <<'EOF'
import sys; sys.path.insert(0, "/home/USER/CheckMyRender/tests/media")
import make_media as m
m.make(".", "defects",
       vf_extra="drawbox=x=0:y=0:w=iw:h=1:color=black:t=fill:enable='between(n,40,44)',"
                "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(n,50,65)'",
       af_extra="volume=volume=30dB:enable='between(t,2.9,3.0)'")
m.make(".", "many", vf_extra=",".join(
       f"drawbox=x=0:y=0:w=iw:h=1:color=black:t=fill:enable='eq(n,{24 + 3 * i})'" for i in range(14)))
EOF
```

`defects/`: 1-pixel black top line on frames 40-44, black frames 50-65, audio pushed into
clipping near frame 70. `many/`: 14 one-frame top-line defects, every 3rd frame from 24.

---

## 20. Installation (Rocky Linux)

Requirements: section 1. The machine needs network access for `pip` (it downloads two small
Python packages, PyYAML and jsonschema).

> **Planned:** a proper installer that also takes care of ffmpeg/ffprobe (and tesseract). It does
> not exist yet; until then ffmpeg/ffprobe come from the OS packages and the steps below apply.

### Steps

Put the CheckMyRender folder you received somewhere permanent, e.g. `~/CheckMyRender`
(clone or unpack it there). Then:

```
source ~/anaconda3/etc/profile.d/conda.sh          # path of YOUR Anaconda install
conda create -y -p ~/envs/cmr python=3.12          # own environment, Python 3.12
conda activate ~/envs/cmr
cd ~/CheckMyRender
pip install -e .                                   # -e is required, see below
conda install -y -p ~/envs/cmr tesseract           # optional: slate checks only
cmr doctor
```

`pip install -e .` ("editable") keeps cmr linked to this folder, which is where it finds the
stock profiles in `profiles/`. A plain `pip install .` installs without them: `cmr profiles
list` then prints nothing and `--profile release` fails with `config error: profile 'release'
not found`. (Verified on Rocky 9.)

Every new shell needs `conda activate ~/envs/cmr` before `cmr` works. For scripts and cron
you can instead call the program directly: `~/envs/cmr/bin/cmr` (no activation needed;
verified).

### cmr doctor

`cmr doctor` checks ffmpeg, ffprobe, tesseract and every ffmpeg filter the checks need.
On the Rocky 9 test machine (install path shortened to `/home/USER/envs/cmr`):

```
cmr --version
cmr 0.0.1
cmr doctor
ffmpeg: /usr/bin/ffmpeg
ffprobe: /usr/bin/ffprobe
ffmpeg version: ffmpeg version 5.1.10 Copyright (c) 2000-2026 the FFmpeg developers
tesseract (slate checks): /home/USER/envs/cmr/bin/tesseract
check audio.clipping: ok
check audio.format: ok
...                                   (one line per check)
check video.scan: ok
OK
```

`OK` and exit code 0 = ready. `PROBLEMS FOUND` (exit 3) lists what is missing, e.g.
`check video.blanking: MISSING filters lut2`. Without tesseract the line reads
`tesseract (slate checks): NOT FOUND - ...`; that does not stop `OK` (only slate checks need it).

### Updating

Replace or `git pull` the CheckMyRender folder, then run `cmr doctor` again. With the editable
install there is nothing to reinstall unless the release notes say so.
