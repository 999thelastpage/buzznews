#!/usr/bin/env bash
# Lives on the remote backup VPS, NOT on the BuzzNews VPS.
# Run nightly via cron (e.g. `30 22 * * *  /home/buzz-backups/remote-prune.sh`)
# AFTER the BuzzNews rsync has landed in incoming/.
#
# Grandfather-father-son rotation:
#   daily/    7 days  (one per night)
#   weekly/   4 weeks (one per ISO week, hardlinked from that week's last daily)
#   monthly/  12 months (one per calendar month, hardlinked)

set -euo pipefail

ROOT="${BACKUP_ROOT:-/home/buzz-backups}"
INCOMING="$ROOT/incoming"
DAILY="$ROOT/daily"
WEEKLY="$ROOT/weekly"
MONTHLY="$ROOT/monthly"

mkdir -p "$DAILY" "$WEEKLY" "$MONTHLY"

# Move every dump that arrived since the last run into daily/.
shopt -s nullglob
for f in "$INCOMING"/buzz-news-*.pgc; do
    mv -f "$f" "$DAILY/"
done
shopt -u nullglob

# Identify the newest daily; nothing else to do if there isn't one.
LATEST=$(ls -1t "$DAILY"/buzz-news-*.pgc 2>/dev/null | head -1 || true)
if [ -z "$LATEST" ]; then
    echo "[$(date -uIs)] remote-prune: no daily dumps; nothing to promote"
    exit 0
fi

# Hardlink (not copy) so disk usage stays flat.
WEEK_TAG=$(date -u +%G-W%V)
MONTH_TAG=$(date -u +%Y-%m)
ln -f "$LATEST" "$WEEKLY/buzz-news-week-$WEEK_TAG.pgc"
ln -f "$LATEST" "$MONTHLY/buzz-news-month-$MONTH_TAG.pgc"

find "$DAILY"   -maxdepth 1 -name 'buzz-news-*.pgc'       -mtime +7   -delete
find "$WEEKLY"  -maxdepth 1 -name 'buzz-news-week-*.pgc'  -mtime +28  -delete
find "$MONTHLY" -maxdepth 1 -name 'buzz-news-month-*.pgc' -mtime +365 -delete

echo "[$(date -uIs)] remote-prune ok: latest=$LATEST week=$WEEK_TAG month=$MONTH_TAG"
