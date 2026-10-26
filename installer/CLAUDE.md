# installer - release bundle and per-user installer

Overview and numbered decisions: ../CLAUDE.md (D1, D2 production Python policy, D22, D24, D29,
D31, D32). Status: **first real run done 2026-10-26 on the Rocky 9.7 Flame workstation** (sandboxed
HOME, existing Anaconda with conda 26.5.3): every checklist item passed after the fixes listed in
"First real run" below. **Single env with ffmpeg 7.1.1 (2026-10-26):** re-verified on the
Rocky machine and the Mac ("ffmpeg 7.1 single env" below). Still pre-release (D22).

## Files
| File | Role |
|---|---|
| `build_bundle.sh` | builds `dist/cmr-<version>-bundle.tar.gz` (+ `.sha256`) from the source tree |
| `install.sh` | the installer shipped in the bundle (bash, per user, no sudo) |
| `environment.yml` | conda package list (tags `# cmr:ffmpeg`, `# cmr:slate`); install.sh reads it |
| `.gitignore` | `dist/` |
| `../tests/installer/` | pytest: fakes for conda/ffmpeg/curl/df/uname/id/sysctl/sw_vers/getconf, temp HOME, no network |

## Bundle (`build_bundle.sh [-o DIR] [--wheel FILE] [--python PY]`)
`cmr-<v>/`: `install.sh`, `environment.yml`, `required_filters.txt`, `VERSION`, `README.txt`,
`README.md`, `USERGUIDE.md`, `wheels/check_my_render-<v>-py3-none-any.whl`, `examples/` + `templates/` (no CLAUDE.md, no
caches), `SHA256SUMS` (every file but itself). Version = `cmr.__version__`.
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
`~/cmr-workspace`), `--bin-dir DIR` (launcher folder, default `~/.local/bin`; a re-run,
`--upgrade` or `--uninstall` of the same prefix reuses the previous `--workspace`/`--bin-dir`
from install-state unless given again), `--no-slate`, `--upgrade`, `--uninstall`, `--bootstrap-conda`,
`--offline-dir DIR` (hook only: exits 2 "planned"), `-y/--yes`, `-n/--dry-run`, `-h`.
Exit: 0 done, 1 failed (log kept), 2 usage / unsupported platform / root, 3 cancelled.

Layout: `<prefix>/env` (ONE conda env: python, cmr, tesseract and, only when the system ffmpeg
fails the test, conda-forge ffmpeg 7.1.1), `<prefix>/ffmpeg-bin/{ffmpeg,ffprobe}` (symlinks to the
chosen binaries: the system ones or `<prefix>/env/bin/*`),
`<prefix>/install-state` (key=value: version, ffmpeg_mode, ffmpeg, slate, workspace, bin_dir,
conda, installed = time of the last run), `<prefix>/.cmr-install-prefix` (marker), `<prefix>/conda`
(only after a Miniforge bootstrap), launcher `~/.local/bin/cmr`, log
`${XDG_CACHE_HOME:-~/.cache}/cmr/logs/install.log` (D29 line style, component `install`,
6-hex run id; rotated to `.1` above 5 MB; kept on failure; paths, versions, conda/pip/doctor
output and the self-test console report: nothing private beyond paths, checked on the real run).

Steps: 1 platform (Linux x86_64 and macOS arm64; Intel Macs and Rosetta refused, exit 2),
root refused, bundle complete + `sha256sum -c SHA256SUMS` (or `shasum -a 256 -c`),
prefix/launcher not foreign, install mode, free space (`df -Pk` of the nearest existing
ancestor: 3072 MB with slate, 2048 MB without, 512 MB for an update; ESTIMATES) -> 2 conda ->
3 ffmpeg -> 4 network (`curl -I` conda.anaconda.org; skipped without curl) -> 5 env -> 6 wheel
-> 7 launcher + links -> 8 workspace -> state -> 9 verify -> summary + PATH hint + policy text.

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
- **ffmpeg acceptance:** first `ffmpeg`/`ffprobe` on PATH OUTSIDE the prefix and `~/.local/bin`;
  both `-version` >= 5.1 (first line `<name> version [n]X.Y...`; git builds `N-...` =
  unreadable = rejected) and `ffmpeg -hide_banner -filters` (same parsing as
  `engine.available_filters`) lists every filter of `required_filters.txt`. Pass: system
  binaries are linked and used ("not touched"). Fail: `ffmpeg=7.1.1` from conda-forge goes into
  `<prefix>/env` with everything else (create-time on a new install, `conda install` into the
  env on a later run) and `ffmpeg-bin` points at `<prefix>/env/bin`. Re-evaluated on every run (a
  system ffmpeg that turns bad gets ffmpeg added to the env; a good one again wins, the env
  ffmpeg stays unused). ffmpeg 7.1 LTS is the pinned line (user decision 2026-10-26; 9.1 LTS
  later). History: the first installer pinned 6.1 and needed a SEPARATE `<prefix>/ffmpeg-env`,
  because conda-forge ffmpeg 6.1.x needs libxml2 < 2.14 while tesseract 5.5.x (libarchive) needs
  libxml2-16 >= 2.14 (LibMambaUnsatisfiableError, first real run); 7.1.1 solves with libxml2
  2.15.4 next to tesseract 5.5.2.
- **Migration from the ffmpeg-env layout** (only our own sandbox test installs had it): when
  `<prefix>/ffmpeg-env` exists, a run of this installer adds ffmpeg to `<prefix>/env` if the
  system ffmpeg fails (counted as "newly needed", so it also happens on a same-version re-run),
  switches the links, then removes `ffmpeg-env` (after the links are rewritten, so a failed conda
  call leaves the old layout working) and logs/prints "Migrated: removed the old separate ffmpeg
  environment". With a good system ffmpeg the unused ffmpeg-env is just removed. Real run: the
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

## Tests (`tests/installer/test_installer.py`, 70)
Run alone: `PYTHONPATH=src /usr/bin/python3 -m pytest -q tests/installer` (~10 s). Every run uses a
temp HOME and a PATH of symlinked coreutils + fakes; the real conda/ffmpeg are unreachable.
Covered: syntax (shellcheck when installed: not on the dev machine), help/bad args/root/prefix
refusals/offline hook, unsupported OS incl. Intel Mac and Rosetta (nothing created), macOS arm64
fake install (log in ~/Library/Logs/cmr, ~/.zprofile hint, MacOSX-arm64 Miniforge), dry run (nothing written, conda only
`--version`, every action printed, launcher preview), `--no-slate`, ffmpeg acceptance (10 cases:
6.1.1, 5.1.10, n6.0, distro suffix, 5.0, 4.4, git build, old ffprobe, missing filters, absent)
and the own-prefix exclusion, conda discovery order + broken conda skipped, missing conda
message (exit 3), Miniforge bootstrap dry-run / checksum ok / mismatch (not run), free space
(slate / no-slate / ok), tampered bundle, incomplete bundle, network failure, conda failure (log
kept), foreign prefix / launcher, doctor failure, full fake install (conda/pip command lines,
launcher content, symlinks, CMR_TESSERACT and ffmpeg seen by cmr, PYTHONPATH unset, workspace
without CLAUDE.md, state, PATH hint, D29 log lines), env ffmpeg + no slate, prefix with a quote,
idempotent re-run (no conda/pip, edited files kept, missing file restored), `--upgrade`, new
version upgrade (unedited workspace files updated, edited kept, record survives uninstall),
ffmpeg turning bad on re-run (ffmpeg added to the same env, next run nothing, good system ffmpeg
wins again), env ffmpeg + tesseract in ONE env (the fake conda still refuses ffmpeg=6 + tesseract
like the real solver: guard against a regression of the pin), environment.yml pins
`ffmpeg=7.1.1`, migration from the ffmpeg-env layout (dry run, into env, system ffmpeg good,
conda failure keeps the old layout), macOS launcher TMPDIR (unset -> getconf, set -> kept, no
getconf -> unset), `--bin-dir` (+ workspace) remembered by re-run/uninstall,
USERGUIDE.md/README.md in the workspace, incomplete env recreated, uninstall (cancel, EOF,
dry-run, yes, foreign prefix, foreign launcher kept), bundle builder (file list, no CLAUDE.md,
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
   TESSDATA_PREFIX. Slate quick start (templates/quickstart + make_spot `--short`) with the
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
Not installer issues: make_spot.py refuses an output folder under the parent of its own
`tests/` folder (copy it as `<dir>/tests/slate/make_spot.py`); the Mac test run needs
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
4. System ffmpeg 5.1.10 accepted (expected) and the launcher uses it; also force the env path
   once (e.g. a PATH without /usr/bin ffmpeg) and confirm conda-forge ffmpeg 7.1.1 passes doctor.
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
build_bundle.sh), a `--force-env-ffmpeg` switch if
wanted, free-space check of conda's `pkgs/` file system, `installed=` in the state is the last
run, not the first install; the 512 MB update estimate does not cover adding ffmpeg to an
existing env (~0.7 GB); the migration code (`OLD_FFENV`) can go once no ffmpeg-env install exists.
