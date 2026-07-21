"""Shared text-extraction helpers: HTML stripping, phone/email regexes.

Used by both the enrichment worker (website analysis) and the web-crawl
connector (candidate-page extraction) so the two don't drift apart.
"""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"[\+\d][\d\s\-\(\)\.]{7,}\d")
DATE_RE = re.compile(r"^\s*\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}\s*$")

# Only probe the highest-signal contact pages; the first one that responds wins.
CONTACT_PATHS = ["/iletisim", "/contact", "/kontakt", "/impressum", "/hakkimizda"]


def strip_html(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text
