# installer - release bundle and per-user installer

Overview and numbered decisions: ../CLAUDE.md (D1, D2 production Python policy, D22, D24, D29,
D31, D32). Status: **first real run done 2026-10-26 on the Rocky 9.7 Flame workstation** (sandboxed
HOME, existing Anaconda with conda 26.5.3): every checklist item passed after the fixes listed in
"First real run" below. **Single env with ffmpeg 7.1.1 (2026-10-26):** re-verified on the
Rocky machine and the Mac ("ffmpeg 7.1 single env" below). **Pinned ffmpeg by default
(2026-10-26, user decision):** every install gets conda-forge ffmpeg 7.1.1; the system ffmpeg only
with `--use-system-ffmpeg` (tested with fakes + a real `--dry-run`; no real install yet).
**Demo spots at install time (2026-10-26, user decision):** real runs on Rocky and the Mac (see
"Demo spots" below). Still pre-release (D22).

## Files
| File | Role |
|---|---|
| `build_bundle.sh` | builds `dist/cmr-<version>-bundle.tar.gz` (+ `.sha256`) from the source tree |
| `install.sh` | the installer shipped in the bundle (bash, per user, no sudo) |
| `environment.yml` | conda package list (tags `# cmr:ffmpeg`, `# cmr:slate`, `# cmr:demo`); install.sh reads it |
| `.gitignore` | `dist/` |
| `../tests/installer/` | pytest: fakes for conda/ffmpeg/curl/df/uname/id/sysctl/sw_vers/getconf, temp HOME, no network |

## Bundle (`build_bundle.sh [-o DIR] [--wheel FILE] [--python PY]`)
`cmr-<v>/`: `install.sh`, `environment.yml`, `required_filters.txt`, `VERSION`, `README.txt`,
`README.md`, `USERGUIDE.md`, `wheels/check_my_render-<v>-py3-none-any.whl`, `examples/` + `templates/` (no CLAUDE.md, no
caches), `demo/make_spot.py` + `demo/values_fictional.yaml` (copied from `tests/slate/` at build
time: the test script is the single source, no second copy in git), `SHA256SUMS` (every file but
itself). Version = `cmr.__version__`.
- The wheel is built offline: `pip wheel --no-deps --no-index --no-build-isolation` in a temp
  COPY of pyproject/src/profiles (no build/ or egg-info in the repo). Needs setuptools + wheel
  for `--python` (here `/usr/bin/python3`: setuptools 78.1, wheel 0.42; the pyenv `python3`
  3.11 on PATH lacks `bdist_wheel`, so pass `--python /usr/bin/python3`).
- Build fails if the wheel lacks `cmr/profiles/final.yaml` (stock profiles as package data,
  pyproject `cmr.profiles` mapping), contains a CLAUDE.md, or a check plugin fails to import.
- `required_filters.txt` = union of every check's `requires_filters`, read from the BUILT wheel
  (the same list `cmr doctor` checks: one source of truth). 15 filters at 0.0.1.

## install.sh
Flags: `--prefix DIR` (default `~/.local/share/cmr`), `--workspace DIR` (default
`~/cmr-workspace`), `--no-demo` (no demo spots, no Pillow/numpy; NOT remembered, like
`--no-slate`), `--bin-dir DIR` (launcher folder, default `~/.local/bin`; a re-run,
`--upgrade` or `--uninstall` of the same prefix reuses the previous `--workspace`/`--bin-dir`
from install-state unless given again), `--no-slate`, `--use-system-ffmpeg` (NOT remembered:
give it on every run), `--upgrade`, `--uninstall`, `--bootstrap-conda`,
`--offline-dir DIR` (hook only: exits 2 "planned"), `-y/--yes`, `-n/--dry-run`, `-h`.
Exit: 0 done, 1 failed (log kept), 2 usage / unsupported platform / root, 3 cancelled.

Layout: `<prefix>/env` (ONE conda env: python, cmr, tesseract and conda-forge ffmpeg 7.1.1; no
ffmpeg only with `--use-system-ffmpeg` and a system ffmpeg that passes the test),
`<prefix>/ffmpeg-bin/{ffmpeg,ffprobe}` (symlinks to the chosen binaries: `<prefix>/env/bin/*` or,
opt-in, the system ones),
`<prefix>/install-state` (key=value: version, ffmpeg_mode, ffmpeg, slate, demo, workspace, bin_dir,
conda, installed = time of the last run), `<prefix>/.cmr-install-prefix` (marker), `<prefix>/conda`
(only after a Miniforge bootstrap), launcher `~/.local/bin/cmr`, log
`${XDG_CACHE_HOME:-~/.cache}/cmr/logs/install.log` (D29 line style, component `install`,
6-hex run id; rotated to `.1` above 5 MB; kept on failure; paths, versions, conda/pip/doctor
output and the self-test console report: nothing private beyond paths, checked on the real run).

Steps: 1 platform (Linux x86_64 and macOS arm64; Intel Macs and Rosetta refused, exit 2),
root refused, bundle complete + `sha256sum -c SHA256SUMS` (or `shasum -a 256 -c`),
prefix/launcher not foreign, install mode, free space (`df -Pk` of the nearest existing
ancestor: 3072 MB with slate, 2048 MB without, 512 MB for an update, 1024 MB for an update
that adds ffmpeg to an env without it (no `--use-system-ffmpeg`); +512 MB when demo spots are
still to be made for this workspace; ESTIMATES) -> 2 conda ->
3 ffmpeg -> 4 network (`curl -I` conda.anaconda.org; skipped without curl) -> 5 env -> 6 wheel
-> 7 launcher + links -> 8 workspace -> state -> 9 verify -> 10 demo spots -> summary + PATH
hint (with the first-check command when demo spots exist) + policy text.

Decisions (installer-local; the main thread may lift them into the root CLAUDE.md):
- **Channels:** every conda call is `--override-channels -c conda-forge` (never Anaconda
  `defaults`: no Anaconda commercial-licence/ToS question, conda 26 ToS plugin not involved).
  Verified 2026-10-26 on the Rocky machine (`conda search`): conda-forge has python 3.12.16, ffmpeg
  6.0-9.0 (gpl and lgpl builds; 9.0.2 seen on the real run), tesseract 5.5.0-5.5.3 (5.5.2 = 168 MB package, Apache-2.0),
  pyyaml 6.0.3, jsonschema 4.26.0. Nothing needs `defaults`. The tesseract 5.5.2 used so far
  came from pkgs/main; the conda-forge build is a different build (same version).
- **No PyPI:** PyYAML/jsonschema come from conda-forge; the wheel goes in with
  `pip install --no-deps --no-index --force-reinstall` + `pip check`, run as
  `env -u PYTHONPATH -u PYTHONHOME PYTHONNOUSERSITE=1 PIP_CONFIG_FILE=/dev/null <env>/bin/python`.
- **conda discovery order:** conda of the previous install (state) -> `conda` on PATH ->
  `~/Tools/anaconda3`, `~/anaconda3`, `~/miniconda3`, `~/miniforge3` (`bin/conda`) ->
  `$CONDA_EXE` -> `<prefix>/conda`. A candidate must answer `--version`. None: ask (or
  `--bootstrap-conda`) to install Miniforge into `<prefix>/conda`: download installer + `.sha256`
  to files with curl, compare, then `bash installer -b -p <prefix>/conda` (never `curl | bash`;
  batch mode does not edit shell start files). Declined: exit 3 with the searched list.
  Miniforge is PINNED (`MINIFORGE_VERSION=26.9.4-0`, URL
  `releases/download/<v>/Miniforge3-<v>-<Linux-x86_64|MacOSX-arm64>.sh` + `.sha256`): the
  unversioned `releases/latest/download/Miniforge3-<platform>.sh.sha256` does not exist (404,
  checked 2026-10-26), so the old URL could never have verified. Bump the pin on purpose.
  `conda create` adds `--no-default-packages` (ignores `.condarc` `create_default_packages`).
- **ffmpeg: pinned 7.1.1 by default (user decision 2026-10-26):** all machines (Rocky and Mac)
  must run the SAME ffmpeg so results are comparable (Rocky's system 5.1.10 gave false colour-tag
  WARNs: ffprobe 5.1 reports color_transfer/primaries `unknown` on ProRes where 7.1 reports
  bt709). So a run without `--use-system-ffmpeg` always puts `ffmpeg=7.1.1` (the `# cmr:ffmpeg`
  line of environment.yml; the printed version `FF_PIN` is read from that line) into
  `<prefix>/env` and links `ffmpeg-bin` to `<prefix>/env/bin`; the system ffmpeg is not even run
  (only reported as "not used and not touched" when one is on PATH). An install that used the
  system ffmpeg gets ffmpeg added to its env on the next plain run (counted as "newly needed",
  so also on a same-version re-run, no pip reinstall), prints "The previous install used the
  system ffmpeg ...; switching to 7.1.1", and the free-space check asks 1024 MB for it.
- **`--use-system-ffmpeg` (opt-in, not remembered):** the old acceptance test. First
  `ffmpeg`/`ffprobe` on PATH OUTSIDE the prefix and `~/.local/bin`; both `-version` >= 5.1 (first
  line `<name> version [n]X.Y...`; git builds `N-...` = unreadable = rejected) and `ffmpeg
  -hide_banner -filters` (same parsing as `engine.available_filters`) lists every filter of
  `required_filters.txt`. Pass: system binaries are linked and used ("not touched") and a
  WARNING says results may differ from the tested 7.1.1 (printed for every accepted system
  ffmpeg, even a 7.1.x one: another build). Fail: a WARNING with the reason, then 7.1.1 is
  installed into the env as in the default (no exit). Re-evaluated on every run; with the flag a
  good system ffmpeg wins again and the env ffmpeg stays unused. `cmr doctor` (src/cmr/cli.py
  `untested_ffmpeg_note`) prints a WARNING line, never a problem, when ffmpeg or ffprobe is not
  7.1.x. ffmpeg 7.1 LTS is the pinned line (9.1 LTS later). History: the first installer pinned 6.1 and needed a SEPARATE `<prefix>/ffmpeg-env`,
  because conda-forge ffmpeg 6.1.x needs libxml2 < 2.14 while tesseract 5.5.x (libarchive) needs
  libxml2-16 >= 2.14 (LibMambaUnsatisfiableError, first real run); 7.1.1 solves with libxml2
  2.15.4 next to tesseract 5.5.2. Until 2026-10-26 a good system ffmpeg was used by default.
- **Migration from the ffmpeg-env layout** (only our own sandbox test installs had it): when
  `<prefix>/ffmpeg-env` exists, a run of this installer adds ffmpeg to `<prefix>/env` (default,
  or with `--use-system-ffmpeg` when the system ffmpeg fails; counted as "newly needed", so it also happens on a same-version re-run),
  switches the links, then removes `ffmpeg-env` (after the links are rewritten, so a failed conda
  call leaves the old layout working) and logs/prints "Migrated: removed the old separate ffmpeg
  environment". With `--use-system-ffmpeg` and a good system ffmpeg the unused ffmpeg-env is
  just removed. Real run: the
  `conda install` into an env made by the old installer only ADDED packages (nothing updated or
  downgraded, libxml2 unchanged), 14 s.
- **Launcher** (`/bin/sh`, marker line `# CheckMyRender launcher (generated by install.sh)`):
  `PATH=<prefix>/ffmpeg-bin:$PATH` (only ffmpeg/ffprobe are prepended, so cron's short PATH
  works), `CMR_TESSERACT=<prefix>/env/bin/tesseract` (with slate; a profile param `tesseract`
  still wins, D27), `unset PYTHONPATH PYTHONHOME`, `PYTHONNOUSERSITE=1` (Flame/user Python
  settings must not leak into the env Python), on macOS only `TMPDIR` from `getconf
  DARWIN_USER_TEMP_DIR` when unset (see "ffmpeg 7.1 single env", issue 1),
  `exec <prefix>/env/bin/cmr "$@"`. A foreign
  `~/.local/bin/cmr` (e.g. an old manual install) is never overwritten: exit 1.
- **Idempotence:** state version != bundle version -> upgrade in place (conda install to the
  specs, wheel reinstalled); same version -> only re-link, re-write launcher, copy missing
  workspace files and re-verify (adds packages only if newly needed); `--upgrade` -> conda
  install to the specs + wheel reinstall. `conda install` with the same specs does not move
  already-satisfying packages to newer builds (UNVERIFIED whether that is wanted).
  `--no-slate` on a re-run does not remove an installed tesseract (launcher drops CMR_TESSERACT).
- **Workspace:** `examples/` and `templates/` from the bundle, CLAUDE.md filtered again (D31).
  `<workspace>/.cmr-installed-files` records the sha256 of every file the installer put there
  (kept across `--uninstall`): a file that still matches it was not edited and is replaced when
  the bundle has a newer one ("updated"); an edited file is never overwritten ("kept as you
  edited them"; without a record, e.g. a workspace from before this change, every differing
  file counts as edited). `README.md` and `USERGUIDE.md` are copied to the workspace top and
  replaced on every run (reference text; `templates/README.md` links `../USERGUIDE.md`).
- **Uninstall:** asks (EOF = no; `--yes` skips), removes the launcher only if it carries the
  marker (in the bin_dir of the install), then `rm -rf <prefix>` only if the marker exists.
  Workspace and logs are kept.
  (It does not call `conda remove`: the env lives inside the prefix; a stale line may stay in
  `~/.conda/environments.txt`, harmless.)
- **Self-test:** `cmr --version`, `cmr doctor` (exit 0 required, output shown), with slate
  `tesseract --version` (`tesseract 5.*`) + `tesseract --list-langs` contains `eng` + doctor did
  not say NOT FOUND; then `ffmpeg -f lavfi testsrc2 320x180 24000/1001 + sine, -t 2, prores_ks
  -profile:v 2 yuv422p10le, pcm_s24le stereo` and `cmr check CLIP --profile review --spec
  <bundle>/templates/specs/spec_review_render.yaml --report json -o DIR`: exit 0-2 and
  `cmr_report_latest.json` written = pass (findings are expected; exit 3 = ERROR). Self-test
  files are removed on success, kept on failure.
- **Versions:** `tesseract=5.5.2` + `leptonica=1.87` pinned (D32 parity; the Rocky slate tests
  ran 5.5.2/1.87.0 from pkgs/main; unpinned `tesseract=5.5` got 5.5.3 on the real run).
  `ffmpeg=7.1.1` (conda-forge default = the gpl build, `gpl_hbbdf940_911` on linux-64,
  `gpl_h670d5b4_111` on osx-arm64: x264/x265 linked; an LGPL build exists as
  `ffmpeg=7.1.1=lgpl*`: solves with tesseract (dry run), not the default, has no libx264, so
  test-media generation would need another H.264 encoder: UNVERIFIED), python 3.12.16,
  libxml2 2.15.4, pyyaml 6.0.3, jsonschema 4.26.0.
- **Size (measured 2026-10-26, `du -sh`):** tesseract carries all 125 languages (no split
  tessdata packages on conda-forge), so "English only" is not offered. Rocky: env with system
  ffmpeg + slate 605 MB (tessdata 340 MB), env with ffmpeg 7.1.1 + slate 1.2 GB (159 packages;
  OpenVINO libs ~156 MB), env ffmpeg 7.1.1 `--no-slate` 765 MB (system ffmpeg `--no-slate`: 197 MB,
  first run). Mac (osx-arm64, ffmpeg + slate): env 844 MB. Package cache (CONDA_PKGS_DIRS) after
  the system-ffmpeg install 348 MB, after adding the ffmpeg env 555 MB (Rocky); 776 MB for the
  Mac install. Free-space estimates: 3 GB with slate (needs ~1.8 GB), 2 GB `--no-slate` (raised
  from 1.5 GB: ~1.3 GB needed with env ffmpeg), 0.5 GB for an update (a migration that adds
  ffmpeg to an existing env needs ~0.7 GB: not covered by the estimate). The cache goes to the
  base conda's `pkgs/` (here `~/anaconda3/pkgs`, writable), which the free-space check does
  not look at (it checks the prefix's file system only).

## Demo spots (user decision 2026-10-26: the installer generates them; real runs the same day)
First-run flow: install -> copy `<workspace>/templates/starter` -> a clean demo spot is already in
its `media/` -> `cmr check media/*.mov` passes in seconds -> swap in a spot with planted errors ->
then the user's own video.
- Packages: `pillow`, `numpy` (`# cmr:demo`, unpinned; real runs got pillow 13.0.0, numpy 2.5.4)
  only without `--no-demo`; cmr itself never imports them. Re-run: missing when
  `<env>/conda-meta/{pillow,numpy}-[0-9]*.json` is absent (counts as "newly needed": an older
  install or a `--no-demo` install gets them added on a plain re-run, no pip reinstall).
- Step 10 (after the self-test, so a broken install never generates): runs
  `env -u PYTHONPATH -u PYTHONHOME PYTHONNOUSERSITE=1 PATH=<prefix>/ffmpeg-bin:$PATH
  <env>/bin/python <bundle>/demo/make_spot.py TMP/main --short` and `... TMP/typo --short
  --defect client_typo` into `<workspace>/.cmr-demo-tmp.XXXXXX` (same file system: files are
  moved, not copied), output to the log. Placed (never over an existing file):
  `demo_spots/clean/<clean>.mov`; `templates/starter/media/<clean>.mov` (HARD LINK to the
  demo_spots copy, `cp -p` when linking fails); `demo_spots/errors/many_errors/<..._1280x720_Slated>.mov`
  + its `manifest.yaml` (all ERRORS_SET defects); `demo_spots/errors/client_typo/<clean name>.mov`
  + `manifest.yaml`. values.yaml is not kept.
- Record `<workspace>/.cmr-demo-files` (workspace-relative paths of what was placed). When it
  exists, step 10 does nothing ("Already created before"), also with `--upgrade` and a new
  version: a demo file the user deleted or moved is never made again (delete the record to
  get them back). The demo media are NOT templates: not in `.cmr-installed-files`, never
  updated or hashed; `templates/starter/media/README.txt` stays a normal template file.
- Failure (generator exit != 0, e.g. no font): WARNING with the generator's last line, "Run the
  installer again to retry, or make them by hand: mkdir -p ~/cmr-demo && PATH=<ffmpeg-bin>:$PATH
  <env>/bin/python <bundle>/demo/make_spot.py ~/cmr-demo --short", plus the CMR_TEST_FONT hint;
  no record written, install exits 0. A bundle without `demo/make_spot.py` only warns.
- `make_spot.py` refuses an OUTDIR inside the repo only when `<parents[2]>/pyproject.toml`
  exists (in a bundle, parents[2] is the folder the bundle was unpacked in, which may hold
  the workspace).
- Uninstall keeps the workspace incl. demo spots and says so.
- Fonts: the generator uses system fonts (DejaVu / DejaVuLGC on Rocky 9 / Arial on macOS; the
  env's conda fonts are mono only and were not tried for OCR). A machine without any of them
  gets the warning; `CMR_TEST_FONT`/`CMR_TEST_FONT_REGULAR` fix it.
Real runs 2026-10-26 (own sandbox folders, removed afterwards; Rocky with XDG_CACHE_HOME and
CONDA_PKGS_DIRS in the sandbox, Mac with a throwaway HOME + Miniforge via CONDA_EXE):
| | Rocky 9.7 (48 cores) | Mac Studio M1 Ultra |
|---|---|---|
| install with demo, cold package cache | 58 s (demo step 9 s) | 123 s (demo step 16 s) |
| install `--no-demo`, warm cache | 15 s | 44 s |
| env with demo / `--no-demo` (`du -sm`) | 1235 / 1137 MB | 905 / 844 MB |
| demo media (3 spots, starter copy hard-linked) | 176 MB (63 + 57 + 63 MB) | 174 MB |
| re-run (idempotent, nothing generated, mtimes unchanged) | 7 s | 3 s |
Acceptance on both (same results): starter copy `cmr check media/*.mov` PASS 27/SKIPPED 1 exit 0
(5-6 s); `--job job_no_slate.yaml` PASS 22/SKIPPED 1 exit 0; client_typo `FAIL slate.content:
slate CLIENT 'Sample Bugrers'` PASS 26/SKIPPED 1/FAIL 1 exit 2; many_errors `--profile
spot_review` PASS 17/SKIPPED 1/WARN 6/FAIL 13, with job.yaml rules WARN 1/FAIL 18, exit 2;
`env -i HOME=.. PATH=/usr/bin:/bin` (+ junk PYTHONPATH/PYTHONHOME) launcher run PASS exit 0;
`--no-demo` env has no pillow/numpy and the workspace no demo files; real `--dry-run` prints
the generator commands and placements and writes nothing; `--uninstall` prints the demo note.
Note: conda adds every env made by a non-sandboxed HOME to `~/.conda/environments.txt` (the two
test lines on Rocky were removed again).

## macOS (D32; ported 2026-10-26, real run on the Mac the same day)
- install.sh runs on bash 3.2: no `declare -A` (state read by `old KEY`, awk), no `mapfile`
  (while-read loops), empty arrays as `${a[@]+"${a[@]}"}`, `date +%3N` only where GNU date
  prints digits (else `.000`), `SHA256=(sha256sum)` or `(shasum -a 256)` (macOS 26 also has a
  `/sbin/sha256sum` with `-c --quiet`; both verified), `grep -v -e '^[[:space:]]*#' -e ...`
  instead of `\s`/`\|`, `-filters` parser accepts 2 flag columns (ffmpeg >= 8). Verified on the
  Mac: BSD `sort -z`, `env -u`, `sed '\{0,1\}'`, `mktemp -d X.XXXXXX`, `od -An -N3 -tx1`.
- Platform: `uname -s/-m` + `sysctl -n sysctl.proc_translated` (1 under Rosetta, where uname -m
  says x86_64): Darwin/arm64 native = supported; Darwin under Rosetta = "open a native
  Terminal"; Darwin/x86_64 native = "needs Apple silicon"; exit 2, nothing created. Install log on
  macOS `~/Library/Logs/cmr/install.log` (XDG_CACHE_HOME wins, as in cmr); the PATH hint names
  `~/.zprofile`; the log's os line adds `sw_vers -productVersion`.
- build_bundle.sh is unchanged (bundles are built on Linux only, D32; it still uses GNU
  `find -printf` and `sha256sum`); the pure-Python wheel serves both platforms and the
  SHA256SUMS format is read by `shasum -c` as well.
- Real run (Mac, macOS 26.7.1, sandboxed: `--prefix/--bin-dir/--workspace` inside one folder,
  `XDG_CACHE_HOME` there, PATH=/usr/bin:/bin:/usr/sbin:/sbin, no conda/ffmpeg on the machine):
  `--dry-run --bootstrap-conda` printed the plan; `--yes --bootstrap-conda` 142 s: Miniforge
  26.9.4-0 downloaded + sha256 verified + `-b -p` (1.4 GB with package cache), env python 3.12.16
  + tesseract 5.5.2 / leptonica 1.87.0, ffmpeg-env conda-forge ffmpeg 6.1.2 (no system ffmpeg),
  doctor OK (all 15 filters), tesseract ok, self-test ok (exit 2), PATH hint ~/.zprofile. Re-run
  3 s (launcher byte-identical, remembered bin dir), `--uninstall` + `n` = nothing removed.
  No file outside the folder changed except conda touching `~/.conda/environments.txt`'s mtime
  (content unchanged). Gatekeeper: curl downloads carry no quarantine attribute; a bundle
  downloaded with a browser does (run `xattr -dr com.apple.quarantine cmr-<v>` or start it with
  `bash install.sh`; UNVERIFIED with a browser download).
- Tests: tests/installer runs green on the Mac with `CMR_TEST_BASH=/bin/bash` (3.2.57) and BSD
  tools (57 passed, 6 skipped: shellcheck, bundle builds without a setuptools /usr/bin/python3).
  A bash:3.2 container (docker, musl/busybox) was used for templates/automation/overnight_qc.sh
  only; the Mac run is the stronger bash 3.2 test for the installer.

## Tests (`tests/installer/test_installer.py`, 83)
Run alone: `PYTHONPATH=src /usr/bin/python3 -m pytest -q tests/installer` (~10 s). Every run uses a
temp HOME and a PATH of symlinked coreutils + fakes; the real conda/ffmpeg are unreachable.
Covered: syntax (shellcheck when installed: not on the dev machine), help/bad args/root/prefix
refusals/offline hook, unsupported OS incl. Intel Mac and Rosetta (nothing created), macOS arm64
fake install (log in ~/Library/Logs/cmr, ~/.zprofile hint, MacOSX-arm64 Miniforge), dry run (nothing written, conda only
`--version`, every action printed, launcher preview), `--no-slate`, default = pinned 7.1.1 even with a good system
ffmpeg (6.1.1, 7.1.1, 5.1.10 on PATH; the system ffmpeg is never run), `--use-system-ffmpeg`
acceptance (10 cases: 6.1.1, 5.1.10, n6.0, distro suffix, 5.0, 4.4, git build, old ffprobe,
missing filters, absent; accepted = warning, rejected = warning + 7.1.1) and the own-prefix
exclusion, a system-ffmpeg install re-run without the flag gets 7.1.1 added (no pip, next run
nothing; with the flag nothing changes) and its 1024 MB free-space check, conda discovery order + broken conda skipped, missing conda
message (exit 3), Miniforge bootstrap dry-run / checksum ok / mismatch (not run), free space
(slate / no-slate / ok), tampered bundle, incomplete bundle, network failure, conda failure (log
kept), foreign prefix / launcher, doctor failure, full fake install (conda/pip command lines,
launcher content, symlinks, CMR_TESSERACT and ffmpeg seen by cmr, PYTHONPATH unset, workspace
without CLAUDE.md, state, PATH hint, D29 log lines), env ffmpeg + no slate, prefix with a quote,
idempotent re-run (no conda/pip, edited files kept, missing file restored), `--upgrade`, new
version upgrade (unedited workspace files updated, edited kept, record survives uninstall),
ffmpeg turning bad on a `--use-system-ffmpeg` re-run (ffmpeg added to the same env, next run
nothing, good system ffmpeg wins again with the flag), env ffmpeg + tesseract in ONE env (the fake conda still refuses ffmpeg=6 + tesseract
like the real solver: guard against a regression of the pin), environment.yml pins
`ffmpeg=7.1.1`, migration from the ffmpeg-env layout (dry run, into env, system ffmpeg good,
conda failure keeps the old layout), macOS launcher TMPDIR (unset -> getconf, set -> kept, no
getconf -> unset), `--bin-dir` (+ workspace) remembered by re-run/uninstall,
USERGUIDE.md/README.md in the workspace, demo spots (on by default: files, record, not in the
template record, temp folder removed, generator run with cmr's ffmpeg and a clean PYTHONPATH;
`--no-demo` then a plain re-run adds pillow/numpy and generates; dry run; idempotent re-run and
`--upgrade` do not regenerate a deleted file or touch an edited one; an existing file is never
overwritten; a failing generator warns, exits 0 and a re-run retries; bundle without the
generator warns; uninstall keeps them; the fake python plays the generator, the fake conda writes
conda-meta records), incomplete env recreated, uninstall (cancel, EOF,
dry-run, yes, foreign prefix, foreign launcher kept), bundle builder (file list incl.
`demo/make_spot.py` byte-identical to tests/slate, no .mov, no CLAUDE.md,
SHA256SUMS complete and valid, tarball .sha256, filters = package list, wheel has profiles,
mismatched wheel rejected, repo not polluted) and the REAL built bundle through the installer
with fakes.

## First real run (2026-10-26, Rocky 9.7 workstation, conda 26.5.3 from ~/Tools/anaconda3)
Sandbox: `HOME=<big disk>/inst_home`, `CONDA_PKGS_DIRS=$HOME/conda-pkgs` (otherwise the cache
lands in the real `~/anaconda3/pkgs`), `nice -n 15`, `ulimit -v 40000000`; real HOME, Anaconda
(pkgs/ mtime unchanged), ~/.condarc, ~/.bashrc verified untouched; sandbox deleted afterwards.
1. Dry run matches the machine: conda via PATH (`~/anaconda3/condabin/conda`), system ffmpeg
   5.1.10 accepted with all 15 filters (also checked by hand with `ffmpeg -filters`), disk ok,
   nothing written. PASS.
2. `conda create` conda-forge only: 61 packages, 235 MB download, solve + install 38 s, whole
   install 45 s (cold cache), no ToS prompt (conda prints "Retrieving notices" and a
   `conda_pypi` "Did you know?" WARNING: noise from the Anaconda base plugins, harmless). PASS.
3. pip `--no-deps --no-index` + `pip check` ("No broken requirements found."); `cmr profiles
   list` from the installed package lists the 11 stock profiles. PASS.
4. Launcher uses /usr/bin/ffmpeg 5.1.10 (symlinks in ffmpeg-bin; doctor OK). Forced env path
   (PATH = a folder of symlinks to coreutils/curl only): FAILED first (issue 1), after the fix
   conda-forge ffmpeg 6.1.2 in ffmpeg-env, doctor OK, self-test and slate quick start PASS; the
   next run with the normal PATH switched back to the system ffmpeg. PASS.
5. tesseract 5.5.2 / leptonica 1.87.0 (after the pin), `--list-langs` 125 incl. eng, without
   TESSDATA_PREFIX. Slate quick start (templates/quickstart, now templates/starter, + make_spot `--short`) with the
   installed cmr: clean spot `RESULT PASS (exit 0): PASS 27, SKIPPED 1`; `--defect client_typo`:
   `WARN slate.content: slate CLIENT 'Sample Bugrers'`, exactly as in the guide. PASS.
6. Self-test ok (exit 2 = findings, report written); PATH hint printed (sandbox bin not on PATH).
   PASS.
7. Re-run 6.5 s (5 s of it the curl check), launcher byte-identical; `--upgrade` 9-21 s (conda
   install to the specs; also downgraded 5.5.3 -> 5.5.2); `--uninstall`: `n` = exit 3 nothing
   removed, `y` = prefix + launcher gone, workspace + logs kept. PASS.
8. `env -i HOME=.. PATH=/usr/bin:/bin` and `env -i HOME=..` (no PATH at all) with PYTHONPATH /
   PYTHONHOME set to junk: `cmr doctor` OK, quick start PASS. PASS.
9. `--no-slate` (own prefix, `--bin-dir`, `--workspace`): 12 s, env 197 MB, no tesseract,
   doctor OK with "tesseract NOT FOUND", launcher without CMR_TESSERACT, uninstall finds the
   launcher in the remembered bin dir. PASS.
10. Refusals: fake `df` (1 GB) -> "not enough free disk space", exit 1; unreachable proxy
   (`HTTPS_PROXY=http://127.0.0.1:9`) -> "cannot reach https://conda.anaconda.org", exit 1;
   nothing created in either case. PASS.

Issues found and fixed (each with a regression test):
1. conda-forge ffmpeg 6.1 + tesseract 5.5 unsolvable in one env -> ffmpeg in `<prefix>/ffmpeg-env`
   (superseded 2026-10-26: ffmpeg 7.1.1 in the one env, old layouts migrated).
2. Unpinned `tesseract=5.5` installed 5.5.3 (D32 wants 5.5.2) -> `tesseract=5.5.2`, `leptonica=1.87`.
3. Re-run without `--workspace` copied into ~/cmr-workspace and rewrote the state; uninstall
   message named the wrong workspace -> workspace and bin_dir remembered in install-state.
4. Changed templates in a new bundle were "kept as you edited them" forever -> sha256 record in
   the workspace; unedited files are updated.
5. templates/README.md's `../USERGUIDE.md` link was broken in the workspace -> guide + README copied.
6. `--no-slate` size hint said ~1.4 GB (pkgs/main figure) -> ~0.9 GB measured.
Added on request: `--bin-dir DIR`; the PATH hint names `~/.zprofile` on Darwin (message only;
Darwin still exits at the platform stub before reaching it).
Not an installer issue: `tests/slate/make_spot.py` on Rocky needs `CMR_TEST_FONT` and
`CMR_TEST_FONT_REGULAR` (no DejaVuSans at the Debian paths; used
/usr/share/fonts/dejavu-lgc-sans-fonts/DejaVuLGCSans{-Bold,}.ttf) and a Python with numpy +
Pillow (the system /usr/bin/python3 3.9 was not used).

## ffmpeg 7.1 single env (2026-10-26, both machines, bundle sha256 13c10dd4...)
Rocky 9.7 workstation (conda 26.5.3 of the existing Anaconda, same sandbox rules as the first run,
throwaway HOME deleted afterwards; real ~/.bashrc, ~/.condarc, ~/.conda/environments.txt
(md5) and Anaconda pkgs/ mtime unchanged):
1. Dry run: conda via PATH, system ffmpeg 5.1.10 accepted (15 filters), packages without ffmpeg. PASS.
2. Install with system ffmpeg + slate: 47 s (cold package cache), env 605 MB, doctor OK (5.1.10),
   tesseract 5.5.2, self-test ok; slate quick start (make_spot `--short` with the
   `CMR_TEST_FONT*` DejaVuLGC fonts): clean `PASS 27, SKIPPED 1` exit 0, client_typo `WARN
   slate.content ... 'Sample Bugrers'` exit 1. PASS.
3. Forced env ffmpeg (PATH = folder of symlinks to every /usr/bin and /bin tool except
   ffmpeg/ffprobe): ONE `conda create` with python, pip, pyyaml, jsonschema, ffmpeg=7.1.1,
   tesseract=5.5.2, leptonica=1.87 -> ffmpeg 7.1.1 gpl_hbbdf940_911, libxml2 2.15.4, python
   3.12.16, leptonica 1.87.0; 36 s, env 1.2 GB; doctor OK with "ffmpeg version 7.1.1", self-test
   ok; quick start clean PASS 27/SKIPPED 1, client_typo WARN, errors spot `PASS 17, SKIPPED 1,
   WARN 6, FAIL 13` (as in the guide). PASS.
4. Migration: the OLD installer (previous bundle) made env + ffmpeg-env (6.1.2, 391 MB) under
   the same forced PATH; the new installer: `conda install` of ffmpeg 7.1.1 into env (only NEW
   packages), links to env/bin, ffmpeg-env removed, "Migrated: ..." printed and logged, 14 s;
   re-run 7 s "Environment is up to date". PASS.
5. Re-run (system-ffmpeg install) 7 s, launcher byte-identical; `--upgrade` 10 s ("All requested
   packages already installed"). PASS.
6. Cron-like `env -i HOME=.. PATH=/usr/bin:/bin` / no PATH, PYTHONPATH/PYTHONHOME junk, for both
   the system-ffmpeg and the env-ffmpeg install: doctor OK, quick start PASS. PASS.
7. `--no-slate` with env ffmpeg (own prefix/bin/workspace): 17 s, env 765 MB, doctor OK with
   "tesseract NOT FOUND", no CMR_TESSERACT in the launcher. PASS.
8. `--uninstall`: `n` and EOF = exit 3 nothing removed, `y` = prefix + launcher gone, workspace
   and logs kept; all four test prefixes uninstalled. PASS.
Mac (macOS 26.7.1, existing Miniforge conda 26.9.4 via `CONDA_EXE`, no conda and no ffmpeg on
PATH; throwaway HOME + CONDA_PKGS_DIRS inside one sandbox folder, deleted afterwards; real
~/.conda/environments.txt (md5 + mtime), ~/.zshrc, ~/.bash_profile, the Miniforge pkgs/ mtime
unchanged, no ~/.zprofile / ~/Library/Logs/cmr created):
1. Dry run with conda on PATH ("found via PATH") and without ("found via CONDA_EXE"). PASS.
2. Install: 99 s (cold cache), one env with ffmpeg 7.1.1 gpl_h670d5b4_111 + tesseract 5.5.2 +
   leptonica 1.87.0 + libxml2 2.15.4 + python 3.12.16, env 844 MB, cache 776 MB, `--list-langs`
   125 + header; doctor OK (ffmpeg 7.1.1), self-test ok. PASS.
3. Slate quick start (make_spot `--short` with a conda-forge test env python + the env ffmpeg,
   macOS Arial): clean PASS 27/SKIPPED 1, client_typo WARN, errors `PASS 17, SKIPPED 1, WARN 6,
   FAIL 13`. PASS.
4. Cron-like `env -i HOME=.. PATH=/usr/bin:/bin`: FAILED first (issue 1 below), after the fix
   clean PASS, client_typo WARN, doctor OK. PASS.
5. Re-run 3 s (launcher byte-identical, conda "found via previous install"), `--upgrade` 6 s,
   `--uninstall` n / yes; reinstall from the warm cache 44 s. PASS.
6. tests/installer with `CMR_TEST_BASH=/bin/bash` (3.2.57) and BSD tools: 64 passed, 6 skipped
   (shellcheck + bundle-build tests: no setuptools Python there). PASS. (Linux: 69 passed, 1
   skipped; `bash -n` under the bash:3.2 docker image OK.)
Issues found and fixed (each with a regression test):
1. macOS, no TMPDIR (cron, launchd without it, `env -i`): every slate OCR check crashed with
   `UnicodeDecodeError ... byte 0x89`. Root cause: Python's temp dir falls back to `/tmp`;
   leptonica on macOS rewrites a path starting with `/tmp` to the per-user temp dir
   (`getconf DARWIN_USER_TEMP_DIR`) regardless of TMPDIR, so tesseract cannot open
   `/tmp/cmr_slate_*/slate_N.png`, then reads the PNG as an image LIST and prints its bytes
   (`\x89PNG`) to stderr, which `text=True` cannot decode. `/private/tmp/...` and
   `tesseract stdin` work. Fix: the macOS launcher sets `TMPDIR=$(getconf DARWIN_USER_TEMP_DIR)`
   when unset (`test_macos_launcher_sets_tmpdir_when_unset`). NOT fixed: a user who sets
   `TMPDIR=/tmp` explicitly still hits it (verified: ERROR 4); needs a fix in
   src/cmr/checks/slate/reader.py (outside the installer).
2. `--no-slate` free-space estimate 1.5 GB was too tight for the env-ffmpeg case (765 MB env +
   cache) -> 2 GB (`test_free_space_check`).
Not installer issues: make_spot.py refused an output folder under the parent of its own
`tests/` folder (copy it as `<dir>/tests/slate/make_spot.py`; since the demo-spot change the
guard applies only when that parent holds a `pyproject.toml`); the Mac test run needs
`src/cmr/__init__.py` next to `tests/` for `test_build_rejects_mismatched_wheel`.

## Still UNVERIFIED
- Miniforge bootstrap on Linux (verified on the Mac only; the Linux URL follows the same pinned
  pattern and its `.sha256` asset exists in the release listing).
- A machine without Anaconda/conda on PATH, a non-writable base `pkgs/`, a studio proxy/firewall.
- conda 26's ToS plugin with Anaconda `defaults` never comes up (only conda-forge is used);
  quiet in every run here, but not tested on a fresh Anaconda that has not accepted the ToS.
- Licences (item 9 below) not reviewed by the studio; conda-forge ffmpeg used is the gpl build.
- The LGPL ffmpeg build (`ffmpeg=7.1.1=lgpl*`) with cmr and the test-media generators.
- Whether launchd agents get TMPDIR (the launcher covers it either way); `TMPDIR=/tmp` set by
  the user on macOS (breaks OCR, src fix pending).

## Checklist for the next clean machine
1. `install.sh --dry-run` output matches the machine (conda found, ffmpeg decision, free space).
2. `conda create` from conda-forge only succeeds; no ToS prompt; time and env size noted.
3. `pip install --no-deps --no-index` + `pip check` clean; `cmr profiles list` lists the stock
   profiles (installed-package profile lookup, `config.stock_profiles_dir()`).
4. Default: conda-forge ffmpeg 7.1.1 in the env, the launcher uses it, doctor OK without the
   "not the tested version" WARNING. Once with `--use-system-ffmpeg`: system 5.1.10 accepted
   with the warning, doctor prints the WARNING lines; a re-run without the flag adds 7.1.1.
5. tesseract: `--version` 5.5.x, `--list-langs` has eng, the slate media tests pass with
   `CMR_TESSERACT=<prefix>/env/bin/tesseract`.
6. Self-test passes; `cmr doctor` OK; `~/.local/bin` on PATH on Rocky (bash profile) or the hint.
7. Re-run (idempotent, fast), `--upgrade`, `--uninstall` (asks, removes only the prefix and
   launcher), install log readable and free of private data beyond paths.
8. A cron job with the launcher's full path works (no conda activation).
9. Licences acceptable: conda-forge ffmpeg is a GPL build by default (x264/x265 linked; lgpl
   builds exist: `ffmpeg=7.1.1=lgpl*` if the studio needs LGPL; untested), tesseract Apache-2.0, Python PSF,
   Miniforge BSD-3; Anaconda `defaults` is not used (its terms apply to orgs > 200 people).

## Known limits / later
Offline cache (`--offline-dir`: conda `--offline` + a `file://` channel built by
build_bundle.sh), free-space check of conda's `pkgs/` file system, `installed=` in the state is the last
run, not the first install; the 512 MB update estimate does not cover adding ffmpeg to an
existing env (~0.7 GB); the migration code (`OLD_FFENV`) can go once no ffmpeg-env install exists.
