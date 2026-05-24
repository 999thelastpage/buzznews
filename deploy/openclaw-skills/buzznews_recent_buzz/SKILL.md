---
name: buzznews-recent-buzz
description: "List the most recent BuzzNews buzz events (spike detections). Use when the user asks for recent buzz, trending events, or spike detections."
---

# BuzzNews Recent Buzz Events

Queries the most recent buzz events from the BuzzNews database.

## Requirements

- Read-only DB access via `buzz_ro` role

## Commands

```bash
# Last 10 buzz events
psql -h 127.0.0.1 -U buzz_ro -d buzz_news -c "
  SELECT
    be.id,
    be.fired_at,
    be.velocity,
    be.distinct_authoritative,
    be.composite,
    c.category,
    c.region,
    (
      SELECT r.title
      FROM raw_items r
      WHERE r.cluster_id = c.id
      LIMIT 1
    ) AS headline_guess
  FROM buzz_events be
  JOIN clusters c ON be.cluster_id = c.id
  ORDER BY be.fired_at DESC
  LIMIT 10;
"
```

## Output

A table of recent buzz events with:
- Event ID and fired time
- Velocity score
- Number of distinct authoritative sources
- Composite score
- Category and region
- Best headline guess from the cluster
