"""Operator defaults for bounded index construction; never raised implicitly."""
from pydantic import Field, StrictInt
from .models import StrictModel, NimaError


class ProjectionLimits(StrictModel):
    max_tokens: StrictInt = Field(default=1_000_000, ge=1, le=10_000_000)
    max_regions: StrictInt = Field(default=100_000, ge=1, le=1_000_000)


class ProjectionLimitError(NimaError):
    def __init__(self, resource, configured, observed):
        self.diagnostic = {"code": "projection." + resource + "_limit",
            "message": "Projection " + resource + " budget exceeded.",
            "configured": configured, "observed": observed,
            "recovery": "Set an explicit supported projection limit and rebuild the index; prepared sources are retained."}
        super().__init__("projection " + resource + " budget exceeded")


class ProjectionStageError(NimaError):
    def __init__(self, diagnostics, receipt_id=None):
        self.diagnostics = tuple(diagnostics)
        self.receipt_id = receipt_id
        super().__init__("projection publication failed")
