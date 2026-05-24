---
name: buzznews-backup-now
description: "Trigger an immediate BuzzNews database backup and upload to Tencent COS. Use when the user asks to backup the database now, run a manual backup, or upload a backup to cloud storage."
---

# BuzzNews — Backup Now

Runs the on-demand backup flow: `pg_dump` → COS upload via `tencent-cos-skill`.

## Requirements

- Tencent COS credentials configured in OpenClaw's environment (via `tencent-cos-skill`)
- `buzz-news-worker` service must be running (or the backup script accessible as `buzz` user)
- COS bucket and region must be configured

## Commands

```bash
# Run the backup script as the buzz user
sudo -u buzz bash /opt/buzz-news/deploy/backup.sh

# The script:
# 1. pg_dump to /var/backups/buzz-news/db-<stamp>.pgcustom
# 2. tar images to /var/backups/buzz-news/images-<stamp>.tgz
# 3. Upload both to COS via OpenClaw's tencent-cos-skill
# 4. Delete local backups older than 7 days
```

## Output

Returns the COS object keys for:
- Database backup: `db/<stamp>.pgcustom`
- Images backup: `images/<stamp>.tgz`

## Notes

- Backup runs as the `buzz` user, not root
- Local copies are kept for 7 days before automatic deletion
- COS lifecycle: 30 days hot → 90 days cold → delete
- Run `buzznews_status` after backup to confirm worker is still healthy
