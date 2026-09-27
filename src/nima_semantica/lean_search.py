"""Optional LeanSearch HTTP discovery. Returned declarations are untrusted candidates."""
import json
import urllib.request
from typing import Annotated
from pydantic import Field, StrictBool, StrictInt
from .models import StrictModel, identity

LeanName = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*$", max_length=240)]


class LeanSearchPolicy(StrictModel):
    enabled: StrictBool = False
    allow_query_disclosure: StrictBool = False
    endpoint: str = Field(default="https://leansearch.net/search", max_length=2048)
    index_revision: str = Field(default="provider-unpinned", max_length=256)
    max_results: StrictInt = Field(default=5, ge=1, le=10)


class SearchLean(StrictModel):
    query: str = Field(min_length=1, max_length=2000)


def search_lean(policy, action):
    if not policy.enabled or not policy.allow_query_disclosure:
        raise ValueError("LeanSearch query disclosure is not authorized")
    if not policy.endpoint.startswith(("https://", "http://")):
        raise ValueError("operator LeanSearch endpoint must be HTTP(S)")
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    request = urllib.request.Request(policy.endpoint,
        data=json.dumps({"query": [action.query], "num_results": policy.max_results}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "NIMA-LeanSearch/1"})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("LeanSearch response exceeds bound")
    value = json.loads(raw)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], list):
        raise ValueError("LeanSearch response does not match batch-search protocol")
    candidates = []
    for row in value[0][:policy.max_results]:
        if not isinstance(row,dict):continue
        result = row.get("result", {})
        if not isinstance(result,dict):continue
        parts = result.get("name")
        if not isinstance(parts, list) or not all(isinstance(p, str) for p in parts):
            continue
        candidates.append({"name": ".".join(parts), "reported_type": str(result.get("type", ""))[:16000],
            "docstring": str(result.get("docstring", ""))[:4000], "doc_url": str(result.get("doc_url", ""))[:2048],
            "locally_resolved": False})
    return {"provider": "LeanSearch", "endpoint": policy.endpoint, "query": action.query,
        "index_revision": policy.index_revision, "response_sha256": identity(value), "candidates": candidates,
        "authority": "untrusted_discovery", "status": "available"}
