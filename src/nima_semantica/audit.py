"""Source-grounded extraction contracts and sound assessment propagation."""
from .models import Assessment, AuditOutput, NimaError
from .models import identity, StrictModel
from pydantic import Field


class AliasMention(StrictModel):
    region_id: str
    text: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    candidates: tuple[str, ...]
    confidence: float = Field(ge=0, le=1)


class AliasOutput(StrictModel):
    mentions: tuple[AliasMention, ...]

SIGNATURES = {
    "infinite series": ("mode of convergence", "permitted interchange of limits"),
    "unbounded operator": ("dense domain", "common ambient space", "adjoint domain", "closability"),
    "analytic continuation": ("initial domain", "continuation domain", "uniqueness conditions"),
    "equivalence": ("forward implication", "reverse implication"),
}


def validate_audit(output: AuditOutput, selected_ids):
    for claim in output.claims:
        if claim.status != Assessment.PROPOSED:
            raise NimaError("extractor may only propose attributed claims")
        if not set(claim.source_regions) <= set(selected_ids):
            raise NimaError("extractor cited an unselected source region")
    return output


def assess(claim_id, obligations, verifications, *, literature_sufficient=True):
    relevant = [v for v in verifications if v.target_id == claim_id and v.correspondence_verified and v.evidence_artifact]
    if any(v.outcome == "refuted" for v in relevant):
        return Assessment.REFUTED
    if any(v.outcome == "verified" for v in relevant) and literature_sufficient:
        return Assessment.VERIFIED
    # A broken proof route is never a refutation of the claim.
    if any(status != "discharged" for status in obligations.values()) or not literature_sufficient:
        return Assessment.NOT_ESTABLISHED
    if obligations:
        return Assessment.CONDITIONAL
    return Assessment.NOT_ESTABLISHED


def obligation_inferences(claim_id, obligations):
    """Persistable deterministic rule evidence, separate from LLM propositions."""
    from semantica.reasoning import Reasoner
    safe_claim = identity(claim_id)
    reasoner = Reasoner()
    rules = ["IF Requires(?c, ?o) AND Unresolved(?o) THEN NotEstablished(?c)",
             "IF Requires(?c, ?o) AND RefutedRoute(?o) THEN NotEstablished(?c)"]
    facts = []
    for obligation, status in obligations.items():
        safe_obligation = identity(obligation)
        facts.append(f"Requires({safe_claim}, {safe_obligation})")
        if status in ("unresolved", "refuted"):
            predicate = "Unresolved" if status == "unresolved" else "RefutedRoute"
            facts.append(f"{predicate}({safe_obligation})")
    return [{"rule": result.rule_used, "premises": result.premises, "conclusion": result.conclusion, "ruleset": "nima-status-v1"}
            for result in reasoner.infer_with_results(facts, rules)]
