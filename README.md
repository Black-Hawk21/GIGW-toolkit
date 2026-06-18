# GIGW 3.0 — Web Accessibility Audit Toolkit

A collection of command-line tools for auditing government and public-sector websites against WCAG and GIGW standards. The four tools work independently or as a pipeline: crawl a site to build a page inventory, then feed that inventory into the alt-text, contrast, and media checkers for automated compliance testing.

---

## Prerequisites

**Python 3.7+** is required. Install all dependencies with:

```bash
pip install requests beautifulsoup4 pandas openpyxl tinycss2
```

---

## Recommended Pipeline

```
1. crawler.py          → crawl.csv
2. alt_text.py         → crawl.csv  →  wcag_report.csv
3. contrast_checker.py → crawl.csv  →  contrast_detail.csv + contrast_summary.csv
4. media_crawler.py    → crawl.csv  →  media_detail.csv    + media_summary.csv
```

```bash
python crawler.py --url https://example.gov.in --output crawl.csv
python alt_text.py crawl.csv wcag_report.csv
python contrast_checker.py crawl.csv --output results/
python media_crawler.py --input crawl.csv --output media_report
```

---

## Tool 1 — Web Crawler (`crawler.py`)

Exhaustively crawls a website domain, following every internal link until all reachable pages have been discovered. No input file is needed — just provide a starting URL.

### Arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `--url` | string | *(prompted)* | The starting URL to crawl. If omitted, the tool will ask for it at runtime. HTTP/HTTPS is added automatically if missing. |
| `--depth` | integer | `-1` | How many link-levels deep to crawl. `-1` means **unlimited** — the crawler will follow links until every reachable internal page has been visited. Set to `3` to stop after three levels from the starting page. |
| `--delay` | float | `1.0` | Seconds to wait between each HTTP request. Increase this to be gentler on the server (e.g. `--delay 2`). Decrease it cautiously on test environments. |
| `--timeout` | integer | `15` | How long (in seconds) to wait for a page to respond before marking it as a timeout and moving on. |
| `--format` | string | `csv` | Output format: `csv` (default) or `xlsx`. |
| `--output` | string | `<domain>_crawl.csv` | File path for the report. Defaults to the domain name with the chosen extension. |

### Usage

```bash
# Prompts for a URL
python crawler.py

# Crawl a specific site (unlimited depth, CSV output)
python crawler.py --url https://example.gov.in

# Crawl only 3 levels deep
python crawler.py --url https://example.gov.in --depth 3

# Slower request rate with a custom output file
python crawler.py --url https://example.gov.in --delay 2 --output my_report.csv

# Full example with all arguments
python crawler.py --url https://example.gov.in --depth -1 --delay 1.5 --timeout 20 --format xlsx --output audit_report.xlsx
```

### How It Works

1. Starts at the URL you provide and fetches the page.
2. Extracts all internal links (`<a>`, `<link>`, `<area>` tags) found on the page.
3. Adds new, unvisited links to a queue and processes them one by one.
4. Skips non-webpage files automatically — PDFs, images, CSS, JS, archives, fonts, videos, and more are excluded from crawling.
5. Stays within the domain — external links are detected and flagged but not followed.
6. Records every URL it visits along with its title, HTTP status, parent page, and depth.

### Output

**CSV format** — one row per page with these columns:

| Column | Description |
|---|---|
| `URL` | The full page address |
| `Page Title` | The `<title>` tag content of the page |
| `HTTP Status` | Response code (`200`, `404`, `500`, `ERR`, etc.) |
| `Found On (Parent URL)` | Which page contained the link to this one |
| `Depth` | How many links deep this page is from the start URL |
| `Discovered At` | Time the page was crawled (`HH:MM:SS`) |
| `Notes` | Any flags, e.g. "Redirected to external domain", "Request timed out" |

**Excel format** (`--format xlsx`) — three sheets: **Crawl Results** (colour-coded rows: green for 200, red for errors, yellow for warnings), **Summary** (totals, depth breakdown, status breakdown), and **Errors & Skipped** (filtered list of problem pages).

### Notes for Testers

- Run the crawler first to get a full page inventory — use the output as your test checklist.
- Use `--depth 2` or `--depth 3` for a quick first pass on large sites, then `--depth -1` for full coverage.
- If the site is slow or rate-sensitive, increase `--delay` to avoid overwhelming the server.
- The crawler identifies itself as `GIGW-Crawler/2.0 (Government Website Evaluation Tool)` in the HTTP `User-Agent` header.

---

## Tool 2 — Alt Text Checker (`alt_text.py`)

WCAG 1.1.1 (Non-Text Content) compliance checker. Reads a CSV of crawled pages and checks each live page for missing or inadequate alt text on images, SVGs, `<input type="image">`, and `<area>` elements.

### Arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `input_csv` | string | *(required)* | Path to the crawler CSV. Must have columns: `URL`, `Page Title`, `HTTP Status`. |
| `output_csv` | string | *(required)* | Path for the output CSV with WCAG results appended. |
| `--delay` | float | `0.5` | Seconds to wait between page requests. |
| `--limit` | integer | *all rows* | Only check the first N rows (useful for testing or quick spot-checks). |

### Usage

```bash
# Check all pages from a crawl
python alt_text.py crawl.csv wcag_report.csv

# Check with a slower request rate
python alt_text.py crawl.csv wcag_report.csv --delay 1.0

# Quick spot-check — first 50 pages only
python alt_text.py crawl.csv wcag_report.csv --limit 50
```

### What It Checks

For each page, the tool inspects every `<img>`, `<svg>`, `<input type="image">`, and `<area>` element:

- **`<img>`** — must have a non-empty, meaningful `alt` attribute. `alt=""` (empty string) is allowed for decorative images. `role="presentation"` or `role="none"` is also accepted. Common placeholders like "image", "photo", "placeholder" are flagged as failures.
- **`<input type="image">`** — must have an accessible name via `alt`, `aria-label`, or `title`.
- **`<area>`** — interactive areas in image maps must have meaningful alt text.
- **`<svg>`** — inline SVGs should have an accessible name via `aria-label`, `aria-labelledby`, or `role="img"` with a `<title>` child. `role="presentation"` / `role="none"` marks them as decorative.

### Output

The output CSV preserves the original columns and adds:

| Column | Description |
|---|---|
| `Result` | `PASS` — all elements OK · `FAIL` — at least one violation · `ERROR` — page could not be fetched · `SKIP` — page was broken in crawl |
| `Total Images` | Number of non-text elements found on the page |
| `Pass Count` | Elements with acceptable text alternatives |
| `Fail Count` | Elements violating WCAG 1.1.1 |
| `Issues` | Pipe-separated list of specific failures, e.g. `[img] src='logo.png' → Missing alt attribute` |

---

## Tool 3 — Contrast Checker (`contrast_checker.py`)

WCAG 1.4.3 (AA) / 1.4.6 (AAA) colour-contrast auditor. Reads the crawler CSV, fetches each page, extracts foreground/background colour pairs from visible text elements, computes contrast ratios, and reports pass/fail against the chosen WCAG threshold.

**WCAG thresholds:**
- **AA** — Normal text ≥ 4.5 : 1 · Large text ≥ 3.0 : 1
- **AAA** — Normal text ≥ 7.0 : 1 · Large text ≥ 4.5 : 1

*"Large text" = ≥ 18 pt (24 px) regular or ≥ 14 pt (18.67 px) bold.*

### Arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `input_csv` | string | *(required)* | Path to the crawler CSV (must have a `URL` column with `HTTP Status` = `200`). |
| `--level` | string | `AA` | WCAG conformance level: `AA` or `AAA`. |
| `--custom-threshold` | float | *none* | Override WCAG thresholds with a single custom contrast ratio applied to all text (e.g. `5.0`). Overrides `--level`. |
| `--output` | string | `.` (current dir) | Directory to write the output CSV files. |
| `--workers` | integer | `3` | Number of parallel fetch workers (max recommended: 8). |
| `--timeout` | integer | `20` | HTTP request timeout in seconds. |
| `--no-verify` | flag | *off* | Disable SSL certificate verification (useful for internal/staging sites). |
| `--sample` | integer | *all* | Only process the first N URLs (useful for quick spot-checks). |
| `--delay` | float | `0.5` | Polite delay between requests per worker thread. |

### Usage

```bash
# Basic AA audit
python contrast_checker.py crawl.csv

# AAA level audit
python contrast_checker.py crawl.csv --level AAA

# Custom threshold, save results to a folder
python contrast_checker.py crawl.csv --custom-threshold 5.0 --output results/

# Parallel workers, faster processing
python contrast_checker.py crawl.csv --workers 6 --delay 0.3

# Skip SSL checks, spot-check first 10 pages
python contrast_checker.py crawl.csv --no-verify --sample 10
```

### How It Works

1. Reads the crawler CSV and filters for pages with `HTTP Status = 200`.
2. Fetches each page and parses visible text elements (`<p>`, `<span>`, `<h1>`–`<h6>`, `<a>`, `<button>`, `<li>`, `<td>`, etc.).
3. Resolves foreground (`color`) and background (`background-color`) from inline styles and ancestor elements, falling back to browser defaults (black on white).
4. Determines whether text is "large" based on font-size and weight.
5. Computes the WCAG contrast ratio and checks it against the required threshold.
6. Deduplicates identical colour pairs per page.

### Output

Two CSV files are generated in the output directory, timestamped:

**`contrast_detail_<timestamp>.csv`** — one row per colour pair checked:

| Column | Description |
|---|---|
| `url` | Page URL |
| `element` | HTML tag name (`p`, `h1`, `a`, etc.) |
| `text_sample` | First 60 characters of the element's text |
| `fg_colour` | Foreground colour as hex (`#rrggbb`) |
| `bg_colour` | Background colour as hex (`#rrggbb`) |
| `contrast_ratio` | Computed ratio (1.00–21.00) |
| `required_ratio` | Threshold that must be met |
| `text_size` | `normal` or `large` |
| `wcag_level` | `AA` or `AAA` |
| `result` | `PASS` or `FAIL` |

**`contrast_summary_<timestamp>.csv`** — one row per page:

| Column | Description |
|---|---|
| `url` | Page URL |
| `page_title` | Page title |
| `total_checks` | Number of colour pairs checked |
| `pass_count` | Pairs that passed |
| `fail_count` | Pairs that failed |
| `fail_rate_%` | Failure percentage |
| `min_contrast` | Lowest contrast ratio on the page |
| `max_contrast` | Highest contrast ratio on the page |
| `page_result` | `PASS`, `FAIL`, `SKIP`, `ERROR`, or `NO_DATA` |

---

## Tool 4 — Media Crawler (`media_crawler.py`)

Scans every successfully-loaded page from the crawler CSV and discovers all embedded media: images, videos, audio, iframes, embeds, objects, inline SVGs, and CSS background images. Optionally verifies each media URL is reachable via a HEAD request.

### Arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `--input` | string | *(prompted)* | Path to the crawl CSV from `crawler.py`. If omitted, the tool will ask for it at runtime. |
| `--output` | string | `<input>_media` | Base output path (extensions are added automatically). |
| `--format` | string | `csv` | Output format: `csv` (default) or `xlsx`. |
| `--delay` | float | `0.5` | Seconds to wait between page requests. |
| `--timeout` | integer | `10` | Request timeout in seconds. |
| `--no-verify` | flag | *on by default* | Skip HEAD requests for media URLs (faster scan, but no HTTP status codes for media files). |

### Usage

```bash
# Prompts for the input CSV
python media_crawler.py

# Scan with default settings
python media_crawler.py --input crawl.csv

# Custom output name
python media_crawler.py --input crawl.csv --output media_report

# Excel output
python media_crawler.py --input crawl.csv --format xlsx

# Faster scan (skip media verification)
python media_crawler.py --input crawl.csv --no-verify

# Slower, more polite scan
python media_crawler.py --input crawl.csv --delay 1.0 --timeout 15
```

### What It Scans

For each page, the tool extracts media from:

- **`<img>`** — `src` and `srcset` attributes
- **`<video>`** — `src` and `poster` attributes
- **`<source>`** — `src` (inherits type from parent `<video>` or `<audio>`)
- **`<audio>`** — `src` attribute
- **`<track>`** — `src` (captions/subtitles)
- **`<iframe>`** — `src` attribute
- **`<embed>`** — `src` attribute
- **`<object>`** — `data` attribute
- **Inline `<svg>`** — detected as inline media (no external URL)
- **`<style>` blocks** — `url()` references in CSS
- **Inline `style` attributes** — `background-image: url()` patterns

### Output

**CSV format** — two files:

**`<output>_detail.csv`** — one row per media item:

| Column | Description |
|---|---|
| `#` | Row number |
| `parent_url` | The page the media was found on |
| `media_type` | `image`, `video`, `audio`, `iframe`, `embed`, `object`, `svg (inline)`, or `image (CSS bg)` |
| `media_url` | Absolute URL to the media file |
| `tag` | The HTML tag that contained the reference (e.g. `<img>`, `<video>`, `<style> url()`) |
| `alt_title` | Alt text or title attribute, if present |
| `http_status` | HEAD-request status code (`200`, `404`, `ERR`, `TIMEOUT`, or `N/A` for inline/skipped) |

**`<output>_summary.csv`** — one row per page:

| Column | Description |
|---|---|
| `Page URL` | Page address |
| `Total Media` | Total media items found |
| `Images` | Image count (including CSS background images) |
| `Videos` | Video count |
| `Audio` | Audio count |
| `Iframes` | Iframe count |
| `Embeds/Objects` | Embed + object count |
| `SVG (inline)` | Inline SVG count |

**Excel format** (`--format xlsx`) — three sheets: **Media Detail** (colour-coded by media type), **Page Summary** (per-page media counts), and **Stats** (overall totals and top pages by media count).

---

## Notes for Testers

- **Start with the crawler** to build a complete page inventory. Use `--depth 2` for a quick first pass on large sites.
- **Feed the crawl CSV** into the other three tools — they all read the same format.
- **Alt Text Checker** flags WCAG 1.1.1 violations. Filter the output for `Result = FAIL` to find pages needing attention.
- **Contrast Checker** checks colour pairs. Start with `--level AA`; use `--level AAA` for stricter audits.
- **Media Crawler** gives you a full asset inventory. Use it to find broken media (`404` status) or missing alt text on images.
- All tools use polite request delays by default. Increase `--delay` on rate-sensitive servers.
