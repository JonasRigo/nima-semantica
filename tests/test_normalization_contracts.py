import pytest
from pydantic import ValidationError

from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.normalization_contracts import (
    FieldMapping,
    FlowAdapterManifest,
    NormalizationArtifact,
    NormalizationDiagnostic,
    NormalizationStatus,
    NormalizedOutput,
    NormalizedRequest,
    NormalizedProposal,
)
from nima_semantica.okf_contracts import OKFDelta


def artifact(artifact_id="raw-1"):
    return NormalizationArtifact(
        artifact_id=artifact_id,
        media_type="application/json",
        content_hash="a" * 64,
        locator="artifact://raw-1",
        role="input",
    )


def test_manifest_declares_mappings_and_is_proposal_only():
    manifest = FlowAdapterManifest(
        adapter_id="example-normalizer",
        name="Example normalizer",
        version="1.0.0",
        input_mappings=(FieldMapping(source_path="question", target_path="objective"),),
        output_mappings=(FieldMapping(source_path="answer", target_path="proposals[0]"),),
    )

    assert manifest.authority == "proposal_only"


def test_request_and_output_carry_scope_and_provenance():
    request = NormalizedRequest(
        request_id="request-1",
        adapter_id="example-normalizer",
        corpus_id="papers",
        project_id="project-a",
        objective="Find a better lower bound.",
        raw_inputs=(artifact(),),
    )
    output = NormalizedOutput(
        request_id=request.request_id,
        adapter_id=request.adapter_id,
        corpus_id=request.corpus_id,
        project_id=request.project_id,
        status=NormalizationStatus.COMPLETE,
        proposals=(
            NormalizedProposal(
                proposal_id="hypothesis-1",
                kind="hypothesis",
                payload={"statement": "x >= 0"},
                evidence=(artifact(),),
            ),
        ),
        diagnostics=(
            NormalizationDiagnostic(
                diagnostic_id="diagnostic-1",
                code="lossy.conversion",
                severity="warning",
                message="The source omitted units.",
                artifact_id="raw-1",
            ),
        ),
    )

    assert output.authority == "proposal_only"
    assert output.proposals[0].evidence[0].artifact_id == "raw-1"


def test_output_rejects_graph_delta_with_wrong_scope():
    delta = OKFDelta(
        delta_id="delta-1",
        base_revision=GraphRevision(corpus_id="other-papers", corpus_revision=0, project_id=None, project_revision=None),
        corpus_id="other-papers",
        reason="Proposed normalized graph update.",
    )

    with pytest.raises(ValidationError, match="corpus scope"):
        NormalizedOutput(
            request_id="request-1",
            adapter_id="example-normalizer",
            corpus_id="papers",
            status=NormalizationStatus.PARTIAL,
            graph_delta=delta,
        )
