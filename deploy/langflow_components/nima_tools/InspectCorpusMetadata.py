"""Read a scoped source inventory and current projection metadata without writes."""
from lfx.io import StrInput
from lfx.schema import DataFrame
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.corpus_inspection import InspectCorpusRequest, InspectCorpusContext, inspect_corpus
from nima_semantica.orchestration.langflow.adapter_support import configured_store
from nima_semantica.orchestration.langflow.stages.base import InspectableStage as BaseComponent, handle
from nima_semantica.orchestration.langflow.values import _value


class InspectCorpusMetadata(BaseComponent):
    name = "InspectCorpusMetadata"
    display_name = "Corpus Inventory and Readiness"
    description = "One scoped read snapshot: source page, exact-region counts, current/stale projection metadata and vector coverage. Not a fidelity or artifact-integrity check."
    nima_manifest = manifest_for_component("InspectCorpusMetadata")
    inputs = [handle("payload", "Validated page"),
        StrInput(name="corpus_id", display_name="Authorized corpus (operator)", value="papers"),
        StrInput(name="project_id", display_name="Authorized project (operator)", value="research",
            info="Empty means corpus-only. Sources are shared corpus-wide; project regions and projections remain scoped.")]

    async def run(self):
        request = InspectCorpusRequest.model_validate(_value(self.payload))
        context = InspectCorpusContext(corpus_id=self.corpus_id, project_id=self.project_id or None)
        with configured_store(required=False) as store:
            return inspect_corpus(store, request, context).model_dump(mode="json")

    async def table_data(self) -> DataFrame:
        result = (await self.result_data()).data
        return DataFrame(result.get("data", {}).get("sources", []))
