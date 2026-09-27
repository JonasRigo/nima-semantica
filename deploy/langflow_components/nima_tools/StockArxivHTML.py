"""Conditional stock Langflow arXiv search followed by the stock URL HTML reader."""
import json
import asyncio

from lfx.io import HandleInput, IntInput, Output, StrInput
from lfx.schema import Data, DataFrame, Message
from lfx_arxiv.components.arxiv.arxiv import ArXivComponent
from lfx.components.data_source.url import URLComponent
from langchain_core.tools import StructuredTool
from lfx.base.tools.base import Tool
from pydantic import BaseModel, Field

from nima_semantica import arxiv_search_fallback
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.deep_research_contracts import DeepResearchRequest
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent, handle
from nima_semantica.orchestration.langflow.values import _value
from nima_semantica.paper_discovery import arxiv
from nima_semantica.paper_discovery import PaperDiscoveryPolicy


class ArxivSearchPhrase(BaseModel):
    query: str = Field(min_length=1, max_length=160)


class StockArxivHTML(BaseComponent):
    name = "StockArxivHTML"
    display_name = "Stock arXiv → URL HTML"
    description = "Run Langflow's official arXiv search component and, only for observed hits, its stock URL component at depth one on available arXiv HTML renditions. This is metadata and an HTML preview, not NIMA source ingestion or evidence."
    nima_manifest = manifest_for_component("StockArxivHTML")
    inputs = [handle("payload", "Harness research question"),
        HandleInput(name="paper_discovery", display_name="Operator discovery policy", input_types=["Tool"], required=False),
        StrInput(name="search_phrase", display_name="Short search phrase (harness or planner)", value=""),
        IntInput(name="max_results", display_name="Maximum arXiv metadata hits", value=5),
        IntInput(name="max_html", display_name="Maximum HTML previews", value=3)]
    outputs = [Output(name="result", display_name="Metadata and HTML availability", method="result_data", group_outputs=True),
        Output(name="preview", display_name="Readable preview", method="preview_message", group_outputs=True),
        Output(name="table", display_name="arXiv metadata table", method="table_data", group_outputs=True),
        Output(name="tool", display_name="Bounded on-demand arXiv discovery", method="build_tool", group_outputs=True)]

    def _policy(self):
        discovery = getattr(self, "paper_discovery", None)
        return PaperDiscoveryPolicy.model_validate(discovery.metadata["nima_paper_discovery"]) if discovery else PaperDiscoveryPolicy()

    async def _search(self, query, policy):
        if "arxiv" not in policy.discovery_providers:
            return {"provider": "arxiv", "backend": "stock_langflow_arxiv_and_url", "outcome": "not_authorized", "rows": [], "html_pages": []}
        query = ArxivSearchPhrase(query=query).query
        arxiv_search = ArXivComponent()
        arxiv_search.search_query = query
        arxiv_search.search_type = "all"
        arxiv_search.max_results = min(max(int(self.max_results), 1), 10)
        source = "stock_langflow_arxiv_api"
        try:
            rows = arxiv_search.search_papers_dataframe().to_dict(orient="records")
        except Exception as exc:
            rows = [{"error": type(exc).__name__ + ":" + str(exc)[:200]}]
        errors = [str(row["error"])[:500] for row in rows if isinstance(row, dict) and row.get("error")]
        if errors and any("406" in error for error in errors):
            source = "arxiv_search_html_fallback"
            try:
                rows = arxiv_search_fallback.search_page(query, max_results=arxiv_search.max_results)
            except RuntimeError as exc:
                errors.append("fallback:" + str(exc)[:300])
                rows = []
            except Exception as exc:
                errors.append("fallback:" + type(exc).__name__)
                rows = []
        elif errors:
            rows = []
        if not rows:
            return {"provider": "arxiv", "backend": source, "query": query,
                "outcome": "unavailable", "rows": [], "html_pages": [], "errors": errors, "metadata_only": True}
        html_pages = []
        for row in rows[:min(max(int(self.max_html), 0), 5)]:
            identifier = arxiv(row.get("arxiv_url") or row.get("id") or "")
            if not identifier:
                continue
            address = "https://arxiv.org/html/" + identifier
            url_reader = URLComponent()
            url_reader.urls = [address]
            url_reader.max_depth = 1
            url_reader.prevent_outside = True
            url_reader.use_async = True
            url_reader.follow_redirects = True
            url_reader.format = "HTML"
            url_reader.timeout = 30
            url_reader.filter_text_html = True
            url_reader.continue_on_failure = True
            url_reader.check_response_status = True
            url_reader.autoset_encoding = True
            url_reader.headers = DataFrame([{"key": "User-Agent", "value": "NIMA-DeepResearch/5.0"}])
            try:
                pages = (await url_reader.fetch_content()).to_dict(orient="records")
            except Exception as exc:
                html_pages.append({"url": address, "error": type(exc).__name__})
                continue
            for page in pages:
                meta = page.get("metadata") if isinstance(page.get("metadata"), dict) else {}
                page_source = page.get("url") or page.get("source") or meta.get("source") or address
                content = page.get("text") or page.get("page_content") or ""
                html_pages.append({"url": page_source, "text": "HTML available" if content else "", "content_chars": len(content),
                    "title": page.get("title") or meta.get("title")})
        return {"provider": "arxiv", "backend": source, "query": query,
            "outcome": "metadata_ready", "rows": rows, "html_pages": html_pages,
            "errors": errors, "metadata_only": True}

    async def run(self):
        payload = _value(self.payload)
        request = DeepResearchRequest.model_validate(payload.get("research", payload) if isinstance(payload, dict) else payload)
        if request.mode == "preview":
            return {"executed": False, "provider": "arxiv", "backend": "stock_langflow_arxiv_and_url"}
        query = self.search_phrase.strip() or " ".join(item.question for item in request.questions)
        return await self._search(query, self._policy())

    async def build_tool(self) -> Tool:
        policy = self._policy()
        def discover(query: str):
            return asyncio.run(self._search(query, policy))
        return StructuredTool.from_function(func=discover, name="stock_arxiv_html_discovery",
            description="On-demand bounded arXiv metadata search with stock API and explicit fixed-host search-page fallback; URL previews are not evidence.",
            args_schema=ArxivSearchPhrase, metadata={"nima_arxiv_policy": policy.model_dump(mode="json")})

    async def table_data(self) -> DataFrame:
        return DataFrame((await self.result_data()).data.get("rows", []))

    async def preview_message(self) -> Message:
        packet = (await self.result_data()).data
        return Message(text=json.dumps({"provider": "arxiv", "rows": len(packet.get("rows", [])),
            "html_pages": packet.get("html_pages", []), "metadata_only": True}, ensure_ascii=False))
