#!/usr/bin/env python3
"""
WCAG 1.1.1 Non-Text Content Checker
=====================================
Reads a CSV of crawled pages produced by crawler.py (or any CSV with the
columns  URL, Page Title, HTTP Status)  and checks each live page for
missing or inadequate alt-text on images / non-text content, in line with
WCAG 2.1 Success Criterion 1.1.1.

Output CSV adds columns:
  Result        – PASS | FAIL | ERROR | SKIP
  Total Images  – number of non-text elements found
  Pass Count    – elements with acceptable text alternatives
  Fail Count    – elements violating WCAG 1.1.1
  Issues        – pipe-separated list of specific failures

Typical pipeline
----------------
  # 1. Crawl the site → crawl.csv  (crawler.py default output)
  python crawler.py --url https://example.gov.in --output crawl.csv

  # 2. Run the WCAG alt-text check on every page in that CSV
  python alt_text.py crawl.csv wcag_report.csv

  # Optional flags
  python alt_text.py crawl.csv wcag_report.csv --delay 1.0 --limit 50

Dependencies: requests, beautifulsoup4  (pip install requests beautifulsoup4)
"""

import csv
import sys
import time
import argparse
from urllib.parse import urljoin
from dataclasses import dataclass, field
from typing import List, Tuple

import requests
from bs4 import BeautifulSoup

# ── Configuration ─────────────────────────────────────────────────────────────

REQUEST_TIMEOUT = 15
RETRY_COUNT     = 2
RETRY_DELAY     = 2

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; WCAG111-Checker/1.0; "
        "+https://www.w3.org/WAI/WCAG21/Understanding/non-text-content.html)"
    )
}

# Common meaningless / placeholder alt values
PLACEHOLDER_ALTS = {
    "image", "img", "picture", "photo", "graphic", "icon", "logo",
    "banner", "spacer", "placeholder", "untitled", "no description",
    "alt text", "alt", "here", "click here", "link", ".", "*",
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ImageIssue:
    tag:     str
    src:     str
    problem: str


@dataclass
class PageResult:
    url:          str
    title:        str
    http_status:  str
    result:       str = "SKIP"
    total_images: int = 0
    pass_count:   int = 0
    fail_count:   int = 0
    issues: List[ImageIssue] = field(default_factory=list)

    @property
    def issues_summary(self) -> str:
        if not self.issues:
            return ""
        return " | ".join(
            f"[{i.tag}] src='{i.src}' → {i.problem}"
            for i in self.issues
        )


# ── Per-tag WCAG 1.1.1 checkers ───────────────────────────────────────────────

def check_img(tag) -> Tuple[bool, str]:
    """
    <img> must have a non-empty, meaningful alt attribute.
    Exceptions allowed by WCAG 1.1.1:
      • alt=""  (empty string) – explicit decorative intent
      • role="presentation" or role="none" – decorative
    """
    role = (tag.get("role") or "").strip().lower()
    alt  = tag.get("alt")   # None = attribute absent

    if role in ("presentation", "none"):
        return True, ""

    if alt is None:
        return False, "Missing alt attribute"

    alt_stripped = alt.strip()

    if alt_stripped == "":
        return True, ""  # intentional decoration

    if alt_stripped.lower() in PLACEHOLDER_ALTS:
        return False, f"Placeholder alt text: '{alt_stripped}'"

    return True, ""


def check_input_image(tag) -> Tuple[bool, str]:
    """
    <input type="image"> is a control — must have a descriptive name
    via alt, aria-label, or title (WCAG 1.1.1 exception 1 + SC 4.1.2).
    """
    if (tag.get("type") or "").lower() != "image":
        return True, ""

    if (tag.get("alt") or "").strip():
        return True, ""
    if (tag.get("aria-label") or "").strip():
        return True, ""
    if (tag.get("title") or "").strip():
        return True, ""

    return False, "<input type=image> has no alt, aria-label, or title"


def check_area(tag) -> Tuple[bool, str]:
    """<area> elements in image maps need meaningful alt text."""
    href = (tag.get("href") or "").strip()
    if not href or href == "#":
        return True, ""  # non-interactive area

    alt = (tag.get("alt") or "").strip()
    if not alt:
        return False, "<area> missing alt attribute"
    if alt.lower() in PLACEHOLDER_ALTS:
        return False, f"<area> placeholder alt: '{alt}'"
    return True, ""


def check_svg(tag) -> Tuple[bool, str]:
    """
    Inline <svg> should have an accessible name, one of:
      • role="presentation" / role="none"  – decorative, skip
      • aria-label or aria-labelledby      – accessible label
      • role="img" + <title> child         – standard SVG pattern
    Empty SVGs (no paths/shapes) are treated as decorative.
    """
    role = (tag.get("role") or "").strip().lower()

    if role in ("presentation", "none"):
        return True, ""
    if (tag.get("aria-label") or "").strip():
        return True, ""
    if (tag.get("aria-labelledby") or "").strip():
        return True, ""
    if role == "img" and tag.find("title"):
        return True, ""

    # Heuristic: SVG with no renderable children is likely decorative
    if not tag.find_all(["path", "use", "image", "circle", "rect",
                          "ellipse", "line", "polyline", "polygon"]):
        return True, ""

    return False, "SVG lacks accessible name (aria-label / role=img+<title> / role=presentation)"


# Map tag name → checker
TAG_CHECKERS = {
    "img":   check_img,
    "input": check_input_image,
    "area":  check_area,
    "svg":   check_svg,
}


# ── HTTP fetcher ──────────────────────────────────────────────────────────────

def fetch_page(url: str) -> Tuple[int, str]:
    """Fetch URL, return (status_code, html). Retries on transient errors."""
    for attempt in range(RETRY_COUNT + 1):
        try:
            resp = requests.get(
                url, headers=FETCH_HEADERS,
                timeout=REQUEST_TIMEOUT, allow_redirects=True
            )
            return resp.status_code, resp.text
        except requests.RequestException as exc:
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)
            else:
                raise


# ── Page analyser ─────────────────────────────────────────────────────────────

def analyse_page(url: str, title: str, http_status: str) -> PageResult:
    result = PageResult(url=url, title=title, http_status=http_status)

    # Skip pages already marked as broken in the crawl CSV
    try:
        if int(http_status) >= 400:
            result.result = "SKIP"
            return result
    except (ValueError, TypeError):
        pass

    try:
        live_status, html = fetch_page(url)
    except Exception as exc:
        result.result = "ERROR"
        result.issues = [ImageIssue(tag="fetch", src=url, problem=str(exc))]
        return result

    if live_status >= 400:
        result.result = "SKIP"
        return result

    soup = BeautifulSoup(html, "html.parser")

    for tag_name, checker in TAG_CHECKERS.items():
        for tag in soup.find_all(tag_name):
            passed, problem = checker(tag)

            # Build a readable source reference
            src = (
                tag.get("src") or tag.get("data-src") or
                tag.get("href") or tag.get("id") or "(inline)"
            )
            if src and not src.startswith(("http", "data:", "#", "(")):
                src = urljoin(url, src)
            # Truncate very long data URIs
            if src and src.startswith("data:"):
                src = src[:60] + "…"

            result.total_images += 1
            if passed:
                result.pass_count += 1
            else:
                result.fail_count += 1
                result.issues.append(ImageIssue(tag=tag_name, src=src, problem=problem))

    result.result = "PASS" if result.fail_count == 0 else "FAIL"
    return result


# ── CSV I/O ───────────────────────────────────────────────────────────────────

OUTPUT_FIELDS = [
    "URL", "Page Title", "HTTP Status",
    "Result", "Total Images", "Pass Count", "Fail Count", "Issues",
]

def load_csv(path: str) -> List[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def save_csv(results: List[PageResult], out_path: str):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "URL":          r.url,
                "Page Title":   r.title,
                "HTTP Status":  r.http_status,
                "Result":       r.result,
                "Total Images": r.total_images,
                "Pass Count":   r.pass_count,
                "Fail Count":   r.fail_count,
                "Issues":       r.issues_summary,
            })


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="WCAG 1.1.1 Alt-text compliance checker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input_csv",
                        help="CSV from crawler.py (needs columns: URL, Page Title, HTTP Status)")
    parser.add_argument("output_csv",
                        help="Output CSV — original columns plus Result, Total Images, "
                             "Pass Count, Fail Count, Issues")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Seconds between requests (default: 0.5)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only check first N rows (for testing)")
    args = parser.parse_args()

    rows = load_csv(args.input_csv)
    if args.limit:
        rows = rows[: args.limit]

    total   = len(rows)
    results = []
    counts  = {"PASS": 0, "FAIL": 0, "ERROR": 0, "SKIP": 0}

    SEP = "─" * 65
    print(f"\n{SEP}")
    print("  WCAG 1.1.1 — Non-Text Content Checker")
    print(f"  Input  : {args.input_csv}")
    print(f"  Output : {args.output_csv}")
    print(f"  Pages  : {total}")
    print(f"{SEP}\n")

    BADGE = {"PASS": "✓", "FAIL": "✗", "ERROR": "!", "SKIP": "–"}

    for idx, row in enumerate(rows, 1):
        url    = (row.get("URL") or "").strip()
        title  = (row.get("Page Title") or "").strip()
        status = (row.get("HTTP Status") or "").strip()

        if not url:
            counts["SKIP"] += 1
            results.append(PageResult(url=url, title=title,
                                       http_status=status, result="SKIP"))
            print(f"  [{idx:>4}/{total}]  – SKIP   (empty URL)")
            continue

        print(f"  [{idx:>4}/{total}]  … {url[:62]}", end="\r", flush=True)

        pr = analyse_page(url, title, status)
        results.append(pr)
        counts[pr.result] += 1

        badge = BADGE.get(pr.result, "?")
        note  = (f"  ({pr.fail_count} violation(s))" if pr.result == "FAIL"
                 else f"  ({pr.total_images} element(s))" if pr.result == "PASS"
                 else "")
        label = f"{badge} {pr.result:<5}"
        print(f"  [{idx:>4}/{total}]  {label}  {url[:60]}{note}       ")

        time.sleep(args.delay)

    save_csv(results, args.output_csv)

    print(f"\n{SEP}")
    print("  Summary")
    print(f"  ✓ PASS  : {counts['PASS']}")
    print(f"  ✗ FAIL  : {counts['FAIL']}")
    print(f"  ! ERROR : {counts['ERROR']}")
    print(f"  – SKIP  : {counts['SKIP']}")
    print(f"\n  Report saved → {args.output_csv}")
    print(f"{SEP}\n")

    sys.exit(1 if counts["FAIL"] or counts["ERROR"] else 0)


if __name__ == "__main__":
    main()