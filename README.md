# BuzzNews

A multi-source news aggregation and synthesis site — English and Hindi, India-focused — running on a single 2 GB / 2-core VPS.

Live at **https://slow.myvnc.com/**.

BuzzNews pulls from many sources (RSS + Tavily web search), clusters items about the same event, scores them for genuine importance rather than virality, synthesizes a short editorial summary per cluster with an LLM, and serves a fast server-rendered site with daily / weekly / monthly / yearly rollups.

## Stack

| Concern | Choice |
| --- | --- |
| OS | Ubuntu 24.04 LTS |
| Language | Python 3.12 |
| Web | FastAPI + Uvicorn behind Caddy |
| Templates | Jinja2 (server-rendered) |
| Frontend | HTMX + Alpine.js (no build step) |
| DB | PostgreSQL 16 + `pgvector` |
| Cache | Redis 7 |
| Jobs | APScheduler (in-process, Postgres jobstore) |
| Embeddings | Gemini `gemini-embedding-001` (768-dim) |
| Writer LLM | DeepSeek → Gemini 2.5 Flash → Claude Haiku 4.5 (fallback chain) |
| Extraction | `trafilatura` + OpenClaw browser fallback (env-gated) |
| Dedup | `datasketch` MinHash LSH |
| TLS / proxy | Caddy 2 (auto-HTTPS) |
| Process supervision | systemd |

Hard constraints, by design: no local ML models, no Docker on the VPS, no Node in the BuzzNews runtime path, no client-side React, no paid image generation, no hosting of news-source images. Memory budget is ~1.3 GB steady-state on a 1.9 GB host.

## Layout

```
src/buzz_news/   app code (ingest, cluster, write, publish, web)
alembic/         schema migrations
deploy/          systemd units, Caddyfile
scripts/         one-shot ops scripts
tests/           pytest + respx
CLAUDE.md        working agreement for AI contributors
AGENTS.md        OpenClaw skill inventory and BuzzNews-specific skills
```

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env   # fill in real keys before running ingest / publish
alembic upgrade head
python -m buzz_news preflight
python -m buzz_news ingest
python -m buzz_news cluster
python -m buzz_news publish
uvicorn buzz_news.web.app:app --reload
```

Pytest:

```bash
pytest
```

## On the VPS

Two systemd units: `buzz-news-web` (uvicorn on `127.0.0.1:8000`) and `buzz-news-worker` (APScheduler). Caddy terminates TLS via Let's Encrypt and reverse-proxies to the web unit.

## Docs

`CLAUDE.md` is the working agreement for AI contributors — rules, host facts, conventions. `AGENTS.md` lists the OpenClaw skills BuzzNews depends on. The canonical phased build plan, per-phase progress log, and visual design notes are kept VPS-local and are not pushed.

## License

No license set yet. All rights reserved until one is added.
