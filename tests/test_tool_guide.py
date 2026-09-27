"""Contract discovery is pure, honest about delivery, and never dispatches."""
import json
from pathlib import Path

import pytest

from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.tool_guide import ToolGuideRequest, public_tool_catalog, tool_guide


def test_catalog_matches_approved_inventory_and_is_honest():
    catalog = public_tool_catalog()
    assert len(catalog) == len({item.tool_id for item in catalog}) == 24
    for index, item in enumerate(catalog, 1):
        assert item.input_schema is not None
        if item.tool_id in {"draft_proof", "conduct_proof"}:
            continue
        if item.tool_id not in ("tool_guide", "research_run", "load_ontology", "save_ontology", "prepare_and_index_sources", "inspect_corpus", "retrieve_research_context", "read_evidence", "calculate_mathematics", "search_for_counterexamples", "verify_lean", "deep_extraction", "analyze_graph", "trace_claim_dependencies", "substantiate_graph_snapshot", "update_project_graph", "hypothesis_generation", "compare_research_objects", "deep_research", "review_research", "save_research_analysis", "develop_proof", "draft_lean"):
            assert item.implementation == "planned"
            assert item.input_schema is None and item.output_schema is None
            assert item.examples == ()
    guide = catalog[0]
    assert guide.implementation == "implemented" and guide.visual_approval == "approved"
    assert guide.input_schema == ToolGuideRequest.model_json_schema()
    for example in guide.examples:
        assert tool_guide(ToolGuideRequest.model_validate(example)).status == "complete"


def test_catalog_records_current_visual_approvals():
    catalog = {item.tool_id: item for item in public_tool_catalog()}
    assert catalog["verify_lean"].visual_approval == "approved"
    assert catalog["deep_research"].visual_approval == "approved"
    assert catalog["review_research"].visual_approval == "approved"
    assert catalog["draft_lean"].visual_approval == "approved"
    assert "develop_proof" not in catalog
    assert catalog["conduct_proof"].contract_version == "proof-session-v1"


@pytest.mark.parametrize("name", ["Tool Guide", "tool_guide", " tool GUIDE "])
def test_exact_named_lookup(name):
    result = tool_guide(ToolGuideRequest(tool=name))
    assert result.status == "complete"
    assert [item["tool_id"] for item in result.data["tools"]] == ["tool_guide"]
    assert result.receipt_ids == () and result.artifacts == {}
    assert result.data["execution_supported"] is False


@pytest.mark.parametrize("name", ["Choose the best tool", "Calculate", "__import__('os').system('true')", "../secrets"])
def test_unknown_names_never_become_dispatch_or_recommendation(name):
    result = tool_guide(ToolGuideRequest(tool=name))
    assert result.status == "failed"
    assert result.data["tools"] == []
    assert result.diagnostics[0]["code"] == "tool_guide.unknown_tool"
    assert name not in result.model_dump_json()


@pytest.mark.parametrize("payload", [{"execute": True}, {"approved": True}, {"corpus_id": "private"}, {"tool": 4}, {"tool": None}, {"tool": "x" * 129}])
def test_invalid_request_rejected(payload):
    with pytest.raises(ValueError):
        ToolGuideRequest.model_validate(payload)


def test_lookup_does_not_open_store_or_network(monkeypatch):
    from nima_semantica.storage import GraphStore
    import socket
    def forbidden(*args, **kwargs):
        pytest.fail("catalog lookup must not execute external operations")
    monkeypatch.setattr(GraphStore, "__init__", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    first = tool_guide(ToolGuideRequest())
    first.data["tools"][0]["input_schema"].clear()
    second = tool_guide(ToolGuideRequest())
    assert second.data["tools"][0]["input_schema"]
    assert second.data["catalog_hash"] == first.data["catalog_hash"]
    json.dumps(second.model_dump(mode="json"), allow_nan=False)


@pytest.mark.parametrize("name", ["GuideFields", "ToolGuide"])
def test_guide_manifests_declare_no_scope_or_side_effects(name):
    manifest = manifest_for_component(name)
    assert manifest.scope_fields == manifest.revision_fields == manifest.capabilities == ()
    assert manifest.side_effect_class.value == "pure_transform"
    assert manifest.receipt.mode.value == "none"
