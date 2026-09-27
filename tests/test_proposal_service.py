import pytest

from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.evidence_contracts import EvidenceReference, PromotionProposal
from nima_semantica.models import ConflictError
from nima_semantica.proposal_service import ProposalService
from nima_semantica.storage import GraphStore
from nima_semantica.workflow_contracts import HypothesisProposal


def proposal(statement="The bound can be improved.", project_id=None):
    return HypothesisProposal(
        proposal_id="hypothesis-1", project_id=project_id,
        kind="research", statement=statement,
    )


def test_proposal_service_scopes_and_replays_hypotheses_without_graph_mutation(tmp_path):
    store = GraphStore(tmp_path)
    try:
        service = ProposalService(store)
        record_id = service.persist_hypothesis(
            proposal(), corpus_id="papers", project_id="project-a", graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
        )
        assert service.persist_hypothesis(
            proposal(), corpus_id="papers", project_id="project-a", graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
        ) == record_id
        stored = service.get("HypothesisProposal", "hypothesis-1", corpus_id="papers", project_id="project-a")
        assert stored["content"]["project_id"] == "project-a"
        assert stored["content"]["graph_revision"] == GraphRevision(corpus_id="papers", project_id="project-a", project_revision=0).model_dump(mode="json")
        assert store.read_okf_snapshot(corpus_id="papers", project_id="project-a").nodes == ()
    finally:
        store.close()


def test_proposal_service_rejects_scope_and_divergent_replay(tmp_path):
    store = GraphStore(tmp_path)
    try:
        service = ProposalService(store)
        service.persist_hypothesis(
            proposal(), corpus_id="papers", project_id="project-a", graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
        )
        with pytest.raises(ConflictError, match="project scope"):
            service.persist_hypothesis(
                proposal(project_id="project-a"), corpus_id="papers", project_id="project-b", graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-b", project_revision=0),
            )
        with pytest.raises(ConflictError, match="different metadata"):
            service.persist_hypothesis(
                proposal("A different bound."), corpus_id="papers", project_id="project-a",
                graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
            )
    finally:
        store.close()


def test_promotion_is_project_scoped_and_remains_proposal_only(tmp_path):
    store = GraphStore(tmp_path)
    try:
        service = ProposalService(store)
        promotion = PromotionProposal(
            proposal_id="promotion-1", source_project_id="project-a",
            target_corpus_id="papers", source_snapshot_id="snapshot-1",
            source_graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0), node_ids=("claim-1",),
            evidence=(EvidenceReference(
                corpus_id="papers", artifact_id="a" * 64, region_id="region-1",
                source_revision="a" * 64, content_hash="a" * 64,
            ),), rationale="Review before promotion.",
        )
        record_id = service.persist_promotion(promotion)
        record = store.get(record_id, corpus_id="papers", project_id="project-a")
        assert record is not None and record.content["status"] == "proposed"
        assert store.read_okf_snapshot(corpus_id="papers").nodes == ()
    finally:
        store.close()
