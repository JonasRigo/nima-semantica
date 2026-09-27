import hashlib

import pytest

from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.evidence_provenance import EvidenceProvenanceService
from nima_semantica.models import ConflictError, Record
from nima_semantica.okf_contracts import EvidenceReference, OKFNode, OKFSnapshot


def source_evidence(store, *, text="Exact source text."):
    artifact_id = store.artifact(text.encode())
    from conftest import seed_region
    region = seed_region(store, text)
    return EvidenceReference(
        corpus_id="papers",
        artifact_id=artifact_id,
        region_id=region.id,
        source_revision=artifact_id,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        quotation=text,
    )


def test_service_validates_and_binds_exact_source_evidence(store):
    service = EvidenceProvenanceService(store)
    reference = source_evidence(store)
    node = OKFNode(node_id="claim-1", node_type="claim", corpus_id="papers")

    bound = service.bind_node(node, (reference,))

    assert bound.evidence == (reference,)
    assert service.validate_snapshot(
        OKFSnapshot(
            snapshot_id="snapshot-1",
            graph_revision=store.graph_revision("papers", None),
            corpus_id="papers",
            nodes=(bound,),
        )
    ).valid

    with pytest.raises(ConflictError, match="invalid evidence"):
        service.bind_node(node, (reference.model_copy(update={"quotation": "fabricated"}),))


def test_service_prepares_and_persists_proposal_only_promotion(store):
    service = EvidenceProvenanceService(store)
    reference = source_evidence(store)
    snapshot = OKFSnapshot(
        snapshot_id="snapshot-project",
        graph_revision=store.graph_revision("papers", "project-a"),
        corpus_id="papers",
        project_id="project-a",
        nodes=(
            OKFNode(
                node_id="claim-1",
                node_type="claim",
                corpus_id="papers",
                project_id="project-a",
                evidence=(reference,),
            ),
        ),
    )

    result = service.prepare_promotion(
        snapshot,
        proposal_id="promotion-1",
        target_corpus_id="papers",
        rationale="Evidence is reusable.",
    )

    assert result.authority == "proposal_only"
    assert result.record_id is not None
    assert result.proposal.status == "proposed"
    assert store.read_okf_snapshot(corpus_id="papers").nodes == ()
