"""Bounded, fixed-host arXiv search-page fallback for an unavailable Atom API.

This does not impersonate the arXiv API. Callers retain its distinct provenance.
"""

import re
from urllib.parse import urlencode, urlsplit

import httpx
from bs4 import BeautifulSoup

from .paper_discovery import arxiv


SEARCH_ORIGIN = "https://arxiv.org"
MAX_PAGE_BYTES = 2_000_000


def parse_search_page(html: str, query: str, *, max_results: int = 5) -> list[dict]:
    """Extract observed arXiv identities from one public search-results page."""
    if not 1 <= max_results <= 10:
        raise ValueError("arXiv fallback result limit must be between 1 and 10")
    soup = BeautifulSoup(html, "html.parser")
    words = set(re.findall(r"[a-z0-9]{4,}", query.casefold()))
    rows = []
    seen = set()
    for item in soup.select("li.arxiv-result")[:50]:
        anchor = item.select_one('p.list-title a[href*="/abs/"]')
        title_element = item.select_one("p.title")
        if anchor is None or title_element is None:
            continue
        address = anchor.get("href", "")
        parts = urlsplit(address)
        identifier = arxiv(address) if parts.scheme == "https" and parts.hostname == "arxiv.org" else None
        title = title_element.get_text(" ", strip=True)
        if not identifier or not title or identifier in seen:
            continue
        seen.add(identifier)
        abstract_element = item.select_one("span.abstract-full") or item.select_one("span.abstract-short")
        abstract = abstract_element.get_text(" ", strip=True)[:6000] if abstract_element else ""
        score = sum(word in (title + " " + abstract).casefold() for word in words)
        rows.append((score, {"id": "https://arxiv.org/abs/" + identifier, "title": title,
            "summary": abstract, "pdf_url": "https://arxiv.org/pdf/" + identifier,
            "source": "arxiv_search_html_fallback"}))
    rows.sort(key=lambda item: (-item[0], item[1]["id"]))
    return [row for _, row in rows[:max_results]]


def search_page(query: str, *, max_results: int = 5, client=None) -> list[dict]:
    """Fetch one capped public-search page; never follow redirects or other hosts."""
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 160 or any(ord(char) < 32 for char in query):
        raise ValueError("arXiv fallback requires a short printable search phrase")
    if not 1 <= max_results <= 10:
        raise ValueError("arXiv fallback result limit must be between 1 and 10")
    address = SEARCH_ORIGIN + "/search/?" + urlencode({"query": query.strip(), "searchtype": "all",
        "abstracts": "show", "order": "-announced_date_first", "size": 50})
    if client is None:
        with httpx.Client(timeout=20, follow_redirects=False,
            headers={"User-Agent": "NIMA-Semantica/0.1 (bounded research metadata search)", "Accept": "text/html"}) as connection:
            response = connection.get(address)
    else:
        response = client.get(address)
    if response.status_code != 200:
        raise RuntimeError("arXiv search page unavailable: HTTP " + str(response.status_code))
    if len(response.content) > MAX_PAGE_BYTES or "text/html" not in response.headers.get("content-type", ""):
        raise RuntimeError("arXiv search page has unexpected size or media type")
    rows = parse_search_page(response.text, query, max_results=max_results)
    if not rows:
        raise RuntimeError("arXiv search page had no parseable paper identities")
    return rows
