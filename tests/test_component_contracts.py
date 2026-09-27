import pytest

from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.component_contracts import (
    ComponentAuthority,
    ComponentExecutionContext,
    ComponentManifest,
    FOUNDATIONAL_COMPONENT_NAMES,
    ReceiptDeclaration,
    ReceiptMode,
    SideEffectClass,
    manifest_for_component,
    validate_foundational_manifests,
    validate_manifest_execution,
)


def test_transformation_manifest_is_side_effect_free_and_json_safe():
    manifest = ComponentManifest(
        component_id="input_normalizer",
        version="1",
        input_contract="normalized_request_input",
        output_contract="normalized_request",
        authority=ComponentAuthority.TRANSFORMATION,
        side_effect_class=SideEffectClass.PURE_TRANSFORM,
        receipt=ReceiptDeclaration(mode=ReceiptMode.NONE),
    )
    assert manifest.model_dump(mode="json")["diagnostics"] == "required"
    assert manifest.side_effect_class is SideEffectClass.PURE_TRANSFORM
    assert manifest.receipt.mode is ReceiptMode.NONE


def test_graph_commit_requires_approved_authority():
    with pytest.raises(ValueError, match="approved_commit"):
        ComponentManifest(
            component_id="unsafe_commit",
            version="1",
            input_contract="delta",
            output_contract="receipt",
            authority=ComponentAuthority.PROPOSAL_ONLY,
            side_effect_class=SideEffectClass.GRAPH_COMMIT,
            receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED, contract_id="operation_receipt"),
        )


def test_manifest_execution_requires_declared_revision_and_scope():
    manifest = ComponentManifest(
        component_id="graph_reader",
        version="1",
        input_contract="request",
        output_contract="context_packet",
        authority=ComponentAuthority.READ_ONLY,
        side_effect_class=SideEffectClass.READ_ONLY_RETRIEVAL,
        receipt=ReceiptDeclaration(mode=ReceiptMode.EMITTED, contract_id="operation_receipt"),
    )
    with pytest.raises(ValueError, match="graph_revision"):
        validate_manifest_execution(
            manifest,
            ComponentExecutionContext(corpus_id="corpus", project_id="project"),
        )
    validate_manifest_execution(
        manifest,
        ComponentExecutionContext(
            corpus_id="corpus", project_id="project", graph_revision=GraphRevision(corpus_id="corpus", corpus_revision=0, project_id="project", project_revision=0)
        ),
    )


def test_foundational_manifests_freeze_authority_and_side_effects():
    manifests = validate_foundational_manifests()
    assert tuple(item.component_id for item in manifests) == (
        "input_normalizer",
        "graph_retrieval_normalizer",
        "hypothesis_generation",
        "hypothesis_comparison",
        "output_normalizer",
        "graph_commit",
    )
    by_id = {item.component_id: item for item in manifests}
    assert by_id["input_normalizer"].side_effect_class is SideEffectClass.READ_ONLY_RETRIEVAL
    assert by_id["output_normalizer"].side_effect_class is SideEffectClass.PROPOSAL_ONLY
    assert by_id["graph_retrieval_normalizer"].side_effect_class is SideEffectClass.READ_ONLY_RETRIEVAL
    assert by_id["hypothesis_generation"].side_effect_class is SideEffectClass.PROPOSAL_ONLY
    assert by_id["hypothesis_comparison"].side_effect_class is SideEffectClass.PROPOSAL_ONLY
    assert by_id["graph_commit"].side_effect_class is SideEffectClass.GRAPH_COMMIT
    assert by_id["graph_commit"].authority is ComponentAuthority.APPROVED_COMMIT
    assert all(item.diagnostics == "required" for item in manifests)
    assert by_id["graph_retrieval_normalizer"].revision_fields == ("graph_revision",)
    assert not by_id["graph_retrieval_normalizer"].registry_resource_kinds
    assert by_id["input_normalizer"].receipt.mode.value == "none"
    assert by_id["output_normalizer"].receipt.mode.value == "none"
    assert all(item.model_dump(mode="json") for item in manifests)


def test_foundational_manifest_lookup_is_first_class():
    assert manifest_for_component("HypothesisGeneration").output_contract == "hypothesis_generation_output"
    assert manifest_for_component("GraphCommit").input_contract == "graph_commit_request"
    assert FOUNDATIONAL_COMPONENT_NAMES[-1] == "GraphCommit"


def test_registry_bound_manifest_requires_registry_revision_context():
    manifest = manifest_for_component("HypothesisGeneration")
    with pytest.raises(ValueError, match="registry_revision"):
        validate_manifest_execution(
            manifest,
            ComponentExecutionContext(
                corpus_id="papers", project_id="project-a",
                graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0), source_revision="source:1",
            ),
        )
    validate_manifest_execution(
        manifest,
        ComponentExecutionContext(
            corpus_id="papers", project_id="project-a",
            registry_revision="registry:1", graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
            source_revision="source:1",
        ),
    )
