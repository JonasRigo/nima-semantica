"""Fast, provisional literature reading. No source-region or index publication."""

from html.parser import HTMLParser
from pathlib import PurePosixPath
import re
import subprocess
import sys
from urllib.parse import urlsplit

from .literature_acquisition import LiteratureAcquisitionRequest, LiteratureAcquisitionService
from .models import ConflictError, Record, canonical, identity
from .paper_discovery import preferred_full_text_urls


class _VisibleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "nav", "footer"}:
            self.hidden += 1
        elif tag in {"p", "div", "section", "article", "h1", "h2", "h3", "li", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "nav", "footer"} and self.hidden:
            self.hidden -= 1
        elif tag in {"p", "div", "section", "article", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def fast_text(data: bytes, name: str) -> tuple[str, str]:
    """Use text-layer extraction only; absent/OCR-only text fails explicitly."""
    suffix = name.lower().split("?")[0]
    if data.startswith(b"%PDF-"):
        try:
            converted = subprocess.run(["pdftotext", "-layout", "-", "-"], input=data,
                capture_output=True, timeout=45, check=False)
        except FileNotFoundError:
            converted = subprocess.run([sys.executable, "-m", "nima_semantica.deep_research_fast_pdf"],
                input=data, capture_output=True, timeout=50, check=False,
                env={"PYTHONPATH": str(PurePosixPath(__file__).parent.parent), "PYTHONNOUSERSITE": "1"})
            if converted.returncode != 0:
                raise ValueError("fast PDF text extraction failed")
            text = converted.stdout.decode("utf-8", errors="replace")
            method = "pypdf-text-v1"
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("fast PDF text extraction unavailable") from exc
        else:
            if converted.returncode != 0:
                raise ValueError("fast PDF text extraction failed")
            text = converted.stdout.decode("utf-8", errors="replace")
            method = "pdftotext-layout-v1"
    elif suffix.endswith((".html", ".htm")) or data.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        parser = _VisibleHTML()
        parser.feed(data.decode("utf-8", errors="replace"))
        text = "".join(parser.parts)
        method = "html-visible-text-v1"
    else:
        text = data.decode("utf-8", errors="strict")
        method = "utf8-text-v1"
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text.encode("utf-8")) > 4_000_000 or text.count("\f") > 150:
        raise ValueError("fast-read text exceeds output limits")
    if len(text) < 200:
        raise ValueError("fast reading found insufficient text; full preparation may be required")
    return text, method


def _spans(text: str, query: str, *, limit: int, chars: int):
    words = set(re.findall(r"[a-z0-9]{4,}", query.casefold()))
    candidates = []
    for page, page_text in enumerate(text.split("\f"), start=1):
        for start in range(0, len(page_text), chars):
            raw = page_text[start:start + chars]
            offset = start + len(raw) - len(raw.lstrip())
            excerpt = raw.strip()
            if len(excerpt) < 80:
                continue
            score = sum(excerpt.casefold().count(word) for word in words)
            candidates.append((-score, page, offset, excerpt))
    return sorted(candidates)[:limit]


def stage_fast_paper(store, request, context, candidate, query: str):
    """Retain acquired bytes and cited excerpts privately, without corpus preparation."""
    scope = {"corpus_id": context.corpus_id, "project_id": context.project_id}
    attempts = []
    for address in preferred_full_text_urls(candidate):
        parsed = urlsplit(address)
        if parsed.scheme != "https" or parsed.hostname not in context.acquisition.domains:
            attempts.append({"url": address, "status": "domain_not_authorized"})
            continue
        key = identity((request.operation_id, candidate["candidate_id"], address, "fast"))
        acquired = LiteratureAcquisitionService(store).execute(LiteratureAcquisitionRequest(**scope, url=address,
            policy=context.acquisition, motivating_gap="Provisional research review", idempotency_key=key,
            staging_only=True))
        if acquired.status != "completed":
            attempts.append({"url": address, "status": acquired.status, "receipt_id": acquired.receipt_id})
            continue
        try:
            original = store.read_artifact(acquired.result["artifact_id"])
            if original.startswith(b"%PDF-") and not context.allow_pdf:
                attempts.append({"url": address, "status": "pdf_not_authorized"})
                continue
            text, method = fast_text(original, acquired.result["name"])
            normalized_id = store.artifact(text.encode("utf-8"))
            passages = []
            for _, page, start, excerpt in _spans(text, query, limit=min(context.max_regions, 8), chars=3000):
                content = {"candidate_id": candidate["candidate_id"], "url": address,
                    "original_artifact_id": acquired.result["artifact_id"],
                    "normalized_artifact_id": normalized_id, "method": method,
                    "page": page, "start": start, "text": excerpt, "text_hash": identity(excerpt),
                    "acquisition_receipt_id": acquired.receipt_id, "authority": "provisional_fast_read"}
                passage_id = store.put(Record(kind="ResearchFastPassage", **scope, content=content))
                passages.append(passage_id)
            if not passages:
                raise ValueError("fast reading produced no usable passages")
            acquired_name = acquired.result["name"]
            suffix = PurePosixPath(acquired_name).suffix.lower()
            if original.startswith(b"%PDF-"):
                suffix = ".pdf"
            elif original.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                suffix = ".html"
            if suffix not in {".pdf", ".html", ".htm", ".txt", ".md", ".tex"}:
                suffix = ".txt"
            source_input = {"name": "paper-" + acquired.result["artifact_id"][:24] + suffix,
                "acquired_name": acquired_name, "artifact_id": acquired.result["artifact_id"],
                "acquisition_id": acquired.result["acquisition_id"], "document_id": None}
            return {"candidate_id": candidate["candidate_id"], "status": "provisional_read",
                "passage_ids": passages, "artifact_id": acquired.result["artifact_id"],
                "acquisition_id": acquired.result["acquisition_id"], "method": method,
                "prepare_source_input": source_input,
                "attempts": attempts}
        except Exception as exc:
            attempts.append({"url": address, "status": "fast_read_failed", "diagnostic": type(exc).__name__})
    return {"candidate_id": candidate["candidate_id"], "status": "unavailable", "passage_ids": [],
        "attempts": attempts}


def exact_fast_passage(store, passage_id: str, *, corpus_id: str, project_id: str):
    record = store.get(passage_id, corpus_id=corpus_id, project_id=project_id)
    if record is None or record.kind != "ResearchFastPassage":
        raise ConflictError("provisional passage is missing or outside scope")
    item = record.content
    text = store.read_artifact(item["normalized_artifact_id"]).decode("utf-8")
    page_text = text.split("\f")[item["page"] - 1]
    excerpt = page_text[item["start"]:item["start"] + len(item["text"])].strip()
    if excerpt != item["text"] or identity(excerpt) != item["text_hash"]:
        raise ConflictError("provisional passage no longer matches retained text")
    store.read_artifact(item["original_artifact_id"])
    return item


def fast_evidence(store, ingestions, context):
    selected = []
    for item in ingestions:
        for passage_id in item.get("passage_ids", ()):
            passage = exact_fast_passage(store, passage_id, corpus_id=context.corpus_id,
                project_id=context.project_id)
            if sum(len(row["text"]) for row in selected) + len(passage["text"]) > context.max_chars:
                continue
            selected.append({"region_id": passage_id, "candidate_id": item["candidate_id"],
                "text": passage["text"], "source_revision": passage["normalized_artifact_id"],
                "evidence": {"url": passage["url"], "page": passage["page"],
                    "start": passage["start"], "original_artifact_id": passage["original_artifact_id"],
                    "normalized_artifact_id": passage["normalized_artifact_id"],
                    "method": passage["method"], "authority": "provisional_fast_read"}})
    return selected
