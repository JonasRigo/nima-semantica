"""Exact scope and honest outcomes for the new research tools."""

import copy
import json

import pytest

from nima_semantica.addition_contracts import (
    SearchCounterexamplesInput,
    TraceDependenciesInput,
)
from nima_semantica.research_additions import (
    search_integer_witness,
    check_integer_witness,
    trace_dependencies,
)
from nima_semantica.mathematical_relations import MathematicalRelation, bind_relations
from nima_semantica.models import Record


def relation():
    return dict(
        field="linear algebra",
        formulation="A proposed translation.",
        relation="conditional_translation",
        direction="original_to_reformulation",
        dictionary=["A corresponds to B."],
        additional_assumptions=["Finite dimension."],
        preserved_structure=["Linearity"],
        lost_structure=[],
        proof_obligations=["Prove the implication."],
        useful_methods=["Spectral analysis"],
        discriminating_checks=["Test a two-dimensional example."],
        search_queries=["spectral theorem"],
        citations=[],
        equivalence_verified=False,
    )


def test_counterexample_found_rechecked_and_not_prose_verified():
    search = search_integer_witness(SearchCounterexamplesInput().model_dump())
    checked = check_integer_witness(search)
    assert checked["verified_refutation"]
    assert not checked["source_correspondence_verified"]
    assert checked["findings"][0]["left"] != checked["findings"][0]["right"]
    bad = copy.deepcopy(search)
    bad["task"]["claim"]["relation"] = "le"
    with pytest.raises(ValueError, match="identity"):
        check_integer_witness(bad)
    bad = copy.deepcopy(search)
    bad["witness"] = {"x": True}
    with pytest.raises(ValueError, match="domain"):
        check_integer_witness(bad)


def test_absent_witness_is_not_proof_and_budget_is_explicit():
    task = SearchCounterexamplesInput(lower=0, upper=1).model_dump()
    checked = check_integer_witness(search_integer_witness(task))
    assert checked["box_exhausted"] and not checked["verified_refutation"]
    task.update(upper=1000000, max_evaluations=1)
    search = search_integer_witness(task)
    assert search["truncated"] and search["evaluations"] == 1


def test_dependency_cycle_depth_cut_and_no_admission():
    task = TraceDependenciesInput().model_dump()
    task["edges"].append({"source": "lemma", "target": "claim", "relation": "requires"})
    original = copy.deepcopy(task)
    trace = trace_dependencies(task)
    assert trace["cycles"] == [["claim", "lemma", "claim"]]
    assert task == original
    task["max_hops"] = 0
    assert trace_dependencies(task)["truncated"]
    task["targets"] = ["unknown"]
    with pytest.raises(ValueError, match="unknown"):
        trace_dependencies(task)


def test_relation_citations_and_equivalence_cannot_be_invented():
    item = relation()
    item["equivalence_verified"] = True
    with pytest.raises(ValueError):
        MathematicalRelation.model_validate(item)
    item = relation()
    item["citations"] = [{"region_id": "r", "quote": "invented"}]
    with pytest.raises(ValueError, match="citation"):
        bind_relations(
            [MathematicalRelation.model_validate(item)],
            [{"id": "r", "text": "actual text"}],
        )
    item["citations"][0]["quote"] = "actual"
    row = bind_relations(
        [MathematicalRelation.model_validate(item)],
        [{"id": "r", "text": "actual text"}],
    )[0]
    assert row["attribution_checked"] and not row["equivalence_verified"]
    item = relation()
    item["relation"] = "analogy"
    with pytest.raises(ValueError, match="analogy"):
        bind_relations([MathematicalRelation.model_validate(item)], [])
