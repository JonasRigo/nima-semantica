import hashlib

from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.artifact_contracts import ArtifactEnvelope
from nima_semantica.graph_extraction import (
    ExtractedEdge, ExtractedNode, GraphExtractionCandidate,
    GraphExtractionRequest, GraphExtractionService,
)
from nima_semantica.models import Record
from nima_semantica.ontology_services import OntologyService
from nima_semantica.registry_contracts import CorpusDescriptor, RegistryRevision
from nima_semantica.corpus_registry import CorpusRegistry
from nima_semantica.storage import GraphStore


class ArtifactPublisher:
    def __init__(self, store):
        self.store = store

    def publish(self, data, envelope, *, registry_revision):
        assert hashlib.sha256(data).hexdigest() == envelope.artifact_id
        self.store.artifact(data)
        return envelope


def source_region(store):
    text = "A claim requires an obligation."
    from conftest import seed_region
    return seed_region(store, text, project_id="project-a")


def request(store, region):
    return GraphExtractionRequest(
        corpus_id="papers", project_id="project-a", graph_revision=store.graph_revision("papers", "project-a"),
        registry_revision="registry:1", ontology_profile="claim_obligation",
        source_region_ids=(region.id,), question="What obligation is required?",
        idempotency_key="extraction-replay",
    )


def candidate():
    return GraphExtractionCandidate(
        nodes=(
            ExtractedNode(node_id="claim", node_type="claim", source_region_ids=(REGION_ID,)),
            ExtractedNode(node_id="obligation", node_type="obligation", source_region_ids=(REGION_ID,)),
        ),
        edges=(ExtractedEdge(
            edge_id="requires", relation="requires", source_id="claim", target_id="obligation",
            source_region_ids=(REGION_ID,),
        ),),
        unresolved=("Mathematical entailment remains unresolved.",),
    )


REGION_ID = "placeholder"


def test_extraction_is_grounded_and_proposal_only(tmp_path):
    global REGION_ID
    store = GraphStore(tmp_path)
    try:
        region = source_region(store)
        REGION_ID = region.id
        registry = CorpusRegistry(store)
        registry.register_corpus(CorpusDescriptor(corpus_id="papers", name="Papers", corpus_revision="source:1"))
        registry.register_revision(RegistryRevision(
            revision_id="registry:1", corpus_id="papers", sequence=0,
            changed_resource_ids=("placeholder",),
        ))
        service = GraphExtractionService(store, ontology=OntologyService(), artifacts=ArtifactPublisher(store))
        result = service.execute(request(store, region), lambda payload: candidate())
        assert result.status == "completed"
        assert result.artifact is not None and result.proposal_record_id is not None
        assert result.artifact.delta is not None
        assert result.artifact.delta.upsert_nodes[0].evidence[0].region_id == region.id
        assert store.read_okf_snapshot(corpus_id="papers", project_id="project-a").nodes == ()
        assert len(store.records("GraphArtifactProposal", corpus_id="papers", project_id="project-a")) == 1
        assert service.execute(request(store, region), lambda payload: candidate()).proposal_record_id == result.proposal_record_id
    finally:
        store.close()


def test_extraction_rejects_fabricated_region_and_records_failure(tmp_path):
    store = GraphStore(tmp_path)
    try:
        region = source_region(store)
        bad = request(store, region).model_copy(update={"source_region_ids": ("missing-region",), "idempotency_key": "bad"})
        result = GraphExtractionService(store).execute(bad, lambda payload: candidate())
        assert result.status == "failed"
        assert result.diagnostics == ({"code": "NimaError"},)
        assert len(store.records("ExecutionReceipt", corpus_id="papers", project_id="project-a")) == 1
    finally:
        store.close()


def test_candidate_ontology_feedback_identifies_bad_edge_and_type(tmp_path):
    import pytest
    from nima_semantica.graph_extraction import CandidateOntologyError
    store = GraphStore(tmp_path)
    try:
        region = source_region(store)
        req = request(store, region).model_copy(update={"ontology_profile":"literature_review@1.0.0"})
        bad = GraphExtractionCandidate(nodes=(
            ExtractedNode(node_id="claim", node_type="claim", source_region_ids=(region.id,)),
            ExtractedNode(node_id="method", node_type="method", source_region_ids=(region.id,))),
            edges=(ExtractedEdge(edge_id="wrong-endpoint", relation="about", source_id="claim", target_id="method", source_region_ids=(region.id,)),))
        service = GraphExtractionService(store)
        with pytest.raises(CandidateOntologyError, match="wrong-endpoint.*Target type 'method'.*'about'"):
            service.prepare_candidate(req, bad)
        result = service.execute(req, lambda _: bad)
        assert result.status == "failed" and result.diagnostics[0]["edge_id"] == "wrong-endpoint"
        assert not store.records("GraphArtifactProposal")
    finally:
        store.close()
