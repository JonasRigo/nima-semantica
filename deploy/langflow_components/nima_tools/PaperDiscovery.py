"""Inspectable on-demand provider capability; building the tool performs no search."""
import json
from lfx.custom import Component
from lfx.io import StrInput, IntInput, Output
from lfx.base.tools.base import Tool
from langchain_core.tools import StructuredTool
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.paper_discovery import PaperDiscoveryPolicy, PaperDiscoveryQuery, search_papers


class PaperDiscovery(Component):
    name = "PaperDiscovery"
    display_name = "Paper Discovery"
    description = "On-demand arXiv, OpenAlex, Semantic Scholar and Crossref metadata search. The OSA controls when to search and retains request limits, receipts and deduplication. No full-text ingestion or scientific evidence admission."
    nima_manifest = manifest_for_component("PaperDiscovery")
    inputs = [
        StrInput(name="discovery_providers_json",display_name="Approved providers (JSON list, operator)",value="[]"),
        IntInput(name="max_discoveries",display_name="Maximum provider requests per research run (operator)",value=3),
    ]
    outputs = [Output(name="tool",display_name="Paper discovery capability",method="build_tool",group_outputs=True)]

    async def build_tool(self) -> Tool:
        policy = PaperDiscoveryPolicy(discovery_providers=json.loads(self.discovery_providers_json),max_discoveries=self.max_discoveries)

        def discover(provider: str, query: str):
            request = PaperDiscoveryQuery(provider=provider,query=query)
            if request.provider not in policy.discovery_providers or policy.max_discoveries == 0:
                raise ValueError("Paper discovery is not authorized for this provider")
            return search_papers(request.provider,request.query)

        return StructuredTool.from_function(
            func=discover,name="paper_discovery",description=self.description,
            args_schema=PaperDiscoveryQuery,
            metadata={"nima_paper_discovery":policy.model_dump(mode="json")},
        )
