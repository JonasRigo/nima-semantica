import pytest
from pydantic import ValidationError

from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.artifact_contracts import ArtifactEnvelope, GraphArtifact
from nima_semantica.models import canonical, identity
from nima_semantica.normalization_boundary import execute_normalizer
from nima_semantica.normalization_contracts import (
    FieldMapping,
    FlowAdapterManifest,
    NormalizedRequest,
)
from nima_semantica.okf_contracts import OKFReference, OKFReferenceKind, OKFSnapshot, OKFNode
from nima_semantica.retrieval_contracts import EmbeddingProjectionManifest, RetrievalContextPacket, validate_projection_revision


def test_canonical_serialization_and_identity_ignore_mapping_order():
    left = {"b": {"z": 1, "a": "é"}, "a": [3, 2, 1]}
    right = {"a": [3, 2, 1], "b": {"a": "é", "z": 1}}
    assert canonical(left) == canonical(right)
    assert identity(left) == identity(right)


def test_okf_provenance_and_artifact_links_are_unique():
    reference = OKFReference(
        reference_kind=OKFReferenceKind.RECORD,
        target_id="record-1",
        corpus_id="papers",
        revision="sqlite:1",
    )
    with pytest.raises(ValidationError, match="duplicate OKF provenance"):
        OKFNode(
            node_id="claim-1", node_type="claim", corpus_id="papers",
            provenance=(reference, reference),
        )
    with pytest.raises(ValidationError, match="duplicate source artifacts"):
        ArtifactEnvelope(
            artifact_id="a" * 64, artifact_kind="source_text",
            media_type="text/plain", content_hash="a" * 64,
            corpus_id="papers", source_artifact_ids=("b" * 64, "b" * 64),
        )


def test_graph_artifact_and_context_packet_reject_scope_mismatch():
    envelope = ArtifactEnvelope(
        artifact_id="a" * 64, artifact_kind="graph_artifact",
        media_type="application/json", content_hash="a" * 64,
        corpus_id="papers", project_id="project-a",
    )
    snapshot = OKFSnapshot(
        snapshot_id="snapshot-1", graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-b", project_revision=0),
        corpus_id="papers", project_id="project-b",
    )
    with pytest.raises(ValidationError, match="project scope"):
        GraphArtifact(envelope=envelope, snapshot=snapshot)

    projection = EmbeddingProjectionManifest(
        projection_id="projection-1", corpus_id="papers", project_id="project-b",
        source_revision="source:1", provider="local", model="test",
        model_revision="1", dimension=3,
    )
    with pytest.raises(ValidationError, match="projection project scope"):
        RetrievalContextPacket(
            query="q", corpus_id="papers", project_id="project-a",
            graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0), projection=projection,
        )


def test_revision_bound_retrieval_rejects_stale_projection():
    projection = EmbeddingProjectionManifest(
        projection_id="projection-1", corpus_id="papers", project_id="project-a",
        source_revision="source:1", provider="local", model="test",
        model_revision="1", dimension=3,
    )
    with pytest.raises(ValueError, match="stale"):
        validate_projection_revision(
            projection, corpus_id="papers", project_id="project-a", source_revision="source:2"
        )


def test_normalization_rejects_unknown_output_and_authority_escalation():
    manifest = FlowAdapterManifest(
        adapter_id="adapter-1", name="Adapter", version="1",
        input_mappings=(FieldMapping(source_path="question", target_path="objective"),),
    )
    request = NormalizedRequest(
        request_id="request-1", adapter_id="adapter-1", corpus_id="papers",
        project_id="project-a", graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
    )
    output = execute_normalizer(
        manifest, request,
        lambda _: {
            "request_id": "request-1", "adapter_id": "adapter-1",
            "corpus_id": "papers", "project_id": "project-a",
            "graph_revision": "sqlite:1", "status": "complete",
            "authority": "approved_commit", "unexpected": True,
        },
    )
    assert output.status == "failed"
    assert output.diagnostics[0].code == "normalizer.execution_error"
