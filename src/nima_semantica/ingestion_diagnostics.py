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


def preparation_diagnostic(exc):
    """Allow-list deterministic preparation errors; never return input/provider text."""
    messages = {
        "fast reading found insufficient text; full preparation may be required": (
            "sources.fast_text_insufficient",
            "Fast preparation requires at least 200 extracted characters. Use full preparation for short text, or a full PDF/OCR parser if the PDF has no usable text layer."),
        "fast-read text exceeds output limits": (
            "sources.fast_text_limit",
            "Fast preparation exceeded its 4 MB text or 150 page-separator limit. Split the source into smaller inputs."),
        "fast PDF text extraction failed": (
            "sources.fast_pdf_failed", "Fast PDF extraction failed. Check the text layer and configured isolated reader, or try full preparation."),
        "fast PDF text extraction unavailable": (
            "sources.fast_pdf_unavailable", "The fast PDF reader is unavailable or timed out. Check its installation and worker health."),
    }
    item = messages.get(str(exc)) if isinstance(exc, ValueError) else None
    return {"code": item[0], "message": item[1]} if item else None
