"""Bounded arXiv metadata transport; no model calls or paper acquisition."""
from datetime import datetime, timezone
import fcntl
import hashlib
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

import httpx

API = "https://export.arxiv.org/api/query"


def parse_metadata(raw: bytes) -> list[dict]:
    if len(raw) > 2_000_000 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("unsafe or oversized arXiv Atom feed")
    root = ET.fromstring(raw)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    if root.tag != "{http://www.w3.org/2005/Atom}feed":
        raise ValueError("expected arXiv Atom feed")
    hits = []
    for entry in root.findall("a:entry", ns):
        identifier = entry.findtext("a:id", "", ns).split("/abs/", 1)[-1]
        if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v[1-9]\d*)?", identifier):
            raise ValueError("invalid arXiv identifier or API error entry")
        title = entry.findtext("a:title", "", ns).strip()
        abstract = entry.findtext("a:summary", "", ns).strip()
        if not title or not abstract:
            raise ValueError("arXiv entry lacks title or abstract")
        hits.append({"arxiv_id": identifier, "title": title[:1000], "abstract": abstract[:3000]})
    if len(hits) > 5 or len({hit["arxiv_id"] for hit in hits}) != len(hits):
        raise ValueError("arXiv hit count or identity mismatch")
    return hits


def fetch_metadata(query: str) -> dict:
    """One fixed-host request, serialized across this host's NIMA workers."""
    url = API + "?" + urlencode({"search_query": query, "start": 0, "max_results": 5,
                                 "sortBy": "relevance", "sortOrder": "descending"})
    receipt = {"url": url, "retrieved_at": datetime.now(timezone.utc).isoformat(),
               "hits": [], "outcome": "validation_unavailable"}
    # The lock covers request duration and the interval between requests, including other processes.
    lock_path = Path(tempfile.gettempdir()) / f"nima-arxiv-metadata-{os.getuid()}.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            time.sleep(3.1)
            with httpx.Client(timeout=30, follow_redirects=False, trust_env=False,
                              headers={"User-Agent": "NIMA-DeepResearch/1.0 (arXiv metadata query validation)"}) as client:
                with client.stream("GET", url) as response:
                    receipt["status_code"] = response.status_code
                    if response.headers.get("Retry-After"):
                        receipt["retry_after"] = response.headers["Retry-After"]
                    response.raise_for_status()
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 2_000_000:
                            raise ValueError("arXiv response exceeds byte budget")
            receipt.update(hits=parse_metadata(bytes(raw)), response_sha256=hashlib.sha256(raw).hexdigest(), outcome="metadata_ready")
        except (httpx.HTTPError, ValueError, ET.ParseError) as error:
            receipt["error"] = type(error).__name__
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    return receipt
