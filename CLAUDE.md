# BuzzNews — Working Agreement

You are implementing BuzzNews on the VPS this file lives on. The canonical specification is **`/home/ubuntu/buzznews/PROJECT_PLAN.md`** (~1450 lines). Read it end-to-end before any tool call. This file is the rules of engagement; the plan is the spec.

## Non-negotiable rules

1. **Phases are sequential.** Do not start phase N+1 until phase N's acceptance criteria pass on this VPS (not on a laptop, not in a mock). The acceptance criteria are concrete and testable for a reason.
2. **Stop after each phase.** Write a `PROGRESS.md` entry at the repo root (what was done, what the acceptance run showed, anything to review). Then wait for the developer.
3. **Locked decisions in §1 are final.** Do not swap frameworks, libraries, models, or schema without asking first. If you're tempted to "improve" something locked, you've misread the plan — re-read §1.
4. **Ambiguity → stop and ask.** The developer prefers one clarifying question over an undoing-a-wrong-assumption session. Default to asking.
5. **One commit per passing phase.** Message prefix: `phase-N: <summary>`.

## Hard "no"s (these trip up fresh models)

- **No local ML models.** Embeddings go to **Gemini `text-embedding-004`** over the API. No `sentence-transformers`, no PyTorch, no HDBSCAN, no scikit-learn, no spaCy. This is a 1.9 GB RAM constraint, not a preference. If you find yourself reaching for any of these, you're solving the wrong problem.
- **No Docker on the VPS.** Bare metal + systemd. (Local dev is fine.)
- **No Node.js in the BuzzNews runtime path.** OpenClaw is the only Node process and it's already running.
- **No client-side React.** HTMX + Alpine.js, server-rendered Jinja2.
- **No paid image generation, no hosting of news-source images.**

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
- `tencent-cos-skill` — backups to Tencent COS (replaces rclone+B2)
- `github`, `tencentcloud-lighthouse-skill`, etc. — available but not in BuzzNews scope

BuzzNews adds its own skills under the same directory (see Phase 8). Custom skill names are prefixed `buzznews_*`.

## Pre-launch placeholders

Several `.env` values are intentionally `TODO_PRE_LAUNCH` / `TODO_BEFORE_PHASE_1`. See plan §13 for the checklist. **Build and locally test against placeholders; do not block on real keys.** The `preflight` CLI command validates the `.env` at startup — critical missing values abort, non-critical ones warn.

Concretely, you can develop and test all of Phases 0–7 with placeholder values. Phase 8 (Telegram delivery) and Phase 9 (COS backups) are the only ones that need real keys to pass acceptance.

## Conventions

- **Working directory**: `/opt/buzz-news/` once provisioned. Initial scaffolding happens wherever the developer clones.
- **App user**: `buzz`. Run pipeline commands as `sudo -u buzz ...` not root.
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

## When you're unsure

- About a library choice → re-read §1, then ask.
- About a schema field → re-read §4, then ask.
- About an algorithm constant → §8 is canonical; only tweak after a phase passes.
- About an LLM prompt → §9 is canonical; do not "improve" without approval.
- About anything else → ask.

## Memory

Persistent memory for this project lives at `/root/.claude/projects/-home-ubuntu/memory/`. Read `MEMORY.md` there for the index. It contains the OpenClaw skill inventory, the developer's collab style, and ongoing project state. Update memory when you learn something durable and non-obvious.
