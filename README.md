# Web Crawler

A command-line tool that exhaustively crawls a website and records every internal webpage it finds — no manual browsing required. Built to help testers audit government and public-sector websites by automatically discovering all reachable pages and exporting them to a structured Excel report.

---

## Why This Tool Exists

Manually navigating a website to find all its pages is slow, error-prone, and easy to miss pages on. This crawler automates that entirely — give it a starting URL, and it will follow every internal link it finds (across all depths) until it has seen every reachable page on the domain. The results are saved to an Excel workbook that testers can filter, sort, and annotate.

---

## Prerequisites

**Python 3.7+** is required. Install the dependencies with:

```bash
pip install requests beautifulsoup4 pandas openpyxl
```

---

## Arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `--url` | string | *(prompted)* | The starting URL to crawl. If omitted, the tool will ask for it at runtime. HTTP/HTTPS is added automatically if missing. |
| `--depth` | integer | `-1` | How many link-levels deep to crawl. `-1` means **unlimited** — the crawler will follow links until every reachable internal page has been visited. Set to `3` to stop after three levels from the starting page. |
| `--delay` | float | `1.0` | Seconds to wait between each HTTP request. Increase this to be gentler on the server (e.g. `--delay 2`). Decrease it cautiously on test environments. |
| `--timeout` | integer | `15` | How long (in seconds) to wait for a page to respond before marking it as a timeout and moving on. |
| `--output` | string | `<domain>_crawl.xlsx` | File path for the Excel report. Defaults to the domain name, e.g. `example.gov.in_crawl.xlsx` in the current directory. |

---

## How to Use

**Basic usage — prompts you for a URL:**
```bash
python crawler.py
```

**Crawl a specific site (unlimited depth):**
```bash
python crawler.py --url https://example.gov.in
```

**Crawl only 3 levels deep:**
```bash
python crawler.py --url https://example.gov.in --depth 3
```

**Crawl with a slower request rate and a custom output file:**
```bash
python crawler.py --url https://example.gov.in --delay 2 --output my_report.xlsx
```

**Full example with all arguments:**
```bash
python crawler.py --url https://example.gov.in --depth -1 --delay 1.5 --timeout 20 --output audit_report.xlsx
```

---

## What the Crawler Does

1. Starts at the URL you provide and fetches the page.
2. Extracts all internal links (`<a>`, `<link>`, `<area>` tags) found on the page.
3. Adds new, unvisited links to a queue and processes them one by one.
4. Skips non-webpage files automatically — PDFs, images, CSS, JS, archives, fonts, videos, and more are excluded from crawling (but links to them are still noted if encountered).
5. Stays within the domain — external links are detected and flagged but not followed.
6. Records every URL it visits along with its title, HTTP status, which page it was found on, and how deep it sits in the site structure.

---

## Output: Excel Report

The tool produces a `.xlsx` file with up to three sheets:

**Sheet 1 — Crawl Results**
A complete list of every URL visited, with the following columns:

| Column | Description |
|---|---|
| `#` | Row number |
| `URL` | The full page address |
| `Page Title` | The `<title>` tag content of the page |
| `HTTP Status` | Response code (`200`, `404`, `500`, `ERR`, etc.) |
| `Found On (Parent URL)` | Which page contained the link to this one |
| `Depth` | How many links deep this page is from the start URL |
| `Discovered At` | Time the page was crawled (`HH:MM:SS`) |
| `Notes` | Any flags, e.g. "Redirected to external domain", "Request timed out" |

Rows are colour-coded: green for successful pages (HTTP 200), red for errors (4xx/5xx), and yellow for warnings (redirects, non-HTML content).

**Sheet 2 — Summary**
An at-a-glance overview including total pages found, success/error counts, pages broken down by depth level, and a full HTTP status code breakdown.

**Sheet 3 — Errors & Skipped** *(only present if there are issues)*
A filtered list of every URL that returned an error, timed out, redirected externally, or served non-HTML content — useful for quickly identifying broken links and problem pages.

---

## Notes for Testers

- Run the crawler before a testing session to get a full page inventory — use the Excel output as your test checklist.
- Filter the **Errors & Skipped** sheet first to find broken links and pages that may need investigation.
- Use `--depth 2` or `--depth 3` for a quick first pass on large sites, then run with `--depth -1` for full coverage.
- If the site is slow or rate-sensitive, increase `--delay` to avoid overwhelming the server.
- The crawler identifies itself as `GIGW-Crawler/2.0 (Government Website Evaluation Tool)` in the HTTP `User-Agent` header.
