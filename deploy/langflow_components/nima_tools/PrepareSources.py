"""Visible normalization, PDF worker configuration, and corpus publication boundary."""
from lfx.io import BoolInput, StrInput, IntInput
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.source_tools import PrepareSourcesRequest, SourceToolContext, prepare_sources
from nima_semantica.orchestration.langflow.adapter_support import configured_store
from nima_semantica.orchestration.langflow.pdf_client import PdfNormalizerClient
from nima_semantica.orchestration.langflow.stages.base import BlockingStage as BaseComponent, handle
from nima_semantica.orchestration.langflow.values import _value


class PrepareSources(BaseComponent):
    name = "PrepareSources"
    display_name = "Normalize, Chunk and Store Sources"
    description = "Normalize mathematics-preserving text/PDF, retain original bytes and PDF evidence, validate exact regions, and register immutable corpus-wide sources."
    nima_manifest = manifest_for_component("PrepareSources")
    inputs = [handle("payload", "Validated sources"),
        StrInput(name="corpus_id", display_name="Authorized corpus (operator)", value="papers"),
        StrInput(name="project_id", display_name="Index/run project (operator)", value="research"),
        StrInput(name="actor", display_name="Actor (operator)", value="harness"),
        IntInput(name="max_pdf_evidence_bytes",display_name="Derived PDF evidence byte budget (operator)",value=20000000),
        StrInput(name="local_staging_root", display_name="Scoped local-file staging directory (operator)", value=""),
        StrInput(name="projection_limits_json", display_name="Projection limits (operator)", value='{}'),
        BoolInput(name="allow_corpus_writes", display_name="Allow CORPUS source writes (operator)", value=False,
            info="Sources are corpus-wide, not private to the index project. Requires an existing registered corpus."),
        BoolInput(name="allow_project_writes",display_name="Allow notes in the bound project (operator)",value=False),
        BoolInput(name="allow_pdf", display_name="Allow configured PDF worker (operator)", value=False),
        StrInput(name="pdf_url", display_name="PDF worker URL (operator)", value=""),
        StrInput(name="pdf_token_file", display_name="PDF worker token file (operator)", value=""),
    ]

    def run_sync(self):
        request = PrepareSourcesRequest.model_validate(_value(self.payload))
        context = SourceToolContext(corpus_id=self.corpus_id, project_id=self.project_id or None,
            actor=self.actor, allow_corpus_writes=self.allow_corpus_writes, allow_pdf=self.allow_pdf, allow_project_writes=getattr(self,"allow_project_writes",False),
            projection_limits=__import__('json').loads(self.projection_limits_json), local_staging_root=getattr(self,"local_staging_root","") or None, max_pdf_evidence_bytes=getattr(self,"max_pdf_evidence_bytes",20000000))
        # Client construction/secret access happens inside the receipted attempt,
        # only if authorized PDF input actually requires the worker.
        pdf_client = None
        def pdf_profile():
            nonlocal pdf_client
            if pdf_client is None:
                pdf_client = PdfNormalizerClient(self.pdf_url,self.pdf_token_file)
                pdf_client.expected_profile = pdf_client.profile()["profile_hash"]
            return {"profile_hash":pdf_client.expected_profile}
        def pdf_normalizer(data):
            pdf_profile()
            result = pdf_client.normalize(data)
            return result["text"], result["diagnostics"]
        pdf_normalizer.profile = pdf_profile
        if request.mode == "preview":
            return prepare_sources(None, request, context).model_dump(mode="json")
        with configured_store(required=False) as store:
            return prepare_sources(store, request, context,
                pdf_normalizer=pdf_normalizer if self.allow_pdf else None).model_dump(mode="json")
