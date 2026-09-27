"""Optional on-demand LeanSearch; provider candidates are not locally verified."""
from lfx.custom import Component
from lfx.io import BoolInput, StrInput, IntInput, Output
from lfx.base.tools.base import Tool
from langchain_core.tools import StructuredTool
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.lean_search import LeanSearchPolicy, SearchLean, search_lean


class LeanSearch(Component):
    name = "LeanSearch"
    display_name = "LeanSearch · Declaration Discovery"
    description = "Optional external library search, gated by explicit query-disclosure approval. Candidates must be resolved in the pinned local environment. Building the capability sends no request."
    nima_manifest = manifest_for_component("LeanSearch")
    inputs = [BoolInput(name="enabled",display_name="Enable LeanSearch (operator)",value=False),
        BoolInput(name="allow_query_disclosure",display_name="Approve external query disclosure (operator)",value=False),
        StrInput(name="endpoint",display_name="Approved endpoint (operator)",value="https://leansearch.net/search"),
        StrInput(name="index_revision",display_name="Provider index revision (operator)",value="provider-unpinned"),
        IntInput(name="max_results",display_name="Maximum candidates (operator)",value=5)]
    outputs = [Output(name="tool",display_name="LeanSearch capability",method="build_tool",group_outputs=True)]

    async def build_tool(self) -> Tool:
        policy=LeanSearchPolicy(enabled=self.enabled,allow_query_disclosure=self.allow_query_disclosure,
            endpoint=self.endpoint,index_revision=self.index_revision,max_results=self.max_results)
        def discover(query: str):return search_lean(policy,SearchLean(query=query))
        return StructuredTool.from_function(func=discover,name="search_lean",description=self.description,
            args_schema=SearchLean,metadata={"nima_lean_search":policy.model_dump(mode="json")})
