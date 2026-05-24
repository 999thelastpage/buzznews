---
name: buzznews-pause-source
description: "Pause (disable) a BuzzNews source by its slug. Use when the user asks to pause a news source, disable a feed, or stop fetching from a particular source."
---

# BuzzNews — Pause Source

Disables a BuzzNews source so it will be skipped during fetch cycles.

## Requirements

- The `pause_source(slug)` SQL function must exist in the `buzz_news` database
- This function is granted to `buzz_ro` for read-only role access

## Commands

```bash
# Pause a source by slug (e.g. 'ndtv_hindi')
psql -h 127.0.0.1 -U buzz_ro -d buzz_news -c "SELECT pause_source('ndtv_hindi');"

# Verify it is paused
psql -h 127.0.0.1 -U buzz_ro -d buzz_news -c "SELECT slug, name, enabled FROM sources WHERE slug='ndtv_hindi';"
```

## Arguments

- `slug` — the source slug to pause (e.g. `ndtv_hindi`, `bbc_world`)

## Notes

- Pausing a source is reversible — a DBA can re-enable it with:
  ```sql
  UPDATE sources SET enabled=true WHERE slug='<slug>';
  ```
- To re-enable via this skill, use the buzznews_restart_worker skill after manual DB intervention.
