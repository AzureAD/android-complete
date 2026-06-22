"""
Content sanitisation helpers for ICM discussion entries.

Applies the same three-step pipeline used in DRICopilot:
  1. Strip HTML → readable text (preserving links as Markdown).
  2. Replace inline base64 images with numbered placeholders.
  3. Truncate over-long entries to a character limit.
"""

import json
import re
from typing import List, Tuple

from bs4 import BeautifulSoup, Tag

# ---------------------------------------------------------------------------
# 1. HTML → text (preserving links & tables)
# ---------------------------------------------------------------------------

def _html_table_to_json(table: Tag, max_rows: int = 30) -> str:
    """Convert an HTML <table> to a compact JSON string."""
    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if cells:
            rows.append(cells)
    rows = rows[:max_rows]
    if not rows:
        return ""
    if headers:
        return json.dumps([dict(zip(headers, r)) for r in rows], indent=1)
    return json.dumps(rows, indent=1)


def extract_text_preserving_links(html_content: str) -> str:
    """Convert HTML to plain text while preserving links as Markdown.

    Mirrors ``ticket_util.extract_text_preserving_links`` from the main
    DRICopilot codebase.
    """
    if not html_content:
        return ""

    soup = BeautifulSoup(html_content, features="html.parser")

    # Convert tables to JSON before stripping
    tables_json: list[str] = []
    for table in soup.find_all("table"):
        tj = _html_table_to_json(table)
        if tj:
            tables_json.append(tj)
        table.decompose()

    # Convert <a> tags to Markdown links
    for link in soup.find_all("a"):
        href = link.get("href")
        if href and href.strip():
            link_text = link.get_text(strip=True)
            if link_text:
                link.replace_with(f"[{link_text}]({href})")
            else:
                link.replace_with(f"[{href}]({href})")
        else:
            link_text = link.get_text(strip=True)
            if link_text:
                link.replace_with(link_text)
            else:
                link.decompose()

    result = soup.get_text(separator=" ", strip=True)
    if tables_json:
        result = result.rstrip() + "\n\n" + "\n\n".join(tables_json)
    return result


# ---------------------------------------------------------------------------
# 2. Base64 image replacement
# ---------------------------------------------------------------------------

_BASE64_IMAGE_RE = re.compile(
    r"data:image/(?:png|jpg|jpeg|gif|bmp|webp|svg\+xml);base64,"
    r"[A-Za-z0-9+/=\r\n]+",
    re.IGNORECASE,
)

MAX_EXTRACTED_IMAGES = 20


def sanitize_inline_images(
    text: str,
    image_prefix: str = "IMAGE",
    max_images: int = MAX_EXTRACTED_IMAGES,
) -> Tuple[str, List[str]]:
    """Replace inline ``data:image`` base64 blobs with ``[IMAGE_N]`` placeholders.

    Returns ``(sanitized_text, list_of_extracted_base64_strings)``.
    """
    if not text:
        return text, []

    matches = list(_BASE64_IMAGE_RE.finditer(text))
    if not matches:
        return text, []

    extracted: List[str] = []
    sanitized = text
    for idx, m in enumerate(matches[:max_images]):
        full = m.group(0)
        extracted.append(full)
        placeholder = f"[{image_prefix}_{idx}]"
        sanitized = sanitized.replace(full, placeholder, 1)

    # Remove any remaining images beyond max
    if len(matches) > max_images:
        sanitized = _BASE64_IMAGE_RE.sub("", sanitized)
        sanitized += f" [{len(matches) - max_images} more images removed]"

    return sanitized, extracted


# ---------------------------------------------------------------------------
# 3. Truncation
# ---------------------------------------------------------------------------

DEFAULT_MAX_ENTRY_LEN = 10_000


def truncate_entry(text: str, max_len: int = DEFAULT_MAX_ENTRY_LEN) -> str:
    """Truncate a single text entry to *max_len* characters."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "… [truncated]"


# ---------------------------------------------------------------------------
# Combined pipeline
# ---------------------------------------------------------------------------

def sanitize_discussion_entry(
    raw_html: str,
    max_len: int = DEFAULT_MAX_ENTRY_LEN,
) -> str:
    """Run the full 3-step sanitisation on a single discussion entry.

    1. Strip HTML → text (preserve links).
    2. Replace base64 images with placeholders.
    3. Truncate to *max_len* characters.
    """
    text = extract_text_preserving_links(raw_html)
    text, _ = sanitize_inline_images(text)
    text = truncate_entry(text, max_len)
    return text
