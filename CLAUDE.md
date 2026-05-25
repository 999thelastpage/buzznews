# BuzzNews — Working Agreement

You are implementing BuzzNews on the VPS this file lives on. The canonical specification is **`/home/ubuntu/buzznews/PROJECT_PLAN.md`** (~1450 lines). Read it end-to-end before any tool call. This file is the rules of engagement; the plan is the spec.

## Non-negotiable rules

1. **Phases are sequential.** Do not start phase N+1 until phase N's acceptance criteria pass on this VPS (not on a laptop, not in a mock). The acceptance criteria are concrete and testable for a reason.
2. **Stop after each phase.** Write a `PROGRESS.md` entry at the repo root (what was done, what the acceptance run showed, anything to review). Then wait for the developer.
3. **Locked decisions in §1 are final.** Do not swap frameworks, libraries, models, or schema without asking first. If you're tempted to "improve" something locked, you've misread the plan — re-read §1.
4. **Ambiguity → stop and ask.** The developer prefers one clarifying question over an undoing-a-wrong-assumption session. Default to asking.
5. **One commit per passing phase.** Message prefix: `phase-N: <summary>`.

## Hard "no"s (these trip up fresh models)

- **No local ML models.** Embeddings go to **Gemini `gemini-embedding-001`** over the API (768-dim via `output_dimensionality`). No `sentence-transformers`, no PyTorch, no HDBSCAN, no scikit-learn, no spaCy. This is a 1.9 GB RAM constraint, not a preference. If you find yourself reaching for any of these, you're solving the wrong problem.
- **No Docker on the VPS.** Bare metal + systemd. (Local dev is fine.)
- **No Node.js in the BuzzNews runtime path.** OpenClaw is the only Node process and it's already running.
- **No client-side React.** HTMX + Alpine.js, server-rendered Jinja2.
- **No paid image generation, no hosting of news-source images.**

## Writer LLM chain (current, 2026-05-25)

Original spec called for Gemini 2.5 Flash as primary. That has been **superseded**: the live chain in `writer.py:write_article` is now **DeepSeek → Gemini → Anthropic**.

- **Primary**: DeepSeek `deepseek-v4-flash` via OpenAI-compat endpoint `https://api.deepseek.com/v1/chat/completions` (called via `httpx`, no extra SDK).
- **Fallback 1**: Gemini `gemini-2.5-flash` — currently 429-ing because the AI Studio project spend cap is hit. Until the cap is raised at https://ai.studio/spend, every call straight-through falls to Anthropic.
- **Fallback 2**: Anthropic Claude Haiku 4.5.
- Embeddings are on Gemini **`gemini-embedding-001`** (paid GA, ~$0.15/1M input tokens) via `embedder.py`. `text-embedding-004` was the original free-tier choice but Google **removed it from the API entirely on 2026-05-25** (404 NOT_FOUND on every batch). Do **not** swap to `gemini-embedding-2` — same family but more expensive. The embedder passes `output_dimensionality=768` to keep the existing pgvector column shape.

`.env` keys: `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL=deepseek-v4-flash`, `DEEPSEEK_BASE_URL=https://api.deepseek.com`.

## Host facts (verify, don't assume)

- Tencent Lighthouse VPS, Ubuntu 24.04, **1.9 GB RAM, 2 vCPU, 40 GB disk, 10 GB swap**.
- **OpenClaw is already running** at `~/.openclaw/` as root, on `127.0.0.1:18789`. Treat it as a fixed external tenant:
  - Do **not** install, start, stop, restart, or supervise it.
  - Do **not** create a `deploy/systemd/openclaw.service`. If the plan you remember from training shows one, you're working from a stale version — check the actual file.
  - BuzzNews ↔ OpenClaw IPC is plain HTTP on `127.0.0.1:18789` (loopback only, no auth).
- Memory budget (steady-state): ~1.3 GB. Worst case with OpenClaw browser active: ~1.8 GB. Swap is the safety net, not a free-RAM extension.
- Before installing anything heavy, run `free -h` and `df -h`. If less than 700 MB available, stop and investigate.

## Existing OpenClaw skills BuzzNews uses

Already installed at `~/.openclaw/workspace/skills/` and `~/.openclaw/plugin-skills/`:

- `openclaw-tavily-search` — web-search source (used as a `kind: tavily` adapter)
- `agent-browser-clawdbot` + `browser-automation` — Chrome fallback for JS-heavy page extraction (env-gated, **off by default**)
- `github`, `tencentcloud-lighthouse-skill`, etc. — available but not in BuzzNews scope

BuzzNews adds its own skills under the same directory (see Phase 8). Custom skill names are prefixed `buzznews_*`.

## Pre-launch placeholders

Several `.env` values are intentionally `TODO_PRE_LAUNCH` / `TODO_BEFORE_PHASE_1`. See plan §13 for the checklist. **Build and locally test against placeholders; do not block on real keys.** The `preflight` CLI command validates the `.env` at startup — critical missing values abort, non-critical ones warn.

Concretely, you can develop and test all of Phases 0–7 with placeholder values. Phase 8 (Telegram delivery) is the only one that needs real keys to pass acceptance. (Phase 9 used to call for Tencent COS automated backups; that's been dropped — Anjali handles DB backups manually.)

## Conventions

- **Working directory**: `/home/ubuntu/buzznews/` (actual deploy, decided in 2026-05-25 session). The original spec called for `/opt/buzz-news/` but the developer chose to defer that migration; systemd units and Caddyfile already target the home-dir path. If a future session needs to migrate, see `PROGRESS.md` for the rewrite checklist.
- **App user**: `ubuntu` (not `buzz` per original spec). Run pipeline commands as `sudo -u ubuntu /home/ubuntu/buzznews/.venv/bin/python -m buzz_news <cmd>` or simply `.venv/bin/python -m buzz_news <cmd>` from the repo root.
- **OpenClaw port**: actually `127.0.0.1:19262` (not 18789 per the original spec). Set in `OPENCLAW_GATEWAY_URL`. The basePath is `/oi8dhw`.
- **`.env` ownership**: must be `ubuntu:ubuntu 600`. After any `sed -i` edit run as root, re-`sudo chown ubuntu:ubuntu .env` or both services will crash with PermissionError on next restart.
- **Timestamps**: UTC in the DB. Convert to `Asia/Kolkata` only in templates and rollup boundaries.
- **Logs**: `/var/log/buzz-news/<service>.log`, RotatingFileHandler (10 MB × 5).
- **Secrets**: `.env` is `chmod 600` owned by `buzz`. Never commit it. Never echo its contents in tool output.
- **Tests**: `pytest` + `pytest-asyncio` + `respx` for httpx mocking. Use stored fixtures, not live API calls, in unit tests.
- **PROGRESS.md** format per phase entry:

  ```
  ## Phase N — <name> (YYYY-MM-DD)
  Done:
    - bullet
  Acceptance:
    - criterion 1: PASS — <evidence>
    - criterion 2: PASS — <evidence>
  Notes for review:
    - anything surprising or worth a second look
  ```

## Template & grid architecture (2026-05-25)

- **Jinja2 comments only** — never use JSX `{/* */}` in `.html` templates. Jinja2 treats it as literal text and renders it into the page. Use `{# comment #}`.
- **Tile wrappers are transparent** — do NOT apply `.s-intl`, `.s-pol`, etc. (category background-color classes) to grid tile `<div>` wrappers. Per the mockup, tiles have `bg-transparent` with subtle borders. Category colors belong on **text labels only** (via `k-*` classes inside card macros). If you see `{{ cat_c(art.category) }}` on a tile wrapper, remove it.
- **Homepage grid cycle is 14 positions** — article 0 is the lead (rendered separately in `home.html` with hardcoded classes). Articles 1+ use `_compute_tile_sizes` with `(rank - 1) % 14`. The old `rank % 15` produced a duplicate lead layout at article 15. Default limit is **15** (1 lead + 14 cards = one complete cycle) to avoid orphaned partial rows.

## Archive structure (2026-05-26)

Two tiers only — `today` and `monthly`. Weekly + yearly were dropped (never linked, confusing). Daily-per-date snapshots are no longer written; existing `archive/day/2026-05-24.html` is kept for SEO continuity.

- **Today**: `/{lang}/archive/today.html`. Static file regenerated each `publish-once` cycle via `publisher.render_today_pages()`. IST day window (not UTC) so boundaries match the editorial calendar. **No `verifier_passed` filter** — verifier is over-strict (94% rejection on grammar / sentence-starters). The home page already drops the filter; the archive matches.
- **Monthly**: `/{lang}/archive/month/YYYY-MM.html`. Static file regenerated **hourly** via `monthly_archive` interval job, and once more at 00:30 IST on the 1st of each month via `previous_month_finalize` cron. Sorted by `published_at DESC`, capped at `_MONTH_TOP_LIMIT=500`. IST calendar boundaries. Drops the old per-category overwrite bug — single "all" file per month.
- **Search**: `/api/search?q=&lang=` is a live FastAPI route (10/min ratelimit). Returns a full HTML results page with `<meta name="robots" content="noindex">`. Hybrid: Postgres FTS (`ts_rank_cd` on `articles.search_vector`) + pgvector cosine (`embedding <=> qvec`), weighted 0.4 / 0.6. **Cost-capped** via `search_query_cache` (each unique query embeds exactly once forever) + daily budget guard (`MAX_DAILY_EMBEDS=500` ≈ $0.075/day; over budget → FTS-only). The search box on archive pages is a plain form GET (no JS).
- **IST helpers** live in `publisher.py`: `_ist_day_window()` returns `(start_utc, end_utc, ist_date_str, ist_month_str)`; `_archive_windows(current, lang, labels, today_str, month_str)` builds the 2-window nav passed to `render_windows`. **Reuse these** instead of re-deriving IST math.
- **CLI**: `python -m buzz_news today-archive` and `python -m buzz_news monthly-archive [--month YYYY-MM]`. The old `rollup` and `backfill-rollups` subcommands are gone.

## When you're unsure

- About a library choice → re-read §1, then ask.
- About a schema field → re-read §4, then ask.
- About an algorithm constant → §8 is canonical; only tweak after a phase passes.
- About an LLM prompt → §9 is canonical; do not "improve" without approval.
- About anything else → ask.

## Memory

Persistent memory for this project lives at `/root/.claude/projects/-home-ubuntu-buzznews/memory/`. Read `MEMORY.md` there for the index. It contains the OpenClaw skill inventory, the developer's collab style, and ongoing project state. Update memory when you learn something durable and non-obvious.

## Live deployment (as of 2026-05-25)

- **Public URL**: https://slow.myvnc.com/ (no-ip Type A record → VPS public IP 129.226.83.187)
- **TLS**: Let's Encrypt via Caddy HTTP-01 challenge (auto-managed). Caddy is `caddy` user, Caddyfile at `/etc/caddy/Caddyfile`, `SITE_HOST` injected via systemd drop-in `/etc/systemd/system/caddy.service.d/site-host.conf`.
- **Cloud firewall**: Tencent Lighthouse console controls inbound ports separately from host `ufw`. Ports 22/80/443 currently open. To open more, the developer must use the Lighthouse console — you cannot do this from inside the VPS.
- **Services**: `buzz-news-web` and `buzz-news-worker` both active under systemd. uvicorn on `127.0.0.1:8000`, worker runs APScheduler.
