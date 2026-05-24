---
name: buzznews-status
description: "Get BuzzNews pipeline status: last fetch time, last publish time, total articles today, worker health. Use when the user asks for pipeline status, system health, or how the news pipeline is doing."
---

# BuzzNews Pipeline Status

Queries the BuzzNews pipeline status via the health API and read-only SQL.

## Requirements

- BuzzNews web service must be running at `127.0.0.1:8000`
- Read-only DB access via `buzz_ro` role (password: same as configured in BuzzNews `.env`)

## Commands

Run from the OpenClaw workspace:

```bash
# Health check (fast)
curl -s http://127.0.0.1:8000/api/healthz

# Read-only status from DB (as buzz_ro user)
psql -h 127.0.0.1 -U buzz_ro -d buzz_news -c "
  SELECT
    (SELECT COUNT(*) FROM raw_items WHERE fetched_at > NOW() - INTERVAL '1 hour') AS items_last_hour,
    (SELECT COUNT(*) FROM articles WHERE published_at > NOW() - INTERVAL '1 day') AS articles_today,
    (SELECT MAX(fired_at) FROM buzz_events) AS last_buzz,
    (SELECT MAX(last_fetched_at) FROM sources WHERE enabled=true) AS last_fetch;
"
```

## Output

Returns a summary:
- Items fetched in last hour
- Articles published today
- Last buzz event time
- Last source fetch time
- Worker health status from `/api/healthz`
