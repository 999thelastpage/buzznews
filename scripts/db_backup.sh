#!/usr/bin/env bash
# Nightly Postgres dump for BuzzNews.
# Run by systemd timer buzz-news-backup.timer (or by hand for testing).
# Reads .env via systemd EnvironmentFile; when run by hand, source it first.

set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${BACKUP_SSH_HOST:?BACKUP_SSH_HOST is required}"
: "${BACKUP_SSH_USER:?BACKUP_SSH_USER is required}"
: "${BACKUP_REMOTE_DIR:?BACKUP_REMOTE_DIR is required}"
: "${BACKUP_SSH_KEY:=/home/ubuntu/.ssh/buzz_backup_ed25519}"
: "${BACKUP_SSH_PORT:=22}"

# pg_dump takes a libpq URI; our DATABASE_URL is the SQLAlchemy +asyncpg form.
PG_URL="${DATABASE_URL/+asyncpg/}"

LOCAL_DIR=/var/backups/buzz-news
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$LOCAL_DIR/buzz-news-$STAMP.pgc"

mkdir -p "$LOCAL_DIR"

pg_dump \
    --format=custom \
    --no-owner \
    --no-privileges \
    --file="$OUT" \
    "$PG_URL"

pg_restore --list "$OUT" > /dev/null

rsync -az --partial \
    -e "ssh -i $BACKUP_SSH_KEY -p $BACKUP_SSH_PORT -o StrictHostKeyChecking=accept-new -o BatchMode=yes" \
    "$OUT" \
    "$BACKUP_SSH_USER@$BACKUP_SSH_HOST:$BACKUP_REMOTE_DIR/incoming/"

# Local retention: keep 3 most recent dumps on this VPS.
ls -1t "$LOCAL_DIR"/buzz-news-*.pgc 2>/dev/null | tail -n +4 | xargs -r rm -f

SIZE=$(stat -c%s "$OUT")
echo "[$(date -uIs)] backup ok: $OUT ($SIZE bytes)"
