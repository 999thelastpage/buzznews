#!/usr/bin/env bash
# BuzzNews backup script — runs as buzz user via sudo
# Usage: sudo -u buzz bash /opt/buzz-news/deploy/backup.sh
set -euo pipefail

STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/var/backups/buzz-news"
OPENCLAW_GATEWAY="${OPENCLAW_GATEWAY_URL:-http://127.0.0.1:19262}"
OPENCLAW_BASE="${OPENCLAW_GATEWAY}/oi8dhw"

mkdir -p "${BACKUP_DIR}"

log() { echo "[$(date -Iseconds)] $*"; }

# --- Database backup ---
DB_BACKUP="${BACKUP_DIR}/db-${STAMP}.pgcustom"
log "Dumping database to ${DB_BACKUP}"
pg_dump --format=custom "${DATABASE_URL}" -f "${DB_BACKUP}"

# --- Images backup ---
IMG_BACKUP="${BACKUP_DIR}/images-${STAMP}.tgz"
log "Archiving images to ${IMG_BACKUP}"
tar -czf "${IMG_BACKUP}" -C /var/lib/buzz-news/static images/ 2>/dev/null || true

# --- Upload to COS via OpenClaw skill ---
log "Uploading database backup to COS..."
DB_COS_KEY="db/${STAMP}.pgcustom"
RESPONSE=$(curl -s -X POST \
  -F "file=@${DB_BACKUP}" \
  -F "key=${DB_COS_KEY}" \
  "${OPENCLAW_BASE}/skills/tencent-cos-skill/upload")
log "COS DB response: ${RESPONSE}"

log "Uploading images backup to COS..."
IMG_COS_KEY="images/${STAMP}.tgz"
RESPONSE=$(curl -s -X POST \
  -F "file=@${IMG_BACKUP}" \
  -F "key=${IMG_COS_KEY}" \
  "${OPENCLAW_BASE}/skills/tencent-cos-skill/upload")
log "COS IMG response: ${RESPONSE}"

# --- Prune local backups older than 7 days ---
log "Pruning backups older than 7 days..."
find "${BACKUP_DIR}" -type f \( -name "*.pgcustom" -o -name "*.tgz" \) -mtime +7 -delete
log "Backup complete."
