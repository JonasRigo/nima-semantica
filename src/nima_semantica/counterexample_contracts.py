"""Public search requests and small agent actions; scope remains operator-owned."""
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from .addition_contracts import SearchCounterexamplesInput
from .math_retrieval import MathRetrievalPolicy, RetrieveMathContext
from .models import StrictModel
from .okf_contracts import GraphIdentifier, GraphRevision
from .providers import ModelManifest
from .verification import PolynomialClaim

VERSION = "search-counterexamples-v1"


class IntegerSearch(StrictModel):
    claim: PolynomialClaim
    assumptions: tuple[PolynomialClaim, ...] = Field(default=(), max_length=8)
    lower: StrictInt = Field(default=-3, ge=-1000000, le=1000000)
    upper: StrictInt = Field(default=3, ge=-1000000, le=1000000)
    max_evaluations: StrictInt = Field(default=1000, ge=1, le=10000)
    correspondence: str = Field(min_length=1, max_length=4000,
        description="Explain target, assumptions, domain and quantifier correspondence; this explanation is not verified.")

    @model_validator(mode="after")
    def valid_encoding(self):
        SearchCounterexamplesInput(claim=self.claim, lower=self.lower, upper=self.upper,
            max_evaluations=self.max_evaluations)
        for assumption in self.assumptions:
            if not set(assumption.variables) <= set(self.claim.variables):
                raise ValueError("assumption uses variables outside the claim")
            SearchCounterexamplesInput(claim=assumption)
        if len(self.model_dump_json().encode()) > 20000:
            raise ValueError("encoded search exceeds fixed worker source capacity")
        return self


class CounterexampleRequest(StrictModel):
    mode: Literal["preview", "integer", "agent"] = "preview"
    operation_id: GraphIdentifier | None = None
    run_id: GraphIdentifier | None = None
    statement: str = Field(default="For every integer x, x squared equals x.", min_length=1, max_length=12000)
    assumptions: tuple[str, ...] = Field(default=(), max_length=16)
    domain: str = Field(default="integers", min_length=1, max_length=2000)
    quantifier: Literal["forall", "exists", "mixed"] = "forall"
    target_record_id: GraphIdentifier | None = None
    parent_record_id: GraphIdentifier | None = None
    graph_revision: GraphRevision | None = None
    context_region_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=8)
    encoding: IntegerSearch | None = None

    @model_validator(mode="after")
    def execution_fields(self):
        if self.mode != "preview" and not self.operation_id:
            raise ValueError("execution requires operation_id")
        if self.mode == "integer" and self.encoding is None:
            raise ValueError("integer mode requires an explicit encoding")
        if (self.target_record_id or self.parent_record_id) and self.graph_revision is None:
            raise ValueError("project target/parent references require a graph revision")
        if len(set(self.context_region_ids)) != len(self.context_region_ids):
            raise ValueError("duplicate context region")
        if sum(map(len, self.assumptions)) > 8000 or any(not x.strip() for x in self.assumptions):
            raise ValueError("invalid assumption text")
        return self


class CounterexampleContext(StrictModel):
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    allow_execution: StrictBool = False
    allow_audit_writes: StrictBool = False
    allow_model_calls: StrictBool = False
    max_actions: StrictInt = Field(default=10, ge=1, le=16)
    max_evaluations: StrictInt = Field(default=10000, ge=1, le=10000)
    timeout_seconds: StrictInt = Field(default=60, ge=1, le=120)
    retrieval: MathRetrievalPolicy = Field(default_factory=MathRetrievalPolicy)
    model_manifest: ModelManifest | None = None


class PlanSearch(StrictModel):
    encoding: IntegerSearch | None = None
    unsupported_reason: str = Field(default="", max_length=4000)
    revision_reason: str = Field(default="", max_length=4000)
    context_needs: tuple[RetrieveMathContext, ...] = Field(default=(), max_length=3)

    @model_validator(mode="after")
    def one_outcome(self):
        if (self.encoding is None) != bool(self.unsupported_reason.strip()):
            raise ValueError("supply an encoding or an explicit unsupported reason, never both")
        return self


class EmptyAction(StrictModel):
    pass
