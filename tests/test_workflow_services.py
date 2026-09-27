import pytest

from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.graph_commit import OKFCommitApproval
from nima_semantica.models import ConflictError
from nima_semantica.normalization_contracts import FlowAdapterManifest, NormalizedRequest
from nima_semantica.okf_contracts import OKFDelta, OKFNode, OKFReference, OKFReferenceKind
from nima_semantica.retrieval_contracts import (
    EmbeddingProjectionManifest,
    RetrievalContextPacket,
)
from nima_semantica.storage import GraphStore
from nima_semantica.workflow_contracts import (
    GraphCommitRequest,
    GraphRetrievalNormalizerRequest,
    HypothesisComparison,
    HypothesisComparisonRequest,
    HypothesisGenerationRequest,
    HypothesisProposal,
    InputNormalizerRequest,
)
from nima_semantica.workflow_services import (
    GraphCommitService,
    GraphRetrievalNormalizationService,
    InputNormalizationService,
)
from nima_semantica.workflow_boundaries import (
    validate_hypothesis_comparison,
    validate_hypothesis_generation,
)


def packet():
    projection = EmbeddingProjectionManifest(
        projection_id="projection-1",
        corpus_id="papers",
        project_id="project-a",
        source_revision="source:4",
        provider="local",
        model="embedding-model",
        model_revision="v1",
        dimension=3,
    )
    return RetrievalContextPacket(
        query="find evidence",
        corpus_id="papers",
        project_id="project-a",
        graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
        projection=projection,
    )


def proposal():
    return HypothesisProposal(
        proposal_id="hypothesis-1",
        kind="research",
        statement="The quantity has a stronger lower bound.",
        supporting_references=(
            OKFReference(
                reference_kind=OKFReferenceKind.RECORD,
                target_id="claim-1",
                corpus_id="papers",
                project_id="project-a",
            ),
        ),
        unresolved_obligations=("Establish the extremal construction.",),
        verification_plans=("Attempt an independent proof.",),
    )


def test_input_normalization_service_preserves_typed_boundary():
    manifest = FlowAdapterManifest(adapter_id="adapter-1", name="Adapter", version="1")
    request = NormalizedRequest(
        request_id="request-1",
        adapter_id="adapter-1",
        corpus_id="papers",
        project_id="project-a",
        graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
    )
    output = InputNormalizationService().execute(
        InputNormalizerRequest(manifest=manifest, request=request),
        lambda payload: {
            "request_id": payload["request_id"],
            "adapter_id": payload["adapter_id"],
            "corpus_id": payload["corpus_id"],
            "project_id": payload["project_id"],
            "graph_revision": payload["graph_revision"],
            "status": "complete",
        },
    )
    assert output.status == "complete"
    assert output.corpus_id == "papers"
    assert output.project_id == "project-a"
    assert output.graph_revision == request.graph_revision


def test_retrieval_normalizer_rejects_stale_request_before_provider_call(tmp_path):
    store = GraphStore(tmp_path)
    try:
        request = GraphRetrievalNormalizerRequest(
            request_id="retrieval-1",
            corpus_id="papers",
            project_id="project-a",
            graph_revision=GraphRevision(corpus_id="papers", corpus_revision=999, project_id="project-a", project_revision=0),
            query="find evidence",
        )
        with pytest.raises(ConflictError, match="stale graph revision"):
            GraphRetrievalNormalizationService().execute(store, object(), request)
    finally:
        store.close()


def test_hypothesis_generation_is_proposal_only_and_scope_checked():
    request = HypothesisGenerationRequest(
        request_id="generation-1",
        corpus_id="papers",
        project_id="project-a",
        graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
        objective="Find a better lower bound.",
    )
    output = validate_hypothesis_generation(request, [proposal()])
    assert output.authority == "proposal_only"
    assert output.proposals[0].status == "proposed"
    assert (output.corpus_id, output.project_id, output.graph_revision) == (
        "papers", "project-a", request.graph_revision
    )

    foreign = proposal().model_copy(
        update={
            "supporting_references": (
                proposal().supporting_references[0].model_copy(update={"project_id": "other"}),
            )
        }
    )
    with pytest.raises(ValueError, match="out-of-scope"):
        validate_hypothesis_generation(request, [foreign])


def test_hypothesis_comparison_is_proposal_only_and_context_bound():
    request = HypothesisComparisonRequest(
        request_id="comparison-1",
        corpus_id="papers",
        project_id="project-a",
        graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
        context_packet=packet(),
        hypotheses=(proposal(),),
    )
    output = validate_hypothesis_comparison(
        request,
        [
            HypothesisComparison(
                proposal_id="hypothesis-1",
                assessment="unresolved",
                unresolved_obligations=("Check the extremal construction.",),
                rationale="The retrieved context does not settle the claim.",
            )
        ],
    )
    assert output.authority == "proposal_only"
    assert (output.corpus_id, output.project_id, output.graph_revision) == (
        "papers", "project-a", request.graph_revision
    )

    foreign = HypothesisComparison(
        proposal_id="hypothesis-1",
        assessment="mixed",
        supporting_references=(
            OKFReference(
                reference_kind=OKFReferenceKind.RECORD,
                target_id="foreign-claim",
                corpus_id="papers",
                project_id="other-project",
            ),
        ),
        rationale="The comparison is out of scope.",
    )
    with pytest.raises(ValueError, match="out-of-scope"):
        validate_hypothesis_comparison(request, [foreign])


def test_graph_commit_service_is_the_explicit_mutation_path(tmp_path):
    store = GraphStore(tmp_path)
    try:
        delta = OKFDelta(
            delta_id="delta-service-1",
            base_revision=store.graph_revision("papers", "project-a"),
            corpus_id="papers",
            project_id="project-a",
            ontology_profile="claim_obligation",
            upsert_nodes=(
                OKFNode(
                    node_id="claim-service-1",
                    node_type="claim",
                    corpus_id="papers",
                    project_id="project-a",
                ),
            ),
            reason="Commit service fixture.",
        )
        output = GraphCommitService().execute(
            store,
            GraphCommitRequest(
                request_id="commit-request-1",
                delta=delta,
                approval=OKFCommitApproval.for_delta(delta,
                    approval_id="approval-service-1",
                    approved_by="researcher",
                    rationale="Explicitly approved fixture.",
                ),
            ),
        )
        assert output.authority == "approved_commit"
        assert output.receipts[0].receipt_id == output.result.receipt_id
        assert store.read_okf_snapshot(corpus_id="papers", project_id="project-a").nodes
    finally:
        store.close()
