"""Read-only operator policy and pinned projection readiness for private clients."""
import os

from .mcp_diagnostics import redacted_error


def private_readiness(config):
    result = {"scope": {"corpus_id": config.corpus_id, "project_id": config.project_id},
        "symbolic_timeout_seconds": config.timeout_seconds,
        "retrieval": {"state": "disabled"}}
    try:
        from .installation import load_installation
        installation = load_installation()
        result["retrieval_limits"] = installation.retrieval_limits.model_dump(mode="json")
        result["lean_policy"] = installation.lean_policy.model_dump(mode="json")
        result["embedding_configured"] = installation.embedding is not None
        result["corpus_owner_configured"] = bool(os.environ.get("NIMA_RESEARCH_MCP_URL"))
    except Exception as exc:
        result["installation"] = {"state": "unavailable", **redacted_error(exc)}
    if not config.allow_retrieval:
        return result
    try:
        from .corpus_snapshot import CorpusSnapshot
        from .research_retrieval import _projection, ResearchRetrievalRequest, ResearchRetrievalContext
        projection_id = getattr(config, "projection_id", getattr(config, "retrieval_projection_id", None))
        root = getattr(config, "store_root", None) or os.environ.get("NIMA_STORE_ROOT")
        if not root:
            raise ValueError("operator corpus store is unconfigured")
        with CorpusSnapshot(root, **result["scope"]) as store:
            projection = _projection(store, ResearchRetrievalRequest(query="readiness", projection_id=projection_id),
                ResearchRetrievalContext(**result["scope"]))
        result["retrieval"] = {"state": "ready", "projection_id": projection.projection_id}
    except Exception as exc:
        result["retrieval"] = {"state": "unavailable", "code": getattr(exc, "code", "retrieval.unavailable"), **redacted_error(exc)}
    return result
