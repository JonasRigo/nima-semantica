"""Operator-scoped hybrid literature retrieval with inspectable graph traversal.

Evidence, source assertions and conditional graph consequences remain distinct
from verified mathematical truth. Retrieval cannot expand a task's write scope.
"""
from types import SimpleNamespace
from uuid import uuid4
from typing import Literal
from pydantic import Field, StrictBool

from .models import StrictModel, canonical
from .installation import EmbeddingProfile
from .okf_contracts import GraphIdentifier
from .research_retrieval import ResearchRetrievalRequest, ResearchRetrievalContext, retrieve_research_context
from .evidence_reader import ReadEvidenceRequest, ReadEvidenceContext, read_evidence


class MathRetrievalPolicy(StrictModel):
    enabled: StrictBool = False
    projection_id: GraphIdentifier | None = None
    mode: Literal["lexical", "vector", "hybrid"] = "hybrid"
    allow_embeddings: StrictBool = False
    allow_attempt_writes: StrictBool = False
    embedding_profile: EmbeddingProfile | None = None


def installation_retrieval_policy(*, projection_id=None):
    """Private math/proof services use operator installation, never action JSON."""
    from .installation import load_installation
    config = load_installation()
    return MathRetrievalPolicy(enabled=True, projection_id=projection_id,
        allow_embeddings=config.embedding is not None, allow_attempt_writes=True,
        embedding_profile=config.embedding)


class RetrieveMathContext(StrictModel):
    query: str = Field(min_length=1, max_length=2000,
        description="Search terms for exact source evidence and bounded graph context.")
    purpose: str = Field(min_length=1, max_length=2000,
        description="Which uncertainty or mathematical step needs context?")
    mode: Literal["lexical", "vector", "hybrid"] | None = Field(default=None,
        description="Optional explicit mode. Lexical may be requested after a reported vector failure; no silent downgrade.")


class LiteratureUse(StrictModel):
    citation: str = Field(min_length=1, max_length=32, description="A citation label returned by retrieve_context, e.g. S1.")
    application: str = Field(min_length=1, max_length=2000, description="Which mathematical step this passage supports, or why it is rejected.")
    applicability: str = Field(min_length=1, max_length=2000, description="Check source assumptions against the task; identify limitations.")
    convention_alignment: str = Field(min_length=1, max_length=2000, description="Compare definitions, normalization and index conventions explicitly; task definitions prevail.")


def retrieve_math_context(store, context, action, *, provider=None, manifest=None):
    policy = context.retrieval
    if not policy.enabled:
        raise ValueError("Math retrieval is not authorized by the operator.")
    scope = dict(corpus_id=context.corpus_id, project_id=context.project_id)
    mode = action.mode or policy.mode
    if mode != "lexical" and (not policy.allow_embeddings or not policy.allow_attempt_writes):
        raise ValueError("Hybrid/vector retrieval requires an operator embedding connection and attempt-receipt permission. Request lexical explicitly if appropriate.")
    if mode != "lexical" and provider is None and policy.embedding_profile is not None:
        from .setup_services import embedding_provider
        profile = policy.embedding_profile
        if profile.container_url:
            profile = profile.model_copy(update={"base_url": profile.container_url})
        provider, manifest = embedding_provider(SimpleNamespace(embedding=profile))
    result = retrieve_research_context(store, ResearchRetrievalRequest(query=action.query,
        mode=mode, projection_id=policy.projection_id, limit=4, max_results=8,
        max_hops=2, max_nodes=64, max_edges=128, max_chars=12000,
        operation_id="math-retrieval-" + uuid4().hex if mode != "lexical" else None),
        ResearchRetrievalContext(**scope, allow_embeddings=policy.allow_embeddings,
            allow_attempt_writes=policy.allow_attempt_writes), provider=provider, manifest=manifest)
    if result.status not in ("complete", "partial"):
        codes = [v.get("code", "retrieval.failed") for v in result.diagnostics]
        raise ValueError("Requested retrieval failed: " + ", ".join(codes) + ". No lexical fallback was performed.")
    packet = result.data
    passages = []
    for region in packet["regions"]:
        read = read_evidence(store, ReadEvidenceRequest(reference=region["evidence"], max_bytes=100000),
            ReadEvidenceContext(**scope))
        if read.status != "complete" or read.data.get("content") != region["text"] or not read.data.get("exact_source_checked"):
            raise ValueError("Retrieved passage failed exact-source validation.")
        passages.append(region)
    content = dict(passages=passages, graph_nodes=list(packet["graph_nodes"]), paths=list(packet["paths"]))
    budget_truncated = False
    # Whole exact passages and graph objects are retained or omitted, never shortened.
    # Keep the canonical observation bounded across both graph and text content.
    while len(canonical(content).decode()) > 12000:
        budget_truncated = True
        if len(content["passages"]) > 1:
            content["passages"].pop()
        elif content["paths"]:
            content["paths"].pop()
        elif content["graph_nodes"]:
            content["graph_nodes"].pop()
        elif content["passages"]:
            content["passages"].pop()
        else:
            break
    return dict(query=action.query, purpose=action.purpose, projection_id=packet["projection_id"],
        projection_revision=packet["projection_revision"], source_revision=packet["source_revision"],
        graph_revision=packet["graph_revision"], store_revision=packet["store_revision"],
        mode=mode, embedding_identity=packet["embedding_identity"], seeds=packet["seeds"],
        **content, traversal=packet["traversal"],
        truncated=packet["truncated"] or budget_truncated, budget_truncated=budget_truncated,
        receipt_ids=list(result.receipt_ids), source_text=canonical(content).decode(),
        authority="Untrusted exact literature and typed graph context; source claims, prerequisites and conditional consequences are not independently verified proofs.")
