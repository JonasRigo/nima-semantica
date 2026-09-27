"""Execution boundary for user-owned normalization flows.

The boundary deliberately accepts and returns JSON-compatible mappings. A
Langflow adapter can pass these mappings through ``Data`` or a JSON component
without importing NIMA's Python models.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .normalization_contracts import (
    DiagnosticSeverity,
    FlowAdapterManifest,
    NormalizationDiagnostic,
    NormalizationStatus,
    NormalizedOutput,
    NormalizedRequest,
)


AdapterCallable = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def execute_normalizer(
    manifest: FlowAdapterManifest,
    request: NormalizedRequest,
    adapter: AdapterCallable,
) -> NormalizedOutput:
    """Execute one adapter and enforce the normalization envelope boundary.

    The adapter receives only a JSON-compatible request payload. Its output is
    validated before it can leave the boundary. Execution or validation errors
    become failed ``NormalizedOutput`` values, allowing the harness to inspect
    the failure without treating it as a graph update.
    """
    if request.adapter_id != manifest.adapter_id:
        raise ValueError(
            f"request adapter {request.adapter_id!r} does not match manifest {manifest.adapter_id!r}"
        )

    request_payload = request.model_dump(mode="json")
    try:
        raw_output = adapter(request_payload)
        output = NormalizedOutput.model_validate(raw_output)
        if output.request_id != request.request_id:
            raise ValueError("normalizer output request_id does not match the request")
        if output.adapter_id != manifest.adapter_id:
            raise ValueError("normalizer output adapter_id does not match the manifest")
        if output.corpus_id != request.corpus_id or output.project_id != request.project_id:
            raise ValueError("normalizer output scope does not match the request")
        if output.graph_revision != request.graph_revision:
            raise ValueError("normalizer output graph revision does not match the request")
        return output
    except Exception as error:
        return NormalizedOutput(
            request_id=request.request_id,
            adapter_id=manifest.adapter_id,
            corpus_id=request.corpus_id,
            project_id=request.project_id,
            graph_revision=request.graph_revision,
            status=NormalizationStatus.FAILED,
            diagnostics=(
                NormalizationDiagnostic(
                    diagnostic_id=f"{request.request_id}-boundary-error",
                    code="normalizer.execution_error",
                    severity=DiagnosticSeverity.ERROR,
                    message=f"Normalizer did not produce a valid output: {type(error).__name__}.",
                    source="normalization_boundary",
                ),
            ),
        )


__all__ = ["AdapterCallable", "execute_normalizer"]
