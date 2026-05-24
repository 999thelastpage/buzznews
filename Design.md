# BuzzNews — Design System (Companion to PROJECT_PLAN.md)

> Specification for the mosaic-language UI. Pairs with `PROJECT_PLAN.md`. Read both before implementing Phase 6.
>
> The reference implementation is `buzznews-final.html` (single-file mockup with home, article, archive, and Hindi mirror). When this document and the mockup disagree, **the mockup wins** — it is the canonical source.

---

## 1. Design philosophy

Five principles, in priority order. Every choice traces back to one of these.

1. **Synthesis as the product.** The thing visitors get here that they don't get on a feed is "what 9 sources are saying, distilled." This must always be the visual hero — source counts visible on every tile, sources named on every detail page.
2. **Calm even when urgent.** No red banners, no all-caps "BREAKING." A breaking story gets a single pulsing 5px dot, that's the entire urgency vocabulary.
3. **Every byte is respect.** Zero web fonts. Zero framework JS. Zero third-party trackers. No autoplaying anything. Pages stay under 30KB cold; instant warm via Cloudflare.
4. **Devanagari and Latin treated as equals.** Hindi is not a translation toggle; it is a parallel surface generated independently by the writer, with identical visual treatment.
5. **The page is a mosaic, not a feed.** Triage 18+ stories per screen via variable-sized tiles. Importance shows through tile size, not noise.

---

## 2. The mosaic system

The home page and archive ranked list are both mosaics. Tile size is **driven by the cluster's composite score** (computed by `scorer.py` per the project plan), not by hand-picked editorial decisions.

### 2.1 Tile size rules

| Tile class | Score threshold | Phone grid span | Tablet grid span (≥720px) |
|---|---|---|---|
| `tile--2x2` | composite ≥ 0.75 | 2 cols × 2 rows | 3 cols × 2 rows |
| `tile--2x1` | composite 0.45–0.75 | 2 cols × 1 row | 3 cols × 1 row |
| `tile--1x1` | composite < 0.45 | 1 col × 1 row | 2 cols × 1 row |

**Hard constraint**: at most **one** `2x2` per page (the lead). If two clusters both score ≥ 0.75, the higher one wins the 2x2 and the second becomes a 2x1.

**Distribution target per page** (top 18–22 stories):
- 1 × 2x2
- 4–6 × 2x1
- 12–15 × 1x1

The publisher must enforce this distribution after scoring. If too few 2x1-tier stories exist, demote some down to 1x1. Do not pad.

### 2.2 Grid behavior

- **Phone (default)**: 4-column grid, gap 6px, row height 92px
- **Narrow phone (≤380px)**: collapse to 3-column grid, row height 86px. The 2x2 becomes full-width (`grid-column: span 3`)
- **Tablet/desktop (≥720px)**: 6-column grid, gap 8px, row height 100px. Tile spans double accordingly.

Use `grid-auto-flow: dense` so the engine packs irregular tiles efficiently — no gaps in the layout.

### 2.3 Tile content contract

Every tile renders these fields in this order, no exceptions:

1. **Category + buzz dot + time** (top row, sans-serif uppercase, ~9px)
2. **Title** (serif, line-clamped per tile size)
3. **Source line** (sans-serif, muted, line-clamped to one line)

For 1x1 tiles, the source line shows only `"N src"` (e.g., `"6 src"`) — the source-count is the synthesis trust signal even at the smallest size. For 2x1 and 2x2 tiles, show the top 2–3 source names + count of remaining.

### 2.4 The inverted lead tile

The `2x2` tile uses **inverted color** (dark ink background, paper-color text). This is the only inversion on the home page. It's how visual hierarchy is established without enlarging type beyond the system.

---

## 3. Design tokens

These are the only tokens. Add new ones only with explicit approval — every new token weakens the system.

### 3.1 Palette (CSS custom properties)

```css
:root {
  /* Surfaces */
  --paper:    #F4F0E8;       /* base background */
  --paper-2:  #EDE7DC;       /* tile background, callouts */
  --paper-3:  #E4DDD0;       /* hover state */

  /* Ink */
  --ink:      #1A1614;       /* primary text */
  --ink-soft: #3D3530;       /* secondary text */
  --ink-2:    #0E0B09;       /* inverted backgrounds (lead tile, article head) */

  /* Muted */
  --muted:    #807771;       /* meta, timestamps */
  --muted-2:  #A39A92;       /* meta on dark backgrounds */

  /* Lines */
  --hairline:   rgba(26, 22, 20, 0.10);
  --hairline-2: rgba(26, 22, 20, 0.05);

  /* Accent (used SPARINGLY — link hovers, "What's next" border, buzz) */
  --accent: #A8421E;
  --buzz:   #C44830;
}
```

Dark mode counterpart is defined under `@media (prefers-color-scheme: dark)` in the mockup. Implement identically.

### 3.2 Category accent colors

Used for: tile left-edge stripe (3px inset shadow), category label text, archive-rank category label.

```css
--c-intl:  #2E5C8A;   /* International — indigo */
--c-pol:   #6B3A8C;   /* Politics — aubergine */
--c-biz:   #2D6B4F;   /* Business — forest */
--c-tech:  #B8651F;   /* Tech — amber */
--c-sport: #A8421E;   /* Sports — terracotta */
--c-film:  #8C3A5C;   /* Film — rose */
--c-sci:   #4A6B8C;   /* Science — slate */
--c-gen:   #5C5048;   /* General — taupe */
```

If a new category is added in the DB, **add a token before adding it to the UI**. Do not reuse colors across categories.

### 3.3 Typography

**Zero web fonts.** Use the OS stack. This is a design constraint, not a placeholder.

```css
--serif: "Iowan Old Style", "Apple Garamond", Constantia, Georgia, serif;
--sans:  -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
--mono:  ui-monospace, "SF Mono", Menlo, Consolas, monospace;
```

Serif is used for **all** editorial content: tile titles, article body, article title, archive titles.
Sans is used for **all** UI/meta: category labels, timestamps, source names, byline, menu items, footer.

Always enable kerning and ligatures:
```css
font-feature-settings: "kern", "liga";
```

For body text, also enable old-style figures: `"onum" 1`.

For Hindi, no font change is needed — every modern OS supplies Noto Sans Devanagari (or equivalent) via fallback. Relax line-height slightly:
```css
[lang="hi"] { line-height: 1.6; }
[lang="hi"] .tile__title { line-height: 1.3; letter-spacing: 0; }
[lang="hi"] .article__body { line-height: 1.8; }
```

### 3.4 Spacing scale

```
--rail: 14px         /* side padding (24px ≥720px viewport) */
--gap:  6px          /* mosaic tile gap (8px ≥720px) */
```

Vertical rhythm inside tiles is fixed by `padding: 10px 10px 9px 13px` (1x1/2x1) and `padding: 14px 14px 12px 17px` (2x2). The extra left padding accommodates the 3px stripe without clipping text.

---

## 4. Component contracts

Each component below is realized as a Jinja2 macro/include. Names match the mockup CSS classes exactly. Do not rename.

### 4.1 `mast` — masthead

One line, sticky-free. Brand on left, date + lang chip on right.

- Brand: `Buzznews` with italic "news" and a 5px buzz dot
- Dot pulses every 3s via `@keyframes pulse`
- Date format: `Sat 24 May` (EN) or `शनि 24 मई` (HI), uppercase, sans, 10.5px
- Lang chip: `EN·HI`, active link bold

On article and archive pages, replace the date with `← Home` link.

### 4.2 `mos` — mosaic container

```html
<section class="mos">
  <a href="..." class="tile tile--2x2 s-intl"> ... </a>
  <a href="..." class="tile tile--2x1 s-biz"> ... </a>
  <a href="..." class="tile tile--1x1 s-tech"> ... </a>
  ...
</section>
```

Each tile is a single `<a>`. The entire tile is the click target — no nested links inside tiles.

### 4.3 `tile` — story tile

Required structure:

```html
<a href="/{lang}/article/{slug}" class="tile tile--{size} s-{cat}">
  <div class="tile__cat">
    <span>{? hot dot ?}{category name}</span>
    <span class="tile__cat-time">{relative time}</span>
  </div>
  <h2 class="tile__title">{title}</h2>
  <div class="tile__src">{sources line}</div>
</a>
```

**Category label on dark tiles (2x2)**: render without the `k-{cat}` color class, since text is white on dark. The 3px stripe on the left still uses the category color and provides the only color accent.

**Category label on light tiles (2x1, 1x1)**: render with `k-{cat}` color class on the label `<span>`.

**Buzz dot rule**: render `<span class="hot"></span>` inside the category label only when the cluster currently has an active buzz_event (within the last 6 hours). Never on more than 3 tiles per page — if more clusters qualify, take the top 3 by composite score.

**Time format**:
- < 60 minutes: `{N}m`
- < 24 hours: `{N}h`
- < 7 days: `{N}d`
- otherwise: `{N}w`

Hindi: `मि / घं / दि / सप`.

**Source line by tile size**:
- 2x2: top 3 source names (bold), then `· +{N-3} sources`
- 2x1: top 2 source names (bold), then `· +{N-2}`
- 1x1: just `{N} src` (or `{N} स्रोत` in Hindi)

### 4.4 `article` — article detail page

Structure:

```html
<article class="article">
  <header class="article__head">         <!-- dark inversion -->
    <div class="article__kicker">{category} · {buzz?}</div>
    <h1 class="article__title">{title}</h1>
    <p class="article__byline">Synthesized from <strong>{N} sources</strong> · Updated {time}</p>
  </header>

  {? <figure class="article__hero">       <!-- only if hero_image_url IS NOT NULL -->
    <img src="..." alt="">
    <figcaption class="article__hero-credit">{credit}</figcaption>
  </figure> ?}

  <div class="article__body">
    {synthesized prose, paragraphs only}
  </div>

  {? <aside class="next">                  <!-- only if "what's next" sentence exists -->
    <span class="next__label">What's next</span>
    {next sentence}
  </aside> ?}

  <section class="src-mos">                <!-- always -->
    <h2 class="src-mos__h">Read at sources</h2>
    <div class="src-grid">
      {each source as a src-tile}
    </div>
  </section>

  <section class="tl">                     <!-- always, even if minimal -->
    <h2 class="tl__h">How this story developed</h2>
    {each tl-ev row}
  </section>

  <section class="related">                <!-- if related clusters exist -->
    <h2 class="related__h">Related</h2>
    <div class="related__grid">{three tiles}</div>
  </section>
</article>
```

**Category stripe at the top of the article head**: implemented as a 3px `::before` pseudo-element absolutely positioned at the top of `.article__head`, with background set to the category color. The `background` is set inline since the category is dynamic:

```html
<header class="article__head" style="--cat: var(--c-intl);">
```

Then in CSS:

```css
.article__head::before { background: var(--cat, var(--ink)); }
```

**Hero image rule** — this is critical:
- Render the `<figure>` only when `hero_image_url IS NOT NULL` AND the image passed the picker's quality check
- The imager (`imager.py`) sets `hero_image_url` to NULL when no high-confidence match exists
- When NULL: omit the figure entirely. The article head's dark inversion **is** the visual anchor. Do not render a placeholder.
- Credit format: `Photo by {photographer} · {source}` (e.g., `Photo by Christian Lue · Unsplash`)

**Body typography**:
- 17px serif, line-height 1.7 (1.8 for Hindi)
- Max width 640px, centered
- First paragraph's first line gets `font-variant: small-caps; letter-spacing: 0.04em;`
- `<em>` used for source attributions inline (e.g., "according to *Reuters*")

### 4.5 `next` — "What's next" callout

Optional. Rendered after the body, before sources. Paper-2 background, left border 2px accent. Generated by the writer prompt's optional `next:` field (see PROJECT_PLAN.md section 9.1).

### 4.6 `src-mos` / `src-tile` — sources block

2-column grid (always 2 cols, even on narrow phones — readability beats density here). Each source tile shows: source name (bold sans), time (muted sans), then the source's own headline in serif. The whole tile links to the source URL.

### 4.7 `tl` / `tl-ev` — timeline

A grid of `time | event-text` rows. Generated from the cluster's source attachment history:
- First mention by any source
- First mention by an authoritative source (authority ≥ 0.8)
- Each buzz event
- Major source count milestones (3rd, 6th, 10th source)

Order: most recent first. Limit to 6 events; older ones are dropped.

### 4.8 `archive` — archive page

Two sections:

1. **Windows** — 2x2 grid of `today / this week / this month / this year` tiles. The currently-selected window gets the inverted (`is-active`) dark treatment. Each tile shows: window label (uppercase sans, muted), top story headline (serif), article count + date range.

2. **Ranked list** — `rank-mos` is a 2-column grid of `rank-tile` items. Each rank tile shows: rank number (`01`, `02`...), category label, headline, source count + age. Stripe on left edge per category.

---

## 5. Motion

Minimal. Three animations exist in the entire system:

1. **Buzz pulse**: `@keyframes pulse` — opacity 1 → 0.35 → 1 over 3s, infinite. Applied to the masthead brand dot, story-level hot dots, and the article kicker hot dot.
2. **Tile hover**: background transition from `--paper-2` to `--paper-3` over 150ms.
3. **Link hover**: color transition to `--accent`, no underline.

No scroll-triggered animations. No page transitions. No stagger reveals. No loading skeletons. The site is fast enough that none are needed.

---

## 6. Performance budget

Every page (article, home, archive) must meet these gates **before deploy**:

| Metric | Limit |
|---|---|
| HTML size (uncompressed) | ≤ 15KB |
| HTML size (gzipped) | ≤ 5KB |
| Total CSS (inlined `<style>` in `<head>`) | ≤ 8KB gzipped |
| External requests | 0 (no fonts, no scripts, no analytics) |
| Time to first byte (warm, Cloudflare) | ≤ 50ms |
| Largest Contentful Paint (3G phone, cold) | ≤ 1.5s |
| Cumulative Layout Shift | 0 |

**How to meet these**:
- Inline the entire CSS in `<style>` in the `<head>` of every static page (it's the same ~8KB on every page, perfectly Cloudflare-cacheable)
- No `<script>` tags anywhere unless adding one specific feature later (HTMX is fine if needed, but not on day one)
- All images served as `.webp`, three sizes per article (hero 1200×675, card 600×338, thumb 240×135)
- `<img>` tags always include `width`, `height`, `loading="lazy"`, and `decoding="async"`
- Static pages emit `Cache-Control: public, max-age=300, stale-while-revalidate=600`

---

## 7. Implementation guidance for Phase 6

The mockup file (`buzznews-final.html`) is the source of truth. To implement:

1. **Extract tokens** into `src/buzz_news/web/static/tokens.css` (just the `:root { ... }` and dark-mode block).
2. **Extract components** into `src/buzz_news/web/static/components.css` (everything from `.mast` down through `.tile`, `.article`, `.src-mos`, `.tl`, `.archive`).
3. **Inline both** into the `<head>` of every rendered page. Do not link as external stylesheets — the extra request is not worth it at our scale. Build a Jinja2 include called `_inline_styles.html` that pastes the combined CSS.
4. **Build Jinja2 macros** matching every component in section 4. Filename suggestion: `_macros.html`, with macros named `mosaic_tile(article, size)`, `article_header(article)`, `sources_block(article)`, `timeline(events)`, `archive_windows(windows)`, `rank_tile(article, rank)`.
5. **Build page templates**: `home.html`, `article.html`, `archive.html`. Each extends `base.html` which provides `<html>`, `<head>`, `<body>`, masthead, footer.
6. **Mosaic packing logic** lives in `publisher.py`: after scoring, classify each cluster into `2x2 | 2x1 | 1x1` per section 2.1, enforce the at-most-one-2x2 rule and the distribution targets, then pass the ordered list to the home template.
7. **Hindi rendering**: same templates, same macros. The `lang` attribute on `<html>` switches to `hi`, and content fields come from `summary_hi` / `title_hi`. Time formats and category-name translations live in `i18n.py` as a simple dict.

---

## 8. What is explicitly out of scope for v1

These were considered and rejected. Do not add without explicit approval:

- Comments, share buttons, "trending now" sidebars, author bylines, fake reporter names
- Infinite scroll (the home page is finite — link to archive at the bottom)
- Category navigation tabs (category is shown per-tile only)
- Per-region nav UI (region is auto-detected; user can override in footer)
- Newsletter signup, push notifications, in-app banners
- Hero images on the home feed (only on article detail, and only when picker finds a good one)
- Any social embeds (Twitter cards, YouTube, etc.)
- Search (deferred to v2)

---

## 9. Accessibility checklist

- Every interactive element is a real link or button (no `<div onclick>`)
- All text passes WCAG AA contrast on both light and dark themes
- Tile titles use `<h2>` for proper heading structure
- The masthead brand is `<h1>` on the home page, `<a>` linking home on other pages
- Hero images have `alt` text (empty `alt=""` when decorative)
- The pulsing buzz dot has `aria-hidden="true"` (decorative)
- The "How this story developed" timeline section has `aria-label="Timeline"`
- The sources section has `aria-label="Sources"`
- Touch targets on mobile are at least 44×44px (1x1 tiles meet this at 92px row × ~94px column)

---

## 10. Future iteration notes (post-v1)

When the site is live and we have real usage data, the things most worth revisiting:

- **Tap-to-expand previews** on tiles — show 1-line snippet on first tap, navigate on second
- **A "saved" mosaic** — bookmark-to-read-later, stored client-side only (no accounts)
- **Search** with a single full-text query over titles + bodies
- **Per-cluster discussion summary** — if/when we add a way to track public reaction
- **Personalization** — weight scoring toward categories the user reads, stored locally only
- **Native dark/light toggle** in the masthead (currently OS-driven only)

None of these should ship in v1. Ship the small clean thing first.
