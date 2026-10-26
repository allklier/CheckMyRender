# installer - release bundle and per-user installer

Overview and numbered decisions: ../CLAUDE.md (D1, D2 production Python policy, D22, D24, D29,
D31). Status: **built and tested with fakes only; never run for real yet** (see "First real run").

## Files
| File | Role |
|---|---|
| `build_bundle.sh` | builds `dist/cmr-<version>-bundle.tar.gz` (+ `.sha256`) from the source tree |
| `install.sh` | the installer shipped in the bundle (bash, per user, no sudo) |
| `environment.yml` | conda package list (tags `# cmr:ffmpeg`, `# cmr:slate`); install.sh reads it |
| `.gitignore` | `dist/` |
| `../tests/installer/` | pytest: fakes for conda/ffmpeg/curl/df/uname/id, temp HOME, no network |

## Bundle (`build_bundle.sh [-o DIR] [--wheel FILE] [--python PY]`)
`cmr-<v>/`: `install.sh`, `environment.yml`, `required_filters.txt`, `VERSION`, `README.txt`,
`wheels/check_my_render-<v>-py3-none-any.whl`, `examples/` + `templates/` (no CLAUDE.md, no
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
`~/cmr-workspace`), `--no-slate`, `--upgrade`, `--uninstall`, `--bootstrap-conda`,
`--offline-dir DIR` (hook only: exits 2 "planned"), `-y/--yes`, `-n/--dry-run`, `-h`.
Exit: 0 done, 1 failed (log kept), 2 usage / unsupported platform / root, 3 cancelled.

Layout: `<prefix>/env` (conda env), `<prefix>/ffmpeg-bin/{ffmpeg,ffprobe}` (symlinks to the
chosen binaries), `<prefix>/install-state` (key=value: version, ffmpeg_mode, ffmpeg, slate,
workspace, conda, installed), `<prefix>/.cmr-install-prefix` (marker), `<prefix>/conda`
(only after a Miniforge bootstrap), launcher `~/.local/bin/cmr`, log
`${XDG_CACHE_HOME:-~/.cache}/cmr/logs/install.log` (D29 line style, component `install`,
6-hex run id; rotated to `.1` above 5 MB; kept on failure; paths and versions only).

Steps: 1 platform (Linux x86_64 only; Darwin arm64 = polite "planned" stub; checked with sh-3
syntax before any bash-4 feature), root refused, bundle complete + `sha256sum -c SHA256SUMS`,
prefix/launcher not foreign, install mode, free space (`df -Pk` of the nearest existing
ancestor: 3072 MB with slate, 1536 MB without, 512 MB for an update; ESTIMATES) -> 2 conda ->
3 ffmpeg -> 4 network (`curl -I` conda.anaconda.org; skipped without curl) -> 5 env -> 6 wheel
-> 7 launcher + links -> 8 workspace -> state -> 9 verify -> summary + PATH hint + policy text.

Decisions (installer-local; the main thread may lift them into the root CLAUDE.md):
- **Channels:** every conda call is `--override-channels -c conda-forge` (never Anaconda
  `defaults`: no Anaconda commercial-licence/ToS question, conda 26 ToS plugin not involved).
  Verified 2026-10-26 on the Rocky machine (`conda search`): conda-forge has python 3.12.16, ffmpeg
  6.0-8.1 (gpl and lgpl builds), tesseract 5.5.0-5.5.3 (5.5.2 = 168 MB package, Apache-2.0),
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
  `conda create` adds `--no-default-packages` (ignores `.condarc` `create_default_packages`).
- **ffmpeg acceptance:** first `ffmpeg`/`ffprobe` on PATH OUTSIDE the prefix and `~/.local/bin`;
  both `-version` >= 5.1 (first line `<name> version [n]X.Y...`; git builds `N-...` =
  unreadable = rejected) and `ffmpeg -hide_banner -filters` (same parsing as
  `engine.available_filters`) lists every filter of `required_filters.txt`. Pass: system
  binaries are linked and used ("not touched"). Fail: `ffmpeg=6.1` from conda-forge goes into
  the env and is linked. Re-evaluated on every run (a system ffmpeg that turns bad gets the env
  ffmpeg added; a good one again wins, the env copy stays unused).
- **Launcher** (`/bin/sh`, marker line `# CheckMyRender launcher (generated by install.sh)`):
  `PATH=<prefix>/ffmpeg-bin:$PATH` (only ffmpeg/ffprobe are prepended, so cron's short PATH
  works), `CMR_TESSERACT=<prefix>/env/bin/tesseract` (with slate; a profile param `tesseract`
  still wins, D27), `unset PYTHONPATH PYTHONHOME`, `PYTHONNOUSERSITE=1` (Flame/user Python
  settings must not leak into the env Python), `exec <prefix>/env/bin/cmr "$@"`. A foreign
  `~/.local/bin/cmr` (e.g. an old manual install) is never overwritten: exit 1.
- **Idempotence:** state version != bundle version -> upgrade in place (conda install to the
  specs, wheel reinstalled); same version -> only re-link, re-write launcher, copy missing
  workspace files and re-verify (adds packages only if newly needed); `--upgrade` -> conda
  install to the specs + wheel reinstall. `conda install` with the same specs does not move
  already-satisfying packages to newer builds (UNVERIFIED whether that is wanted).
  `--no-slate` on a re-run does not remove an installed tesseract (launcher drops CMR_TESSERACT).
- **Workspace:** `examples/` and `templates/` from the bundle, CLAUDE.md filtered again (D31);
  existing files are never overwritten (counts: new / unchanged / kept as edited).
- **Uninstall:** asks (EOF = no; `--yes` skips), removes the launcher only if it carries the
  marker, then `rm -rf <prefix>` only if the marker exists. Workspace and logs are kept.
  (It does not call `conda remove`: the env lives inside the prefix; a stale line may stay in
  `~/.conda/environments.txt`, harmless.)
- **Self-test:** `cmr --version`, `cmr doctor` (exit 0 required, output shown), with slate
  `tesseract --version` (`tesseract 5.*`) + `tesseract --list-langs` contains `eng` + doctor did
  not say NOT FOUND; then `ffmpeg -f lavfi testsrc2 320x180 24000/1001 + sine, -t 2, prores_ks
  -profile:v 2 yuv422p10le, pcm_s24le stereo` and `cmr check CLIP --profile review --spec
  <bundle>/templates/specs/spec_review_render.yaml --report json -o DIR`: exit 0-2 and
  `cmr_report_latest.json` written = pass (findings are expected; exit 3 = ERROR). Self-test
  files are removed on success, kept on failure.
- **Size:** tesseract from conda carries all language data (no split `tessdata` packages on
  conda-forge, verified by `conda search`), so "English only" is not offered; deleting files
  from a conda package would break conda's own consistency. Documented as ~1.4 GB (that figure
  is from the pkgs/main install; conda-forge size UNVERIFIED).

## Tests (`tests/installer/test_installer.py`, 58)
Run alone: `PYTHONPATH=src /usr/bin/python3 -m pytest -q tests/installer` (~10 s). Every run uses a
temp HOME and a PATH of symlinked coreutils + fakes; the real conda/ffmpeg are unreachable.
Covered: syntax (shellcheck when installed: not on the dev machine), help/bad args/root/prefix
refusals/offline hook, unsupported OS (nothing created), dry run (nothing written, conda only
`--version`, every action printed, launcher preview), `--no-slate`, ffmpeg acceptance (10 cases:
6.1.1, 5.1.10, n6.0, distro suffix, 5.0, 4.4, git build, old ffprobe, missing filters, absent)
and the own-prefix exclusion, conda discovery order + broken conda skipped, missing conda
message (exit 3), Miniforge bootstrap dry-run / checksum ok / mismatch (not run), free space
(slate / no-slate / ok), tampered bundle, incomplete bundle, network failure, conda failure (log
kept), foreign prefix / launcher, doctor failure, full fake install (conda/pip command lines,
launcher content, symlinks, CMR_TESSERACT and ffmpeg seen by cmr, PYTHONPATH unset, workspace
without CLAUDE.md, state, PATH hint, D29 log lines), env ffmpeg + no slate, prefix with a quote,
idempotent re-run (no conda/pip, edited files kept, missing file restored), `--upgrade`, new
version upgrade, ffmpeg turning bad on re-run, incomplete env recreated, uninstall (cancel, EOF,
dry-run, yes, foreign prefix, foreign launcher kept), bundle builder (file list, no CLAUDE.md,
SHA256SUMS complete and valid, tarball .sha256, filters = package list, wheel has profiles,
mismatched wheel rejected, repo not polluted) and the REAL built bundle through the installer
with fakes.

## UNVERIFIED until a real run
- Miniforge installer flags `-b -p` and the `.sha256` asset format/URL (`releases/latest/download`)
  (constructor convention; not downloaded per the no-download rule).
- conda-forge `ffmpeg=6.1` on Rocky 9 has every required filter and runs (glibc/GPU libs);
  conda-forge tesseract 5.5.x finds its `eng` data without TESSDATA_PREFIX; `--list-langs`.
- `curl -sSfI` on `conda.anaconda.org/conda-forge/noarch/repodata.json` answers < 400 (HEAD).
- conda 26.5.3's ToS plugin stays quiet with `--override-channels -c conda-forge`.
- Disk estimates (3 GB / 1.5 GB) and install time; conda's package cache lives under the base
  conda (`pkgs_dirs`), possibly on another file system than the prefix (not checked).
- Proxies/firewalls: curl and conda both honour `HTTPS_PROXY`; `.condarc proxy_servers` is
  conda-only. A studio firewall must allow conda.anaconda.org (and github.com for a bootstrap).

## First real run (clean Rocky 9) must confirm
1. `install.sh --dry-run` output matches the machine (conda found, ffmpeg decision, free space).
2. `conda create` from conda-forge only succeeds; no ToS prompt; time and env size noted.
3. `pip install --no-deps --no-index` + `pip check` clean; `cmr profiles list` lists the stock
   profiles (installed-package profile lookup, `config.stock_profiles_dir()`).
4. System ffmpeg 5.1.10 accepted (expected) and the launcher uses it; also force the env path
   once (e.g. a PATH without /usr/bin ffmpeg) and confirm conda-forge ffmpeg 6.1 passes doctor.
5. tesseract: `--version` 5.5.x, `--list-langs` has eng, the slate media tests pass with
   `CMR_TESSERACT=<prefix>/env/bin/tesseract`.
6. Self-test passes; `cmr doctor` OK; `~/.local/bin` on PATH on Rocky (bash profile) or the hint.
7. Re-run (idempotent, fast), `--upgrade`, `--uninstall` (asks, removes only the prefix and
   launcher), install log readable and free of private data beyond paths.
8. A cron job with the launcher's full path works (no conda activation).
9. Licences acceptable: conda-forge ffmpeg is a GPL build by default (x264/x265 linked; lgpl
   builds exist: `ffmpeg=6.1=lgpl*` if the studio needs LGPL), tesseract Apache-2.0, Python PSF,
   Miniforge BSD-3; Anaconda `defaults` is not used (its terms apply to orgs > 200 people).

## Known limits / later
Offline cache (`--offline-dir`: conda `--offline` + a `file://` channel built by
build_bundle.sh), macOS arm64, a `--force-env-ffmpeg` switch if wanted, USERGUIDE.md is not in
the bundle (templates/README.md links to `../USERGUIDE.md`).
