import copy

import pytest

from nima_semantica.flow_adapters import AdapterContext, input_context, map_fields, normalize_output
from nima_semantica.normalization_contracts import FieldMapping
from nima_semantica.okf_contracts import GraphRevision


def context(**kwargs):
    return input_context("source", corpus_id="corpus", project_id="project", request_id="request", **kwargs)[1]


def test_native_shapes_and_context_are_preserved():
    for raw in ("text", {"text": "source"}, [{"text": "source"}]):
        mapped, ctx = input_context(raw, corpus_id="corpus", request_id="r")
        assert mapped == ctx.request.payload
        result = normalize_output(raw, ctx)
        assert result.raw_output == raw
        assert result.normalized.authority == "proposal_only"
        assert result.persisted is False
        assert result.normalized.graph_delta is None


def test_binding_required_and_overlap_rejection():
    with pytest.raises(ValueError, match="missing required"):
        map_fields({}, [FieldMapping(source_path="missing", target_path="text", required=True)])
    with pytest.raises(ValueError, match="overlapping"):
        map_fields({"a": 1}, [FieldMapping(source_path="a", target_path="a"),
                               FieldMapping(source_path="a", target_path="a.b")])
    with pytest.raises(ValueError, match="dedicated"):
        map_fields({}, [FieldMapping(source_path="a", target_path="b", transform="eval")])


def test_mapping_never_changes_input_or_scope():
    raw = {"nested": {"value": [1]}}
    previous = copy.deepcopy(raw)
    mapped = map_fields(raw, [FieldMapping(source_path="nested.value", target_path="rows")])
    mapped["rows"].append(2)
    assert raw == previous
    result = normalize_output({"corpus_id": "foreign", "authority": "approved_commit"}, context())
    assert result.normalized.corpus_id == "corpus"
    assert result.normalized.authority == "proposal_only"


def test_context_rejects_foreign_revision_and_unknown_chunk_binding():
    with pytest.raises(ValueError, match="revision scope"):
        context(graph_revision=GraphRevision(corpus_id="foreign"))
    with pytest.raises(ValueError, match="declared source"):
        context(chunk_bindings={"chunk1": "invented"})


def test_graph_output_is_not_falsely_grounded():
    raw = {"objects": [{"components": [{"component_id": "c1", "source_chunk_ids": ["invented"]}],
                       "scope_complete": True}]}
    result = normalize_output(raw, context(), preset="graph_extraction")
    assert result.raw_output == raw
    assert result.normalized.status == "partial"
    assert result.normalized.graph_delta is None
    assert result.normalized.diagnostics[0].code == "normalizer.grounding_required"


def test_failure_preserves_raw_output():
    result = normalize_output("unstructured", context(), bindings=[
        FieldMapping(source_path="absent", target_path="answer", required=True)])
    assert result.normalized.status == "failed"
    assert result.raw_output == "unstructured"
    assert not result.normalized.proposals
