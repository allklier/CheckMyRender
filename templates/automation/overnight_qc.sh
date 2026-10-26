#!/usr/bin/env bash
# =====================================================================================
# overnight_qc.sh - check every render in a folder, one file at a time, in background mode,
# and keep one JSON report per file. Start it at the end of the day (or from cron) and read
# the summary in the morning.
#
#   overnight_qc.sh RENDER_DIR REPORT_DIR [PROFILE SPEC [WAIVERS]]
#
#   RENDER_DIR  folder with the renders (*.mov, *.mxf, *.mp4; not recursive)
#   REPORT_DIR  where the reports go: REPORT_DIR/<file name>/ gets the JSON reports
#               (cmr_report_<date-time>_<run id>.json + a copy cmr_report_latest.json) and
#               console.txt; REPORT_DIR/summary.tsv has one line per file (exit, verdict, file)
#   PROFILE     profile name (release, review ...) or path to a profile .yaml
#   SPEC        spec sheet .yaml
#   WAIVERS     optional waiver file
#   Without PROFILE and SPEC the job file names them (and the waiver file): set
#   CMR_JOB=/path/to/job.yaml (cron starts in your home folder, so give the full path), or
#   run the script from the job's folder (cmr then finds ./job.yaml by itself).
#   The job file's `output` is not used here: every file gets its own folder in REPORT_DIR.
#
# Examples:
#   overnight_qc.sh /mnt/renders/show_104 ~/qc_reports/show_104_$(date +%F) \
#       release ~/qc/specs/spec_example_show_104.yaml ~/qc/waivers/show_104.yaml
#   CMR_JOB=~/qc/jobs/exco_rush/job.yaml overnight_qc.sh /mnt/renders/exco_rush \
#       ~/qc_reports/exco_rush_$(date +%F)
#
# Settings (environment variables, optional):
#   CMR=cmr                 how to call cmr (e.g. "conda run -p ~/envs/cmr cmr")
#   CMR_JOB=PATH            job file (job.yaml) passed as --job
#   CMR_TIMEOUT=6h          give up on one file after this long: a number with s, m, h or d
#                           (GNU timeout syntax; on macOS, which has no `timeout`, a perl
#                           wrapper does the same: exit 124, the whole cmr+ffmpeg group stopped)
#   CMR_EXTRA="..."         extra cmr check options, e.g. "--max-findings 0"
#   REDO=1                  re-check files that already have a report in REPORT_DIR
#
# Why one cmr call per file: one folder per file keeps the reports of each file apart
# (cmr_report_latest.json is per folder), and a crash or timeout on one file does not stop
# the others.
#
# Exit codes of cmr (also in summary.tsv): 0 pass, 1 warn, 2 fail, 3 error,
# 124 = stopped by the timeout.
#
# Runs on Linux and macOS (written for bash 3.2, the macOS /bin/bash: no mapfile, no
# associative arrays, empty arrays expanded with ${a[@]+...}). On a Mac start it from launchd,
# not cron: see overnight_qc.launchd.plist next to this script (caffeinate -i keeps the Mac
# awake while it runs).
# =====================================================================================
set -u

if [ $# -ne 2 ] && [ $# -lt 4 ]; then
    sed -n '3,/^# =====/p' "$0"
    exit 64
fi
RENDER_DIR=$1
REPORT_DIR=$2
PROFILE=${3:-}
SPEC=${4:-}
WAIVERS=${5:-}
CMR=${CMR:-cmr}
CMR_TIMEOUT=${CMR_TIMEOUT:-6h}
CMR_EXTRA=${CMR_EXTRA:-}

mkdir -p "$REPORT_DIR" || exit 3
SUMMARY="$REPORT_DIR/summary.tsv"
[ -f "$SUMMARY" ] || printf 'exit\tverdict\tfile\tstarted\n' > "$SUMMARY"

# run_limited SECONDS CMD...: GNU timeout when present (Linux; Homebrew coreutils gtimeout),
# else perl (always on macOS): the command runs in its own process group, which gets TERM (KILL
# 10 s later) when the time is up; exit 124 like timeout.
to_seconds() {
    case $1 in
        *d) echo $(( ${1%d} * 86400 )) ;;
        *h) echo $(( ${1%h} * 3600 )) ;;
        *m) echo $(( ${1%m} * 60 )) ;;
        *s) echo "${1%s}" ;;
        *)  echo "$1" ;;
    esac
}
run_limited() {
    secs=$1; shift
    if command -v timeout >/dev/null 2>&1; then
        timeout "$secs" "$@"
    elif command -v gtimeout >/dev/null 2>&1; then
        gtimeout "$secs" "$@"
    else
        perl -e '
            my $t = shift; my $pid = fork; defined $pid or exit 125;
            if ($pid == 0) { setpgrp(0, 0); exec { $ARGV[0] } @ARGV; exit 127 }
            $SIG{ALRM} = sub { kill "TERM", -$pid;
                               for (1 .. 10) { last if waitpid($pid, 1) > 0; sleep 1 }  # 1 = WNOHANG
                               kill "KILL", -$pid; waitpid($pid, 0); exit 124 };
            alarm $t; waitpid($pid, 0); alarm 0;
            exit($? & 127 ? 128 + ($? & 127) : $? >> 8)' "$secs" "$@"
    fi
}
CMR_TIMEOUT_S=$(to_seconds "$CMR_TIMEOUT")
case $CMR_TIMEOUT_S in ''|*[!0-9]*) echo "CMR_TIMEOUT: use a number with s, m, h or d" >&2; exit 64 ;; esac

opts=()   # what the command line gives; the rest comes from the job file
[ -n "$PROFILE" ] && opts+=(--profile "$PROFILE" --spec "$SPEC")
[ -n "$WAIVERS" ] && opts+=(--waivers "$WAIVERS")
[ -n "${CMR_JOB:-}" ] && opts+=(--job "$CMR_JOB")

# sorted list of renders, safe for names with spaces (bash 3.2: no mapfile)
files=()
while IFS= read -r -d '' f; do
    files[${#files[@]}]=$f
done < <(find "$RENDER_DIR" -maxdepth 1 -type f \
    \( -iname '*.mov' -o -iname '*.mxf' -o -iname '*.mp4' \) -print0 | sort -z)

echo "$(date '+%F %T') ${#files[@]} file(s) in $RENDER_DIR -> $REPORT_DIR"
for f in ${files[@]+"${files[@]}"}; do
    name=$(basename "$f")
    out="$REPORT_DIR/$name"
    if [ -f "$out/cmr_report_latest.json" ] && [ "${REDO:-0}" != 1 ]; then
        echo "skip (already checked): $name"
        continue
    fi
    mkdir -p "$out"
    started=$(date '+%F %T')
    echo "$started checking $name"
    # shellcheck disable=SC2086  # CMR and CMR_EXTRA may hold several words on purpose
    run_limited "$CMR_TIMEOUT_S" $CMR check "$f" ${opts[@]+"${opts[@]}"} \
        --mode background --report json -o "$out" $CMR_EXTRA \
        > "$out/console.txt" 2>&1
    rc=$?
    verdict=$(sed -n 's/^RESULT \([A-Z]*\).*/\1/p' "$out/console.txt" | tail -1)
    printf '%s\t%s\t%s\t%s\n' "$rc" "${verdict:-NONE}" "$name" "$started" >> "$SUMMARY"
done

echo "$(date '+%F %T') done. Summary: $SUMMARY"
column -t -s $'\t' "$SUMMARY" 2>/dev/null || cat "$SUMMARY"
