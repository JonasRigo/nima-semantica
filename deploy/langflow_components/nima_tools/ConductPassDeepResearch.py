"""Visible controller for two bounded regional-graph literature passes."""
import asyncio
import json

from lfx.io import BoolInput, DropdownInput, HandleInput, IntInput, StrInput
from lfx.schema import Message

from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.deep_research_passes import PassResearchRequest, deep_research_passes
from nima_semantica.models import AcquisitionPolicy, canonical
from nima_semantica.orchestration.langflow.adapter_support import configured_store
from nima_semantica.orchestration.langflow.pdf_client import PdfNormalizerClient
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent, handle
from nima_semantica.orchestration.langflow.values import _value
from nima_semantica.paper_discovery import PaperDiscoveryPolicy
from nima_semantica.providers import ModelManifest, completion_envelope
from nima_semantica.simple_deep_research import SimpleDeepResearchContext


class ConductPassDeepResearch(BaseComponent):
    name = "ConductPassDeepResearch"
    display_name = "Deep Research · Regional Graph Passes"
    description = "Pin atomic coverage questions, screen and refine short paper queries, confirm passage relevance, then build regional graphs. Return uncovered questions and graph links, not a narrative gap answer. Supplied graph skips first-pass search. No automatic scientific admission."
    nima_manifest = manifest_for_component("ConductPassDeepResearch")
    inputs = [handle("payload", "Harness pass request"),
        HandleInput(name="stock_arxiv", display_name="Stock arXiv and URL capability", input_types=["Tool"], required=False),
        HandleInput(name="paper_discovery", display_name="Other approved paper providers", input_types=["Tool"], required=False),
        StrInput(name="corpus_id", display_name="Authorized corpus (operator)", value="papers"),
        StrInput(name="project_id", display_name="Authorized project (operator)", value="research"),
        BoolInput(name="allow_audit_writes", display_name="Allow attempts and outcome artifacts (operator)", value=False),
        BoolInput(name="allow_model_calls", display_name="Allow bounded model stages (operator)", value=False),
        DropdownInput(name="reading_mode", display_name="Source reading path (operator)",
            options=["fast_provisional", "prepared"], value="fast_provisional"),
        BoolInput(name="allow_fast_read", display_name="Allow staged fast reading (operator)", value=False),
        BoolInput(name="allow_source_ingestion", display_name="Allow corpus ingestion (operator)", value=False),
        StrInput(name="acquisition_policy_json", display_name="Approved acquisition policy JSON (operator)", value="{}"),
        BoolInput(name="allow_pdf", display_name="Allow PDF reading (operator)", value=False),
        StrInput(name="pdf_url", display_name="PDF worker URL (operator)", value=""),
        StrInput(name="pdf_token_file", display_name="PDF worker token file (operator)", value=""),
        IntInput(name="max_papers", display_name="Maximum papers per search", value=2),
        IntInput(name="max_regions", display_name="Maximum exact-read regions per search", value=16),
        IntInput(name="max_chars", display_name="Maximum evidence characters per search", value=48000),
        StrInput(name="model_manifest_json", display_name="Model identity JSON (operator)", value="{}", advanced=True),
        HandleInput(name="model", display_name="Review language model (operator)", input_types=["LanguageModel"], required=False)]

    async def run(self):
        packet = PassResearchRequest.model_validate(_value(self.payload))
        paper = getattr(self, "paper_discovery", None)
        stock = getattr(self, "stock_arxiv", None)
        policy = PaperDiscoveryPolicy.model_validate(paper.metadata["nima_paper_discovery"]) if paper else PaperDiscoveryPolicy()
        manifest = ModelManifest.model_validate_json(self.model_manifest_json) if self.model_manifest_json.strip() not in ("", "{}") else None
        context = SimpleDeepResearchContext(corpus_id=self.corpus_id, project_id=self.project_id,
            allow_model_calls=self.allow_model_calls, allow_audit_writes=self.allow_audit_writes,
            allow_source_ingestion=self.allow_source_ingestion, allow_fast_read=self.allow_fast_read,
            reading_mode=self.reading_mode, allow_pdf=self.allow_pdf,
            discovery_providers=policy.discovery_providers, acquisition=AcquisitionPolicy.model_validate_json(self.acquisition_policy_json),
            max_papers=self.max_papers, max_regions=self.max_regions, max_chars=self.max_chars, model_manifest=manifest)
        if packet.research.mode == "preview":
            return deep_research_passes(None, packet, context).model_dump(mode="json")
        language_model = getattr(self, "model", None)
        def invoke(prompt):
            response = language_model.invoke([{"role": "system", "content": "Return exactly one JSON object matching the supplied stage contract, without Markdown. Pin atomic research subquestions. Search phrases must be short. Reject off-topic metadata before acquisition and require exact passage support before regional graphing. At gap assessment, return only per-facet coverage and specific unanswered questions, not narrative answers or source-read claims. Metadata never substantiates scientific claims. Regional claims cite supplied passage IDs. Fast-read passages and gap judgments are provisional, not certifications."},
                {"role": "user", "content": canonical(prompt).decode()}])
            if not response.usage_metadata:
                raise ValueError("model omitted usage metadata")
            content = response.content if isinstance(response.content, str) else json.dumps(response.content)
            return completion_envelope(content, manifest, response.usage_metadata, response.response_metadata)
        def normalize_pdf(data):
            result = PdfNormalizerClient(self.pdf_url, self.pdf_token_file).normalize(data)
            return result["text"], result["diagnostics"]
        def check_pdf_worker():
            return PdfNormalizerClient(self.pdf_url, self.pdf_token_file).health()
        with configured_store(required=False) as store:
            result = await asyncio.to_thread(deep_research_passes, store, packet, context,
                model=invoke if language_model is not None else None,
                arxiv_search=(lambda query: stock.invoke({"query": query})) if stock else None,
                paper_search=(lambda provider, query: paper.invoke({"provider": provider, "query": query})) if paper else None,
                pdf_normalizer=normalize_pdf if self.allow_pdf and self.reading_mode == "prepared" else None,
                pdf_preflight=check_pdf_worker if self.allow_pdf and self.reading_mode == "prepared" else None)
        return result.model_dump(mode="json")

    async def preview_message(self) -> Message:
        packet = (await self.result_data()).data
        data = packet.get("data", {})
        return Message(text=json.dumps({"status": packet.get("status"), "entry_mode": data.get("entry_mode"),
            "regional_passes": data.get("regional_passes"), "coverage_facets": data.get("coverage_facets"),
            "coverage": data.get("coverage"), "uncovered_questions": data.get("uncovered_questions"),
            "source_inventory": data.get("source_inventory"), "assessments": data.get("assessments"),
            "review_graph": data.get("review_graph"), "graph_commit": data.get("graph_commit")}, ensure_ascii=False, indent=2))
