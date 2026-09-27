"""Small fixed-provider discovery boundary; metadata is never scientific evidence."""
import hashlib
import os
import re
import time
from urllib.parse import urlsplit
from typing import Literal
import httpx
from pydantic import Field, StrictInt
from .models import StrictModel,identity,now

Provider=Literal["arxiv","openalex","semantic_scholar","crossref"]
ENDPOINTS={"openalex":"https://api.openalex.org/works","semantic_scholar":"https://api.semanticscholar.org/graph/v1/paper/search","crossref":"https://api.crossref.org/works"}


class PaperDiscoveryPolicy(StrictModel):
    """Operator-owned capability configuration, not public research arguments."""
    discovery_providers: tuple[Provider,...]=Field(default=(),max_length=4)
    max_discoveries: StrictInt=Field(default=3,ge=0,le=8)


class PaperDiscoveryQuery(StrictModel):
    provider: Provider
    query: str=Field(min_length=1,max_length=500)


class PaperHit(StrictModel):
    provider: Provider
    provider_id: str=Field(min_length=1,max_length=2000)
    title: str=Field(min_length=1,max_length=2000)
    doi: str | None=None
    arxiv_id: str | None=None
    year: int | None=None
    abstract: str=Field(default="",max_length=6000)
    full_text_urls: tuple[str,...]=Field(default=(),max_length=8)
    license: str | None=None
    metadata_hash: str


def doi(value):
    if not value:return None
    value=re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)","",str(value).strip(),flags=re.I).casefold()
    return value if re.fullmatch(r"10\.\d{4,9}/\S+",value) else None


def arxiv(value):
    if not value:return None
    value=re.sub(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/","",str(value)).removesuffix(".pdf")
    value=re.sub(r"v\d+$","",value)
    return value if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})",value) else None


def urls(values):
    result=[]
    for u in values:
        if not isinstance(u,str) or len(u)>8192:continue
        try:p=urlsplit(u)
        except ValueError:continue
        if p.scheme in ("https","http") and p.hostname and not p.username and not p.password and u not in result:result.append(u)
    return tuple(result[:8])


def preferred_full_text_urls(candidate):
    """Try an exact-version arXiv HTML rendition before an observed PDF URL.

    HTML availability is not inferred from metadata. The guarded acquisition
    boundary records a failed HTML attempt and may then try the original URL.
    """
    original = candidate.get("full_text_urls", ())
    html = []
    for address in original:
        parsed = urlsplit(address)
        if (parsed.scheme != "https" or parsed.hostname not in {"arxiv.org", "www.arxiv.org"}
            or parsed.username is not None or parsed.password is not None):
            continue
        match = re.fullmatch(r"/(?:pdf|abs|html)/(.+?)(?:\.pdf)?", parsed.path)
        if not match or arxiv(match.group(1)) is None:
            continue
        identifier = match.group(1)
        html.append("https://arxiv.org/html/" + identifier)
    for observation in candidate.get("observations", ()):
        identifier = observation.get("arxiv_id")
        if arxiv(identifier) is not None and not html:
            html.append("https://arxiv.org/html/" + identifier)
    return tuple(dict.fromkeys([*html, *original]))


def normalize_hits(provider,payload):
    rows=payload["results"] if provider=="openalex" else payload["data"] if provider=="semantic_scholar" else payload["message"]["items"]
    if not isinstance(rows,list) or len(rows)>5:raise ValueError("invalid provider result count")
    hits=[]
    for r in rows:
        if provider=="openalex":
            loc=r.get("best_oa_location") or {};locations=r.get("locations") or []
            title=r["title"];pid=r["id"];d=doi(r.get("doi"));a=None;y=r.get("publication_year");abstract=""
            links=urls([loc.get("pdf_url"),*(v.get("pdf_url") for v in locations if v.get("is_oa"))]);license=loc.get("license")
        elif provider=="semantic_scholar":
            ids=r.get("externalIds") or {};oa=r.get("openAccessPdf") or {}
            title=r["title"];pid=r["paperId"];d=doi(ids.get("DOI"));a=arxiv(ids.get("ArXiv"));y=r.get("year");abstract=r.get("abstract") or ""
            links=urls([oa.get("url")]);license=oa.get("license")
        else:
            title=r["title"][0];pid=r["DOI"];d=doi(pid);a=None
            dates=(r.get("published") or {}).get("date-parts",[]);y=dates[0][0] if dates and dates[0] else None;abstract=r.get("abstract") or ""
            links=urls(v.get("URL") for v in r.get("link",[]) if v.get("content-type") in ("application/pdf","text/plain","text/html"));license=None
        hits.append(PaperHit(provider=provider,provider_id=str(pid),title=title[:2000],doi=d,arxiv_id=a,year=y,abstract=abstract[:6000],full_text_urls=links,license=license,metadata_hash=identity(r)))
    return hits


def search_papers(provider,query):
    """No arbitrary endpoint, redirects, credential logging, retries or paper downloads."""
    if provider=="arxiv":
        from .arxiv_metadata import fetch_metadata
        packet=fetch_metadata(query)
        hits=[PaperHit(provider="arxiv",provider_id=h["arxiv_id"],title=h["title"],abstract=h["abstract"],arxiv_id=arxiv(h["arxiv_id"]),
            full_text_urls=urls(["https://arxiv.org/pdf/"+h["arxiv_id"]]),metadata_hash=identity(h)).model_dump(mode="json") for h in packet["hits"]]
        return {**packet,"provider":provider,"hits":hits,"query":query,"authority":"discovery_metadata_only"}
    if provider not in ENDPOINTS:raise ValueError("unknown discovery provider")
    params={"search":query,"per_page":5} if provider=="openalex" else {"query":query,"limit":5,"fields":"title,year,abstract,externalIds,openAccessPdf"} if provider=="semantic_scholar" else {"query.bibliographic":query,"rows":5}
    headers={"User-Agent":"NIMA-DeepResearch/2.0"}
    key=os.environ.get("NIMA_OPENALEX_API_KEY" if provider=="openalex" else "NIMA_SEMANTIC_SCHOLAR_API_KEY" if provider=="semantic_scholar" else "","")
    if key:headers["Authorization" if provider=="openalex" else "x-api-key"]=("Bearer "+key) if provider=="openalex" else key
    packet={"provider":provider,"query":query,"endpoint":ENDPOINTS[provider],"retrieved_at":now(),"outcome":"validation_unavailable","hits":[],"authority":"discovery_metadata_only"}
    try:
        with httpx.Client(timeout=30,follow_redirects=False,trust_env=False,headers=headers) as client:
            with client.stream("GET",ENDPOINTS[provider],params=params) as response:
                packet["status_code"]=response.status_code
                if response.headers.get("Retry-After"):packet["retry_after"]=response.headers["Retry-After"][:128]
                response.raise_for_status();raw=bytearray();deadline=time.monotonic()+30
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw)>2_000_000 or time.monotonic()>deadline:raise ValueError("provider response limit")
        import json
        hits=normalize_hits(provider,json.loads(raw))
        packet.update(outcome="metadata_ready",response_sha256=hashlib.sha256(raw).hexdigest(),hits=[h.model_dump(mode="json") for h in hits])
    except Exception as exc:packet["error"]=type(exc).__name__
    return packet


def merge_candidates(existing,hits):
    """Merge strong DOI/arXiv/provider identities; titles alone never prove identity."""
    import copy
    result=copy.deepcopy(existing)
    for raw in hits:
        h=PaperHit.model_validate(raw).model_dump(mode="json")
        aliases={h["provider"]+":"+h["provider_id"]}
        if h["doi"]:aliases.add("doi:"+doi(h["doi"]))
        if h["arxiv_id"]:aliases.add("arxiv:"+arxiv(h["arxiv_id"]))
        matches=[k for k,v in result.items() if aliases&set(v["aliases"])]
        key=matches[0] if matches else "paper-"+identity(sorted(aliases))[:24]
        row=result.setdefault(key,{"candidate_id":key,"aliases":[],"observations":[],"full_text_urls":[],"merged_candidate_ids":[]})
        for other in matches[1:]:
            old=result.pop(other);row["aliases"].extend(old["aliases"]);row["observations"].extend(old["observations"]);row["full_text_urls"].extend(old["full_text_urls"]);row["merged_candidate_ids"].extend([other,*old["merged_candidate_ids"]])
        row["aliases"]=sorted(set(row["aliases"])|aliases)
        if h not in row["observations"]:row["observations"].append(h)
        row["full_text_urls"]=list(dict.fromkeys([*row["full_text_urls"],*h["full_text_urls"]]))
        row["independent_paper_count"]=1
        row["identity_conflict"]=any(sum(a.startswith(prefix) for a in row["aliases"])>1 for prefix in ("doi:","arxiv:"))
        row["candidate_hash"]=identity({k:v for k,v in row.items() if k!="candidate_hash"})
    return result
