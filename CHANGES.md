# BuzzNews Magazine Layout & Publisher Enhancements

This document details the magazine layout integration, style enhancements, and data publishing corrections implemented on the `feature/layout-refinements` branch.

---

## 1. Summary of Enhancements

### A. Masthead Ticker Optimization
- **Goal**: Expand the rotating trending headline ticker bar horizontally for improved space usage and prominence.
- **Changes**:
  - Decreased margin constraints (`margin-left` and `margin-right`) to `15px` (previously `30px`) to position the ticker cleaner relative to the logo and language selector.
  - Set `max-width: none;` (previously `1000px`) so it leverages all available space on wider desktop screens.
  - Adjusted internal padding-left to `15px` (previously `20px`) for better alignment.

### B. Excerpt Visibility on Standard Card Sizes
- **Goal**: Show summary excerpts on all cards on the homepage, keeping a cohesive visual flow instead of completely hiding them on default `col-3` / `col-4` cards.
- **Changes**:
  - Added custom style rule `.hide-excerpt-default .card-excerpt` to clamp the summary to **1 line** (`-webkit-line-clamp: 1`).
  - Huge and large cards (`card-huge` / `card-large`) continue to render a **3-line summary** (`-webkit-line-clamp: 3`), ensuring clear visual hierarchy.

### C. Publisher Source Deduplication
- **Goal**: Prevent publication boxes (e.g. Al Jazeera, The Hindu) from repeating under the "Read at sources" section on article detail pages.
- **Changes**:
  - Updated the database queries in `publisher.py` to retrieve all raw items associated with the cluster instead of limiting to 6 immediately.
  - Added memory-based deduplication filtering by the publisher name (`Source.name` / `ArticleSource.source_name`) in addition to URL and title checks.
  - Applied the same unique-filtering logic during manual re-rendering (`rerender_articles.py`) and AI rewriting (`rewrite_articles.py`).
  - Limited the final list to at most 6 unique, diverse publisher source blocks.

---

## 2. File-by-File Changes

### 📁 `src/buzz_news/`

#### 📄 [\_inline_styles.html](file:///e:/GenerativeAI/BuzzNews/buzznews/src/buzz_news/web/templates/_inline_styles.html)
- Integrated Google Fonts (`Playfair Display` and `Inter`) and configured variables.
- Added modern typography, theme variables, glassmorphism card-hover transitions, and magazine layout rules.
- Set up custom `.hide-excerpt-default .card-excerpt` style rules to enable 1-line excerpts for standard sizes and 3-line excerpts for larger sizes.
- Enlarged `.headline-bar` to utilize full width on desktop screen sizes.

#### 📄 [\_macros.html](file:///e:/GenerativeAI/BuzzNews/buzznews/src/buzz_news/web/templates/_macros.html)
- Defined the `news_card` macro to build the modern card grid structure.
- Embedded a lightweight, non-blocking JS interval animation inside the `mast` macro to rotate the top 10 headlines smoothly every 5 seconds.

#### 📄 [home.html](file:///e:/GenerativeAI/BuzzNews/buzznews/src/buzz_news/web/templates/home.html)
- Migrated legacy mosaic view (`mos`) to the CSS Grid `grid-container`.
- Integrated `news_card` to display cards using rank-based responsive column spans.
- Redesigned the archive footer tile to span full width (`col-12`) using the news-card structure for better alignment.

#### 📄 [publisher.py](file:///e:/GenerativeAI/BuzzNews/buzznews/src/buzz_news/publisher.py)
- Updated homepage publisher query to fetch summaries, generate 200-character excerpts, and map card layouts (`12, 3, 3, 3, 3, 8, 4`).
- Changed grid calculation to run *after* category interleaving to ensure row grid sums always align to 12.
- Implemented publisher deduplication check in `article_sources` creation.

---

### 📁 `scripts/`

#### 📄 [rerender_articles.py](file:///e:/GenerativeAI/BuzzNews/buzznews/scripts/rerender_articles.py)
- Added publisher name deduplication (`seen_names` check) and a 6-item limit constraint.
- Standardized file outputs to open using `encoding="utf-8"`, preventing Windows-specific `UnicodeEncodeError` and `UnicodeDecodeError`.

#### 📄 [rewrite_articles.py](file:///e:/GenerativeAI/BuzzNews/buzznews/scripts/rewrite_articles.py)
- Updated standard source iteration block to perform publisher deduplication and output up to 6 unique sources.

---

### 📁 `tests/`

#### 📄 [test_publisher.py](file:///e:/GenerativeAI/BuzzNews/buzznews/tests/test_publisher.py)
- Updated unit tests to assert the layout rules (`col-12`, `col-8`, `col-4`, `col-3`, `card-huge`, `card-large`) applied during home rendering.

---

## 3. Verification Details

### Automated Test Output
All 72 tests passed successfully:
```
tests\test_buzz.py ...                                                   [  4%]
tests\test_cli.py ..                                                     [  6%]
tests\test_clusterer.py .....                                            [ 13%]
tests\test_embedder.py ..                                                [ 16%]
tests\test_hn_adapter.py .                                               [ 18%]
tests\test_imager.py ....                                                [ 23%]
tests\test_minhash.py .....                                              [ 30%]
tests\test_normalizer.py ..                                              [ 33%]
tests\test_publisher.py ....                                             [ 38%]
tests\test_reddit_adapter.py ...                                         [ 43%]
tests\test_rollups.py ...........                                        [ 58%]
tests\test_rss_adapter.py ...                                            [ 62%]
tests\test_scorer.py .......                                             [ 72%]
tests\test_verifier.py ........                                          [ 83%]
tests\test_web.py ........                                               [ 94%]
tests\test_writer.py ....                                                [100%]
======================== 72 passed, 1 warning in 3.60s ========================
```

---

## 4. Grid & Theme Fixes (2026-05-25, session 2)

### A. Jinja2 Comment Syntax Fix (Masthead)
- **Problem**: Three JSX-style comments (`{/* Logo */}`, `{/* Trending Ticker */}`, `{/* Metadata & Theme Toggler */}`) were used in `_macros.html`. Jinja2 doesn't recognize `{/* */}` — it rendered them as literal text into the HTML, appearing as broken code at the top of every page (especially visible in light mode on a white background).
- **Fix**: Replaced with proper Jinja2 comments (`{# Logo #}`, `{# Trending Ticker #}`, `{# Metadata & Theme Toggler #}`).
- **Files**: `_macros.html` (lines 102, 108, 133)

### B. Transparent Tile Backgrounds (Removing Category Background Colors)
- **Problem**: The `{{ cat_c(art.category) }}` call in `home.html` applied classes like `s-intl`, `s-pol`, `s-sport` to tile wrapper `<div>`s. These `.s-*` classes set `background-color` to the full category hex, producing bright-colored tile backgrounds (blue, purple, orange, etc.) that didn't match the mockup's clean transparent-tile aesthetic.
- **Fix**: Removed `{{ cat_c(art.category) }}` from all three wrapper div types:
  - Lead story wrapper (`col-span-12 lg:col-span-8 lg:row-span-2 tile-lg`)
  - Daily Brief sidebar (`col-span-12 lg:col-span-4 lg:row-span-2 tile`)
  - Article loop cards (`{{ art.col_span }} tile / tile-lg`)
- Category colors now only apply to **text labels** (via `k-*` classes inside the card macros), not the tile containers.
- **Files**: `home.html` (lines 2, 12, 17, 23)

### C. 14-Position Grid Cycle (Fixing Grid Alignment for Non-Lead Articles)
- **Problem**: The old `_compute_tile_sizes` used a 15-article repeating cycle (`rank % 15`). Article[0] (the lead story) is rendered separately in the template with hardcoded grid classes. When article 15 arrived, `15 % 15 = 0` gave it lead-sized layout classes (`col-span-8`, `row-span-2`), breaking the grid with an oversized tile in the middle of the page.
- **Fix**: Article 0 keeps its lead layout (used by the template's hardcoded section). All articles at rank 1+ now use a **14-position repeating cycle** (`(rank - 1) % 14`), which never produces another lead-sized tile:

  | Cycle Position | Grid Classes | Card Class |
  |---|---|---|
  | 0, 1, 2 | `col-span-12 md:col-span-6 lg:col-span-4` | standard |
  | 3 | `col-span-12 lg:col-span-7` | card-large |
  | 4 | `col-span-12 lg:col-span-5` | card-large |
  | 5 | `col-span-12 lg:col-span-6 lg:row-span-2` | card-large (bento) |
  | 6, 7, 8, 9 | `col-span-12 md:col-span-6 lg:col-span-3` | standard |
  | 10 | `col-span-12 lg:col-span-4` | standard |
  | 11 | `col-span-12 lg:col-span-8` | card-large |
  | 12, 13 | `col-span-12 lg:col-span-6` | card-large |

- **Files**: `publisher.py` (function `_compute_tile_sizes`)

### D. Homepage Article Limit (15 → Complete Grid Cycle)
- **Problem**: The default `limit=22` in `render_home_pages` produced 1 lead + 21 loop articles. Since the 14-position cycle doesn't divide 21 evenly (21 = 14 + 7), the second cycle started but didn't complete — leaving an orphaned 3-col card at the bottom with no siblings in its row.
- **Fix**: Changed `limit` from 22 to **15** (1 lead + 14 cards = exactly one complete grid cycle). The page now ends cleanly on a full 12-column row.
- **Files**: `publisher.py` (function signature `render_home_pages(limit: int = 15)`)

### E. Test Update
- Updated `test_render_home_produces_tiles` to assert `tile-lg` instead of `card-huge` (since the lead wrapper div no longer includes `{{ lead_art.card_class }}`).
- **Files**: `tests/test_publisher.py`
