"""Langflow adapters for the five frozen workflow boundaries.

These components deliberately validate already-produced typed payloads. Model
calls, retrieval, and graph mutation remain explicit upstream services or flow
steps; the adapters only expose their contracts through Langflow ports.
"""

from __future__ import annotations

from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.workflow_boundaries import (
    validate_hypothesis_comparison,
    validate_hypothesis_generation,
)
from nima_semantica.workflow_contracts import (
    GraphCommitRequest,
    GraphRetrievalNormalizerOutput,
    GraphRetrievalNormalizerRequest,
    HypothesisComparison as HypothesisComparisonContract,
    HypothesisComparisonRequest,
    HypothesisProposal,
    HypothesisGenerationRequest,
    InputNormalizerRequest,
)

from .base import InspectableStage, body, result
from ..values import _diagnostics


class InputNormalizer(InspectableStage):
    name = "InputNormalizer"
    display_name = "NIMA Input Normalizer"
    description = "Validate a normalized request and preserve its scope and provenance."
    nima_manifest = manifest_for_component("InputNormalizer")

    async def run(self):
        value = body(self.payload)
        try:
            envelope = InputNormalizerRequest.model_validate(value)
            return result(
                "Input Normalizer",
                {"manifest": envelope.manifest.model_dump(mode="json"),
                 "request": envelope.request.model_dump(mode="json"),
                 "diagnostics": []},
                self.payload,
            )
        except Exception as error:
            return result("Input Normalizer", {}, status="failed", diagnostics=_diagnostics(error))


class GraphRetrievalNormalizer(InspectableStage):
    name = "GraphRetrievalNormalizer"
    display_name = "NIMA Graph Retrieval Normalizer"
    description = "Validate a revision-bound GraphRAG result for ordinary imported flows."
    nima_manifest = manifest_for_component("GraphRetrievalNormalizer")

    async def run(self):
        value = body(self.payload)
        try:
            request = GraphRetrievalNormalizerRequest.model_validate(value["request"])
            output = GraphRetrievalNormalizerOutput.model_validate(value["output"])
            if output.request_id != request.request_id:
                raise ValueError("retrieval output request_id differs from request")
            return result("Graph Retrieval Normalizer", output.model_dump(mode="json"), self.payload)
        except Exception as error:
            return result("Graph Retrieval Normalizer", {}, status="failed", diagnostics=_diagnostics(error))


class HypothesisGeneration(InspectableStage):
    name = "HypothesisGeneration"
    display_name = "NIMA Hypothesis Generation Boundary"
    description = "Validate proposal-only hypotheses produced by a visible flow."
    nima_manifest = manifest_for_component("HypothesisGeneration")

    async def run(self):
        value = body(self.payload)
        try:
            request = HypothesisGenerationRequest.model_validate(value["request"])
            proposals = value.get("proposals", value.get("output", {}).get("proposals", []))
            output = validate_hypothesis_generation(
                request,
                [HypothesisProposal.model_validate(item) for item in proposals],
            )
            return result("Hypothesis Generation", output.model_dump(mode="json"), self.payload)
        except Exception as error:
            return result("Hypothesis Generation", {}, status="failed", diagnostics=_diagnostics(error))


class HypothesisComparison(InspectableStage):
    name = "HypothesisComparison"
    display_name = "NIMA Hypothesis Comparison Boundary"
    description = "Validate graph-grounded, proposal-only hypothesis comparisons."
    nima_manifest = manifest_for_component("HypothesisComparison")

    async def run(self):
        value = body(self.payload)
        try:
            request = HypothesisComparisonRequest.model_validate(value["request"])
            comparisons = value.get("comparisons", value.get("output", {}).get("comparisons", []))
            output = validate_hypothesis_comparison(
                request,
                [HypothesisComparisonContract.model_validate(item) for item in comparisons],
            )
            return result("Hypothesis Comparison", output.model_dump(mode="json"), self.payload)
        except Exception as error:
            return result("Hypothesis Comparison", {}, status="failed", diagnostics=_diagnostics(error))


class GraphCommit(InspectableStage):
    name = "GraphCommit"
    display_name = "NIMA Graph Commit Boundary"
    description = "Validate an explicitly approved commit request before the commit service applies it."
    nima_manifest = manifest_for_component("GraphCommit")

    async def run(self):
        value = body(self.payload)
        try:
            request = GraphCommitRequest.model_validate(value["request"])
            return result(
                "Graph Commit",
                {"request": request.model_dump(mode="json"),
                 "ready_for_explicit_commit": True,
                 "diagnostics": []},
                self.payload,
            )
        except Exception as error:
            return result("Graph Commit", {"ready_for_explicit_commit": False}, status="failed", diagnostics=_diagnostics(error))


__all__ = ["GraphCommit", "GraphRetrievalNormalizer", "HypothesisComparison", "HypothesisGeneration", "InputNormalizer"]
