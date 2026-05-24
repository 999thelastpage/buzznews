# BuzzNews — Project Plan

> A multi-source news aggregation and synthesis site, English + Hindi, India-focused, running on a single 2GB / 2-core VPS. Plan is written for Claude Code to execute. Read this whole document before starting and ask the developer to clarify any ambiguity before deviating.

---

## How to use this document

- Implement **phases in order**. Each phase has explicit acceptance criteria; do not advance until they pass.
- All **locked decisions** in section 1 are final. Do not switch frameworks, databases, or libraries without asking the developer.
- The **algorithms in section 8** are canonical. Implement them as written. Tweak constants only after a phase passes acceptance.
- The **LLM prompts in section 9** are canonical too. Do not "improve" them without explicit approval.
- When you encounter ambiguity, **stop and ask the developer** rather than guessing. The developer prefers a clarifying question over a wrong assumption.
- After each phase, write a short progress note in `PROGRESS.md` (created at the repo root): what was done, what the acceptance run showed, what the developer should review.

---

## 0. Context and constraints

- **Goal**: A trending-news site that pulls from many sources, clusters items about the same event, scores them for genuine importance (not virality), synthesizes a short editorial summary per cluster using an LLM, and serves a fast static-rendered site in English and Hindi. Daily / weekly / monthly / yearly rollups are also generated.
- **Host**: Single Tencent Cloud Lighthouse VPS, Ubuntu 24.04 LTS, **1.9 GB RAM, 2 vCPU @ 2.0 GHz, 40 GB disk, 10 GB swap** (verify with `free -h` and `df -h` before starting). OpenClaw is already installed and resident; treat its memory footprint (~500 MB idle node + on-demand Chrome) as a fixed tenant.
- **Target traffic**: ~100 visitors/day in 6 months. No monetization until ~10k users.
- **Budget for external services**: USD 15–20/month total (LLM API + embeddings + backups + Tavily). No paid image generation.
- **Locales**: English and Hindi only.
- **Legal posture**: We *synthesize* from multiple sources and *link out*. We do not host any image we did not obtain a permissive license for. We follow Indian DPDP Act for any visitor tracking.
- **Developer profile**: 10+ years experience. Comfortable with Linux, Postgres, Python, systemd, Docker. No hand-holding needed for basics.
- **Co-tenant**: OpenClaw (`~/.openclaw/`) is a hard dependency, not an optional add-on. BuzzNews delegates four jobs to it: web search (Tavily), JS-heavy page extraction (browser), Tencent COS backups, and the ops chat bot. The Phase 8 work is configuration, not installation.

---

## 1. Tech stack — locked decisions

| Concern | Choice | Notes |
|---|---|---|
| OS | Ubuntu 24.04 LTS | apt-managed Postgres / Redis / Caddy |
| Language | Python 3.12 | One language for the whole stack |
| Web framework | FastAPI | With Uvicorn worker behind Caddy |
| Templates | Jinja2 | Server-rendered HTML |
| Frontend interactivity | HTMX + Alpine.js | No React, no build step |
| Database | PostgreSQL 16 + `pgvector` | Single source of truth |
| Cache | Redis 7 | `maxmemory 128mb`, `allkeys-lru` |
| Web server / TLS | Caddy 2 | Auto-HTTPS, reverse proxy |
| CDN | Cloudflare free tier | In front of Caddy |
| Background jobs | APScheduler (in-process) with `SQLAlchemyJobStore` on Postgres | Run inside a dedicated worker process; jobs survive restarts |
| Embeddings | Google Gemini `text-embedding-004` via `google-genai` | **768-dim, hosted** (no local model — see §0 RAM budget). Same API key as the LLM. Free tier covers our volume. |
| Clustering | pgvector ANN, incremental only | Attach-or-create against recent centroids. No HDBSCAN (too heavy + unnecessary at our volume). Periodic sanity sweep is SQL-based. |
| Dedup | `datasketch` MinHash LSH | Near-duplicate filter before clustering |
| Article extraction | `trafilatura` (primary) + OpenClaw browser skill (fallback for JS-heavy sites, env-gated, off by default) | newspaper3k is not used |
| Web search source | OpenClaw `openclaw-tavily-search` skill | Adds an "everything-on-the-web" channel beyond the curated RSS list |
| RSS parsing | `feedparser` | |
| HTTP client | `httpx` | Async |
| LLM | Google Gemini 2.0 Flash via `google-genai` | Primary. Anthropic Claude Haiku via `anthropic` as fallback. |
| Image sources | Unsplash, Pexels, Wikimedia Commons APIs | All free with attribution |
| Ops bot | **Existing** OpenClaw install at `~/.openclaw/` | Already running. We add workspace skills under `~/.openclaw/workspace/skills/`. No separate systemd unit. |
| Backups | OpenClaw `tencent-cos-skill` → Tencent COS bucket | Same data center as the VPS. No rclone, no B2. |
| Testing | `pytest` + `pytest-asyncio` | |
| Lint / format | `ruff` | Single config |
| Migrations | `alembic` | |
| Process supervision | `systemd` | One unit per long-running service |

**Hard "no"s** (do not introduce these without asking):

- No Node.js / npm in the **BuzzNews** runtime path. (OpenClaw already runs on Node 22; that is a separate, pre-existing process.)
- No Docker on the VPS. (Local dev is fine; deployment is bare metal + systemd.)
- No Celery / RabbitMQ. APScheduler is enough for our throughput.
- No client-side React framework. HTMX only.
- No paid image generation. No hosting of news-source images.
- No scraping behind paywalls or auth.
- No local ML models in the BuzzNews worker (no sentence-transformers, no PyTorch, no spaCy, no HDBSCAN). Every embedding / generation call is to a hosted API. This is a RAM constraint, not a preference.

---

## 2. VPS prerequisites

Run these as root once. Skip steps that are already done. **Verify free RAM with `free -h` first — if less than 700 MB is available, stop and investigate OpenClaw/Chrome footprint before installing Postgres.**

```bash
# Base — note: no rclone (we use Tencent COS via OpenClaw), no compile-heavy ML libs
apt-get update && apt-get -y upgrade
apt-get -y install build-essential git curl ufw \
  python3.12 python3.12-venv python3.12-dev \
  postgresql-16 postgresql-16-pgvector postgresql-contrib-16 \
  redis-server \
  caddy \
  ca-certificates rsync

# Firewall
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80
ufw allow 443
ufw --force enable

# Postgres: tune for 1.9GB box (tighter than the previous 2GB plan)
cat >> /etc/postgresql/16/main/postgresql.conf <<'EOF'
shared_buffers = 192MB
effective_cache_size = 768MB
work_mem = 6MB
maintenance_work_mem = 48MB
max_connections = 30
wal_compression = on
EOF
systemctl restart postgresql

# Redis: cap memory
sed -i 's/^# maxmemory <bytes>/maxmemory 96mb/' /etc/redis/redis.conf
sed -i 's/^# maxmemory-policy noeviction/maxmemory-policy allkeys-lru/' /etc/redis/redis.conf
systemctl restart redis-server

# App user
useradd -m -s /bin/bash buzz
mkdir -p /opt/buzz-news /var/log/buzz-news /var/lib/buzz-news/static
chown -R buzz:buzz /opt/buzz-news /var/log/buzz-news /var/lib/buzz-news

# Secrets hygiene (must be done before writing .env)
install -m 600 -o buzz -g buzz /dev/null /opt/buzz-news/.env
```

**OpenClaw is already installed** at `~/.openclaw/` (running as root). Workspace skills live in `~/.openclaw/workspace/skills/`. The Phase 8 work is configuration only.

Create the database:

```bash
sudo -u postgres psql <<'EOF'
CREATE USER buzz WITH PASSWORD 'CHANGE_ME_BEFORE_RUNNING';
CREATE DATABASE buzz_news OWNER buzz;
\c buzz_news
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- read-only role for OpenClaw
CREATE USER buzz_ro WITH PASSWORD 'CHANGE_ME_BEFORE_RUNNING_RO';
GRANT CONNECT ON DATABASE buzz_news TO buzz_ro;
GRANT USAGE ON SCHEMA public TO buzz_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO buzz_ro;
EOF
```

The two CHANGE_ME passwords must be set by the developer and stored in `.env`. Do not commit them.

---

## 3. Project layout

```
/opt/buzz-news/
├── pyproject.toml
├── README.md
├── PROGRESS.md           # you write this per phase
├── .env.example
├── .env                  # gitignored
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
├── src/
│   └── buzz_news/
│       ├── __init__.py
│       ├── config.py             # pydantic-settings, reads .env
│       ├── db.py                 # async engine, session factory
│       ├── models.py             # SQLAlchemy ORM
│       ├── sources/
│       │   ├── __init__.py
│       │   ├── base.py           # SourceAdapter protocol
│       │   ├── rss.py
│       │   ├── reddit.py
│       │   ├── hn.py
│       │   ├── gdelt.py
│       │   ├── tavily.py         # calls OpenClaw openclaw-tavily-search skill
│       │   └── catalog.yaml      # see section 5
│       ├── openclaw_client.py    # HTTP client for the OpenClaw gateway
│       ├── fetcher.py            # orchestrates one fetch cycle
│       ├── normalizer.py         # standardizes items, runs trafilatura (+ optional OpenClaw browser fallback)
│       ├── embedder.py           # Gemini text-embedding-004
│       ├── minhash.py            # near-dup detection
│       ├── clusterer.py          # attach to existing or create new (+ sanity sweep)
│       ├── scorer.py             # see section 8.1
│       ├── buzz.py               # spike detection + webhook
│       ├── writer.py             # LLM call to generate summaries (logs token usage)
│       ├── verifier.py           # named-entity verification (EN + HI paths)
│       ├── imager.py             # Unsplash/Pexels/Wikimedia
│       ├── publisher.py          # write article + render static page + Cloudflare purge
│       ├── rollups.py            # daily/weekly/monthly/yearly
│       ├── retention.py          # raw_items/cluster_scores/images cleanup
│       ├── cloudflare.py         # cache purge client
│       ├── scheduler.py          # APScheduler entry point (see §7.0)
│       ├── web/
│       │   ├── app.py            # FastAPI
│       │   ├── routes.py
│       │   ├── i18n.py           # EN/HI selection logic
│       │   ├── i18n/
│       │   │   ├── en.yaml       # UI chrome translations
│       │   │   └── hi.yaml
│       │   ├── templates/
│       │   │   ├── base.html     # includes OG + JSON-LD blocks
│       │   │   ├── home.html
│       │   │   ├── article.html
│       │   │   ├── category.html
│       │   │   ├── rollup.html
│       │   │   └── privacy.html
│       │   └── static/
│       │       ├── style.css
│       │       └── htmx.min.js
│       └── cli.py                # python -m buzz_news ...
├── tests/
│   ├── conftest.py
│   ├── test_fetcher.py
│   ├── test_clusterer.py
│   ├── test_scorer.py
│   ├── test_verifier.py
│   └── fixtures/
├── deploy/
│   ├── Caddyfile
│   ├── systemd/
│   │   ├── buzz-news-worker.service
│   │   └── buzz-news-web.service     # no openclaw.service — already externally supervised
│   ├── backup.sh
│   └── openclaw-skills/              # synced to ~/.openclaw/workspace/skills/
│       ├── buzznews_status/SKILL.md
│       ├── buzznews_recent_buzz/SKILL.md
│       ├── buzznews_pause_source/SKILL.md
│       ├── buzznews_restart_worker/SKILL.md
│       ├── buzznews_split_cluster/SKILL.md
│       └── buzznews_backup_now/SKILL.md
└── scripts/
    ├── seed_sources.py
    └── manual_fetch_once.py
```

---

## 4. Database schema

Save as `alembic/versions/0001_initial.py` (or hand-write the SQL into a first migration). All timestamps are `TIMESTAMPTZ`.

```sql
-- Extensions assumed present: vector, pg_trgm

CREATE TABLE sources (
  id           BIGSERIAL PRIMARY KEY,
  slug         TEXT UNIQUE NOT NULL,
  name         TEXT NOT NULL,
  url          TEXT NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN ('rss','reddit','hn','gdelt')),
  language     TEXT NOT NULL CHECK (language IN ('en','hi')),
  region       TEXT NOT NULL,                       -- 'IN' | 'GLOBAL' | 'US' | 'UK' | ...
  category     TEXT NOT NULL,                       -- 'general','international','sports','film','tech','business','politics','entertainment'
  authority    NUMERIC(3,2) NOT NULL DEFAULT 0.5,   -- 0.0..1.0
  is_tabloid   BOOLEAN NOT NULL DEFAULT FALSE,
  enabled      BOOLEAN NOT NULL DEFAULT TRUE,
  last_fetched_at  TIMESTAMPTZ,
  last_etag        TEXT,
  last_modified    TEXT,
  fail_count       INT NOT NULL DEFAULT 0,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE clusters (
  id                BIGSERIAL PRIMARY KEY,
  centroid          vector(768),
  first_seen_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  source_count      INT NOT NULL DEFAULT 0,
  distinct_sources  INT NOT NULL DEFAULT 0,
  authority_sum     NUMERIC NOT NULL DEFAULT 0,
  tabloid_count     INT NOT NULL DEFAULT 0,
  category          TEXT,
  region            TEXT,
  primary_language  TEXT,
  current_score     NUMERIC NOT NULL DEFAULT 0,
  is_published      BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX clusters_score_idx       ON clusters (current_score DESC) WHERE is_published;
CREATE INDEX clusters_last_seen_idx   ON clusters (last_seen_at DESC);

CREATE TABLE raw_items (
  id            BIGSERIAL PRIMARY KEY,
  source_id     BIGINT NOT NULL REFERENCES sources(id),
  external_id   TEXT NOT NULL,                      -- url or stable id
  url           TEXT NOT NULL,
  title         TEXT NOT NULL,
  snippet       TEXT,
  body          TEXT,                               -- post-trafilatura
  language      TEXT NOT NULL,
  published_at  TIMESTAMPTZ NOT NULL,
  fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  embedding     vector(768),
  minhash       BYTEA,                              -- serialized MinHash signature
  cluster_id    BIGINT REFERENCES clusters(id) ON DELETE SET NULL,
  UNIQUE (source_id, external_id)
);
CREATE INDEX raw_items_published_idx ON raw_items (published_at DESC);
CREATE INDEX raw_items_cluster_idx   ON raw_items (cluster_id);
CREATE INDEX raw_items_embed_hnsw    ON raw_items USING hnsw (embedding vector_cosine_ops);

CREATE TABLE cluster_scores (
  id                  BIGSERIAL PRIMARY KEY,
  cluster_id          BIGINT NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
  computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  source_diversity    NUMERIC NOT NULL,
  velocity            NUMERIC NOT NULL,
  authority           NUMERIC NOT NULL,
  time_decay          NUMERIC NOT NULL,
  anti_viral_penalty  NUMERIC NOT NULL,
  composite           NUMERIC NOT NULL
);
CREATE INDEX cluster_scores_cluster_time_idx ON cluster_scores (cluster_id, computed_at DESC);

CREATE TABLE articles (
  id                BIGSERIAL PRIMARY KEY,
  cluster_id        BIGINT NOT NULL UNIQUE REFERENCES clusters(id),
  slug              TEXT UNIQUE NOT NULL,
  title_en          TEXT NOT NULL,
  title_hi          TEXT,
  summary_en        TEXT NOT NULL,
  summary_hi        TEXT,
  hero_image_url    TEXT,
  hero_image_credit TEXT,
  category          TEXT NOT NULL,
  region            TEXT NOT NULL,
  published_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  verifier_passed   BOOLEAN NOT NULL DEFAULT FALSE,
  verifier_notes    JSONB
);
CREATE INDEX articles_published_idx ON articles (published_at DESC);
CREATE INDEX articles_category_idx  ON articles (category, published_at DESC);
CREATE INDEX articles_region_idx    ON articles (region, published_at DESC);

CREATE TABLE article_sources (
  id           BIGSERIAL PRIMARY KEY,
  article_id   BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  raw_item_id  BIGINT NOT NULL REFERENCES raw_items(id),
  source_name  TEXT NOT NULL,
  url          TEXT NOT NULL,
  rank         INT NOT NULL
);
CREATE INDEX article_sources_article_idx ON article_sources (article_id);

CREATE TABLE rollups (
  id           BIGSERIAL PRIMARY KEY,
  period       TEXT NOT NULL CHECK (period IN ('day','week','month','year')),
  start_at     TIMESTAMPTZ NOT NULL,
  end_at       TIMESTAMPTZ NOT NULL,
  category     TEXT,                       -- nullable = all categories
  region       TEXT,                       -- nullable = all regions
  article_ids  JSONB NOT NULL,             -- ordered list of article ids
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (period, start_at, end_at, category, region)
);

CREATE TABLE buzz_events (
  id             BIGSERIAL PRIMARY KEY,
  cluster_id     BIGINT NOT NULL REFERENCES clusters(id),
  fired_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  velocity       NUMERIC NOT NULL,
  distinct_authoritative INT NOT NULL,
  composite      NUMERIC NOT NULL,
  payload        JSONB,
  delivered      BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX buzz_events_fired_idx ON buzz_events (fired_at DESC);
```

**Retention policy** (enforced by the `retention-cleanup` job — see §7.9 and §11):

| Table / path | Keep | Reason |
|---|---|---|
| `raw_items` | 90 days from `fetched_at`; **never** delete rows referenced by `article_sources` | Cluster history + audit. Embeddings dominate row size (~3KB ea); 90d × 9.6k/day × 3KB ≈ 2.6 GB cap. |
| `cluster_scores` | 30 days from `computed_at` | History exists to debug velocity calcs; older rows have no operational value. |
| `buzz_events` | 365 days | Cheap, useful for tuning. |
| `clusters` with no `raw_items` (orphaned by retention) | Delete | Cleaned in the same job. |
| `static/images/<article_id>/` | While the article is referenced in any rollup OR `published_at > now - 365 days` | Avoid orphaned images. |
| `/var/log/buzz-news/*.log` | RotatingFileHandler 10 MB × 5 | Python logging handles it. |
| Backups in COS | 30 days hot, 90 days cold (lifecycle rule on the bucket) | See §9. |

All retention deletes use small batches (1000 rows) and run during off-peak (04:30 IST).

---

## 5. Source catalog

Save as `src/buzz_news/sources/catalog.yaml`. This is the **seed list**, not exhaustive. The developer may add or remove sources later. Verify every URL works before seeding — if a feed 404s or has moved, mark `enabled: false` and leave a comment.

```yaml
sources:
  # ---------- English / global wires ----------
  - slug: bbc_world
    name: BBC News — World
    url: https://feeds.bbci.co.uk/news/world/rss.xml
    kind: rss
    language: en
    region: GLOBAL
    category: international
    authority: 0.90
  - slug: bbc_business
    name: BBC News — Business
    url: https://feeds.bbci.co.uk/news/business/rss.xml
    kind: rss
    language: en
    region: GLOBAL
    category: business
    authority: 0.90
  - slug: bbc_tech
    name: BBC News — Technology
    url: https://feeds.bbci.co.uk/news/technology/rss.xml
    kind: rss
    language: en
    region: GLOBAL
    category: tech
    authority: 0.90
  - slug: reuters_world
    name: Reuters — World
    url: https://www.reuters.com/world/rss
    kind: rss
    language: en
    region: GLOBAL
    category: international
    authority: 0.95
  - slug: aljazeera_en
    name: Al Jazeera English
    url: https://www.aljazeera.com/xml/rss/all.xml
    kind: rss
    language: en
    region: GLOBAL
    category: international
    authority: 0.85
  - slug: ap_topnews
    name: Associated Press — Top News
    url: https://apnews.com/index.rss
    kind: rss
    language: en
    region: GLOBAL
    category: international
    authority: 0.95

  # ---------- India-focused English ----------
  - slug: thehindu_national
    name: The Hindu — National
    url: https://www.thehindu.com/news/national/feeder/default.rss
    kind: rss
    language: en
    region: IN
    category: politics
    authority: 0.85
  - slug: thehindu_business
    name: The Hindu — Business
    url: https://www.thehindu.com/business/feeder/default.rss
    kind: rss
    language: en
    region: IN
    category: business
    authority: 0.80
  - slug: thehindu_sport
    name: The Hindu — Sport
    url: https://www.thehindu.com/sport/feeder/default.rss
    kind: rss
    language: en
    region: IN
    category: sports
    authority: 0.80
  - slug: indianexpress_india
    name: Indian Express — India
    url: https://indianexpress.com/section/india/feed/
    kind: rss
    language: en
    region: IN
    category: politics
    authority: 0.80
  - slug: scroll_in
    name: Scroll.in
    url: https://scroll.in/feed
    kind: rss
    language: en
    region: IN
    category: general
    authority: 0.70
  - slug: ndtv_topstories
    name: NDTV — Top Stories
    url: https://feeds.feedburner.com/ndtvnews-top-stories
    kind: rss
    language: en
    region: IN
    category: general
    authority: 0.70

  # ---------- Hindi ----------
  - slug: bbc_hindi
    name: BBC Hindi
    url: https://feeds.bbci.co.uk/hindi/rss.xml
    kind: rss
    language: hi
    region: IN
    category: general
    authority: 0.90
  - slug: ndtv_hindi
    name: NDTV Hindi
    url: https://feeds.feedburner.com/ndtvkhabar-latest
    kind: rss
    language: hi
    region: IN
    category: general
    authority: 0.70
  - slug: aajtak_home
    name: Aaj Tak — Home
    url: https://www.aajtak.in/rssfeeds/?id=home
    kind: rss
    language: hi
    region: IN
    category: general
    authority: 0.60
    is_tabloid: true   # leans clickbait — keep but penalize in scorer
  - slug: jagran_news
    name: Dainik Jagran — News
    url: https://www.jagran.com/rss/news/national.xml
    kind: rss
    language: hi
    region: IN
    category: general
    authority: 0.65

  # ---------- APIs ----------
  - slug: hn_top
    name: Hacker News — Top
    url: https://hacker-news.firebaseio.com/v0/topstories.json
    kind: hn
    language: en
    region: GLOBAL
    category: tech
    authority: 0.70
  - slug: reddit_worldnews
    name: Reddit — r/worldnews
    url: https://www.reddit.com/r/worldnews/top.json?t=hour&limit=25
    kind: reddit
    language: en
    region: GLOBAL
    category: international
    authority: 0.40
  - slug: reddit_india
    name: Reddit — r/india
    url: https://www.reddit.com/r/india/top.json?t=hour&limit=25
    kind: reddit
    language: en
    region: IN
    category: general
    authority: 0.40

  # ---------- GDELT (international event signal) ----------
  - slug: gdelt_latest
    name: GDELT — Latest English
    url: https://api.gdeltproject.org/api/v2/doc/doc?query=sourcelang%3Aenglish&mode=ArtList&format=json&maxrecords=75&sort=DateDesc
    kind: gdelt
    language: en
    region: GLOBAL
    category: international
    authority: 0.60

  # ---------- Tavily (web-search source via OpenClaw skill) ----------
  # `url` is unused for kind=tavily; the adapter calls the OpenClaw
  # `openclaw-tavily-search` skill. `query` (in extra) drives the search.
  - slug: tavily_breaking_global
    name: Tavily — Breaking News (Global)
    url: openclaw://skill/openclaw-tavily-search
    kind: tavily
    language: en
    region: GLOBAL
    category: international
    authority: 0.55
    extra:
      query: "breaking news today"
      max_results: 20
      include_domains: []
      exclude_domains: ["pinterest.com", "youtube.com"]
      search_depth: "basic"
      cadence_minutes: 90       # 16 fires/day × 30 = 480/mo, fits free tier
  - slug: tavily_breaking_india
    name: Tavily — Breaking News (India)
    url: openclaw://skill/openclaw-tavily-search
    kind: tavily
    language: en
    region: IN
    category: general
    authority: 0.55
    extra:
      query: "India breaking news today"
      max_results: 20
      search_depth: "basic"
      cadence_minutes: 90
```

**Tavily quota**: Tavily free tier = 1000 searches/month. Two sources × 16 fires/day × 30 days ≈ **960/mo**, just under the cap. The adapter MUST respect `cadence_minutes` in `extra` and skip a fetch cycle if `last_fetched_at` is more recent than that. Add a daily Tavily-usage SQL check in the `llm_usage_rollup` job: if `COUNT(*) FROM raw_items WHERE source_id IN (tavily_*) AND fetched_at > now() - interval '1 day'` exceeds 35, warn via webhook.

---

## 6. Configuration

`.env.example` (copy to `.env` and fill in). **The real `.env` must be `chmod 600`, owned by `buzz`. Do not commit it.**

```
# Database
DATABASE_URL=postgresql+asyncpg://buzz:CHANGE_ME@localhost:5432/buzz_news
DATABASE_URL_RO=postgresql://buzz_ro:CHANGE_ME_RO@localhost:5432/buzz_news

# Redis
REDIS_URL=redis://localhost:6379/0

# LLM (primary) — same key powers embeddings via text-embedding-004
GEMINI_API_KEY=
GEMINI_MODEL_TEXT=gemini-2.0-flash
GEMINI_MODEL_EMBED=text-embedding-004
EMBED_DIM=768

# LLM (fallback)
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-haiku-4-5-20251001

# Image providers
UNSPLASH_ACCESS_KEY=
PEXELS_API_KEY=
# Wikimedia needs no key

# Reddit
REDDIT_USER_AGENT=buzz-news/0.1 (by /u/TODO_PRE_LAUNCH contact:TODO_PRE_LAUNCH@example.com)

# Tavily (free tier: 1000 searches/month — get a key at https://tavily.com)
# Used via OpenClaw skill; the adapter also accepts the key directly as a fallback.
TAVILY_API_KEY=TODO_BEFORE_PHASE_1

# OpenClaw bridge
OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789
OPENCLAW_BROWSER_FALLBACK_ENABLED=false   # set true only after a site demonstrably needs it

# Buzz alert webhook (Telegram, Discord, or OpenClaw)
BUZZ_WEBHOOK_URL=

# Cloudflare (used to purge CDN cache when an article is republished)
# Configured during pre-launch — see §13. Until then keep PURGE_ENABLED=false.
CLOUDFLARE_ZONE_ID=TODO_PRE_LAUNCH
CLOUDFLARE_API_TOKEN=TODO_PRE_LAUNCH    # token with "Zone.Cache Purge" permission only
CLOUDFLARE_PURGE_ENABLED=false

# Tencent COS (for backups; used by the OpenClaw tencent-cos-skill)
# Configured during pre-launch — see §13.
TENCENT_COS_BUCKET=TODO_PRE_LAUNCH
TENCENT_COS_REGION=TODO_PRE_LAUNCH
# Credentials live in OpenClaw's skill config, not here.

# Site (configured pre-launch)
SITE_BASE_URL=https://TODO_PRE_LAUNCH
SITE_HOST=TODO_PRE_LAUNCH
STATIC_DIR=/var/lib/buzz-news/static
LOG_DIR=/var/log/buzz-news
TZ=Asia/Kolkata

# Scoring constants (overrideable)
SCORE_TIME_GRAVITY=1.5
SCORE_DIVERSITY_CAP=8
BUZZ_VELOCITY_THRESHOLD=0.4
BUZZ_MIN_AUTHORITATIVE=3

# Pipeline cadence (minutes) — see §7.0 APScheduler job table
FETCH_INTERVAL_MIN=15
SCORE_INTERVAL_MIN=5
PUBLISH_INTERVAL_MIN=30
TOP_N_PER_CYCLE=10

# Retention (days)
RETENTION_RAW_ITEMS_DAYS=90
RETENTION_CLUSTER_SCORES_DAYS=30
RETENTION_BUZZ_EVENTS_DAYS=365
RETENTION_IMAGES_DAYS=365
```

---

## 7. Implementation phases

Each phase is sequential. After every phase, write a `PROGRESS.md` entry, run the acceptance commands, and stop for the developer to review.

### 7.0 — APScheduler job table (canonical)

The `run-worker` process owns all of these. **Jobstore: `SQLAlchemyJobStore` against the BuzzNews Postgres database** (so jobs survive worker restarts without missing fires). `coalesce=True`, `misfire_grace_time=300`, `max_instances=1` per job. All times are IST (`TZ=Asia/Kolkata`).

| Job ID | Trigger | Cadence | Module / function | Notes |
|---|---|---|---|---|
| `fetch` | interval | every `FETCH_INTERVAL_MIN` (15 m) | `fetcher.run_once` | Tavily sources self-skip via `cadence_minutes` |
| `embed` | interval | every 5 m | `embedder.embed_pending` | Picks up `raw_items` with `embedding IS NULL` |
| `cluster` | interval | every 5 m, offset +1 m from `embed` | `clusterer.run_once` | Operates on embedded-but-unclustered rows |
| `score` | interval | every `SCORE_INTERVAL_MIN` (5 m) | `scorer.score_all_recent` | Also runs `buzz.detect_and_fire` after |
| `publish` | interval | every `PUBLISH_INTERVAL_MIN` (30 m) | `publisher.publish_top_n` | EN + HI + image + verify + CF purge |
| `cluster_sanity_sweep` | cron | hourly @ :15 | `clusterer.sanity_sweep` | SQL-based, replaces HDBSCAN |
| `imager_backfill` | cron | hourly @ :40 | `imager.backfill_missing` | For articles with `hero_image_url IS NULL` |
| `rollup_daily` | cron | 00:30 IST daily | `rollups.build_daily(yesterday)` | Then regenerates `sitemap.xml` |
| `rollup_weekly` | cron | Mon 01:00 IST | `rollups.build_weekly(prev_week)` | |
| `rollup_monthly` | cron | 1st 02:00 IST | `rollups.build_monthly(prev_month)` | |
| `rollup_yearly` | cron | Jan 1 03:00 IST | `rollups.build_yearly(prev_year)` | |
| `backup` | cron | 03:30 IST daily | shells out to `deploy/backup.sh` | Healthcheck ping on success |
| `retention_cleanup` | cron | 04:30 IST daily | `retention.cleanup_all` | Honors `RETENTION_*_DAYS` env |
| `llm_usage_rollup` | cron | 23:55 IST daily | `writer.aggregate_daily_usage` | Sends warning to buzz webhook if > $0.40 |

Misfire policy: if the worker is down when a job should fire, on restart APScheduler runs the latest missed fire (coalesced). For the rollup jobs that's the intended behavior; for `fetch`/`score`/`publish` it means a single catch-up cycle on restart.

### Phase 0 — Bootstrap

**Goal**: Project scaffolding compiles, lints, and connects to a database.

**Tasks**:
1. Initialize Python project with `pyproject.toml`. Use `setuptools` or `hatch`. Pin Python to `>=3.12`.
2. Add runtime dependencies: `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]`, `asyncpg`, `psycopg2-binary`, `alembic`, `redis`, `httpx`, `feedparser`, `trafilatura`, `pydantic-settings`, `python-dotenv`, `apscheduler`, `jinja2`, `datasketch`, `numpy`, `google-genai`, `anthropic`, `pyyaml`, `python-slugify`, `feedgen`, `pgvector`, `langid`, `tenacity`, `Pillow`, `slowapi` (FastAPI rate limiting).
   - **Removed vs the original plan**: `sentence-transformers`, `hdbscan`, `scipy`, `scikit-learn` — no local ML.
3. Add dev dependencies: `ruff`, `pytest`, `pytest-asyncio`, `pytest-cov`, `httpx[cli]`, `respx` (httpx mocking).
4. Create `src/buzz_news/config.py` using `pydantic-settings` that reads from `.env`. Settings include every `EMBED_*`, `RETENTION_*`, `OPENCLAW_*`, and `CLOUDFLARE_*` var from §6.
5. Create `src/buzz_news/db.py` with an async SQLAlchemy engine and session factory.
6. Set up `alembic` pointing at `src/buzz_news/models.py`. Write `models.py` ORM that matches section 4 schema exactly (note 768-dim vectors).
7. Write the first migration that creates all tables from section 4.
8. Add a CLI entry point `python -m buzz_news` (using `argparse` is fine) with the **full** subcommand list:
   - **Setup**: `migrate`, `seed-sources`, `preflight` (validates `.env` against the §13 checklist)
   - **Pipeline single-shot**: `fetch-once`, `embed-once`, `cluster-once`, `score-once`, `write-once`, `publish-once`, `republish-today`
   - **Maintenance**: `rollup` (with `--period day|week|month|year` and `--date`), `retention-cleanup`, `backfill-rollups`, `split-cluster <id>` (operator escape hatch — accepts a list of `raw_item` IDs to detach into a new cluster)
   - **Long-running**: `run-worker`, `run-web`
   - Every subcommand exits non-zero on failure with a single-line error and a stacktrace in `LOG_DIR`.

**Acceptance**:
- `ruff check src tests` passes.
- `pytest -q` runs (empty test pass is OK).
- `alembic upgrade head` succeeds on a fresh DB and creates every table from section 4.
- `python -m buzz_news --help` lists every subcommand.

---

### Phase 1 — Fetcher + source seeding

**Goal**: We can pull items from all configured sources into `raw_items` without duplication.

**Tasks**:
1. Write `src/buzz_news/sources/base.py` defining a `SourceAdapter` protocol:
   ```python
   class SourceAdapter(Protocol):
       async def fetch(self, source: Source, http: httpx.AsyncClient) -> list[RawCandidate]: ...
   ```
   `RawCandidate` is a dataclass with: `external_id`, `url`, `title`, `snippet`, `published_at`, `language` (default from source).
2. Implement adapters:
   - `rss.py`: feedparser-based. Honor `etag` / `last-modified` from prior fetch.
   - `reddit.py`: Reddit JSON endpoints with the configured user agent. Treat each post as a candidate; use `permalink` as external_id. Note: Reddit has tightened anonymous JSON access since 2023; back off aggressively on 429/403 and surface the error in `fail_count`.
   - `hn.py`: Firebase API. Pull top story IDs, then resolve each to a story.
   - `gdelt.py`: GDELT doc API. Use the `url` and `seendate` fields.
   - `tavily.py`: calls the OpenClaw skill `openclaw-tavily-search` via the OpenClaw gateway HTTP endpoint (`OPENCLAW_GATEWAY_URL`). Maps results into `RawCandidate`s using `url`, `title`, `content` snippet, `published_date`. Honors `cadence_minutes` from `source.extra` and short-circuits if too soon since `last_fetched_at`.
3. Write `src/buzz_news/scripts/seed_sources.py` that reads `catalog.yaml` and upserts rows in `sources`. The `extra` YAML field maps to a JSONB column — add `extra JSONB NOT NULL DEFAULT '{}'::jsonb` to the `sources` table in a follow-up migration if not already present.
4. Write `src/buzz_news/normalizer.py`:
   - Given a `RawCandidate` and a `Source`, fetch the article URL via `httpx` (timeout 10s, follow redirects, sane User-Agent).
   - Run `trafilatura.extract()` to pull the main body.
   - **Fallback chain** when extraction fails or returns < 200 chars:
     1. If `OPENCLAW_BROWSER_FALLBACK_ENABLED=true` **and** `source.extra.js_heavy=true`, POST to the OpenClaw gateway invoking `agent-browser-clawdbot` to render and extract. 15 s timeout. If it succeeds, store the result and continue.
     2. Otherwise, fall back to the candidate's `snippet`. Mark `body=NULL`.
   - Detect language with `langid` (loaded once at module import — it's pure Python, ~5 MB RAM). Trust the source's declared language if `langid` confidence < 0.6.
5. Write `src/buzz_news/fetcher.py` with an async `run_once()` that:
   - Loads all enabled sources.
   - Concurrently fetches each source (cap concurrency at 10).
   - For each new candidate (not already present by `source_id, external_id`), normalizes and inserts a `raw_items` row with `body` and `embedding=NULL` (embedding happens in Phase 2).
   - Increments `fail_count` and disables a source if it fails 5 times consecutively (re-enable manually).

**Acceptance**:
- `python -m buzz_news seed-sources` populates the `sources` table from the catalog (including both `tavily_*` entries).
- `python -m buzz_news fetch-once` fetches successfully from at least 80% of enabled non-Tavily sources. Tavily sources may be skipped if their cadence hasn't elapsed.
- At least 100 rows land in `raw_items` from one cycle.
- Re-running `fetch-once` immediately does **not** create duplicate rows.
- For at least 70% of items, `body` is populated and >= 200 chars.
- A unit test confirms each adapter parses a stored fixture correctly (use `respx` to mock httpx, including the OpenClaw gateway for the Tavily adapter).
- With `OPENCLAW_BROWSER_FALLBACK_ENABLED=false` (default), the normalizer never calls OpenClaw. Verified by a test that asserts zero httpx calls to `OPENCLAW_GATEWAY_URL`.

---

### Phase 2 — Embeddings, dedup, clustering

**Goal**: New items are embedded via the Gemini API, near-duplicates are filtered, remaining items attach to an existing cluster or form a new one.

**Tasks**:
1. `src/buzz_news/embedder.py`:
   - Async function `embed_batch(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> np.ndarray` that calls Gemini `text-embedding-004` via `google-genai`. Returns a `(N, 768)` array, L2-normalized.
   - Batch up to 100 texts per API call. The free tier caps at ~1500 req/min — bursts that exceed should back off with `tenacity` (exponential, max 5 tries).
   - For queries (rare — we mostly embed documents), use `task_type="RETRIEVAL_QUERY"`.
   - **Cache hot-path**: keep an in-memory LRU (`functools.lru_cache(maxsize=2000)`) keyed on `sha1(text)` to avoid re-embedding the same string within a cycle. Do not persist this cache.
2. `src/buzz_news/minhash.py`:
   - Use `datasketch.MinHash` with 128 permutations and 4-gram word shingles.
   - LSH index with Jaccard threshold 0.85 — items within threshold are treated as near-duplicates and one is kept (prefer higher-authority source). MinHash runs **before** embedding, so duplicates skip the API call.
3. CLI command `embed-once`: for every `raw_items` row where `embedding IS NULL` and `body IS NOT NULL OR snippet IS NOT NULL`, computes and stores the embedding (input = `title + ". " + (body[:1000] or snippet)`). Batched in groups of 100.
4. `src/buzz_news/clusterer.py`:
   - Operates on `raw_items` where `embedding IS NOT NULL AND cluster_id IS NULL`.
   - For each such item:
     1. Run MinHash against last 24 h of items. If a near-dup is found, attach the new item to the same cluster as the dup, increment cluster counters, return.
     2. Otherwise query pgvector for the nearest cluster centroid within `cosine_distance < 0.25` (i.e. similarity > 0.75 — slightly looser than the original 0.22 since Gemini embeddings are calibrated differently from e5), limited to clusters whose `last_seen_at > now - 48h`.
     3. If a neighbor is found, attach the item, update `centroid` as EMA (α=0.2), update `last_seen_at`, recompute `category` / `region` by majority vote across attached items.
     4. If no neighbor, create a new cluster with this item's embedding as centroid.
   - After attachment, recompute: `source_count`, `distinct_sources`, `authority_sum`, `tabloid_count`.
5. **SQL-based sanity sweep** (hourly job, replaces the original HDBSCAN re-cluster):
   - Find pairs of clusters with cosine similarity between centroids > 0.92 and overlapping `last_seen_at` windows. Merge the smaller into the larger (re-point `raw_items.cluster_id`, recompute counters, delete the empty cluster).
   - Limit to 20 merges per run to keep transactions small. Log each merge.

**Acceptance**:
- After Phase 1 has ingested ~500 items, `python -m buzz_news embed-once && python -m buzz_news cluster-once` produces fewer than 250 clusters.
- Manual spot-check: pick 5 clusters at random; in each, all attached items must plausibly be about the same event.
- A unit test embeds two paraphrased headlines via the real Gemini API (or a fixture-backed mock) and asserts cosine > 0.80.
- A unit test confirms MinHash flags an obvious copy as a duplicate before any Gemini call is made (assert zero outbound httpx calls).
- pgvector ANN query against 10k embeddings returns in < 100 ms locally.
- 24-hour embedding spend (from the Gemini console) is **$0.00** (free tier should cover us). If non-zero, investigate.

---

### Phase 3 — Scoring + buzz detection

**Goal**: Every cluster has an up-to-date composite score, and sudden spikes fire a webhook.

**Tasks**:
1. Implement `src/buzz_news/scorer.py` exactly per section 8.1.
2. Provide `score_all_recent(window_hours=48) -> None` that:
   - For each cluster whose `last_seen_at > now - window_hours`, computes the score and **inserts a row in `cluster_scores`** (history), then updates `clusters.current_score`.
3. Implement `src/buzz_news/buzz.py`:
   - For each cluster, compare current cycle's signal vs the prior cycle's signal (read from `cluster_scores`).
   - Fire a buzz event if **all** of: `velocity > BUZZ_VELOCITY_THRESHOLD` (default 0.4), `distinct_authoritative >= BUZZ_MIN_AUTHORITATIVE` (default 3, where "authoritative" = source authority ≥ 0.8), and **no prior buzz_event for this cluster in the last 6 hours**.
   - Persist to `buzz_events` and POST to `BUZZ_WEBHOOK_URL`. Retry once on failure; mark `delivered=True` only after a 2xx.
4. Webhook payload schema:
   ```json
   {
     "cluster_id": 123,
     "fired_at": "2026-05-23T11:32:00+05:30",
     "headline_guess": "<best raw_item title>",
     "sources": [{"name":"Reuters","url":"..."}],
     "velocity": 0.62,
     "distinct_authoritative": 5,
     "composite": 0.84,
     "category": "international",
     "region": "GLOBAL"
   }
   ```

**Acceptance**:
- `python -m buzz_news score-once` updates `current_score` for every recent cluster and writes a `cluster_scores` row.
- Top 10 clusters by `current_score` look intuitively important when reviewed by a human.
- A simulated spike (inject 5 new raw_items into a cluster within a single cycle) produces exactly one `buzz_events` row and one webhook POST.
- A second injection 5 minutes later does **not** re-fire (6h cooldown).

---

### Phase 4 — LLM writer + verifier

**Goal**: Each selected cluster becomes a structured EN + HI summary that passes a factuality check.

**Tasks**:
1. `src/buzz_news/writer.py`:
   - Function `write_article(cluster_id) -> ArticleDraft`.
   - Pull up to 6 best raw_items for the cluster (highest source authority, dedupe by source_id, prefer body over snippet).
   - Build the sources block per the prompt in section 9.
   - Call Gemini 2.0 Flash via `google-genai`. Request JSON output. Set `temperature=0.3`, `max_output_tokens=900`.
   - On any failure (timeout, JSON parse error, schema mismatch), retry once. If still failing, fall back to Claude Haiku via `anthropic`.
   - Generate **both** EN and HI in **separate calls** (do not ask the model to do both in one call — quality drops).
2. `src/buzz_news/verifier.py`:
   - **English path** — entity extraction via the regex in §8.3. Build a normalized token bag from the cluster's source titles + bodies. For each entity in the EN body, check it appears in the source bag (case-insensitive substring). The EN article passes if **at most 1** entity is unverified.
   - **Hindi path** — the capitalization regex doesn't work on Devanagari, so:
     1. The EN entity set (extracted above) is the canonical "expected entities" for this cluster.
     2. Check that each EN entity (or a transliterated form, if `indic-transliteration` is available — optional) appears at least once in either the HI body or the EN body that produced it. In practice, names like "Modi" / "मोदी" usually both appear; a single match in either form is enough.
     3. Additionally, scan the HI body for **English-script capitalized tokens** matching the §8.3 regex. Any such token must also appear in the cluster's source corpus. This catches the failure mode "Gemini hallucinates an English name into the Hindi summary."
     4. HI passes if EN passed AND the English-token check finds no unverified token.
   - Record `verifier_passed` and `verifier_notes` (JSONB: `{en_unverified: [...], hi_unverified: [...]}`) on the article row.
3. Articles that fail verification are saved with `verifier_passed=false` and **not** published. Log the cluster_id; the developer reviews manually.
4. **Token usage logging** — every LLM call writes a single structured log line: `LLM_USAGE provider=gemini model=... prompt_tokens=... completion_tokens=... cluster_id=... lang=en|hi`. The retention-cleanup job aggregates daily totals and a soft cap of $0.40/day triggers a warning via the buzz webhook.

**Acceptance**:
- Given 10 clusters, the writer produces 10 EN and 10 HI summaries totaling within token budget.
- Word count of every generated summary is between 120 and 280 words.
- No summary contains a quoted phrase longer than 8 words. (Heuristic: longest token run inside double quotes ≤ 8.)
- Verifier flags at least one obvious hallucination in a deliberately-broken test cluster.
- Total LLM cost over a 24h dry run stays under USD 0.50.

---

### Phase 5 — Image picker + publisher

**Goal**: Articles get a hero image (or a typographic card) and become published rows that the site can render.

**Tasks**:
1. `src/buzz_news/imager.py`:
   - Extract 2–3 likely topic keywords from the article title using simple frequency analysis (stopwords removed).
   - Query Unsplash → Pexels → Wikimedia Commons in that order. Take the first result with the right aspect ratio (16:9 or close).
   - Download to `STATIC_DIR/images/<article_id>/{hero,card,thumb}.webp` using Pillow to resize. Three sizes: hero 1200x675, card 600x338, thumb 240x135.
   - Store `hero_image_url` (the local served path) and `hero_image_credit` (e.g. "Photo by X on Unsplash") on the article row.
   - If all three providers fail, set `hero_image_url=NULL`. The template renders a typographic card in that case.
   - **Do not** download images from news sites.
2. `src/buzz_news/publisher.py`:
   - Function `publish_top_n(n=10)` that:
     1. Selects top `n` clusters by `current_score` that have `is_published=false` OR an existing article with `updated_at` older than 2 hours.
     2. For each, calls `write_article` (EN), `write_article` (HI), `verify_article` (both), then `imager.pick_image`.
     3. Inserts/updates `articles` and `article_sources`. Sets `is_published=true` on the cluster.
     4. Re-renders the static pages whose content changed (home, affected category, affected region, the article page itself).
     5. **Cloudflare cache purge** — if `CLOUDFLARE_PURGE_ENABLED=true`, POST to the CF API to purge the specific URLs that were re-rendered (not a full-zone purge). Use the API token with only `Zone.Cache Purge` scope. On failure, log and continue (purge will catch up on the next publish cycle).
   - Slug rule: `slugify(title_en) + "-" + cluster_id` to keep URLs stable across edits. If `title_en` is missing (HI-only article — see Phase 6 fallback rule), use `slugify(title_hi)`.
3. Static page rendering: write Jinja2 templates and a `render_static(template_name, context, out_path)` helper. Output to `STATIC_DIR`. Each language is a separate file (`/en/article/<slug>.html`, `/hi/article/<slug>.html`).
4. **Image cleanup hook** — when the retention-cleanup job decides an article's image is no longer needed (§4 retention policy), it deletes the `static/images/<article_id>/` directory and clears `hero_image_url`. Articles older than `RETENTION_IMAGES_DAYS` get re-rendered with the typographic card.

**Acceptance**:
- A full pipeline run (fetch → cluster → score → publish 10) produces 10 article rows, 10 hero images on disk, and 30+ rendered static files (home/category/region pages plus per-article pages).
- Every published article has `verifier_passed=true`.
- The site root `STATIC_DIR/en/index.html` lists the 10 articles ordered by score, with thumbnails.
- The Hindi mirror `STATIC_DIR/hi/index.html` exists and renders correctly.

---

### Phase 6 — Web layer + Caddy

**Goal**: The site is publicly accessible over HTTPS with region-aware content selection.

**Tasks**:
1. `src/buzz_news/web/app.py` — FastAPI:
   - `GET /` → 302 to `/{lang}/` based on `Accept-Language` and Cloudflare's `CF-IPCountry`.
   - `GET /{lang}/` → serve `STATIC_DIR/{lang}/index.html`.
   - `GET /{lang}/article/{slug}` → serve the static file.
     - **Language fallback rule**: when `/hi/article/<slug>.html` does not exist but `/en/article/<slug>.html` does, the static rendering step writes a stub HI page that renders the EN content with a small banner: "हिन्दी संस्करण उपलब्ध नहीं — English version below." This avoids 404s while making the gap visible.
   - `GET /{lang}/category/{cat}` → serve the static category index.
   - `GET /{lang}/archive/{period}/{date}` → daily/weekly/monthly/yearly rollup. `{date}` format: `YYYY-MM-DD` (day), `YYYY-Www` (week), `YYYY-MM` (month), `YYYY` (year).
   - `GET /api/healthz` → `{ "status": "ok", "lag_minutes": <last fetch age> }`. **Bound to 127.0.0.1 only** at the Caddy layer; not exposed publicly.
   - `GET /api/buzz/recent` → last 20 buzz events (used by OpenClaw). Localhost-only, same as above.
   - Set cache headers: `Cache-Control: public, max-age=300, stale-while-revalidate=600` on HTML pages.
   - Language preference written to a cookie `lang=en|hi` so the user override persists.
   - **Rate limiting**: use `slowapi` middleware (60 req/min per IP, applied to all `/api/*`). Replaces the original "Caddy rate_limit plugin" plan since that plugin isn't in the default Caddy build.
   - **Templates** include OpenGraph (`og:title`, `og:description`, `og:image`, `og:locale`) and JSON-LD `NewsArticle` structured data on every article page. Article cards on the home/category pages emit `og:image` for the thumbnail variant.
2. Region detection helper `i18n.py`:
   - Prefer cookie if set.
   - Else `CF-IPCountry`: `IN` → Hindi-default; else English-default. (**Confirm with developer** — see §13 Q8.)
   - Else `Accept-Language` parsing.
   - Static UI labels (category names, "Trending Now", "Daily Roundup", etc.) live in `src/buzz_news/web/i18n/{en,hi}.yaml`. No LLM call for chrome translations.
3. `deploy/Caddyfile`:
   ```
   {$SITE_HOST} {
       encode zstd gzip
       root * /var/lib/buzz-news/static
       @static path *.css *.js *.webp *.png *.svg *.ico
       header @static Cache-Control "public, max-age=2592000, immutable"
       header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
       header X-Content-Type-Options "nosniff"
       header Referrer-Policy "strict-origin-when-cross-origin"
       header Content-Security-Policy "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; script-src 'self'"
       handle /api/healthz /api/buzz/* {
           # Localhost-only endpoints: deny external traffic.
           # Cloudflare may probe healthz from its edge; allow CF IP ranges if needed.
           respond "Not Found" 404
       }
       handle /api/* {
           reverse_proxy 127.0.0.1:8000
       }
       handle {
           try_files {path} {path}/index.html
           file_server
       }
       log {
           output file /var/log/caddy/access.log
       }
   }
   ```
4. Put Cloudflare in front, proxied (orange cloud). Turn on "Always Use HTTPS" and "Auto Minify: off" (we already minimize output). Configure a Page Rule or Cache Rule: HTML pages → "Cache Everything", Edge TTL 5 minutes. The Phase 5 cache-purge step keeps stale pages from outliving an article update.

**Acceptance**:
- `https://<your-domain>/` resolves and returns within 300 ms cached, 800 ms cold.
- Setting `lang=hi` cookie shows the Hindi home.
- A Lighthouse mobile run scores ≥ 90 on Performance and Best Practices.
- `curl localhost:8000/api/healthz` returns `lag_minutes` < 20 right after a fetch cycle.
- `curl https://<host>/api/healthz` returns 404 (endpoint is internal-only).
- View source of any article page shows OpenGraph + JSON-LD `NewsArticle` blocks.
- For an EN article whose HI version failed verification, `/hi/article/<slug>` renders the EN body with the fallback banner (not a 404).

---

### Phase 7 — Rollups (daily / weekly / monthly / yearly)

**Goal**: Aggregated views for each time window.

**Tasks**:
1. `src/buzz_news/rollups.py`:
   - `build_daily(date)`: from articles `published_at` in `[date, date+1)`, rank by max `current_score` seen during that window. Top 30 per `(category, region)` combo and one "All" view. Persist to `rollups`.
   - `build_weekly(start_monday)`: from daily rollups in that week, score = sum of daily scores divided by sqrt(days_present). Top 50 per combo.
   - `build_monthly(year, month)`: similar over the month, top 75.
   - `build_yearly(year)`: similar over the year, top 100.
2. Render static rollup pages at `STATIC_DIR/{lang}/archive/{period}/{date}.html` where `{date}` matches the route format in Phase 6 (`YYYY-MM-DD` / `YYYY-Www` / `YYYY-MM` / `YYYY`).
3. Schedule — see the canonical APScheduler job table in §7.0. Rollup cadence summary:
   - Daily: 00:30 IST for previous day.
   - Weekly: Monday 01:00 IST for previous week.
   - Monthly: 1st of month 02:00 IST for previous month.
   - Yearly: Jan 1 03:00 IST for previous year.

**Acceptance**:
- `python -m buzz_news backfill-rollups --days 7` produces rollups for the last 7 days from existing data.
- `/en/archive/day/2026-05-22` renders a clean list of that day's top articles.
- Weekly rollup row exists for the last completed week.
- Rollup pages include the same OpenGraph + JSON-LD blocks as article pages (ItemList schema).

---

### Phase 8 — OpenClaw integration + buzz delivery

**Goal**: Operate the system from Telegram or Discord. Receive buzz alerts the same way. Reuse OpenClaw for browser-based extraction, Tavily web search, and COS backups.

**OpenClaw is already running** at `~/.openclaw/` as root. We do **not** install it, supervise it, or move it to the `buzz` user — that risks breaking the existing setup. No `deploy/systemd/openclaw.service` is shipped.

**Tasks**:
1. Confirm OpenClaw's existing `~/.openclaw/openclaw.json` has:
   - A model configured (Gemini Flash is fine).
   - `dmPolicy: "pairing"` for every channel.
   - The gateway listening on `127.0.0.1:18789` (or note the actual port in `OPENCLAW_GATEWAY_URL`).
2. Add custom skills under `~/.openclaw/workspace/skills/`:
   - `buzznews_status/SKILL.md` — queries `localhost:8000/api/healthz` and runs read-only SQL via the `buzz_ro` user; reports last fetch time, last publish time, total articles today, recent buzz events.
   - `buzznews_recent_buzz/SKILL.md` — lists the last N buzz events.
   - `buzznews_pause_source/SKILL.md` — sets `sources.enabled=false` for a given slug via the read-only role plus a narrowly-scoped writable function (`pause_source(slug)`) granted to `buzz_ro`. **Do not** give `buzz_ro` broader write access.
   - `buzznews_restart_worker/SKILL.md` — runs `sudo systemctl restart buzz-news-worker` (sudoers entry on the `buzz` user limited to that one command; the skill is invoked from a process that can `sudo -u buzz`).
   - `buzznews_split_cluster/SKILL.md` — operator escape hatch for Gotcha #12: takes a cluster ID and a list of `raw_item` IDs, calls `python -m buzz_news split-cluster <id> --items <comma,list>`.
   - `buzznews_backup_now/SKILL.md` — invokes the on-demand backup flow (pg_dump → COS upload via `tencent-cos-skill`).
3. **IPC pattern between BuzzNews ↔ OpenClaw**: BuzzNews calls OpenClaw's gateway over plain HTTP on `127.0.0.1:18789` (no auth needed for localhost). Reverse direction — OpenClaw → BuzzNews — uses `localhost:8000/api/*` endpoints. Both bind to localhost only; nothing on this bridge is exposed externally.
4. Connect OpenClaw to **Telegram** via a bot token (developer's choice — confirmed). Discord can be added later as a second channel. Pair only the developer's own Telegram account; `dmPolicy: "pairing"`.
5. Buzz delivery: set `BUZZ_WEBHOOK_URL` to OpenClaw's gateway webhook, formatted such that OpenClaw posts a structured message: `🚨 {headline_guess} — score {composite}, picked up by {distinct_authoritative} authoritative sources in {category}/{region}`. The webhook payload schema in §7 (Phase 3) is unchanged; the formatting happens inside OpenClaw.

**Acceptance**:
- From Telegram/Discord, "pipeline status" returns a non-empty response with last fetch time and counts.
- A simulated buzz spike (re-run Phase 3 acceptance test) results in a message in the same channel within 30 seconds.
- `buzznews_split_cluster` successfully detaches 2 items from a cluster into a new one; verify by SQL.
- OpenClaw's RSS as measured by `ps -o rss -p <node-pid>` does not increase by more than 50 MB after the BuzzNews skills are added (the skills are small markdown + a few SQL queries).

---

### Phase 9 — Backups + hardening

**Goal**: Production-ready operations.

**Tasks**:
1. `deploy/backup.sh` (called by the APScheduler `backup` job, not by system cron — so that retention/log handling stays inside the worker):
   ```bash
   #!/bin/bash
   set -euo pipefail
   STAMP=$(date -u +%Y%m%d-%H%M%S)
   DEST=/var/backups/buzz-news
   mkdir -p $DEST
   sudo -u postgres pg_dump --format=custom buzz_news \
       > $DEST/db-$STAMP.pgcustom
   tar -C /var/lib/buzz-news -czf $DEST/images-$STAMP.tgz static/images
   # Upload via OpenClaw's tencent-cos-skill
   curl -fsS -X POST "$OPENCLAW_GATEWAY_URL/skills/tencent-cos-skill/upload" \
       -H "Content-Type: application/json" \
       -d "{\"local_path\": \"$DEST/db-$STAMP.pgcustom\", \"bucket\": \"$TENCENT_COS_BUCKET\", \"key\": \"db/$STAMP.pgcustom\"}"
   curl -fsS -X POST "$OPENCLAW_GATEWAY_URL/skills/tencent-cos-skill/upload" \
       -H "Content-Type: application/json" \
       -d "{\"local_path\": \"$DEST/images-$STAMP.tgz\", \"bucket\": \"$TENCENT_COS_BUCKET\", \"key\": \"images/$STAMP.tgz\"}"
   find $DEST -type f -mtime +7 -delete   # local copy: 7 days
   ```
   - The COS bucket itself has a lifecycle rule (30 days hot → 90 days cold → delete). Configure once via the Tencent console.
2. Schedule: APScheduler job `backup` at 03:30 IST (see §7.0 table). Healthcheck ping at the end (e.g. healthchecks.io free tier) so silent failures get noticed.
3. Privacy + compliance:
   - Add `/privacy` static page mentioning DPDP Act compliance, what data is collected (none beyond Cloudflare's standard logs), and how to contact for removal.
   - Add a small cookie banner only if/when client-side analytics is added — for now, no analytics, no banner needed.
4. `robots.txt` and `sitemap.xml`:
   - `robots.txt` allows everything but `/api/`.
   - `sitemap.xml` regenerated by the `rollup_daily` job (right after it finishes — same data dependency), listing every published article URL plus rollup pages.
5. Web hardening:
   - Rate limit `/api/*` to 60 req/min per IP via FastAPI middleware (`slowapi`). Cloudflare's free WAF is the second layer. (The original "Caddy rate_limit plugin" path is dropped — not in default Caddy build.)
   - HSTS, CSP, X-Content-Type-Options, Referrer-Policy set in the Caddyfile (see Phase 6).
6. Secrets:
   - `.env` is `chmod 600` owned by `buzz` (already set up in §2).
   - `CLOUDFLARE_API_TOKEN` is scoped to `Zone.Cache Purge` only.
   - OpenClaw's `~/.openclaw/openclaw.json` keys are out of scope of this repo.
7. Logging:
   - Every Python service logs to `/var/log/buzz-news/<service>.log` via Python's logging module (RotatingFileHandler, 10 MB × 5).
   - `journalctl -u buzz-news-worker` for systemd-level events.
8. **Retention enforcement** — the `retention-cleanup` APScheduler job applies the policy in §4 (raw_items, cluster_scores, buzz_events, images). Runs daily 04:30 IST. Batches of 1000 rows. Single-line summary log at the end.

**Acceptance**:
- `bash deploy/backup.sh` produces a backup, uploads to COS via OpenClaw, and verifies the COS object size > 0.
- Restoring from the most recent backup into a fresh empty DB succeeds (test in a throwaway env — e.g. a second database on the same VPS).
- `curl -I https://<host>/` includes HSTS, CSP, X-Content-Type-Options, Referrer-Policy.
- A burst of 100 requests in 10 s to `/api/` (any public endpoint) from one IP gets rate-limited (429).
- `retention-cleanup` deletes the expected rows in a dry-run test where we pre-populate 95-day-old `raw_items`.
- `ls -l /opt/buzz-news/.env` shows mode `-rw-------` owned by `buzz`.

---

## 8. Canonical algorithms

### 8.1 Trending scorer

Implement in `src/buzz_news/scorer.py`. Constants come from `.env` (see section 6).

```python
from datetime import datetime, timezone
from dataclasses import dataclass
from math import pow

@dataclass
class ScoreBreakdown:
    source_diversity: float
    velocity: float
    authority: float
    time_decay: float
    anti_viral_penalty: float
    composite: float

def compute_score(
    *,
    distinct_sources: int,
    new_sources_this_cycle: int,
    source_count: int,
    authority_sum: float,
    tabloid_count: int,
    category: str,
    last_seen_at: datetime,
    now: datetime,
    diversity_cap: int = 8,
    time_gravity: float = 1.5,
) -> ScoreBreakdown:
    # 1. Source diversity (0..1), capped to prevent runaway.
    diversity = min(distinct_sources, diversity_cap) / diversity_cap

    # 2. Average authority of attached sources (0..1).
    authority = (authority_sum / source_count) if source_count else 0.0

    # 3. Velocity: fraction of sources that joined in this cycle.
    velocity = (new_sources_this_cycle / source_count) if source_count else 0.0

    # 4. Time decay (HN-style, longer half-life for our use).
    age_hours = max((now - last_seen_at).total_seconds() / 3600.0, 0.0)
    time_decay = 1.0 / pow(age_hours + 2.0, time_gravity)

    # 5. Anti-viral penalty.
    # If a non-entertainment cluster is mostly tabloid-sourced, dampen it hard.
    anti_viral = 1.0
    if category != "entertainment" and source_count:
        tabloid_ratio = tabloid_count / source_count
        if tabloid_ratio > 0.7:
            anti_viral = 0.3
        elif tabloid_ratio > 0.4:
            anti_viral = 0.7

    composite = diversity * authority * (1.0 + velocity) * time_decay * anti_viral

    return ScoreBreakdown(
        source_diversity=diversity,
        velocity=velocity,
        authority=authority,
        time_decay=time_decay,
        anti_viral_penalty=anti_viral,
        composite=composite,
    )
```

**Tuning notes**:
- `time_gravity=1.5` is gentler than HN's 1.8 because news cycles run longer than HN's tech-story cycle.
- `diversity_cap=8` means a cluster picked up by 8+ distinct sources gets full credit on that axis.
- `velocity` is multiplicative as `1 + velocity` so it amplifies recent activity without dominating.
- All thresholds and constants are overrideable via `.env`. Do not hardcode tweaks; expose them.

### 8.2 Clusterer

Implementation is the prose in Phase 2 task 4. The key constants:

- pgvector nearest-neighbor cosine-distance threshold for attachment: **0.25** (cosine similarity > 0.75). Slightly looser than the original 0.22 because Gemini `text-embedding-004` has different similarity calibration than the original e5 model.
- Recent-window for attachment search: **last 48 hours**.
- Centroid update: exponential moving average with α=0.2 (`centroid = 0.8 * old + 0.2 * new_embedding`). Re-normalize to unit length after update.
- Sanity-sweep merge threshold: centroid-pair cosine similarity > **0.92** triggers a merge candidate. Max 20 merges per sweep.
- No HDBSCAN, no scikit-learn. The previous plan's batch re-cluster is replaced by the SQL sanity sweep — see Phase 2 task 5.

### 8.3 Verifier

```python
import re
from collections.abc import Iterable

# Conservative proper-noun regex: 1+ capitalized words, no leading "The/A/An".
_PROPER_NOUN = re.compile(r"\b(?!The\b|A\b|An\b|This\b|That\b|These\b|Those\b)([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,4})\b")
_STOPWORDS = {"The", "A", "An", "It", "He", "She", "They", "We", "I", "And", "Or", "But", "Reuters", "BBC", "AP", "PTI"}

def extract_entities(text: str) -> set[str]:
    found = set()
    for m in _PROPER_NOUN.finditer(text):
        ent = m.group(1).strip()
        if ent not in _STOPWORDS and len(ent) >= 3:
            found.add(ent)
    return found

def verify(article_body: str, source_corpus: str, max_unverified: int = 1) -> tuple[bool, list[str]]:
    entities = extract_entities(article_body)
    corpus_lc = source_corpus.lower()
    unverified = [e for e in entities if e.lower() not in corpus_lc]
    return (len(unverified) <= max_unverified, unverified)
```

The verifier is intentionally cheap. It catches the most embarrassing class of hallucination (made-up names) without a heavy NER model. If it lets through more than 5% problematic articles in practice, escalate to a hosted NER API (not local spaCy — we have no RAM for it) in a follow-up phase.

**Hindi handling**: this regex matches Latin-script capitalized tokens only. For Hindi summaries, treat the EN entity set extracted from the same cluster as the canonical "expected names." The HI summary passes if:

1. The EN summary passed verification.
2. Every Latin-script capitalized token in the HI body is in the cluster's source corpus (catches hallucinated English names slipped into the Hindi text).

Devanagari-script name verification is deferred to a follow-up phase; it requires either a transliteration library or a hosted NER endpoint.

---

## 9. LLM prompts (canonical)

### 9.1 Writer — English

```
You are an editorial summarizer for a news aggregation site. Your job is to synthesize a short editorial summary from multiple sources covering the same event, in English.

STRICT RULES:
- Output strictly valid JSON: {"title": string, "body": string}
- The body must be 150–250 words
- Synthesize across sources; do not copy any source verbatim
- No quoted phrases longer than 8 words
- Attribute claims inline: "Reuters reports...", "according to BBC..."
- Use a neutral journalistic tone
- Do not invent facts. If sources disagree, note the disagreement
- Do not include opinions, predictions, or editorial commentary
- If sources mention a clear next step or upcoming event, you may end with a single "What's next:" sentence
- Title: 6–12 words, sentence case, no clickbait

SOURCES:
{sources_block}

Output JSON only. No prose before or after.
```

`sources_block` format per source:

```
[Source: {source_name} | Authority: {authority} | Published: {iso8601}]
Title: {title}
Body: {body or snippet, truncated to 800 chars}
URL: {url}
---
```

### 9.2 Writer — Hindi

Identical to 9.1 with these differences:

- Replace "in English" with "in Hindi (हिन्दी)".
- Add: "Use natural Hindi journalistic register. Avoid heavy Sanskritized vocabulary; aim for the style of BBC Hindi or The Wire Hindi."
- Title length guidance: 6–14 words (Hindi runs slightly shorter per word).

Pass the **same** `sources_block` (in original languages). The model handles cross-lingual synthesis well.

---

## 10. Deployment artifacts

### 10.1 systemd units

`/etc/systemd/system/buzz-news-worker.service`:

```ini
[Unit]
Description=BuzzNews background worker
After=network.target postgresql.service redis-server.service
Requires=postgresql.service redis-server.service

[Service]
Type=simple
User=buzz
Group=buzz
WorkingDirectory=/opt/buzz-news
EnvironmentFile=/opt/buzz-news/.env
ExecStart=/opt/buzz-news/.venv/bin/python -m buzz_news run-worker
Restart=always
RestartSec=5
StandardOutput=append:/var/log/buzz-news/worker.log
StandardError=append:/var/log/buzz-news/worker.err.log

# Resource caps (no local ML model — much lower than the original plan)
MemoryMax=350M
CPUQuota=150%

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/buzz-news-web.service`:

```ini
[Unit]
Description=BuzzNews web (FastAPI/Uvicorn)
After=network.target postgresql.service redis-server.service

[Service]
Type=simple
User=buzz
Group=buzz
WorkingDirectory=/opt/buzz-news
EnvironmentFile=/opt/buzz-news/.env
ExecStart=/opt/buzz-news/.venv/bin/uvicorn buzz_news.web.app:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=5
StandardOutput=append:/var/log/buzz-news/web.log
StandardError=append:/var/log/buzz-news/web.err.log

MemoryMax=200M
CPUQuota=80%

[Install]
WantedBy=multi-user.target
```

**No `openclaw.service`** — OpenClaw is already running as root, supervised externally. Do not duplicate the unit.

**Revised memory budget** (1.9 GB box):

| Tenant | Cap | Steady-state expected |
|---|---|---|
| OpenClaw (node, idle) | n/a (external) | ~500 MB |
| OpenClaw browser (when active) | n/a (external) | +500 MB peak, ~0 when idle |
| BuzzNews worker | 350 MB | ~250 MB |
| BuzzNews web | 200 MB | ~120 MB |
| Postgres shared_buffers + connections | — | ~280 MB |
| Redis | 96 MB | ~30 MB |
| Caddy + system | — | ~100 MB |
| **Total typical** | | **~1.3 GB** |
| **Total worst-case (browser active)** | | **~1.8 GB** |

Headroom: ~100 MB. The 10 GB swap is the safety net for browser-active windows. If we still OOM, the first lever is to set `OPENCLAW_BROWSER_FALLBACK_ENABLED=false` and never call the browser path.

---

## 11. Operations runbook

**Common commands** (run as `buzz` user where possible):

```bash
# Check the pipeline (note: openclaw is NOT in this list — it's externally supervised)
systemctl status buzz-news-worker buzz-news-web
journalctl -u buzz-news-worker -f --since "30 minutes ago"
tail -F /var/log/buzz-news/worker.log

# Manual one-off pipeline (full cycle)
sudo -u buzz /opt/buzz-news/.venv/bin/python -m buzz_news fetch-once
sudo -u buzz /opt/buzz-news/.venv/bin/python -m buzz_news embed-once
sudo -u buzz /opt/buzz-news/.venv/bin/python -m buzz_news cluster-once
sudo -u buzz /opt/buzz-news/.venv/bin/python -m buzz_news score-once
sudo -u buzz /opt/buzz-news/.venv/bin/python -m buzz_news publish-once

# Check DB lag
psql -U buzz -d buzz_news -c "SELECT MAX(fetched_at) FROM raw_items;"
psql -U buzz -d buzz_news -c "SELECT COUNT(*) FROM articles WHERE published_at::date = CURRENT_DATE;"

# Pause a misbehaving source (or via OpenClaw skill: buzznews_pause_source)
psql -U buzz -d buzz_news -c "UPDATE sources SET enabled=false WHERE slug='aajtak_home';"

# Operator escape hatch: split a bad cluster
sudo -u buzz /opt/buzz-news/.venv/bin/python -m buzz_news split-cluster 12345 --items 678,679,680

# Force rebuild static pages for everything published today
sudo -u buzz /opt/buzz-news/.venv/bin/python -m buzz_news republish-today

# Backup on demand (also available via OpenClaw skill: buzznews_backup_now)
bash /opt/buzz-news/deploy/backup.sh

# Retention cleanup on demand
sudo -u buzz /opt/buzz-news/.venv/bin/python -m buzz_news retention-cleanup

# Check OpenClaw bridge from BuzzNews side
curl -fsS http://127.0.0.1:18789/health
```

**Pipeline lag SLO**: the site is "lagging" if `max(raw_items.fetched_at)` is older than 30 minutes. Healthz endpoint reports this; OpenClaw should be configured to alert if lag > 45 min for 15+ minutes.

**Memory pressure signals**: kswapd active in `top`, swap usage > 200MB. If you hit this, the first lever is to lower `MemoryMax` on the worker and force smaller batch sizes in the embedder.

---

## 12. Known gotchas (read before coding)

1. **Gemini embedding task_type**: pass `task_type="RETRIEVAL_DOCUMENT"` for stored items and `"RETRIEVAL_QUERY"` for live similarity lookups. Mixing them silently degrades similarity scores.
2. **pgvector HNSW**: index must be built after a few hundred rows exist; building on an empty table picks bad parameters. Run `REINDEX` after the first 1k rows. Note vectors are 768-dim now, not 384.
3. **Reddit rate limits**: anonymous JSON access has tightened post-2023. Even with a polite User-Agent you can get 403s for "blocked country" or rate-limited at low thresholds. Back off aggressively and accept that Reddit may contribute fewer items than the RSS feeds.
4. **GDELT URL changes**: GDELT occasionally updates query parameters. If `kind=gdelt` adapter starts returning 0 results, re-check `https://api.gdeltproject.org/api/v2/doc/`.
5. **Trafilatura on JS-heavy sites**: it does not run JavaScript. About 10–15% of sites give garbage extraction. **Do not** install Playwright in the BuzzNews venv — call the existing OpenClaw browser skill instead (env-gated, off by default). See Phase 1 task 4 and Phase 8.
6. **Gemini JSON output**: even with strict instructions, occasionally returns a stray `}` or text outside the JSON block. Use a tolerant parser (strip leading/trailing non-JSON, then `json.loads`). On `google-genai`, prefer `response_mime_type="application/json"` plus a response schema where supported.
7. **LLM cost runaway**: log `prompt_tokens` and `completion_tokens` per call (see Phase 4 task 4). The `llm_usage_rollup` job sends a webhook warning at $0.40/day. Free tier of Gemini 2.0 Flash plus `text-embedding-004` should cover us indefinitely at v1 volume.
8. **DPDP Act**: even with no analytics, Cloudflare IP logging counts as processing. Mention it in the privacy page. Provide a contact email for deletion requests.
9. **Hindi rendering**: ensure the base template uses a font stack including a Devanagari-capable system font; e.g. `font-family: -apple-system, "Noto Sans Devanagari", "Segoe UI", sans-serif;`. Without this, Devanagari can render with mixed weights on some platforms.
10. **Image copyright**: never download from news sources. Watermarks don't grant a license. The picker should silently fall back to a typographic card if no permissive image is found.
11. **OpenClaw security**: never set `dmPolicy="open"`. The gateway listens on localhost only; do not bind it to a public IP. The BuzzNews ↔ OpenClaw bridge is unauthenticated because both ends are loopback-only.
12. **Cluster splits**: occasionally two distinct events get glued together when one mentions the other. The `split-cluster` CLI and matching OpenClaw skill exist as an operator escape hatch — Phase 2 does not solve splits automatically.
13. **Time zones**: store every timestamp as UTC in the DB. Convert to `Asia/Kolkata` only in templates and rollup boundaries.
14. **OpenClaw is a tenant, not a child process**: BuzzNews does not start, stop, or restart OpenClaw. If OpenClaw needs to restart, that happens out-of-band; BuzzNews features that depend on it (Tavily, browser, COS, buzz delivery) degrade gracefully and log.
15. **Tavily quota**: free tier is 1000 searches/month. The two seeded sources at 90-min cadence consume ~960/mo — within budget but close. The `llm_usage_rollup` job warns if daily Tavily fetches exceed 35. If we ever upgrade to a paid plan, also bump `cadence_minutes` down in `catalog.yaml`.
16. **APScheduler job persistence**: if you change a job's trigger in code, APScheduler does **not** automatically reconcile against the SQLAlchemyJobStore. The worker startup code must explicitly call `add_job(..., replace_existing=True)` for every job. Otherwise old triggers persist forever.
17. **Cloudflare cache purge token scope**: the API token must have `Zone.Cache Purge` permission only. A broader token in `.env` is a much bigger blast radius if compromised.

---

## 13. Pre-launch configuration checklist

These items don't block Phases 0–7 of development. They MUST be filled in before flipping the live `SITE_HOST` to Cloudflare. Every item below has a `TODO_PRE_LAUNCH` (or `TODO_BEFORE_PHASE_1`) placeholder in `.env.example`. None of them require code changes — just config.

### Resolved decisions

| # | Decision | Resolution |
|---|---|---|
| 3 | Chat channel | **Telegram first.** Discord can be added later. |
| 5 | Categories | Use the list in §4 as-is: `general, international, sports, film, tech, business, politics, entertainment`. |
| 6 | Hindi tone | "BBC Hindi / The Wire Hindi" style as the prompt says. |
| 7 | Image attribution | Visible on the card AND detail page (small caption under the hero). |
| 8 | Region default | Indian visitor → Hindi default unless cookie says otherwise. |
| 9 | Tavily cadence | 90 min × 2 sources (~960/mo, fits free tier). Adapter enforces. |
| 10 | Browser fallback | `OPENCLAW_BROWSER_FALLBACK_ENABLED=false` for v1. Enable per-source as needed. |

### Deferred to pre-launch

Each of these needs a one-time action before going live. Group them into a single pre-launch session.

| # | Item | What's needed | Blocks |
|---|---|---|---|
| 1a | **Domain name** | Pick hostname; set `SITE_HOST`, `SITE_BASE_URL` in `.env`. | Phase 6 deploy (not Phase 6 dev — local testing works without it). |
| 1b | **Cloudflare zone** | Create zone, point DNS, orange-cloud. Get `CLOUDFLARE_ZONE_ID`. | Phase 6 deploy. |
| 1c | **Cloudflare API token** | Create token with `Zone.Cache Purge` only. Set `CLOUDFLARE_API_TOKEN`, flip `CLOUDFLARE_PURGE_ENABLED=true`. | Phase 5 cache purge (graceful degrade if missing). |
| 2 | **Contact email** | Pick one address. Substitute in: Reddit UA, privacy page, OpenClaw pairing notice. | Phase 1 (Reddit adapter only — fetcher works with the placeholder UA, Reddit may downrank it). |
| 4a | **Tencent COS bucket** | Create bucket. Configure lifecycle rule: 30d hot → 90d cold → delete. Set `TENCENT_COS_BUCKET`, `TENCENT_COS_REGION`. | Phase 9 backups (graceful degrade if missing). |
| 4b | **OpenClaw `tencent-cos-skill` auth** | Confirm OpenClaw already has Tencent credentials configured; if not, populate. | Phase 9 backups. |
| 5 | **Tavily API key** | Sign up at tavily.com (free, 1000/mo). Set `TAVILY_API_KEY`. Confirm OpenClaw's `openclaw-tavily-search` skill is using the same key OR adjust. | Phase 1 Tavily adapter (graceful degrade — other adapters still work). |
| 11 | **Telegram bot** | Create via @BotFather. Get bot token. Configure in OpenClaw `~/.openclaw/openclaw.json`. Set `BUZZ_WEBHOOK_URL` to point at OpenClaw's gateway. | Phase 8 buzz delivery. |
| 12 | **OpenClaw ↔ buzz user bridge** | As `buzz` user: `curl -fsS http://127.0.0.1:18789/health`. Should return 200. If not, investigate (likely OpenClaw bound to a different address or firewall rule). | Phase 1 Tavily adapter + Phase 8. |

**Verification command** (run after all items are set):

```bash
sudo -u buzz bash -c '
  set -e
  test "$SITE_HOST" != "TODO_PRE_LAUNCH" || (echo "SITE_HOST not set"; exit 1)
  test -n "$TAVILY_API_KEY" -a "$TAVILY_API_KEY" != "TODO_BEFORE_PHASE_1" || (echo "TAVILY_API_KEY missing"; exit 1)
  test "$CLOUDFLARE_ZONE_ID" != "TODO_PRE_LAUNCH" || (echo "CLOUDFLARE_ZONE_ID not set"; exit 1)
  test "$TENCENT_COS_BUCKET" != "TODO_PRE_LAUNCH" || (echo "TENCENT_COS_BUCKET not set"; exit 1)
  curl -fsS http://127.0.0.1:18789/health > /dev/null || (echo "OpenClaw gateway unreachable"; exit 1)
  echo "Pre-launch config OK"
'
```

This check is also a CLI command: `python -m buzz_news preflight`. It runs automatically at `run-worker` and `run-web` startup and exits non-zero on missing critical config (`SITE_HOST`, `GEMINI_API_KEY`, `DATABASE_URL`). Non-critical missing items (`CLOUDFLARE_*`, `TENCENT_COS_*`) log a warning but do not abort.

---

## 14. Definition of done (v1)

- Every phase passes its acceptance criteria.
- A continuous 7-day run produces, daily, at least 30 published articles (EN+HI combined) with `verifier_passed=true`.
- Memory steady-state under 1.6GB. No OOM kills over the 7-day window.
- 99th-percentile static-page response time under 800ms cold, 200ms warm via Cloudflare.
- LLM spend under USD 12/month at this volume.
- Developer can ask "pipeline status" in Telegram/Discord and get a coherent answer.
- A buzz event for a real-world breaking story is delivered within 15 minutes of the breaking story showing up across 3+ authoritative sources.

When all of the above hold for 7 consecutive days, declare v1 shipped and write a brief retro in `PROGRESS.md` listing what to revisit in v2 (cluster splits, more sources, regional dialect coverage, monetization hooks).
