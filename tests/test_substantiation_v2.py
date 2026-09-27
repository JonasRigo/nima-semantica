"""Focused v2 citation, diagnostic and referent regression checks."""
import copy
import json

from nima_semantica.substantiation_contracts import AssessTarget, VERSION
from nima_semantica.substantiation_tool import INSTRUCTIONS, safe_validation_feedback, substantiation_tools, substantiate_graph_snapshot
from nima_semantica.execution_receipts import ExecutionReceiptService
from test_claim_dependencies import seed
from test_deep_extraction import Model, region
from test_source_pipeline import store, pipeline, request as source_request
from test_substantiation import actions, assessment, context, request


def _action_outputs(store, result):
    service = ExecutionReceiptService(store)
    return [service.get(ref, corpus_id="papers", project_id="research").metadata["output"]
        for ref in result.receipt_ids[1:]
        if service.get(ref, corpus_id="papers", project_id="research").stage == "substantiate_graph_snapshot_action"]


def test_document_id_diagnostic_recovers_without_re_read_or_weakened_grounding(store):
    seed(store)
    source = region(store, "The earlier lemma supplies the missing premise.")
    bad = copy.deepcopy(assessment(source))
    bad["arguments"]["supporting"][0]["source_id"] = source.content["source_id"]
    sequence = [actions(source)[0], bad, *actions(source)[1:]]
    model = Model(sequence)
    result = substantiate_graph_snapshot(store, request(store, source), context(max_actions=5), model=model)
    assert result.status == "partial"
    output = _action_outputs(store, result)
    assert output[1]["rejected"] is True
    assert "document source_id" in output[1]["reason"]
    assert source.id in output[1]["reason"]
    assert len([o for o in output if "passages" in o]) == 1
    final = result.data["result"]["assessments"][0]
    assert final["grounding"][0]["region_id"] == source.id
    assert final["grounding"][0]["source_id"] == source.content["source_id"]
    assert final["source_substantiation_subject"] == "graph_claim"
    assert final["recommendation_subject"] == "harness_finding"
    feedback = json.loads(model.calls[1]["messages"][-1]["content"])
    assert feedback["referents"]["citation_id"].startswith("Anchor source_id must equal")
    assert "graph claim" in feedback["referents"]["graph_claim"]


def test_foreign_or_unread_id_never_enumerates_foreign_source(store):
    seed(store)
    source = region(store, "An exact local passage.")
    bad = copy.deepcopy(assessment(source))
    bad["arguments"]["supporting"][0]["source_id"] = "foreign-document"
    sequence = [actions(source)[0], bad, *actions(source)[1:]]
    result = substantiate_graph_snapshot(store, request(store, source), context(max_actions=5), model=Model(sequence))
    assert result.status == "partial"
    output = _action_outputs(store, result)
    assert output[1] == {"rejected": True, "reason": "Cite only exact region_ids already read in this attempt."}


def test_ambiguous_document_id_lists_both_authorized_regions_without_substitution(store):
    seed(store)
    prepared, _, _ = pipeline(store, source_request(operation_id="prepare-two-regions",
        sources=[{"name": "long.txt", "text": "A distinct lemma supplies evidence.\n\n" * 110}]))
    region_ids = prepared.data["region_ids"]
    assert len(region_ids) >= 2
    first = store.get(region_ids[0])
    bad = copy.deepcopy(assessment(first))
    bad["arguments"]["supporting"][0]["source_id"] = first.content["source_id"]
    sequence = [{"name": "read_regions", "arguments": {"region_ids": region_ids[:2]}}, bad, assessment(first),
        {"name": "analyze_dependencies", "arguments": {}}, {"name": "submit_result", "arguments": {}}]
    req = request(store, first, source_region_ids=tuple(region_ids[:2]))
    result = substantiate_graph_snapshot(store, req, context(max_actions=5), model=Model(sequence))
    assert result.status == "partial"
    rejected = _action_outputs(store, result)[1]
    assert rejected["rejected"] is True
    assert all(ref in rejected["reason"] for ref in region_ids[:2])
    assert result.data["result"]["assessments"][0]["grounding"][0]["region_id"] == first.id


def test_validation_diagnostic_is_precise_safe_and_repairable(store):
    seed(store)
    source = region(store, "The cited passage supplies a usable premise.")
    bad = copy.deepcopy(assessment(source))
    bad["arguments"]["source_substantiation"] = "support_not_located"
    sequence = [actions(source)[0], bad, *actions(source)[1:]]
    result = substantiate_graph_snapshot(store, request(store, source), context(max_actions=5), model=Model(sequence))
    assert result.status == "partial"
    output = _action_outputs(store, result)
    assert output[1]["fields"][0]["code"] == "support_not_located_has_support"
    assert "support_not_located cannot include supporting anchors" in output[1]["fields"][0]["guidance"]
    assert "code" not in safe_validation_feedback(_unknown_error())["fields"][0]
    assert result.data["version"] == VERSION == "substantiate-graph-snapshot-v2"


def _unknown_error():
    from pydantic import ValidationError
    try:
        AssessTarget.model_validate({**assessment()["arguments"], "target_index": 99})
    except ValidationError as exc:
        return exc
    raise AssertionError("Expected validation failure")


def test_schema_and_prompt_use_distinct_referents():
    schema = next(t["function"]["parameters"] for t in substantiation_tools(context()) if t["function"]["name"] == "assess_target")
    props = schema["properties"]
    assert "underlying graph claim" in props["source_substantiation"]["description"]
    assert "harness finding" in props["recommendation"]["description"]
    assert "region_id" in schema["$defs"]["RegionAnchor"]["properties"]["source_id"]["description"]
    assert "document source_id" in INSTRUCTIONS
    assert "proposed_claim_revision" in props
