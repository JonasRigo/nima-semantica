import asyncio
import json

import pytest

from nima_semantica.deep_research import evaluate_arxiv_query, prepare_research_batches, validate_deep_research_synthesis


def request(**updates):
    return {"corpus_id": "c", "project_id": "p", "question": "What is known?", "region_ids": ["r1"], **updates}


def regions(text="a" * 7000):
    return [{"id": "r1", "document_id": "paper", "text": text, "normalized_artifact": "a" * 64}]


def test_batches_preserve_offsets_and_resume_without_rereading_completed_spans():
    first = prepare_research_batches(request(max_batches=1), regions())
    assert first["truncated"] and len(first["batches"]) == 1
    batch = first["batches"][0]
    assert batch["start"] == 0 and batch["end"] == len(batch["text"])
    second = prepare_research_batches(request(max_batches=4, previous_state=first["state"]), regions())
    assert [b["start"] for b in second["batches"]] == [6000]
    with pytest.raises(ValueError, match="differs"):
        prepare_research_batches(request(), [{"id": "other", "text": "x"}])


def assessment(hits, relevant):
    return {"query": {"query": "operator algebras", "purpose": "fill a gap", "evidence_gap": "missing method"},
            "hits": [{"arxiv_id": str(i), "title": "title", "abstract": "abstract"} for i in range(hits)],
            "relevant_ids": [str(i) for i in relevant], "reasons": {str(i): "on topic" for i in relevant}}


def test_query_validation_requires_three_of_five_or_every_small_result_set():
    assert evaluate_arxiv_query(assessment(5, (0, 1, 2)))["validated"]
    assert not evaluate_arxiv_query(assessment(5, (0, 1)))["validated"]
    assert evaluate_arxiv_query(assessment(2, (0, 1)))["validated"]
    assert not evaluate_arxiv_query(assessment(0, ())) ["validated"]


def test_synthesis_binds_findings_and_summaries_to_current_batches():
    context = prepare_research_batches(request(), regions("x"))
    batch = context["batches"][0]["id"]
    output = {"summaries": {batch: "A source summary."},
              "findings": [{"statement": "An attributed finding.", "batch_ids": [batch]}],
              "ontology_graph": {}, "search_queries": [], "unresolved": []}
    assert validate_deep_research_synthesis(context, output)["processed_batches"] == 1
    output["findings"][0]["batch_ids"] = ["invented"]
    with pytest.raises(ValueError, match="outside"):
        validate_deep_research_synthesis(context, output)
