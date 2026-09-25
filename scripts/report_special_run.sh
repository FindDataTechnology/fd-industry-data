#!/usr/bin/env sh
# Report a special-source (non-runtime) crawl run into the central crawl_runs
# table without changing how the job itself runs. Appended to any job script:
#
#   ./scripts/report_special_run.sh <source> <success|failed> <rows> [error_head] &
#
# Requires PGPASSWORD (or ~/.pgpass) for the central PG; best-effort by design
# — a failed report logs to stderr and exits 0 so it never breaks the job.
set -u
SRC="${1:?source}"; STATUS="${2:?success|failed}"; ROWS="${3:-0}"; ERR="${4:-}"
HOST="${FD_CRAWL_DB_HOST:-100.64.0.3}"; PORT="${FD_CRAWL_DB_PORT:-30432}"
NOW=$(date +%s); T0="${FD_RUN_STARTED:-$NOW}"
if command -v psql >/dev/null 2>&1; then
  ESC=$(printf '%s' "$ERR" | sed "s/'/''/g")
  psql -h "$HOST" -p "$PORT" -U fd -d fd_open_data -qtA -c \
    "INSERT INTO crawl_runs (source, kind, status, started_at, finished_at, rows_written, error_head)
     VALUES ('$SRC','special-job','$STATUS', to_timestamp($T0), to_timestamp($NOW), $ROWS, '$ESC')" \
    && exit 0
  echo "report_special_run: psql report failed for $SRC (continuing)" >&2
fi
echo "report_special_run: psql unavailable; run $SRC $STATUS not recorded" >&2
exit 0
