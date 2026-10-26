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
#   CMR_TIMEOUT=6h          give up on one file after this long (GNU timeout syntax)
#   CMR_EXTRA="..."         extra cmr check options, e.g. "--max-findings 0"
#   REDO=1                  re-check files that already have a report in REPORT_DIR
#
# Why one cmr call per file: one folder per file keeps the reports of each file apart
# (cmr_report_latest.json is per folder), and a crash or timeout on one file does not stop
# the others.
#
# Exit codes of cmr (also in summary.tsv): 0 pass, 1 warn, 2 fail, 3 error,
# 124 = stopped by the timeout.
# =====================================================================================
set -u

if [ $# -ne 2 ] && [ $# -lt 4 ]; then
    sed -n '2,40p' "$0"
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

opts=()   # what the command line gives; the rest comes from the job file
[ -n "$PROFILE" ] && opts+=(--profile "$PROFILE" --spec "$SPEC")
[ -n "$WAIVERS" ] && opts+=(--waivers "$WAIVERS")
[ -n "${CMR_JOB:-}" ] && opts+=(--job "$CMR_JOB")

# sorted list of renders, safe for names with spaces
mapfile -d '' files < <(find "$RENDER_DIR" -maxdepth 1 -type f \
    \( -iname '*.mov' -o -iname '*.mxf' -o -iname '*.mp4' \) -print0 | sort -z)

echo "$(date '+%F %T') ${#files[@]} file(s) in $RENDER_DIR -> $REPORT_DIR"
for f in "${files[@]}"; do
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
    timeout "$CMR_TIMEOUT" $CMR check "$f" "${opts[@]}" \
        --mode background --report json -o "$out" $CMR_EXTRA \
        > "$out/console.txt" 2>&1
    rc=$?
    verdict=$(sed -n 's/^RESULT \([A-Z]*\).*/\1/p' "$out/console.txt" | tail -1)
    printf '%s\t%s\t%s\t%s\n' "$rc" "${verdict:-NONE}" "$name" "$started" >> "$SUMMARY"
done

echo "$(date '+%F %T') done. Summary: $SUMMARY"
column -t -s $'\t' "$SUMMARY" 2>/dev/null || cat "$SUMMARY"
