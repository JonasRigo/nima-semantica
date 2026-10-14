"""Typed artifact failures without source bytes or private filesystem paths."""
from .models import NimaError, ConflictError


class ArtifactReadError(NimaError):
    def __init__(self, code, artifact_id, message):
        self.diagnostic = {"code": code, "artifact_id": artifact_id, "message": message}
        super().__init__(message)


class SourceValidationError(ConflictError):
    def __init__(self, diagnostics, region_id):
        self.diagnostics = tuple({**(d.model_dump(mode="json") if hasattr(d, "model_dump") else d), "region_id": region_id} for d in diagnostics)
        super().__init__("source region failed exact-source validation")
