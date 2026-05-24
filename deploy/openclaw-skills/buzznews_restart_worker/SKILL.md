---
name: buzznews-restart-worker
description: "Restart the BuzzNews worker service. Use when the user asks to restart the pipeline worker, restart BuzzNews, or reload the worker after a config change."
---

# BuzzNews — Restart Worker

Restarts the `buzz-news-worker` systemd service via sudo.

## Requirements

- The `buzz` system user must be allowed to run `sudo systemctl restart buzz-news-worker` without a password
- This is configured via sudoers and limited to this single command

## Commands

```bash
# Restart the worker (as buzz user via sudo)
sudo -u buzz systemctl restart buzz-news-worker

# Check status
sudo -u buzz systemctl status buzz-news-worker
```

## Notes

- The worker auto-restarts on failure via systemd `Restart=always`
- On restart, APScheduler will fire any missed jobs (coalesced)
- Wait ~10 seconds after restart before checking `/api/healthz`
