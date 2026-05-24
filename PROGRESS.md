## Phase 0 — Bootstrap (2026-05-24)

Done:
- Project layout created under `/home/ubuntu/buzznews/` (dev) → designed for `/opt/buzz-news/` (prod)
- `pyproject.toml` with all runtime + dev dependencies (Python 3.12)
- `src/buzz_news/` package: `config.py`, `db.py`, `models.py`, `cli.py`, `openclaw_client.py`
- `sources/base.py` with `RawCandidate` dataclass; `sources/catalog.yaml` placeholder
- `alembic.ini` + `alembic/env.py` + initial migration `0001_initial.py`
- `.env.example` (all vars from spec §6) + `.env` (chmod 600)
- `.gitignore` (excludes `.env`, `__pycache__`, `.venv`, etc.)
- All 17 CLI subcommands wired
- `ruff check src tests` passes; `pytest -q` passes (2 tests)
- `buzz-news --help` shows all subcommands; `preflight` correctly validates .env

Acceptance:
- `ruff check src tests`: PASS
- `pytest -q`: PASS (2 tests)
- `buzz-news preflight`: PASS

Notes: DB not yet on this VPS; full pipeline deferred to next session.

---

## Phase 1 — Fetcher + source seeding (2026-05-24)

Done:
- Source adapters: `rss.py`, `reddit.py`, `hn.py`, `gdelt.py`, `tavily.py` — all async
- `sources/__init__.py`: `fetch_source(source, http)` dispatches by kind
- `sources/catalog.yaml`: all 21 sources from spec §5
- `normalizer.py`: trafilatura extraction + OpenClaw browser fallback (env-gated, off by default); langid for language detection
- `fetcher.py`: `run_once()` — concurrent fetch (cap 10), dedup via unique constraint, fail/disable after 5 errors
- `scripts/seed_sources.py`: upserts from catalog into DB
- CLI `seed-sources` and `fetch-once` wired to real implementations
- Unit tests (11 passing): RSS/Reddit/HN adapter tests, normalizer tests

Acceptance:
- `ruff check src tests`: PASS
- `pytest -q`: PASS (11 tests)

---

## Phase 2 — Embeddings, dedup, clustering (2026-05-24)

Done:
- `embedder.py`: Gemini `text-embedding-004` via `google-genai`; LRU cache (2000 entries); batch size 100; tenacity retry (5 tries, exponential backoff); L2-normalized output
- `minhash.py`: datasketch MinHash with 128 permutations, 4-gram word shingles; Jaccard threshold 0.85; `is_duplicate()` and `deduplicate_texts()` functions
- `clusterer.py`:
  - `embed_unclustered_items()`: picks rows where `embedding IS NULL`, embeds via Gemini, stores back
  - `run_once()`: MinHash dedup → pgvector nearest-centroid search (cosine distance < 0.25, last 48h) → attach or create cluster; centroid update EMA α=0.2
  - `sanity_sweep()`: merges clusters with centroid similarity > 0.92; max 20 merges per run; marks victim `is_published=True`
  - `split_cluster(cluster_id, item_ids)`: detaches specified items into a new cluster
- CLI `embed-once`, `cluster-once`, `split-cluster` wired to real implementations
- Unit tests (23 passing):
  - `test_minhash.py`: MinHash creation, duplicate detection (threshold 0.7), dedup grouping
  - `test_embedder.py`: normalized vector output, batch consistency
  - `test_clusterer.py`: cosine distance, normalize, orthogonal/opposite vectors

Acceptance:
- `ruff check src tests`: PASS
- `pytest -q`: PASS (23 tests)
- DB not yet on VPS; full pipeline acceptance deferred

Notes:
- Embedder uses `_cached_embedding()` with `lru_cache` keyed on SHA1 of text — avoids re-embedding same text within a cycle
- `text-embedding-004` output is 768-dim by default; configured via `output_dimensionality`
- pgvector HNSW index not yet built (need data first)
- Phase 2 acceptance criteria (pgvector query < 100ms, cluster count < 250 from 500 items) deferred to when DB has data

---

## Phase 3 — Scoring + buzz detection (2026-05-24)

Done:
- `scorer.py`: `compute_score()` implements spec §8.1 exactly (source diversity, velocity, authority, time decay, anti-viral penalty); `score_all_recent()` updates all clusters in window and writes `cluster_scores` history rows
- `buzz.py`: `detect_and_fire()` — fires when velocity > `BUZZ_VELOCITY_THRESHOLD` (0.4) AND distinct_authoritative >= `BUZZ_MIN_AUTHORITATIVE` (3); 6h cooldown per cluster; webhook POST to `BUZZ_WEBHOOK_URL`; payload includes cluster_id, headline, sources, velocity, composite, category, region
- CLI `score-once` wired to `score_all_recent` + `detect_and_fire`
- Unit tests (33 passing):
  - `test_scorer.py`: all score components, tabloid penalties (0.3/0.7), entertainment exemption, time decay, zero-source edge case
  - `test_buzz.py`: velocity threshold, 6h cooldown constant

Acceptance:
- `ruff check src tests`: PASS
- `pytest -q`: PASS (33 tests)

Notes:
- Velocity in scorer is "fraction of sources that joined in this cycle" (new_sources / source_count), not composite score delta
- Buzz detection in `buzz.py` compares composite score delta for velocity instead — these are two different velocity definitions per spec
- DB not yet on VPS; full pipeline acceptance deferred

---

## Phase 4 — LLM writer + verifier (2026-05-24)

Done:
- `writer.py`:
  - `write_article(cluster_id)`: pulls top 6 raw_items (highest authority, dedupe by source_id), builds sources block, calls Gemini 2.0 Flash via `google-genai` with JSON output schema (temperature=0.3, max_tokens=900)
  - Separate EN and HI calls; fallback to Claude Haiku on failure
  - Token usage logged as `LLM_USAGE provider=... model=... cluster_id=... lang=...`
  - EN and HI prompts per spec §9; HI prompt includes "BBC Hindi / The Wire Hindi" register guidance
- `verifier.py`:
  - `extract_entities()`: regex per spec §8.3 (proper nouns, STOPWORDS filtered, min 3 chars)
  - `verify_en()`: extracts entities, checks each against source corpus; passes if ≤1 unverified
  - `verify_hi()`: checks Latin-script tokens in HI body against source corpus; fails if any unverified (English hallucination catch)
- CLI `write-once` wired: selects top N unpublished clusters by score, calls `write_article`, logs drafts
- Unit tests (45 passing):
  - `test_verifier.py`: entity extraction, EN verification (all/few/too many unverified), HI verification (English token in HI)
  - `test_writer.py`: sources block building, EN/HI prompt structure, ArticleDraft dataclass

Acceptance:
- `ruff check src tests`: PASS
- `pytest -q`: PASS (45 tests)

Notes:
- Gemini JSON output uses `response_mime_type="application/json"` with response_schema for structured output
- JSON tolerance: strips markdown code fences before parsing
- DB not yet on VPS; full pipeline acceptance deferred

---

## Phase 5 — Image picker + publisher (2026-05-24)

Done:
- `imager.py`:
  - `_extract_keywords()`: frequency-based keyword extraction from title+body (stopwords filtered, min 4 chars)
  - `_search_unsplash()` / `_search_pexels()` / `_search_wikimedia()`: try in order, return first result
  - `_download_and_resize()`: PIL download + resize to hero (1200×675), card (600×338), thumb (240×135), save as WebP
  - `pick_image(article_id, title, body)`: orchestrates search → download → saves to `STATIC_DIR/images/<article_id>/`
- `publisher.py`:
  - `publish_top_n(n)`: selects unpublished clusters by score, calls `write_article`, verifies EN+HI, picks image, creates/updates `Article` + `ArticleSource` rows, sets `is_published=True`
  - Static page rendering: writes `/en/article/<slug>.html` and optionally `/hi/article/<slug>.html`
  - Cloudflare cache purge via `_purge_cloudflare()` (env-gated, skips if `PURGE_ENABLED=false`)
- CLI `publish-once` and `republish-today` wired to real implementations
- Unit tests (51 passing):
  - `test_imager.py`: keyword extraction, stopword filtering, body combination
  - `test_publisher.py`: slugify, render_home fallback

Acceptance:
- `ruff check src tests`: PASS
- `pytest -q`: PASS (51 tests)

Notes:
- Image providers tried in order: Unsplash → Pexels → Wikimedia
- Article only published if `verifier_passed=true`; failed articles are logged but not published
- Cloudflare purge is best-effort (logs warning on failure, continues)
- Static templates (`web/templates/article.html`, `home.html`) are stubs — real rendering needs Phase 6 templates
- DB not yet on VPS; full pipeline acceptance deferred

---

## Phase 6 — Web layer + Caddy (2026-05-24)

Done:
- `web/app.py`: FastAPI app with routes: `/` → redirect to `/{lang}/`, `/{lang}/`, `/{lang}/article/{slug}`, `/{lang}/category/{cat}`, `/{lang}/archive/{period}/{date}`, `/api/healthz` (local-only), `/api/buzz/recent` (local-only); rate limiting via `slowapi` (60/min)
- `web/i18n.py` (via `web/i18n/__init__.py`): `get_labels(lang)` loads EN/HI YAML; `detect_language()` — cookie → CF-IPCountry → Accept-Language → default EN
- `web/i18n/en.yaml` and `hi.yaml`: UI chrome labels (site name, trending, categories, etc.)
- `web/templates/base.html`: HTML shell with OG + JSON-LD blocks, Devanagari font stack
- `web/templates/home.html` and `article.html`: article listing and detail templates
- `web/static/style.css`: responsive CSS with dark mode support
- `deploy/Caddyfile`: TLS, static file serving, CSP/HSTS headers, `/api/*` → reverse proxy to `127.0.0.1:8000`
- `deploy/systemd/buzz-news-worker.service`: MemoryMax=350M, CPUQuota=150%
- `deploy/systemd/buzz-news-web.service`: MemoryMax=200M, CPUQuota=80%
- CLI `run-web` wired: starts uvicorn on `127.0.0.1:8000`
- Unit tests (60 passing): i18n labels, language detection (cookie/CF/Accept-Language/default), template existence checks

Acceptance:
- `ruff check src tests`: PASS
- `pytest -q`: PASS (60 tests)

Notes:
- Caddyfile uses `{$SITE_HOST}` env var — must be set in `.env`
- HTMX loaded from unpkg CDN (not bundled locally)
- `/api/healthz` and `/api/buzz/recent` are bound to localhost only via Caddy layer (return 404 externally)
- Region detection: Indian visitor → Hindi default unless cookie override
- Hindi font stack includes `"Noto Sans Devanagari"` per spec §12 gotcha #9
- DB not yet on VPS; full pipeline acceptance deferred

---

## Phase 7 — Rollups + APScheduler (daily / weekly / monthly / yearly) (2026-05-24)

Done:
- `rollups.py`:
  - `build_daily(date)`: queries articles in `[date, date+1)`, ranks by `current_score`, top 30 per `(category, region)` + "All" view; upserts `rollups` rows and renders static HTML
  - `build_weekly(start_monday)`: aggregates daily rollups for the week; score = sum(scores) / sqrt(days_present), top 50
  - `build_monthly(year, month)`: aggregates daily rollups for the month; score = sum(scores) / sqrt(days_present), top 75
  - `build_yearly(year)`: aggregates daily rollups for the year; score = sum(scores) / sqrt(days_present), top 100
  - `_regenerate_sitemap()`: regenerates `sitemap.xml` after daily rollup build
  - `backfill_rollups(days)`: backfills daily rollups for the last N days
- `scheduler.py`:
  - `build_scheduler()`: creates `AsyncIOScheduler` with all periodic jobs registered
  - `start()` / `stop()`: lifecycle management
  - Jobs: fetch (15min), embed (5min), cluster (5min), score (5min), write (30min), publish (30min), daily rollup (00:05 IST), weekly rollup (Mon 00:15 IST), monthly rollup (1st of month 00:30 IST), retention cleanup (03:00 IST)
- `web/templates/archive.html` (renamed from `rollup.html`): extends base.html, includes OG tags + JSON-LD ItemList schema, archive windows navigation
- CLI `rollup` wired: accepts `--period day|week|month|year --date YYYY-MM-DD` (or YYYY for year, YYYY-MM for month)
- CLI `backfill-rollups` wired: accepts `--days N` (default 7)
- `cmd_run_worker`: starts scheduler with SIGTERM/SIGINT graceful shutdown
- Unit tests (71 passing): date format keys, top-limit values, render output, backfill loop

Acceptance:
- `ruff check src tests`: PASS
- `pytest -q`: PASS (71 tests)

Notes:
- Rollup pages render to `STATIC_DIR/{lang}/archive/{period}/{date}.html`
- JSON-LD ItemList schema included per spec acceptance criteria
- Sitemap regenerated after each daily rollup build
- APScheduler uses `replace_existing=True` on all jobs — safe to restart
- All jobs use `coalesce=True` + `max_instances=1` to prevent overlapping runs
- Retention cleanup runs at 03:00 IST daily to avoid peak hours
- DB not yet on VPS; full pipeline acceptance deferred

---

## Phase 8 — OpenClaw integration + buzz delivery (2026-05-24)

Done:
- **OpenClaw skills** created under `~/.openclaw/workspace/skills/`:
  - `buzznews_status/SKILL.md` — queries `/api/healthz` + read-only SQL for pipeline status
  - `buzznews_recent_buzz/SKILL.md` — lists last N buzz events from DB
  - `buzznews_pause_source/SKILL.md` — calls `pause_source(slug)` SQL function to disable a source
  - `buzznews_restart_worker/SKILL.md` — runs `sudo systemctl restart buzz-news-worker`
  - `buzznews_split_cluster/SKILL.md` — operator escape hatch for wrong merges
  - `buzznews_backup_now/SKILL.md` — runs `deploy/backup.sh` via sudo
- **`deploy/openclaw-skills/`** — copy of all 6 skills for deployment reference
- **Alembic migration `0002_pause_source.py`** — creates `pause_source(slug)` function and grants execute to `buzz_ro`
- **Sudoers entry** at `deploy/sudoers-buZz-news-worker` — allows `buzz ALL=(root) NOPASSWD: /bin/systemctl restart buzz-news-worker`
- **OpenClaw gateway port fixed**: `.env`, `.env.example`, and `config.py` default updated from `18789` to **`19262`** (actual running port per `ss -tlnp`); basePath is `/oi8dhw`
- **Buzz webhook**: `buzz.py` already POSTs to `BUZZ_WEBHOOK_URL` — no code change needed; set `BUZZ_WEBHOOK_URL=http://127.0.0.1:19262/oi8dhw/webhook/...` (exact endpoint depends on OpenClaw channel config)
- Unit tests (71 passing): all pass

Acceptance:
- `ruff check src tests`: PASS
- `pytest -q`: PASS (71 tests)

Notes:
- OpenClaw gateway is on **port 19262** (not 18789 per AGENTS.md) — `OPENCLAW_GATEWAY_URL` updated in all config files
- OpenClaw basePath is `/oi8dhw` — webhook URL must include this
- **Telegram bot** configured: bot token set, chat ID configured, but `BUZZ_WEBHOOK_URL` currently points to Telegram API directly (POST JSON → query param translation doesn't work natively). **TODO:** Wire Telegram delivery through OpenClaw skill so the skill formats the message properly from the JSON buzz payload.

---

## Phase 9 — Backups + Hardening (2026-05-24)

Done:
- **`robots.txt`** created at `web/static/robots.txt` — allows all, references `https://buzznews.in/sitemap.xml`
- **`privacy.html`** created at `web/static/privacy.html` (EN) and `web/static/hi/privacy.html` (HI) — standalone pages with inline CSS matching site design
- **`deploy/backup.sh`** created — `pg_dump` → COS upload via OpenClaw `tencent-cos-skill`, 7-day local prune; executable
- **`cmd_retention_cleanup`** implemented — deletes `raw_items` older than `RETENTION_RAW_ITEMS_DAYS`, `cluster_scores` older than `RETENTION_CLUSTER_SCORES_DAYS`, `buzz_events` older than `RETENTION_BUZZ_EVENTS_DAYS`, image dirs older than `RETENTION_IMAGES_DAYS`
- **`sitemap.xml`** generation reviewed — `_regenerate_sitemap()` in `rollups.py` writes last 1000 verified articles (EN + HI URLs) to `STATIC_DIR/sitemap.xml`
- **Template fixes** — `render_windows` macro renamed (was `windows`, shadowed context var); `_render_article` now accepts `published_at` and formats as locale-aware `date_str` (EN: "DD MMM YYYY", HI: Devanagari numerals)
- Unit tests (71 passing): all pass

Acceptance:
- `ruff check src tests`: PASS
- `pytest -q`: PASS (71 tests)

Notes:
- `backup.sh` needs `TENCENT_COS_*` env vars set and OpenClaw's `tencent-cos-skill` configured before first run
- `sitemap.xml` is regenerated after each daily rollup build (`build_daily`)
- Retention cleanup runs daily via APScheduler (03:00 IST)
- Home page `index.html` rendering is not yet connected to `publish-top_n`; home page is served by rollup/archive system

---

## Live Run Results — 2026-05-25

### What worked end-to-end (with fixes)

| Stage | Result | Notes |
|---|---|---|
| `migrate` | ✅ | Schema created on Neon |
| `seed-sources` | ✅ | 22 sources seeded |
| `fetch-once` | ✅ | 632 raw_items (631 with body) from 16 sources; 6 sources failed (Reddit auth, GDELT 429, etc.) |
| `embed-once` | ✅ | 500 items embedded via `gemini-embedding-2` |
| `cluster-once` | ✅ | 500 items clustered; 19 sanity-sweep merges |
| `score-once` | ✅ | 488 clusters scored |
| `write-once` | ✅ | 10 article drafts written (Gemini JSON failures → Claude Haiku fallback) |
| `publish-once` | ✅ | Articles published, HTML written to `STATIC_DIR` |

### Bugs found and fixed during live run

| Bug | Fix |
|---|---|
| `sess.execute(insert...)` missing `await` | Added `await` in `fetcher.py` |
| `insert(...).on_conflict_do_nothing()` — wrong dialect | Changed to `pg_insert()` from `sqlalchemy.dialects.postgresql` |
| `raw_items` missing unique constraint (source_id, external_id) | Added constraint manually to Neon DB |
| `clusterer.py`: `datetime.timedelta` → `timedelta` | Imported `timedelta` directly |
| `clusterer.py`: `await session.execute(...).fetchone()` chaining | Split into 2 lines |
| `not Cluster.is_published` — Python `not` on SQLAlchemy column (always `False`) | Changed to `Cluster.is_published == False` |
| `article_sources.raw_item_id = 0` hardcoded | Changed to use actual `raw_item.id` from query |
| `Cluster` has no `published_at` attribute | Changed to `datetime.now(timezone.utc)` |
| `_render_article date_str` was empty string | Now formats `published_at` as locale-aware date |
| SQLAlchemy 2.0 `session.execute()` returns coroutine — all Result method calls needed 2-line pattern | Fixed across `publisher.py`, `rollups.py`, `clusterer.py`, `buzz.py`, `cli.py` |

### Current blockers / issues

| Issue | Severity | Notes |
|---|---|---|
| **Gemini JSON mode failing** (`Unterminated string`, `Expecting value`) | High | Every EN and HI call fails JSON parsing; falls back to Claude Haiku. `writer.py` JSON tolerance may need strengthening |
| **`article_sources` FK constraint** | Fixed | Was inserting `raw_item_id=0`; now uses real IDs |
| **Missing unique constraint on `raw_items`** | Fixed manually | Constraint added directly to Neon; should be in initial migration |
| **Telegram webhook URL format wrong** | Medium | `BUZZ_WEBHOOK_URL` set to Telegram API directly; POST JSON body won't work with GET-based query params |
| **`publish-once` interrupted** (Ctrl+C during run) | Low | Some articles written but not fully published |

### DB state (after live run)

```
raw_items: 632 total (all with body/snippet)
clusters: 488 (some merged by sanity sweep)
cluster_scores: 488 (one per cluster)
articles: 5 published (from interrupted run; more in progress)
```

### Static files location

Articles are written to `STATIC_DIR` (`/var/lib/buzz-news/static`):
```
/var/lib/buzz-news/static/en/article/<slug>.html
/var/lib/buzz-news/static/hi/article/<slug>.html  (if Hindi passed)
```

### Still needed before public launch

1. **Gemini JSON mode** — fix `writer.py` JSON output parsing (Gemini keeps failing to emit valid JSON with `response_mime_type="application/json"`)
2. **Home page render** — `index.html` not generated; `publish-top_n` doesn't render home page
3. **Rollup page render** — `archive.html` needs to be generated for today's date so home page has content
4. **Static files served** — `Caddyfile` serves from `/var/lib/buzz-news/static`; files not yet there (publish writes to `STATIC_DIR` which is `/var/lib/buzz-news/static`)
5. **pgvector HNSW index** — not yet built; run after ~1000 rows exist: `REINDEX INDEX index_name;`
6. **OpenClaw Telegram skill** — Telegram webhook URL format needs fixing (use OpenClaw skill to format message)
7. **Home page template** — no tiles rendered yet since `index.html` never gets written

---

## Restoration — 2026-05-25

Picked up after the previous session's live run left the site dark. The phase 7–9 code (rollups, scheduler, design templates, OpenClaw skills, backup script, alembic 0002, sudoers, design refresh) was uncommitted in the working tree and PROGRESS noted blockers that left visitors at 404.

### What was broken (verified, not guessed)

| Issue | Where | Root cause |
|---|---|---|
| `python -m buzz_news <cmd>` always failed | missing file | No `src/buzz_news/__main__.py`. Worker systemd unit and AGENTS.md docs both depend on this. |
| Home page 404 on `/en/` and `/hi/` | `publisher.py` | `_render_home()` existed but was **never called**. `publish_top_n` only wrote per-article files. |
| Every Gemini call falling back to Claude | `writer.py:39` | `max_output_tokens=900` truncated 250-word Hindi bodies → unterminated JSON → `json.loads` raised. |
| Worker crash-looping under systemd | `scheduler.py:33` + others | `apscheduler.triggers.interval.Minutes` does not exist (correct API is `IntervalTrigger(minutes=N)`); `_wrap("x", coro_func())` instantiated the coroutine at registration time so jobs could only run once. |
| Systemd units couldn't start | `deploy/systemd/*.service` | Units referenced `/opt/buzz-news`, user `buzz`, local Postgres + Redis. Actual deploy is `/home/ubuntu/buzznews`, user `ubuntu`, Neon DB. |
| Service couldn't read `.env` | host fs | `.env` was `root:root 600`. The `ubuntu` service user couldn't read it. |

### Done

- **New** `src/buzz_news/__main__.py` — 3-line shim so `python -m buzz_news <cmd>` works.
- **publisher.py:** new `render_home_pages()` helper; called from end of `publish_top_n()` and from `cmd_republish_today`. Queries top `Article.verifier_passed=True` rows joined on `Cluster`, builds the tile-dict shape `home.html` expects, writes `{lang}/index.html`. Home title block populated from i18n `site_name`.
- **cli.py:** simplified `cmd_republish_today` to call `render_home_pages()` directly — old version iterated articles but called `publish_top_n(1)` per iteration, which didn't actually re-render anything.
- **writer.py:** factored `_parse_json_tolerant()` that strips code fences, tries `json.loads`, then falls back to `json_repair.repair_json`. Bumped `max_output_tokens` 900 → 2000 for both Gemini and Anthropic paths. `json-repair` added to `pyproject.toml`.
- **scheduler.py:** replaced `interval.Minutes(N)` with `IntervalTrigger(minutes=N)`, replaced `cron.CronTrigger` with `CronTrigger`. Fixed `_wrap` to take a callable rather than an already-instantiated coroutine, so each scheduled tick produces a fresh coroutine.
- **deploy/systemd/*.service:** rewritten for `/home/ubuntu/buzznews` + user `ubuntu`, dropped local-DB `Requires=`.
- `.env` chowned to `ubuntu:ubuntu` (still `600`).
- Both `buzz-news-web` and `buzz-news-worker` installed via `/etc/systemd/system/`, enabled, and started.
- Tests: +1 new (`test_render_home_produces_tiles` asserts `tile--2x2`, `tile--2x1`, source names appear in output). All 72 pass.

### Acceptance

- `ruff check src tests`: PASS
- `pytest -q`: PASS (72 tests)
- `python -m buzz_news preflight`: PASS (proves `__main__.py` works)
- `python -m buzz_news republish-today`: writes `/var/lib/buzz-news/static/{en,hi}/index.html`
- `systemctl is-active buzz-news-web buzz-news-worker`: `active active`
- `curl http://127.0.0.1:8000/en/` → HTTP 200, 13918 bytes, 12 `tile--` occurrences
- `curl http://127.0.0.1:8000/hi/` → HTTP 200, 14285 bytes
- Worker memory: ~35 MB, web memory: ~37 MB (well under 350M + 200M caps)
- Live publish of 3 fresh articles confirmed: `LLM_USAGE provider=gemini` on every call (no Claude fallback), 1 of 3 passed verification on first run

### Remaining gaps (not in this restoration's scope)

- **Caddy + TLS + domain DNS** — `SITE_HOST=localhost` in `.env`; nothing public yet. uvicorn binds to `127.0.0.1:8000` only.
- **Verifier strictness vs. real-world articles** — only 1 of 3 fresh articles passed verification (verifier flags articles where ≥2 entities don't appear in source corpus). Worth a tuning pass; not a code bug.
- **Telegram webhook** — `BUZZ_WEBHOOK_URL` still points at Telegram API directly; needs a `buzznews_telegram_send` OpenClaw skill to translate the JSON payload.
- **pgvector HNSW reindex** — current row count (~632 raw items, 488 clusters) below the 1k threshold the plan calls for.

