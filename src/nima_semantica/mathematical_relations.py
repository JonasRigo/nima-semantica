"""Typed, evidence-bound comparisons and cross-field hypotheses.

Source attribution is checked; mathematical equivalence is not certified by a
model or a citation. Direction, additional assumptions, and losses stay explicit.
"""

from typing import Annotated, Literal

from pydantic import Field

from .models import StrictModel

Text = Annotated[str, Field(min_length=1, max_length=6000)]


class RelationCitation(StrictModel):
    region_id: str = Field(min_length=1, max_length=200)
    quote: Text


class MathematicalRelation(StrictModel):
    field: str = Field(min_length=1, max_length=100)
    formulation: Text
    relation: Literal["equivalence_candidate", "conditional_translation", "analogy"]
    direction: Literal[
        "both_directions",
        "original_to_reformulation",
        "reformulation_to_original",
        "neither_established",
    ]
    dictionary: list[Text] = Field(min_length=1, max_length=16)
    additional_assumptions: list[Text] = Field(max_length=16)
    preserved_structure: list[Text] = Field(max_length=16)
    lost_structure: list[Text] = Field(max_length=16)
    proof_obligations: list[Text] = Field(min_length=1, max_length=16)
    useful_methods: list[Text] = Field(min_length=1, max_length=16)
    discriminating_checks: list[Text] = Field(min_length=1, max_length=8)
    search_queries: list[Text] = Field(max_length=8)
    citations: list[RelationCitation] = Field(default_factory=list, max_length=8)
    equivalence_verified: Literal[False] = False


class MathematicalComparison(StrictModel):
    task_id: str
    relations: list[MathematicalRelation] = Field(min_length=1, max_length=12)
    differences: list[Text] = Field(min_length=1, max_length=16)
    unresolved: list[Text] = Field(min_length=1, max_length=16)


class HypothesisReformulations(StrictModel):
    task_id: str
    reformulations: list[MathematicalRelation] = Field(min_length=1, max_length=24)
    unexplored_fields: list[Text] = Field(max_length=12)
    unresolved: list[Text] = Field(min_length=1, max_length=16)


def bind_relations(relations, regions):
    catalogue = {region["id"]: region for region in regions}
    rows = []
    for relation in relations:
        row = relation.model_dump(mode="json")
        if (
            relation.relation == "analogy"
            and relation.direction != "neither_established"
        ):
            raise ValueError("an analogy must not assert an implication direction")
        if (
            relation.relation == "equivalence_candidate"
            and relation.direction != "both_directions"
        ):
            raise ValueError(
                "equivalence candidates require obligations in both directions"
            )
        citations = []
        for citation in relation.citations:
            region = catalogue.get(citation.region_id)
            if region is None or citation.quote not in region["text"]:
                raise ValueError("citation absent from supplied exact source passage")
            citations.append(
                {
                    **citation.model_dump(),
                    "normalized_artifact": region.get("normalized_artifact"),
                    "start": region.get("start"),
                    "end": region.get("end"),
                }
            )
        row.update(
            citations=citations,
            attribution_checked=bool(citations),
            scientific_status="unverified_proposal",
            source_correspondence_verified=False,
        )
        rows.append(row)
    return rows
