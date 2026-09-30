"""One bounded literature pass: discovery, full-text preparation and cited review graph."""
import json

from lfx.io import BoolInput, HandleInput, IntInput, StrInput
from lfx.schema import Message

from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.deep_research_contracts import DeepResearchRequest
from nima_semantica.models import AcquisitionPolicy, canonical
from nima_semantica.orchestration.langflow.adapter_support import configured_store
from nima_semantica.orchestration.langflow.pdf_client import PdfNormalizerClient
from nima_semantica.orchestration.langflow.stages.base import BlockingStage as BaseComponent, handle
from nima_semantica.orchestration.langflow.values import _value
from nima_semantica.paper_discovery import PaperDiscoveryPolicy
from nima_semantica.providers import ModelManifest, completion_envelope
from nima_semantica.simple_deep_research import SimpleDeepResearchContext, simple_deep_research


class SimpleDeepResearch(BaseComponent):
    name = "SimpleDeepResearch"
    display_name = "Deep Research · Bounded Review"
    description = "One search/ingestion/retrieval/review pass with exact-source graph proposal. The harness owns iteration; project admission is a separate exact-approved step."
    nima_manifest = manifest_for_component("SimpleDeepResearch")
    inputs = [handle("payload", "Harness research request"), handle("arxiv_packet", "Stock arXiv and HTML packet", required=False),
        HandleInput(name="paper_discovery", display_name="Paper Discovery: OpenAlex/Crossref", input_types=["Tool"], required=False),
        StrInput(name="corpus_id", display_name="Authorized corpus (operator)", value="papers"),
        StrInput(name="project_id", display_name="Authorized project (operator)", value="research"),
        BoolInput(name="allow_audit_writes", display_name="Allow attempts and outcome artifacts (operator)", value=False),
        BoolInput(name="allow_model_calls", display_name="Allow one review model call (operator)", value=False),
        BoolInput(name="allow_source_ingestion", display_name="Allow corpus source ingestion (operator)", value=False),
        StrInput(name="acquisition_policy_json", display_name="Approved acquisition policy JSON (operator)", value="{}"),
        BoolInput(name="allow_pdf", display_name="Allow configured PDF worker (operator)", value=False),
        StrInput(name="pdf_url", display_name="PDF worker URL (operator)", value=""),
        StrInput(name="pdf_token_file", display_name="PDF worker token file (operator)", value=""),
        IntInput(name="max_papers", display_name="Maximum selected papers", value=3),
        IntInput(name="max_regions", display_name="Maximum exact-read regions", value=16),
        IntInput(name="max_chars", display_name="Maximum model-facing evidence characters", value=48000),
        StrInput(name="model_manifest_json", display_name="Model identity JSON (operator)", value="{}", advanced=True),
        HandleInput(name="model", display_name="Review language model (operator)", input_types=["LanguageModel"], required=False)]

    def run_sync(self):
        request = DeepResearchRequest.model_validate(_value(self.payload))
        stock = _value(getattr(self, "arxiv_packet", None)) if getattr(self, "arxiv_packet", None) else {}
        discovery = getattr(self, "paper_discovery", None)
        policy = PaperDiscoveryPolicy.model_validate(discovery.metadata["nima_paper_discovery"]) if discovery else PaperDiscoveryPolicy(discovery_providers=("arxiv", "openalex", "crossref"))
        manifest = ModelManifest.model_validate_json(self.model_manifest_json) if self.model_manifest_json.strip() not in ("", "{}") else None
        context = SimpleDeepResearchContext(corpus_id=self.corpus_id, project_id=self.project_id,
            allow_model_calls=self.allow_model_calls, allow_audit_writes=self.allow_audit_writes,
            allow_source_ingestion=self.allow_source_ingestion, allow_pdf=self.allow_pdf,
            discovery_providers=policy.discovery_providers, acquisition=AcquisitionPolicy.model_validate_json(self.acquisition_policy_json),
            max_papers=self.max_papers, max_regions=self.max_regions, max_chars=self.max_chars, model_manifest=manifest)
        language_model = getattr(self, "model", None)
        def invoke(prompt):
            response = language_model.invoke([{"role": "system", "content": "Return exactly one JSON object matching the requested review contract. No Markdown. All nodes and edges require exact supplied region IDs. Attributed paper statements are not certified truths."},
                {"role": "user", "content": canonical(prompt).decode()}])
            if not response.usage_metadata:
                raise ValueError("model omitted usage metadata")
            content = response.content if isinstance(response.content, str) else json.dumps(response.content)
            return completion_envelope(content, manifest, response.usage_metadata, response.response_metadata)
        def normalize_pdf(data):
            result = PdfNormalizerClient(self.pdf_url, self.pdf_token_file).normalize(data)
            return result["text"], result["diagnostics"]
        with configured_store(required=False) as store:
            return simple_deep_research(store, request, context,
                model=invoke if language_model is not None else None,
                arxiv_rows=stock.get("rows", ()), html_pages=stock.get("html_pages", ()), stock_errors=stock.get("errors", ()),
                paper_search=(lambda provider, query: discovery.invoke({"provider": provider, "query": query})) if discovery else None,
                pdf_normalizer=normalize_pdf if self.allow_pdf else None).model_dump(mode="json")

    async def preview_message(self) -> Message:
        data = (await self.result_data()).data
        return Message(text=json.dumps({"status": data.get("status"), "review": data.get("data", {}).get("review"),
            "review_graph": data.get("data", {}).get("review_graph"), "gaps": data.get("data", {}).get("gaps"),
            "graph_commit": data.get("data", {}).get("graph_commit")}, ensure_ascii=False, indent=2))
