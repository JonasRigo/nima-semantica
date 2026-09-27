"""Explicit contracts for corpus inspection and mathematical research additions."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .models import StrictModel
from .verification import PolynomialClaim
from .deep_research import DeepResearchInput

Text = Annotated[str, Field(min_length=1, max_length=12000)]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+$", max_length=200)]


class AdditionScope(StrictModel):
    corpus_id: Identifier = "replacement_preview"
    project_id: Identifier = "replacement_preview"


class InspectCorpusInput(AdditionScope):
    offset: int = Field(default=0, ge=0, le=1000000)
    limit: int = Field(default=25, ge=1, le=100)


def example_claim():
    return PolynomialClaim.model_validate(
        {
            "variables": ["x"],
            "relation": "eq",
            "left": {
                "op": "mul",
                "left": {"op": "variable", "name": "x"},
                "right": {"op": "variable", "name": "x"},
            },
            "right": {"op": "variable", "name": "x"},
        }
    )


class SearchCounterexamplesInput(StrictModel):
    statement: Text = "For every integer x, x squared equals x."
    claim: PolynomialClaim = Field(default_factory=example_claim)
    lower: int = Field(default=-3, ge=-1000000, le=1000000)
    upper: int = Field(default=3, ge=-1000000, le=1000000)
    max_evaluations: int = Field(default=1000, ge=1, le=10000)

    @model_validator(mode="after")
    def bounds(self):
        if self.lower > self.upper:
            raise ValueError("lower exceeds upper")
        pending = [self.claim.left, self.claim.right]
        count = 0
        while pending:
            node = pending.pop()
            count += 1
            if count > 128:
                raise ValueError("search expression exceeds 128-node budget")
            if node.left:
                pending.extend([node.left, node.right])
        return self


class DependencyNode(StrictModel):
    id: Identifier
    statement: Text
    status: Literal["proposed", "attributed", "assumed", "obligation"] = "proposed"


class DependencyEdge(StrictModel):
    source: Identifier
    target: Identifier
    relation: Literal["requires", "depends_on", "uses"] = "requires"


def example_nodes():
    return [
        DependencyNode(id="claim", statement="The proposed conclusion."),
        DependencyNode(
            id="lemma", statement="A needed supporting lemma.", status="obligation"
        ),
    ]


class TraceDependenciesInput(AdditionScope):
    candidate_artifact_id: str = Field(default="", pattern=r"^(?:[a-f0-9]{64})?$")
    nodes: list[DependencyNode] = Field(default_factory=example_nodes, max_length=256)
    edges: list[DependencyEdge] = Field(
        default_factory=lambda: [DependencyEdge(source="claim", target="lemma")],
        max_length=1024,
    )
    targets: list[Identifier] = Field(
        default_factory=lambda: ["claim"], min_length=1, max_length=16
    )
    max_hops: int = Field(default=4, ge=0, le=12)
    max_paths: int = Field(default=128, ge=1, le=512)


class ComparisonInput(AdditionScope):
    object_a: Text = "The additive group of real numbers."
    object_b: Text = "The multiplicative group of positive real numbers."
    question: Text = (
        "Compare their group structure and the assumptions behind a possible isomorphism."
    )
    assumptions: str = Field(
        default="Use the usual operations and topologies.", max_length=12000
    )
    context_region_ids: list[str] = Field(default_factory=list, max_length=8)


class ReformulateInput(AdditionScope):
    hypothesis: Text = (
        "Every real symmetric matrix has an orthonormal basis of eigenvectors."
    )
    assumptions: Text = (
        "Finite-dimensional real inner-product spaces; linear operators and standard matrix representations."
    )
    mathematical_fields: list[Annotated[str, Field(min_length=1, max_length=100)]] = (
        Field(
            default_factory=lambda: [
                "linear algebra",
                "geometry",
                "variational analysis",
                "representation theory",
            ],
            min_length=1,
            max_length=12,
        )
    )
    context_region_ids: list[str] = Field(default_factory=list, max_length=8)
    max_reformulations: int = Field(default=8, ge=1, le=24)
    search_literature: bool = False
    literature_query: str = Field(default="", max_length=500)
    selected_source_url: str = Field(default="", max_length=300)
    use_selected_source: bool = False

    @model_validator(mode="after")
    def unique_fields(self):
        if len({field.casefold() for field in self.mathematical_fields}) != len(
            self.mathematical_fields
        ):
            raise ValueError("duplicate mathematical fields")
        return self


PUBLIC_ADDITIONS = {
    "Inspect Corpus": InspectCorpusInput,
    "Search for Counterexamples": SearchCounterexamplesInput,
    "Trace Claim Dependencies": TraceDependenciesInput,
    "Compare Mathematical Objects": ComparisonInput,
    "Reformulate Hypothesis": ReformulateInput,
    "Deep Research": DeepResearchInput,
}


def addition_examples():
    return {
        name: schema().model_dump(mode="json")
        for name, schema in PUBLIC_ADDITIONS.items()
    }
