# AGENTS.md — BuzzNews

Canonical spec: `PROJECT_PLAN.md` (~1450 lines). Read it before any tool call. This file is only for hard-won context not obvious from the spec.

## Hard constraints (would an agent violate these?)

- **No local ML.** Embeddings → Gemini `text-embedding-004` only. No sentence-transformers, PyTorch, HDBSCAN, scikit-learn, spaCy. This is a 1.9 GB RAM constraint.
- **No Docker on the VPS.** Bare metal + systemd only.
- **No Node.js in the BuzzNews runtime path.** OpenClaw is the only Node process and it's pre-installed.
- **No client-side React.** HTMX + Alpine.js + Jinja2 only.
- **BuzzNews is Python 3.12 only.** FastAPI + Uvicorn + SQLAlchemy (async) + APScheduler.
- **OpenClaw is already running at `127.0.0.1:19262`** (the original spec said 18789 — that's stale; check `ss -tlnp` to confirm). basePath `/oi8dhw`. Do not install, start, stop, restart, or supervise it. No `deploy/systemd/openclaw.service`.
- **App user is `ubuntu`** (deploy chose to skip the `buzz` user migration). Run pipeline commands as the ubuntu user or simply from the repo's `.venv`.
- **Never commit `.env`.** It must be `chmod 600` owned by `ubuntu`. `sed -i` run as root re-owns it to root:root — always `sudo chown ubuntu:ubuntu` after editing as a different user.

## Memory budget (1.9 GB VPS)

| Tenant | Cap |
|---|---|
| OpenClaw (idle) | ~500 MB |
| OpenClaw browser (peak) | +500 MB |
| BuzzNews worker | 350 MB |
| BuzzNews web | 200 MB |
| Postgres + Redis | ~310 MB |
| Caddy + system | ~100 MB |
| **Typical steady-state** | **~1.3 GB** |
| **Worst-case (browser active)** | **~1.8 GB** |

If < 700 MB available before installing something heavy, stop and investigate.

## Project layout

```
/home/ubuntu/buzznews/    # actual deploy (the /opt/buzz-news/ migration was deferred)
src/buzz_news/
  __main__.py             # python -m buzz_news entry shim
  __init__.py
  sources/                # adapters: rss.py, reddit.py, hn.py, gdelt.py, tavily.py
  fetcher.py             # orchestrates one fetch cycle
  normalizer.py           # trafilatura + OpenClaw browser fallback
  embedder.py             # Gemini text-embedding-004
  minhash.py              # datasketch MinHash LSH
  clusterer.py            # pgvector ANN + sanity sweep
  scorer.py               # trending algorithm (§8.1)
  buzz.py                 # spike detection + webhook
  writer.py                # LLM article generation
  verifier.py              # entity verification EN + HI
  imager.py               # Unsplash / Pexels / Wikimedia
  publisher.py            # write + render + Cloudflare purge
  rollups.py              # daily/weekly/monthly/yearly
  retention.py            # cleanup job
  scheduler.py             # APScheduler entry point
  web/app.py              # FastAPI
  cli.py                  # python -m buzz_news <subcommand>
tests/                    # pytest + pytest-asyncio + respx
deploy/
  Caddyfile
  systemd/                # buzz-news-worker.service, buzz-news-web.service (NO openclaw.service)
  backup.sh
  openclaw-skills/        # synced to ~/.openclaw/workspace/skills/
scripts/
  seed_sources.py
  manual_fetch_once.py
```

## CLI subcommands (canonical list)

```bash
# Setup
python -m buzz_news migrate
python -m buzz_news seed-sources
python -m buzz_news preflight          # validates .env, aborts on critical missing values
python -m buzz_news deploy-static      # copy robots.txt, privacy.html (en+hi) into STATIC_DIR

# Pipeline single-shot (in order for a full cycle)
python -m buzz_news fetch-once
python -m buzz_news embed-once
python -m buzz_news cluster-once
python -m buzz_news score-once
python -m buzz_news write-once          # LLM article generation
python -m buzz_news publish-once        # EN+HI + image + verify + render + CF purge
python -m buzz_news republish-today     # force rebuild today's static pages

# Maintenance
python -m buzz_news rollup --period day|week|month|year --date YYYY-MM-DD
python -m buzz_news retention-cleanup
python -m buzz_news backfill-rollups --days 7
python -m buzz_news split-cluster <id> --items <raw_item_ids>   # operator escape hatch

# Long-running
python -m buzz_news run-worker          # APScheduler + all background jobs
python -m buzz_news run-web             # FastAPI/Uvicorn on 127.0.0.1:8000
```

Run all `buzz_news` commands from `/home/ubuntu/buzznews` as ubuntu — `.venv/bin/python -m buzz_news …`. The `__main__.py` shim is required for `-m` to work; without it the package errors with "cannot be directly executed".

## Lint / typecheck / test order

```bash
ruff check src tests
pytest -q
```

## Testing conventions

- `pytest` + `pytest-asyncio` + `respx` for httpx mocking.
- Use **stored fixtures** (not live API calls) in unit tests.
- `OPENCLAW_BROWSER_FALLBACK_ENABLED=false` (default) — tests must assert zero calls to `OPENCLAW_GATEWAY_URL` when this is off.
- MinHash dedup test: assert zero outbound httpx calls (dedup runs before embedding).

## Key integration points with OpenClaw

- **Tavily search**: calls OpenClaw skill `openclaw-tavily-search` via HTTP POST to `http://127.0.0.1:18789/skills/openclaw-tavily-search/search`.
- **Browser extraction**: POST to `http://127.0.0.1:18789/skills/agent-browser-clawdbot/extract` (env-gated, off by default).
- **COS backups**: POST to `http://127.0.0.1:18789/skills/tencent-cos-skill/upload`.
- **Buzz delivery**: `BUZZ_WEBHOOK_URL` → OpenClaw gateway webhook.
- All bridge traffic is **localhost only, no auth**.

## Database

- **Postgres 16 + pgvector** (768-dim vectors, HNSW index).
- Vector similarity threshold for cluster attachment: **cosine distance < 0.25** (similarity > 0.75).
- Centroid update: EMA with α=0.2, re-normalized to unit length.
- Sanity sweep: merge clusters with centroid cosine similarity > 0.92 (max 20 merges/hour).
- Timestamps: **UTC in DB**, `Asia/Kolkata` only in templates and rollup boundaries.

## Pre-launch placeholders

- Phases 0–7: build and test with `TODO_PRE_LAUNCH` / `TODO_BEFORE_PHASE_1` values in `.env`.
- Phase 8 (Telegram) and Phase 9 (COS backups): need real keys.
- `python -m buzz_news preflight` validates `.env` at startup — critical missing values abort, non-critical ones warn.

## Architecture quirks

- **Hero image URL convention**: `imager.pick_image()` returns a web-relative URL like `/images/{cluster_id}/hero.webp` (NOT the on-disk absolute path). Caddy serves these from `STATIC_DIR`. Returning the disk path will silently render broken `<img src="/var/lib/...">` tags.
- **Slug stability**: `publisher.publish_top_n` reuses `existing.slug` on republish instead of recomputing from `_slugify(draft.title_en, cluster.id)`. LLM rewords titles between runs, so recomputing the slug would orphan the previous HTML file at the old URL. Never call `_slugify()` for an article that already has a row in the DB.
- **Detached SQLAlchemy objects across sessions don't persist**: `publish_top_n` loads `existing` in one `async with async_session_factory() as session:` block, then mutates it in a different session block. Use an explicit `update(Article).where(...).values(...)` statement, NOT `setattr(existing, k, v)` — setattr on a detached object is a silent no-op.
- **Inverted surfaces (2x2 lead, article header, active archive window) must use literal hex colors**, not `var(--paper)` / `var(--ink-2)`. The `--paper` token flips to a dark value under `prefers-color-scheme: dark`, which makes the inverted tile dark-on-dark. Use `#0E0B09` background + `#F4F0E8` text + `#1A1614` hover.
- **Mosaic grid breakpoints (revised from spec)**: `<=480px → 2 cols`, `481-719px → 4 cols`, `>=720px → 6 cols`. All breakpoints use `minmax(<base>, auto)` rows so tiles grow if title needs the room. Design.md's "phone default 4 cols" produced unreadably-cramped tiles on real phones.
- **`SITE_BASE_URL` (NOT `SITE_HOST`) is what the sitemap uses.** Two different env vars, easy to confuse. Sitemap URLs default to `http://localhost` if `SITE_BASE_URL` is unset.
- **Langid** (not spaCy) for language detection — pure Python, ~5 MB RAM, loaded once at module import.
- **Trafilatura** primary extraction; OpenClaw browser is fallback only for `js_heavy=true` sources with `OPENCLAW_BROWSER_FALLBACK_ENABLED=true`.
- **Reddit adapter**: 403/429 back-off aggressively; anonymous JSON access is restricted post-2023.
- **GDELT**: occasionally changes query params — if `kind=gdelt` returns 0 results, re-check the API URL.
- **Gemini JSON output**: occasionally returns stray text outside JSON block — use tolerant parser (strip non-JSON, then `json.loads`). Prefer `response_mime_type="application/json"` where supported.
- **APScheduler**: if you change a job trigger in code, startup must call `add_job(..., replace_existing=True)` or old triggers persist in the SQL jobstore.
- **pgvector HNSW index**: build after a few hundred rows exist; building on an empty table picks bad parameters. Run `REINDEX` after first 1k rows.
