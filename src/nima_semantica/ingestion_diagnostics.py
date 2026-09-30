"""Safe failure details: never echo provider bodies, credentials or input text."""
import httpx
from pydantic import ValidationError

_SAFE_MESSAGES = {
    "Embedding credential environment variable is missing",
    "Embedding model revision changed; configure a new index explicitly",
    "Embedding response dimension/count differs from configured identity",
    "embedding provider manifest differs from request",
    "embedding dimensions or numeric values invalid",
    "zero embedding rejected",
    "embedding source region failed exact evidence validation",
    "source region failed exact-source validation",
    "embedding manifest mismatch; explicit corpus reindex required",
    "region already indexed with a different embedding",
}


def diagnostic(exc):
    result = {"code": type(exc).__name__}
    if isinstance(exc, ValidationError):
        result.update(message="Input or embedding publication failed schema validation.",
            fields=[{"field": ".".join(map(str, e["loc"])), "rule": e["type"]}
                    for e in exc.errors(include_input=False, include_url=False)][:16])
    elif isinstance(exc, httpx.HTTPStatusError):
        result.update(message="Embedding provider rejected the request; check model availability, input limits and credentials.",
                      http_status=exc.response.status_code)
    elif isinstance(exc, httpx.TimeoutException):
        result["message"] = "Embedding provider timed out; check service load and batch size."
    elif isinstance(exc, httpx.RequestError):
        result["message"] = "Cannot reach the embedding provider; check its service and configured endpoint."
    elif str(exc) in _SAFE_MESSAGES:
        result["message"] = str(exc)
    else:
        result["message"] = "Embedding generation or publication failed; check model revision, vector dimensions and exact-source validation."
    return result


class EmbeddingStageError(Exception):
    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        super().__init__("Embedding generation or publication failed")
