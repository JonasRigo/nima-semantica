from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.artifact_contracts import ArtifactEnvelope, GraphArtifact
from nima_semantica.evidence_contracts import PromotionProposal
from nima_semantica.models import canonical
from nima_semantica.okf_contracts import EvidenceReference, OKFSnapshot
from nima_semantica.research_contracts import (
    ActionRecord,
    CritiqueFinding,
    ReportManifest,
    SymbolRegistry,
)
from nima_semantica.workflow_contracts import HypothesisComparison, HypothesisProposal


def test_all_research_artifacts_are_json_serializable_and_authority_bounded():
    digest = "a" * 64
    envelope = ArtifactEnvelope(
        artifact_id=digest,
        artifact_kind="graph_artifact",
        media_type="application/json",
        content_hash=digest,
        corpus_id="papers",
        project_id="project-a",
    )
    graph_artifact = GraphArtifact(
        envelope=envelope,
        snapshot=OKFSnapshot(
            snapshot_id="snapshot-1",
            graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
            corpus_id="papers",
            project_id="project-a",
        ),
    )
    proposal = HypothesisProposal(
        proposal_id="hypothesis-1",
        corpus_id="papers",
        project_id="project-a",
        graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
        kind="research",
        statement="The bound can be improved.",
    )
    comparison = HypothesisComparison(
        proposal_id=proposal.proposal_id,
        corpus_id="papers",
        project_id="project-a",
        graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
        assessment="unresolved",
        rationale="The available evidence is insufficient.",
    )
    models = (
        graph_artifact,
        proposal,
        comparison,
        ActionRecord(
            action_id="action-1", run_id="run-1", corpus_id="papers",
            project_id="project-a", action_kind="retrieve", actor="harness",
            authority="observe", side_effect="read_only_retrieval",
        ),
        CritiqueFinding(
            finding_id="finding-1", corpus_id="papers", project_id="project-a",
            target_id="hypothesis-1", kind="gap",
            statement="A verification obligation remains.",
        ),
        SymbolRegistry(
            symbol_id="symbol-1", canonical_name="Q", corpus_id="papers",
            confidence=0.9,
        ),
        PromotionProposal(
            proposal_id="promotion-1", source_project_id="project-a",
            target_corpus_id="papers", source_snapshot_id="snapshot-1",
            source_graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0), node_ids=("claim-1",),
            evidence=(EvidenceReference(
                corpus_id="papers", artifact_id=digest, region_id="region-1",
                source_revision=digest, content_hash=digest,
            ),),
            rationale="Promotion is pending review.",
        ),
        ReportManifest(
            report_id="report-1", corpus_id="papers", project_id="project-a",
            title="Research report", artifact_id=digest,
        ),
    )
    serialized = [model.model_dump(mode="json") for model in models]
    assert all(isinstance(item, dict) for item in serialized)
    assert canonical(serialized) == canonical(serialized)
    assert proposal.authority == "proposal_only"
    assert comparison.authority == "proposal_only"
    assert graph_artifact.envelope.content_hash == digest
