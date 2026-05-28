# AGENTS.md — BuzzNews

Canonical spec: `PROJECT_PLAN.md` (~1450 lines). Read it before any tool call. This file is only for hard-won context not obvious from the spec.

## Hard constraints (would an agent violate these?)

- **No local ML.** Embeddings are hosted only. Default is OpenAI `text-embedding-3-small` at 768 dimensions via `OPENAI_EMBED_DIM`; Gemini `gemini-embedding-001` remains a fallback/provider option. No sentence-transformers, PyTorch, HDBSCAN, scikit-learn, spaCy. This is a 1.9 GB RAM constraint.
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
  embedder.py             # hosted embeddings provider abstraction; OpenAI default, Gemini fallback/provider option
  minhash.py              # datasketch MinHash LSH
  clusterer.py            # pgvector ANN + sanity sweep
  scorer.py               # trending algorithm (§8.1)
  buzz.py                 # spike detection + webhook
  writer.py                # LLM article generation
  verifier.py              # entity verification EN + HI
  imager.py               # Unsplash / Pexels / Wikimedia
  search.py               # hybrid Postgres FTS + pgvector search, cost-capped
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
  openclaw-skills/        # synced to ~/.openclaw/workspace/skills/
scripts/
  seed_sources.py
  manual_fetch_once.py
  db_backup.sh           # nightly Postgres dump → rsync to remote VPS (run by buzz-news-backup.timer)
  make_favicon.py        # regenerates favicon.ico + apple-touch-icon.png from the Pillow mark (favicon.svg is the editable source)
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

# Archive (replaces the old `rollup` / `backfill-rollups` cmds)
python -m buzz_news today-archive                         # rebuild /{lang}/archive/today.html
python -m buzz_news monthly-archive [--month YYYY-MM]     # rebuild /{lang}/archive/month/YYYY-MM.html (defaults to current IST month)

# Maintenance
python -m buzz_news retention-cleanup
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

- **Tavily search**: calls OpenClaw skill `openclaw-tavily-search` via HTTP POST to `http://127.0.0.1:19262/skills/openclaw-tavily-search/search`.
- **Browser extraction**: POST to `http://127.0.0.1:19262/skills/agent-browser-clawdbot/extract` (env-gated, off by default).
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
- Phase 8 (Telegram) needs real keys. (Phase 9 COS backups was dropped — Anjali backs up manually.)
- `python -m buzz_news preflight` validates `.env` at startup — critical missing values abort, non-critical ones warn.

## Architecture quirks

- **`Article.summary_en` / `summary_hi` hold the FULL body, not a teaser.** Despite the name, these columns are the entire LLM-generated article body — re-render scripts read them as the body. The publisher used to do `draft.body_en[:500]` which made every re-render a 70-word stump; removed in commit `b4d96e7` (2026-05-25). Rollup teasers are sliced at render time (`rollup.html` does `[:200]`), so storing the full body has no rollup-side regression. If you need a real short summary, add a separate column — never re-truncate at insert.
- **Static dir must be `ubuntu:ubuntu`.** `/var/lib/buzz-news/static/` is written by the worker (running as `ubuntu`). If a maintenance script runs as root (e.g. via `sudo` from a Claude session) and writes files, those files become root-owned and the next publish cycle silently fails with `PermissionError`, the home page goes stale, and nothing gets logged at WARN level. Always run rerender/rewrite scripts as `sudo -u ubuntu .venv/bin/python scripts/...`. If you find root-owned files there: `sudo chown -R ubuntu:ubuntu /var/lib/buzz-news/static`.
- **LLM routing is cost-aware, not a simple fallback chain.** First-publish uses one bilingual JSON call. DeepSeek `deepseek-v4-flash` is reserved for strong/high-tier first publishes (`distinct_sources >= 2` or avg authority >= 0.75), paced to `DEEPSEEK_DAILY_ARTICLE_CAP=60` accepted DeepSeek articles per IST day. Total new articles are capped by `MAX_NEW_ARTICLES_PER_DAY=96` (`PUBLISH_INTERVAL_MIN=15`, `TOP_N_PER_CYCLE=1`). Lower-tier first publishes use `LLM_LOW_TIER_PROVIDERS` (default Cerebras `gpt-oss-120b` → Groq Scout → Groq Qwen). Revisions use `LLM_REVISION_PROVIDERS` (same free chain, then paid DeepSeek as 4th). Paid revision fallback emits `ALERT_PAID_LLM_REVISION_FALLBACK` and posts to `BUZZ_WEBHOOK_URL`; Telegram `sendMessage` URLs are supported. Gemini/Anthropic helpers still exist but are not in normal routing.
- **LLM usage accounting is in `llm_usage_events`.** Migration `0005_llm_usage_details` adds `task`, `article_id`, `input_tokens`, `output_tokens`, `success`, and `error_type`. Free-provider soft caps use these estimates (`FREE_LLM_DAILY_TOKEN_SOFT_CAP`, `GROQ_DAILY_TOKEN_SOFT_CAP`). Do not bypass `writer.write_article(..., providers=..., task=...)` if you want budget accounting and alerting to work.
- **`write_article` does NOT persist — `publish_top_n` is the only path that writes to DB.** `writer.write_article(cluster_id, ...)` returns an `ArticleDraft` dataclass and has zero Article/ArticleSource persistence. The only persisting caller is `publisher.publish_top_n` (calls `write_article`, validates, writes/updates the article row, slug, embedding, image, sources, verifier notes, and flips `is_published=True`). The scheduler previously had a separate `_run_write` job that called `write_article` and threw the draft away — dropped on 2026-05-26 (it doubled the writer LLM bill). **Do not re-add a "write" scheduler job that doesn't persist its drafts.** Likewise, `cli.cmd_write_once` is a dry-run only — don't expect its drafts to show up anywhere.
- **Hindi output is gated before render.** The bilingual writer may return English in Hindi fields. `writer.is_valid_hindi()` requires enough Devanagari; invalid HI is suppressed on first publish (`title_hi=None`, `summary_hi=None`) and previous HI is preserved on revision. Hindi home/archive/article pages should only surface rows with valid HI. Existing bad rows can be cleaned manually with `python -m buzz_news cleanup-bad-hindi`.
- **Hero image URL convention**: `imager.pick_image()` returns a web-relative URL like `/images/{cluster_id}/hero.webp` (NOT the on-disk absolute path). Caddy serves these from `STATIC_DIR`. Returning the disk path will silently render broken `<img src="/var/lib/...">` tags.
- **Slug stability**: `publisher.publish_top_n` reuses `existing.slug` on republish instead of recomputing from `_slugify(draft.title_en, cluster.id)`. LLM rewords titles between runs, so recomputing the slug would orphan the previous HTML file at the old URL. Never call `_slugify()` for an article that already has a row in the DB. Slugs end in `cluster_id`, not `article_id` — easy to confuse when building URLs by hand.
- **Mast macro takes a full per-language path suffix**, not just a page key. `mast(lang, 'article/' ~ article.slug, labels, date_str)` for article pages, `'archive/today'` or `'archive/month/' ~ month_str` for the new archives, `'home'` (special) for the front page. The macro emits `/en/<page>` and `/hi/<page>` so EN/HI links share the same suffix (slugs and archive dates are language-agnostic). For pages outside the static path scheme (e.g. `/api/search?q=...&lang=...`), pass a `lang_switch={'en': url, 'hi': url}` kwarg — it overrides the auto-built `/{lang}/{page}` links.
- **Home Daily Brief footer** links to `/{lang}/archive/today` and `/{lang}/archive/month/{current_YYYY_MM}` (IST). `month_str` is computed in `render_home_pages` from `_ist_day_window()` — do NOT re-introduce a disk-scan of `archive/day/*.html` (that was the old broken pattern for the deleted daily archive).
- **Two-tier archive**: today (live, regenerated per publish cycle) + monthly (live, regenerated hourly). Weekly + yearly were dropped. Existing `archive/day/<date>.html` static files are left in place for SEO continuity but no new ones get written. See CLAUDE.md "Archive structure" for the full rules.
- **Two distinct embedding columns**: `raw_items.embedding` is `ARRAY(DOUBLE_PRECISION)` (pre-existing, cosine computed Python-side in `clusterer.py`). `articles.embedding` is pgvector's native `vector(768)` (added in `0003_article_search.py`) — has an HNSW index and is queried via `<=>` operator in `search.py`. **Don't mix the two**; they need different bind-parameter handling. The `Vector` type via SQLAlchemy ORM round-trips correctly; for raw SQL pass the vector as text form `[v1,v2,...]` with `CAST(:qvec AS vector)`.
- **Embedding cost cap**: embeddings are tracked in `embedding_usage_events` by provider/model/day. `MAX_DAILY_EMBED_TOKENS=1500000` is the production default, about $0.03/day with OpenAI `text-embedding-3-small` at $0.02/1M tokens. `search.py` also keeps the query-count guard (`MAX_DAILY_EMBEDS=500`) and falls back to FTS-only when embedding is unavailable or over budget. `scripts/backfill_openai_embeddings.py --ignore-budget` is only for explicit one-time backfills; do not use it in scheduler/runtime paths.
- **Embedder identity must match before comparison**: all vector comparisons must filter by provider/model/dim metadata (`embedding_provider`, `embedding_model`, `embedding_dim`; clusters use `centroid_*`). Search query hashes include provider/model/dim. For Gemini, QUERY/DOCUMENT task types still require 768 dimensions; for OpenAI, requests pass `dimensions=OPENAI_EMBED_DIM`.
- **Detached SQLAlchemy objects across sessions don't persist**: `publish_top_n` loads `existing` in one `async with async_session_factory() as session:` block, then mutates it in a different session block. Use an explicit `update(Article).where(...).values(...)` statement, NOT `setattr(existing, k, v)` — setattr on a detached object is a silent no-op.
- **Inverted surfaces (2x2 lead, article header, active archive window) must use literal hex colors**, not `var(--paper)` / `var(--ink-2)`. The `--paper` token flips to a dark value under `prefers-color-scheme: dark`, which makes the inverted tile dark-on-dark. Use `#0E0B09` background + `#F4F0E8` text + `#1A1614` hover.
- **Mosaic grid breakpoints (revised from spec)**: `<=480px → 2 cols`, `481-719px → 4 cols`, `>=720px → 6 cols`. All breakpoints use `minmax(<base>, auto)` rows so tiles grow if title needs the room. Design.md's "phone default 4 cols" produced unreadably-cramped tiles on real phones.
- **`SITE_BASE_URL` (NOT `SITE_HOST`) is what the sitemap uses.** Two different env vars, easy to confuse. Sitemap URLs default to `http://localhost` if `SITE_BASE_URL` is unset.
- **Langid** (not spaCy) for language detection — pure Python, ~5 MB RAM, loaded once at module import.
- **Trafilatura** primary extraction; OpenClaw browser is fallback only for `js_heavy=true` sources with `OPENCLAW_BROWSER_FALLBACK_ENABLED=true`.
- **Reddit adapter**: 403/429 back-off aggressively; anonymous JSON access is restricted post-2023.
- **GDELT**: occasionally changes query params — if `kind=gdelt` returns 0 results, re-check the API URL.
- **LLM JSON output is provider-specific.** Use the shared tolerant parser in `llm_client.py`. DeepSeek/Cerebras/Groq Scout use OpenAI-compatible `response_format={"type":"json_object"}`. Groq Qwen rejects strict JSON mode, so the client intentionally omits `response_format` for `groq:qwen/*` and relies on prompt discipline + tolerant parsing. Gemini occasionally returns stray text outside JSON; prefer `response_mime_type="application/json"` when it is used.
- **APScheduler**: if you change a job trigger in code, startup must call `add_job(..., replace_existing=True)` or old triggers persist in the SQL jobstore.
- **pgvector HNSW index**: build after a few hundred rows exist; building on an empty table picks bad parameters. Run `REINDEX` after first 1k rows.
- **Jinja2 comments only in templates** — never use JSX `{/* */}`. Jinja2 treats it as literal text and renders it into the HTML output, appearing as broken code to users. Use `{# comment #}`. This bit us when porting from a React/TSX mockup into the masthead macro.
- **Tile wrappers must NOT have category background colors.** `.s-intl`, `.s-pol`, `.s-sport` etc. set `background-color` to the full category hex. Per the mockup, tiles are `bg-transparent` with subtle borders — category colors belong on **text labels only** (via `k-*` classes inside card macros). If you see `{{ cat_c(art.category) }}` on a tile wrapper `<div>` in `home.html`, remove it.
- **Homepage grid cycle is 14 positions, not 15.** Article 0 is the lead story (rendered separately in `home.html` with hardcoded grid classes). Articles 1+ use `_compute_tile_sizes` with a 14-position repeating cycle: `(rank - 1) % 14`. The old `rank % 15` produced a duplicate lead layout at article 15, breaking the grid. Default `render_home_pages(limit=15)` gives 1 lead + 14 = one complete grid cycle with no orphaned partial rows.
- **Article dateline is viewer-local, IST is the fallback.** The article kicker renders `<time datetime="<UTC ISO>">` with server text from `publisher._format_datetime()` (UTC→IST). `web/static/js/localtime.js` (deferred, CSP-`self`) rewrites the visible text to the *viewer's* timezone via `Intl.DateTimeFormat`. `Article.published_at` is preserved from first publish; `Article.updated_at` advances only on material refreshes and is shown after a 5-minute grace window. Re-render existing pages with `scripts/rerender_articles.py` after touching the dateline.
- **Favicon assets are bundled + deployed, not auto-generated at runtime.** `favicon.svg` (editable source), `favicon.ico` (16/32/48) and `apple-touch-icon.png` live in `web/static/`; regenerate the rasters with `scripts/make_favicon.py` (Pillow + DejaVuSerif-Bold). All three are in the `cmd_deploy_static` pair list and `<link>`ed from `base.html`. The browser's auto `/favicon.ico` request means the favicon shows site-wide as soon as the file is in `STATIC_DIR`, even on pages not yet re-rendered with the `<link>` tags. Caddy already serves `*.ico`/`*.svg`/`*.png`.
- **Hero image search is driven by the writer's `image_query`, not keywords.** `pick_image(article_id, title, body, image_query, category)` builds its stock-photo query in priority order: the writer's literal `image_query` (a 3–6 word generic visual scene the EN writer emits on the same call as `category` — no proper nouns) → a per-category fallback from `imager.CATEGORY_QUERIES` → legacy frequency keywords. A relevance guard (`_is_relevant`) rejects any Unsplash/Pexels/Wikimedia result whose own caption/tags share zero content tokens with the query (it's lenient when there's nothing to judge), so you no longer get "result[0] for a bad query" (the old medal-for-cricket / bowling-for-football bug). Frequency-keyword search was the root cause and is now the last-resort path only. `publish_top_n` preserves the existing hero image if a republish pick returns None (guard transiently rejecting all). Backfill existing rows with `scripts/repick_images.py` (now category-aware; pass nothing extra — it reads `Article.category`).
- **Published clusters can receive updates but tombstones cannot.** `clusterer._candidate_cluster_stmt()` allows published clusters that have an Article row to keep attracting fresh source items, while sanity-sweep tombstones (`is_published=True` with no Article) stay excluded. `publish_top_n` may refresh an existing article after `REFRESH_DEBOUNCE_SECONDS=7200` when a newer raw item lands in its cluster; it preserves the original slug and `published_at`, updates `updated_at`, refreshes ArticleSource rows, and shows an "Updated" timestamp only after a 5-minute grace window. Existing articles still do not automatically get new writer/imager behavior unless they qualify for this material-refresh path or an explicit script (`rewrite_articles.py`, `repick_images.py`) is run.
