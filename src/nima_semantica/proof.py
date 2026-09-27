"""Typed plans and target-bound proof attempts."""
from typing import Literal
from pydantic import StrictInt, Field

from .models import StrictModel
from .verification import PolynomialClaim


class ProofCandidate(StrictModel):
    statement: str = Field(min_length=1, max_length=20000, pattern=r"\S")
    originating_gaps: tuple[str, ...]
    hypotheses: tuple[str, ...]
    novelty_queries: tuple[str, ...]
    nearest_results: tuple[str, ...]
    novelty_status: Literal["candidate", "no_match_found_within_scope"] = "candidate"
    derivation: tuple[str, ...] = Field(default=(), max_length=24, description="Derive the proposed strengthening explicitly, rather than assuming it. Include intermediate inequalities, hypotheses, and boundary cases.")
    source_regions: tuple[str, ...] = ()


class CandidateOutput(StrictModel):
    candidates: tuple[ProofCandidate, ...] = Field(max_length=5)


class ProofPlan(StrictModel):
    target_statement: str
    goals: dict[str, tuple[str, ...]] = Field(max_length=24, description="At most 24 meaningful goals in a FINITE acyclic graph. Use descriptive names, never an expanding chain of numbered placeholders. Each dependency must EXACTLY equal a goal key, imported_regions identifier, or unresolved_leaves entry. Terminal unproved leaves belong in unresolved_leaves; do not expand them indefinitely.")
    imported_regions: tuple[str, ...] = Field(description="Only exact identifiers of supplied source regions; source attribution is not independent verification.")
    unresolved_leaves: tuple[str, ...] = Field(max_length=24, description="All unproved terminal dependencies. Use these exact same strings wherever referenced in goals; do not use a different description for the same leaf.")
    planning_limitations: tuple[str, ...] = Field(default=(), max_length=24,
        description="Deterministic provenance or planning limitations; never established proof leaves.")

    def validate_dependencies(self):
        visited, active = set(), set()
        def walk(node):
            if node in active:
                raise ValueError("cyclic proof dependency")
            if node in visited:
                return
            active.add(node)
            for dependency in self.goals.get(node, ()):
                if dependency not in self.goals and dependency not in self.imported_regions and dependency not in self.unresolved_leaves:
                    raise ValueError("unsupported proof leaf")
                walk(dependency)
            active.remove(node)
            visited.add(node)
        for goal in self.goals:
            walk(goal)
        return self


class ProofAttempt(StrictModel):
    target_statement: str
    steps: tuple[str, ...] = Field(max_length=64)
    remaining_gaps: tuple[str, ...]
    encoded_claim: PolynomialClaim | None = None
    lean_tactic: Literal["omega", "simp", "rfl", "decide"] | None = None


class CounterexampleOutput(StrictModel):
    target_statement: str
    encoded_claim: PolynomialClaim | None = None
    witness: dict[str, StrictInt] | None = None
    diagnostics: tuple[str, ...]
    scalar_checks: tuple["ScalarCheck", ...] = Field(default=(), max_length=8, description="Translate scalar auxiliary claims into safe exact expressions for boundary checking. Radicals and division are supported; an unsupported full analytic claim does not excuse skipping its elementary scalar subclaims.")


class ProofReview(StrictModel):
    target_statement: str
    mathematical_gaps: tuple[str, ...]
    invalid_steps: tuple[str, ...]
    missing_hypotheses: tuple[str, ...]
    boundary_issues: tuple[str, ...]
    verdict: Literal["needs_revision", "proof_sketch_complete"]
    explanation: str


from .scalar import ScalarCheck
CounterexampleOutput.model_rebuild()
