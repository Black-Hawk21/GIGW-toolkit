#!/usr/bin/env python3
"""
contrast_checker.py – WCAG 1.4.3 / 1.4.6 Colour-Contrast Auditor
────────────────────────────────────────────────────────────────────
Reads the crawler CSV produced by crawler.py, fetches each live page,
extracts foreground/background colour pairs from visible text elements,
computes contrast ratios, and reports PASS / FAIL against configurable
WCAG thresholds.

Pipeline position:  crawler.py  →  contrast_checker.py

WCAG reference
──────────────
  SC 1.4.3 (AA)   Normal text  ≥ 4.5 : 1   |  Large text  ≥ 3.0 : 1
  SC 1.4.6 (AAA)  Normal text  ≥ 7.0 : 1   |  Large text  ≥ 4.5 : 1

"Large text" = ≥ 18 pt (24 px) regular  OR  ≥ 14 pt (18.67 px) bold.

Usage
─────
  python contrast_checker.py input.csv
  python contrast_checker.py input.csv --level AAA
  python contrast_checker.py input.csv --output results/ --workers 4
  python contrast_checker.py input.csv --no-verify --sample 5
  python contrast_checker.py input.csv --custom-threshold 5.0

Dependencies:  requests, beautifulsoup4, tinycss2
  pip install requests beautifulsoup4 tinycss2
"""

import argparse
import csv
import logging
import math
import os
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# WCAG thresholds
# ─────────────────────────────────────────────────────────────
THRESHOLDS = {
    "AA":  {"normal": 4.5, "large": 3.0},
    "AAA": {"normal": 7.0, "large": 4.5},
}

# Pixel sizes that qualify as "large text"
LARGE_TEXT_NORMAL_PX = 24.0   # 18 pt
LARGE_TEXT_BOLD_PX   = 18.67  # 14 pt

# ─────────────────────────────────────────────────────────────
# Colour helpers
# ─────────────────────────────────────────────────────────────

# Named CSS colours – a broad subset covering common usage
CSS_NAMED_COLOURS: dict[str, str] = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000",
    "green": "#008000", "blue": "#0000ff", "yellow": "#ffff00",
    "cyan": "#00ffff", "magenta": "#ff00ff", "orange": "#ffa500",
    "purple": "#800080", "pink": "#ffc0cb", "brown": "#a52a2a",
    "grey": "#808080", "gray": "#808080", "silver": "#c0c0c0",
    "gold": "#ffd700", "navy": "#000080", "teal": "#008080",
    "maroon": "#800000", "olive": "#808000", "lime": "#00ff00",
    "aqua": "#00ffff", "fuchsia": "#ff00ff", "coral": "#ff7f50",
    "salmon": "#fa8072", "khaki": "#f0e68c", "indigo": "#4b0082",
    "violet": "#ee82ee", "turquoise": "#40e0d0", "tan": "#d2b48c",
    "wheat": "#f5deb3", "ivory": "#fffff0", "snow": "#fffafa",
    "beige": "#f5f5dc", "linen": "#faf0e6", "lavender": "#e6e6fa",
    "transparent": "#ffffff",   # treat transparent as white for contrast
}


def parse_colour(value: str) -> Optional[tuple[int, int, int]]:
    """
    Parse a CSS colour string and return (R, G, B) integers 0–255,
    or None if the value cannot be parsed.

    Supports: #RGB, #RRGGBB, #RRGGBBAA, rgb(), rgba(), named colours.
    """
    if not value:
        return None

    value = value.strip().lower()

    # Named colour
    if value in CSS_NAMED_COLOURS:
        value = CSS_NAMED_COLOURS[value]

    # #RGB  → #RRGGBB
    if re.match(r"^#[0-9a-f]{3}$", value):
        r, g, b = value[1], value[2], value[3]
        value = f"#{r}{r}{g}{g}{b}{b}"

    # #RRGGBB or #RRGGBBAA
    m = re.match(r"^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})(?:[0-9a-f]{2})?$", value)
    if m:
        return int(m.group(1), 16), int(m.group(2), 16), int(m.group(3), 16)

    # rgb(R, G, B)  /  rgb(R G B)
    m = re.match(r"^rgba?\(\s*([\d.]+%?)\s*[,\s]\s*([\d.]+%?)\s*[,\s]\s*([\d.]+%?)[\s,/]*[\d.]*%?\s*\)$", value)
    if m:
        def channel(v: str) -> int:
            if v.endswith("%"):
                return round(float(v[:-1]) / 100 * 255)
            return min(255, max(0, round(float(v))))
        return channel(m.group(1)), channel(m.group(2)), channel(m.group(3))

    return None


def relative_luminance(r: int, g: int, b: int) -> float:
    """WCAG 2.x relative luminance formula."""
    def linearise(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    return 0.2126 * linearise(r) + 0.7152 * linearise(g) + 0.0722 * linearise(b)


def contrast_ratio(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> float:
    """
    WCAG contrast ratio between two RGB colours.
    Returns a value in [1.0, 21.0].
    """
    l1 = relative_luminance(*fg)
    l2 = relative_luminance(*bg)
    lighter = max(l1, l2)
    darker  = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


# ─────────────────────────────────────────────────────────────
# Font-size / large-text helpers
# ─────────────────────────────────────────────────────────────

def px_from_font_size(style_value: str) -> Optional[float]:
    """
    Extract a pixel-equivalent size from a font-size CSS value.
    Supports: px, pt, em (relative to 16 px default), rem, %.
    Returns None if unparseable.
    """
    if not style_value:
        return None
    style_value = style_value.strip().lower()

    # Strip !important
    style_value = style_value.replace("!important", "").strip()

    # Keywords → approximate px
    kw = {
        "xx-small": 9, "x-small": 10, "small": 13, "medium": 16,
        "large": 18, "x-large": 24, "xx-large": 32, "xxx-large": 48,
        "smaller": 13, "larger": 19,
    }
    if style_value in kw:
        return float(kw[style_value])

    m = re.match(r"^([\d.]+)(px|pt|em|rem|%)$", style_value)
    if not m:
        return None
    val, unit = float(m.group(1)), m.group(2)
    if unit == "px":  return val
    if unit == "pt":  return val * (96 / 72)
    if unit in ("em", "rem"): return val * 16
    if unit == "%":   return val / 100 * 16
    return None


def is_large_text(font_size_px: Optional[float], is_bold: bool) -> bool:
    """Return True if the text qualifies as "large" under WCAG."""
    if font_size_px is None:
        return False
    if is_bold and font_size_px >= LARGE_TEXT_BOLD_PX:
        return True
    if font_size_px >= LARGE_TEXT_NORMAL_PX:
        return True
    return False


# ─────────────────────────────────────────────────────────────
# Inline-style parser (no tinycss2 needed for simple cases)
# ─────────────────────────────────────────────────────────────

def parse_inline_style(style: str) -> dict[str, str]:
    """Return a dict of {property: value} from an inline style attribute."""
    result: dict[str, str] = {}
    if not style:
        return result
    for declaration in style.split(";"):
        if ":" in declaration:
            prop, _, val = declaration.partition(":")
            result[prop.strip().lower()] = val.strip()
    return result


# ─────────────────────────────────────────────────────────────
# Page fetching
# ─────────────────────────────────────────────────────────────

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (compatible; GIGWContrastChecker/1.0; "
        "+https://gigw.gov.in)"
    )
})


def fetch_page(url: str, timeout: int = 20, verify: bool = True) -> Optional[str]:
    """Fetch a URL and return the HTML body text, or None on error."""
    try:
        resp = SESSION.get(url, timeout=timeout, verify=verify, allow_redirects=True)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            log.debug("Skipping non-HTML content-type: %s for %s", content_type, url)
            return None
        return resp.text
    except requests.exceptions.SSLError:
        log.warning("SSL error for %s – retry with --no-verify", url)
    except requests.exceptions.ConnectionError:
        log.warning("Connection error for %s", url)
    except requests.exceptions.Timeout:
        log.warning("Timeout for %s", url)
    except requests.exceptions.HTTPError as e:
        log.warning("HTTP %s for %s", e.response.status_code, url)
    except Exception as e:
        log.warning("Unexpected error fetching %s: %s", url, e)
    return None


# ─────────────────────────────────────────────────────────────
# Core analysis
# ─────────────────────────────────────────────────────────────

# Tags that commonly render visible text
TEXT_TAGS = {
    "p", "span", "div", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "td", "th", "dt", "dd", "caption", "label", "legend",
    "a", "button", "strong", "em", "b", "i", "small", "mark",
    "abbr", "cite", "q", "blockquote", "figcaption", "summary",
    "nav", "header", "footer", "main", "article", "section",
}

# Default browser rendering colours
DEFAULT_FG = (0, 0, 0)       # black
DEFAULT_BG = (255, 255, 255)  # white


def _resolve_colour_from_ancestors(
    element,
    prop: str,
    default: tuple[int, int, int],
) -> tuple[int, int, int]:
    """
    Walk up the DOM looking for an explicit inline colour property.
    Falls back to default if none is found.
    """
    node = element
    while node and node.name:
        style_str = node.get("style", "")
        if style_str:
            styles = parse_inline_style(style_str)
            raw = styles.get(prop)
            if raw:
                parsed = parse_colour(raw)
                if parsed:
                    return parsed
        node = node.parent
    return default


def analyse_page(
    url: str,
    html: str,
    level: str,
    custom_threshold: Optional[float],
) -> list[dict]:
    """
    Parse *html*, extract colour pairs, compute contrast ratios, and
    return a list of finding dicts (one per element checked).
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-visible content
    for tag in soup.find_all(["script", "style", "noscript", "template",
                               "meta", "head", "svg", "iframe"]):
        tag.decompose()

    thresholds = THRESHOLDS.get(level, THRESHOLDS["AA"])
    findings = []

    seen_pairs: set[tuple] = set()   # deduplicate identical colour pairs per page

    for tag in soup.find_all(TEXT_TAGS):
        text = tag.get_text(strip=True)
        if not text or len(text) < 3:
            continue

        # ── Gather inline style ──────────────────────────────
        inline = parse_inline_style(tag.get("style", ""))

        # Foreground colour
        fg_raw = inline.get("color")
        if fg_raw:
            fg = parse_colour(fg_raw)
        else:
            fg = _resolve_colour_from_ancestors(tag.parent, "color", DEFAULT_FG)

        # Background colour
        bg_raw = inline.get("background-color") or inline.get("background")
        if bg_raw:
            bg = parse_colour(bg_raw)
        else:
            bg = _resolve_colour_from_ancestors(tag.parent, "background-color", DEFAULT_BG)

        if fg is None:
            fg = DEFAULT_FG
        if bg is None:
            bg = DEFAULT_BG

        # Skip pairs already seen on this page
        pair_key = (fg, bg)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        # ── Font size / weight ───────────────────────────────
        font_size_str = inline.get("font-size")
        font_size_px  = px_from_font_size(font_size_str) if font_size_str else None

        # Heading tags count as large by default (typically ≥ 18 px)
        if tag.name in ("h1", "h2", "h3") and font_size_px is None:
            font_size_px = 32.0  # conservative heading size
        elif tag.name in ("h4", "h5", "h6") and font_size_px is None:
            font_size_px = 18.67  # h4–h6 ~ 14 pt bold

        font_weight_str = inline.get("font-weight", "")
        bold = (
            font_weight_str in ("bold", "bolder")
            or (font_weight_str.isdigit() and int(font_weight_str) >= 700)
            or tag.name in ("b", "strong", "h1", "h2", "h3", "h4", "h5", "h6")
        )

        large = is_large_text(font_size_px, bold)
        text_size_label = "large" if large else "normal"

        # ── Contrast calculation ─────────────────────────────
        ratio = contrast_ratio(fg, bg)

        if custom_threshold is not None:
            required = custom_threshold
        else:
            required = thresholds["large"] if large else thresholds["normal"]

        result = "PASS" if ratio >= required else "FAIL"

        fg_hex = "#{:02x}{:02x}{:02x}".format(*fg)
        bg_hex = "#{:02x}{:02x}{:02x}".format(*bg)

        findings.append({
            "url":          url,
            "element":      tag.name,
            "text_sample":  text[:60].replace("\n", " "),
            "fg_colour":    fg_hex,
            "bg_colour":    bg_hex,
            "contrast_ratio": f"{ratio:.2f}",
            "required_ratio": f"{required:.1f}",
            "text_size":    text_size_label,
            "wcag_level":   level,
            "result":       result,
        })

    return findings


# ─────────────────────────────────────────────────────────────
# CSV I/O
# ─────────────────────────────────────────────────────────────

INPUT_URL_COL    = "URL"
INPUT_STATUS_COL = "HTTP Status"

DETAIL_FIELDS = [
    "url", "element", "text_sample", "fg_colour", "bg_colour",
    "contrast_ratio", "required_ratio", "text_size", "wcag_level", "result",
]

SUMMARY_FIELDS = [
    "url", "page_title", "total_checks", "pass_count", "fail_count",
    "fail_rate_%", "min_contrast", "max_contrast", "page_result",
]


def read_input_csv(path: str) -> list[dict]:
    """Read the crawler CSV and return rows where HTTP Status is 200."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            status = row.get(INPUT_STATUS_COL, "").strip()
            url    = row.get(INPUT_URL_COL, "").strip()
            if url and status == "200":
                rows.append(row)
    return rows


def write_detail_csv(path: str, findings: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DETAIL_FIELDS)
        writer.writeheader()
        writer.writerows(findings)


def write_summary_csv(path: str, summaries: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(summaries)


# ─────────────────────────────────────────────────────────────
# Per-page processing
# ─────────────────────────────────────────────────────────────

def process_page(
    row: dict,
    level: str,
    custom_threshold: Optional[float],
    verify_ssl: bool,
    timeout: int,
) -> tuple[list[dict], dict]:
    """
    Fetch and analyse a single page.
    Returns (findings_list, summary_dict).
    """
    url   = row[INPUT_URL_COL].strip()
    title = row.get("Page Title", "").strip()

    empty_summary = {
        "url": url, "page_title": title,
        "total_checks": 0, "pass_count": 0, "fail_count": 0,
        "fail_rate_%": "N/A", "min_contrast": "N/A", "max_contrast": "N/A",
        "page_result": "ERROR",
    }

    html = fetch_page(url, timeout=timeout, verify=verify_ssl)
    if html is None:
        log.warning("Skipped (fetch failed): %s", url)
        empty_summary["page_result"] = "SKIP"
        return [], empty_summary

    findings = analyse_page(url, html, level, custom_threshold)

    if not findings:
        log.info("No checkable text pairs found on: %s", url)
        empty_summary["page_result"] = "NO_DATA"
        return [], empty_summary

    ratios        = [float(f["contrast_ratio"]) for f in findings]
    pass_count    = sum(1 for f in findings if f["result"] == "PASS")
    fail_count    = len(findings) - pass_count
    fail_rate     = (fail_count / len(findings)) * 100 if findings else 0
    page_result   = "PASS" if fail_count == 0 else "FAIL"

    summary = {
        "url":          url,
        "page_title":   title,
        "total_checks": len(findings),
        "pass_count":   pass_count,
        "fail_count":   fail_count,
        "fail_rate_%":  f"{fail_rate:.1f}",
        "min_contrast": f"{min(ratios):.2f}",
        "max_contrast": f"{max(ratios):.2f}",
        "page_result":  page_result,
    }

    log.info(
        "[%s] %s – %d checks, %d FAIL (min ratio %.2f)",
        page_result, url, len(findings), fail_count, min(ratios),
    )
    return findings, summary


# ─────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contrast_checker.py",
        description=(
            "WCAG 1.4.3 / 1.4.6 colour-contrast auditor.\n"
            "Reads crawler.py CSV output and reports contrast pass/fail."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input_csv",
        help="Path to the crawler CSV file (must have a 'URL' column).",
    )
    parser.add_argument(
        "--level",
        choices=["AA", "AAA"],
        default="AA",
        help=(
            "WCAG conformance level to test against.  "
            "AA (default): normal ≥4.5, large ≥3.0.  "
            "AAA: normal ≥7.0, large ≥4.5."
        ),
    )
    parser.add_argument(
        "--custom-threshold",
        type=float,
        default=None,
        metavar="RATIO",
        help=(
            "Override WCAG thresholds with a single custom contrast ratio "
            "applied to ALL text (e.g. --custom-threshold 5.0).  "
            "Overrides --level for the pass/fail decision."
        ),
    )
    parser.add_argument(
        "--output",
        default=".",
        metavar="DIR",
        help="Directory to write output CSV files (default: current directory).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=3,
        metavar="N",
        help="Number of parallel fetch workers (default: 3; max recommended: 8).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        metavar="SECS",
        help="HTTP request timeout in seconds (default: 20).",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Disable SSL certificate verification (useful for internal/staging sites).",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        metavar="N",
        help="Only process the first N URLs (useful for quick spot-checks).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        metavar="SECS",
        help="Polite delay between requests per worker thread (default: 0.5 s).",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args   = parser.parse_args()

    # ── Validate inputs ──────────────────────────────────────
    if not os.path.isfile(args.input_csv):
        log.error("Input CSV not found: %s", args.input_csv)
        return 1

    os.makedirs(args.output, exist_ok=True)

    # ── Load URLs ────────────────────────────────────────────
    all_rows = read_input_csv(args.input_csv)
    if not all_rows:
        log.error(
            "No rows with HTTP Status 200 found in %s.\n"
            "Ensure the CSV has a '%s' column with value '200'.",
            args.input_csv, INPUT_STATUS_COL,
        )
        return 1

    rows = all_rows[: args.sample] if args.sample else all_rows
    log.info(
        "Loaded %d URL(s) from %s (total rows with status 200: %d)",
        len(rows), args.input_csv, len(all_rows),
    )

    if args.custom_threshold:
        log.info(
            "Using custom contrast threshold: %.2f : 1  (overrides --level)",
            args.custom_threshold,
        )
    else:
        t = THRESHOLDS[args.level]
        log.info(
            "WCAG level %s  |  normal text ≥ %.1f : 1  |  large text ≥ %.1f : 1",
            args.level, t["normal"], t["large"],
        )

    verify_ssl = not args.no_verify

    # ── Process pages ────────────────────────────────────────
    all_findings: list[dict]  = []
    all_summaries: list[dict] = []

    def _worker(row: dict) -> tuple[list[dict], dict]:
        time.sleep(args.delay)
        return process_page(
            row,
            level=args.level,
            custom_threshold=args.custom_threshold,
            verify_ssl=verify_ssl,
            timeout=args.timeout,
        )

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_worker, row): row for row in rows}
        done    = 0
        for future in as_completed(futures):
            done += 1
            try:
                findings, summary = future.result()
                all_findings.extend(findings)
                all_summaries.append(summary)
            except Exception as e:
                row = futures[future]
                log.error("Worker error for %s: %s", row.get(INPUT_URL_COL), e)
            if done % 10 == 0 or done == len(rows):
                log.info("Progress: %d / %d pages processed", done, len(rows))

    # ── Write output ─────────────────────────────────────────
    ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
    detail_path  = os.path.join(args.output, f"contrast_detail_{ts}.csv")
    summary_path = os.path.join(args.output, f"contrast_summary_{ts}.csv")

    write_detail_csv(detail_path,   all_findings)
    write_summary_csv(summary_path, all_summaries)

    # ── Print final stats ─────────────────────────────────────
    total_pages   = len(all_summaries)
    pages_pass    = sum(1 for s in all_summaries if s["page_result"] == "PASS")
    pages_fail    = sum(1 for s in all_summaries if s["page_result"] == "FAIL")
    pages_skip    = sum(1 for s in all_summaries if s["page_result"] in ("SKIP", "ERROR", "NO_DATA"))
    total_checks  = sum(f.get("total_checks", 0) for f in all_summaries)
    total_fails   = sum(f.get("fail_count",   0) for f in all_summaries)

    print("\n" + "═" * 60)
    print("  CONTRAST AUDIT COMPLETE")
    print("═" * 60)
    print(f"  Pages processed : {total_pages}")
    print(f"  Pages PASS      : {pages_pass}")
    print(f"  Pages FAIL      : {pages_fail}")
    print(f"  Pages SKIP/ERR  : {pages_skip}")
    print(f"  Total pairs checked : {total_checks}")
    print(f"  Total FAIL pairs    : {total_fails}")
    print("─" * 60)
    print(f"  Detail CSV  → {detail_path}")
    print(f"  Summary CSV → {summary_path}")
    print("═" * 60 + "\n")

    return 0 if pages_fail == 0 else 2   # exit 2 = audit failures found


if __name__ == "__main__":
    sys.exit(main())
