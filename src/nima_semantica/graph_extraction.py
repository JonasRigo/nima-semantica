"""Proposal-only extraction of source-grounded candidate OKF graphs."""

from __future__ import annotations

from .okf_contracts import GraphIdentity, GraphRevision

import hashlib
from asyncio import CancelledError
from typing import Any, Callable

from pydantic import Field, model_validator

from .artifact_contracts import ArtifactEnvelope, GraphArtifact
from .artifact_service import ArtifactService
from .execution_receipts import ExecutionReceiptService
from .evidence_contracts import require_source_region
from .models import ConflictError, NimaError, StrictModel, canonical, identity
from .okf_contracts import (
    EvidenceReference, GraphIdentifier, GraphName, OKFDelta, OKFEdge, OKFNode,
    OKFReference, OKFReferenceKind, OKFStatus,
)
from .ontology_services import OntologyService
from .proposal_service import ProposalService
from .receipts import ExecutionReceipt
from .source_quality import evidence_locator


class CandidateOntologyError(ValueError):
    """Validator-authored vocabulary/endpoint feedback, without source prose."""
    def __init__(self, report):
        self.issues = tuple(i.model_dump(mode="json") for i in report.issues)
        details = "; ".join(f"{i.edge_id or i.node_id or 'graph'}: {i.message}" for i in report.issues[:16])
        super().__init__("Candidate violates extraction ontology: " + details)


class ExtractedNode(StrictModel):
    node_id: GraphIdentifier
    node_type: GraphName
    source_region_ids: tuple[GraphIdentifier, ...] = Field(min_length=1)
    properties: dict[str, Any] = Field(default_factory=dict)


class ExtractedEdge(StrictModel):
    edge_id: GraphIdentifier
    relation: GraphName
    source_id: GraphIdentifier
    target_id: GraphIdentifier
    source_region_ids: tuple[GraphIdentifier, ...] = Field(min_length=1)
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphExtractionCandidate(StrictModel):
    nodes: tuple[ExtractedNode, ...] = ()
    edges: tuple[ExtractedEdge, ...] = ()
    unresolved: tuple[str, ...] = ()
    diagnostics: tuple[dict[str, Any], ...] = ()
    model_metadata: dict[str, Any] = Field(default_factory=dict, max_length=64)


class GraphExtractionRequest(StrictModel):
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    graph_revision: GraphRevision
    registry_revision: GraphIdentifier
    ontology_profile: GraphIdentifier
    source_region_ids: tuple[GraphIdentifier, ...] = Field(min_length=1)
    question: str = Field(min_length=1, max_length=20_000)
    max_nodes: int = Field(default=64, ge=1)
    max_edges: int = Field(default=128, ge=0)
    run_id: GraphIdentifier | None = None
    idempotency_key: GraphIdentifier | None = None

    @model_validator(mode="after")
    def unique_source_regions(self) -> "GraphExtractionRequest":
        if len(self.source_region_ids) != len(set(self.source_region_ids)):
            raise ValueError("graph extraction request contains duplicate source regions")
        return self


class GraphExtractionResult(StrictModel):
    operation_id: GraphIdentifier
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    status: str
    artifact: GraphArtifact | None = None
    proposal_record_id: GraphIdentifier | None = None
    unresolved: tuple[str, ...] = ()
    diagnostics: tuple[dict[str, Any], ...] = ()
    model_metadata: dict[str, Any] = Field(default_factory=dict, max_length=64)
    receipt_id: GraphIdentifier


class GraphExtractionService:
    """Validate and persist one bounded, source-grounded graph proposal."""

    stage = "graph_extraction"

    def __init__(
        self,
        store,
        *,
        ontology: OntologyService | None = None,
        proposals: ProposalService | None = None,
        artifacts: ArtifactService | None = None,
        receipts: ExecutionReceiptService | None = None,
    ):
        self.store = store
        self.ontology = ontology or OntologyService()
        self.proposals = proposals or ProposalService(store)
        self.artifacts = artifacts or ArtifactService(store)
        self.receipts = receipts or ExecutionReceiptService(store)

    def execute(
        self,
        request: GraphExtractionRequest,
        extractor: Callable[[dict[str, Any]], GraphExtractionCandidate | dict[str, Any]],
    ) -> GraphExtractionResult:
        operation_id = request.idempotency_key or identity({
            "stage": self.stage, "request": request.model_dump(mode="json")
        })
        receipt_id = identity({"stage": self.stage, "operation_id": operation_id})
        request_hash = identity(request)
        previous = self.receipts.replay(receipt_id, corpus_id=request.corpus_id,
            project_id=request.project_id, request_hash=request_hash)
        if previous is not None:
            return GraphExtractionResult.model_validate(previous.metadata["result"])

        output_ids: tuple[str, ...] = ()
        interruption = None
        try:
            if self.store.graph_revision(request.corpus_id, request.project_id) != request.graph_revision:
                raise ConflictError("graph extraction request targets a stale graph revision")
            regions = self._source_regions(request)
            profile = self.ontology.resolve(request.ontology_profile)
            payload = {
                "corpus_id": request.corpus_id, "project_id": request.project_id,
                "graph_revision": request.graph_revision, "ontology_profile": profile.model_dump(mode="json"),
                "question": request.question, "regions": regions,
                "max_nodes": request.max_nodes, "max_edges": request.max_edges,
            }
            candidate = GraphExtractionCandidate.model_validate(extractor(payload))
            if len(candidate.nodes) > request.max_nodes or len(candidate.edges) > request.max_edges:
                raise NimaError("graph extraction exceeds the requested item budget")
            source_revision = identity([
                (region["id"], region["source_revision"]) for region in regions
            ])
            delta = self._candidate_delta(request, profile, candidate, regions, source_revision)
            report = self.ontology.validate_delta(delta)
            if not report.valid:
                raise CandidateOntologyError(report)
            artifact = self._graph_artifact(request, delta, candidate, regions, source_revision)
            self.artifacts.publish(
                canonical(delta), artifact.envelope, registry_revision=request.registry_revision
            )
            proposal_record_id = self.proposals.persist_graph_candidate(
                artifact, source_region_ids=request.source_region_ids
            )
            output_ids = (artifact.envelope.artifact_id, proposal_record_id)
            result = GraphExtractionResult(
                operation_id=operation_id, corpus_id=request.corpus_id,
                project_id=request.project_id, status="completed", artifact=artifact,
                proposal_record_id=proposal_record_id, unresolved=candidate.unresolved,
                diagnostics=candidate.diagnostics, model_metadata=candidate.model_metadata,
                receipt_id=receipt_id,
            )
            status, error = "completed", None
        except (Exception, CancelledError, KeyboardInterrupt) as exc:
            interruption = exc if isinstance(exc, (CancelledError, KeyboardInterrupt)) else None
            result = GraphExtractionResult(
                operation_id=operation_id, corpus_id=request.corpus_id,
                project_id=request.project_id, status="failed", unresolved=(),
                diagnostics=exc.issues if isinstance(exc, CandidateOntologyError) else ({"code": type(exc).__name__},), receipt_id=receipt_id,
            )
            status, error = "failed", "graph extraction failed"

        execution_receipt = ExecutionReceipt(
            receipt_id=receipt_id, operation_id=operation_id, stage=self.stage,
            corpus_id=request.corpus_id, project_id=request.project_id, run_id=request.run_id,
            graph_revision=request.graph_revision, input_ids=request.source_region_ids,
            source_revision=source_revision if "source_revision" in locals() else None,
            output_ids=output_ids, status="interrupted" if interruption is not None else status, error=error,
            diagnostics=result.diagnostics, provider=self._metadata_id(result.model_metadata, "provider"),
            model=self._metadata_id(result.model_metadata, "model"), tool_version="okf-extraction-v1",
            idempotency_key=request.idempotency_key,
            metadata={"request_hash": request_hash, "result": result.model_dump(mode="json")},
        )
        self.receipts.record(execution_receipt)
        if interruption is not None:
            raise interruption
        return result

    def _source_regions(self, request: GraphExtractionRequest) -> list[dict[str, Any]]:
        records = dict(self.store.get_selected(
            request.source_region_ids, kind="SourceRegion",
            corpus_id=request.corpus_id, project_id=request.project_id,
        ))
        if set(records) != set(request.source_region_ids) or any(record.project_id not in (None, request.project_id) for record in records.values()):
            raise NimaError("graph extraction references missing or out-of-scope source regions")
        regions = []
        for region_id in request.source_region_ids:
            record = require_source_region(self.store, region_id, corpus_id=request.corpus_id, project_id=request.project_id)
            content = record.content
            artifact_id = content.get("artifact_id")
            text = content.get("text")
            start, end = content.get("start"), content.get("end")
            if not isinstance(artifact_id, str) or not isinstance(text, str):
                raise NimaError("source region lacks exact artifact provenance")
            data = self.store.read_artifact(artifact_id)
            if hashlib.sha256(data).hexdigest() != artifact_id:
                raise NimaError("source region artifact hash validation failed")
            decoded = data.decode("utf-8")
            if (type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(decoded)
                    or decoded[start:end] != text):
                raise ConflictError("source region offsets or text do not match its artifact")
            regions.append({"id": region_id, "artifact_id": artifact_id, "source_revision": content["source_revision"], "project_id": record.project_id,
                            "text": text, "start": start, "end": end,
                            "ordinal": content.get("ordinal"), "metadata": content.get("metadata", {})})
        return regions

    def prepare_candidate(self, request: GraphExtractionRequest, candidate: GraphExtractionCandidate) -> GraphArtifact:
        """Validate a candidate without publishing; used by iterative extraction controllers."""
        request = GraphExtractionRequest.model_validate(request.model_dump(mode="json"))
        candidate = GraphExtractionCandidate.model_validate(candidate.model_dump(mode="json"))
        if self.store.graph_revision(request.corpus_id, request.project_id) != request.graph_revision:
            raise ConflictError("graph extraction request targets a stale graph revision")
        regions = self._source_regions(request)
        profile = self.ontology.resolve(request.ontology_profile)
        if len(candidate.nodes) > request.max_nodes or len(candidate.edges) > request.max_edges:
            raise ValueError("candidate exceeds authorized size")
        source_revision = identity([(region["id"], region["source_revision"]) for region in regions])
        delta = self._candidate_delta(request, profile, candidate, regions, source_revision)
        report = self.ontology.validate_delta(delta)
        if not report.valid:
            raise CandidateOntologyError(report)
        return self._graph_artifact(request, delta, candidate, regions, source_revision)

    def _candidate_delta(self, request, profile, candidate, regions, source_revision) -> OKFDelta:
        region_by_id = {region["id"]: region for region in regions}

        def references(ids):
            evidence, provenance = [], []
            for region_id in ids:
                region = region_by_id[region_id]
                evidence.append(EvidenceReference(
                    corpus_id=request.corpus_id, project_id=region["project_id"],
                    artifact_id=region["artifact_id"], region_id=region_id,
                    source_revision=region["source_revision"], content_hash=region["artifact_id"],
                    locator=evidence_locator(region),
                    quotation=region["text"] if len(region["text"]) <= 20_000 else None,
                ))
                provenance.append(OKFReference(
                    reference_kind=OKFReferenceKind.SOURCE_REGION, target_id=region_id,
                    corpus_id=request.corpus_id, project_id=request.project_id,
                    revision=region["source_revision"], content_hash=region["artifact_id"],
                    locator={"start": region["start"], "end": region["end"]},
                ))
            return tuple(evidence), tuple(provenance)

        nodes = []
        for item in candidate.nodes:
            evidence, provenance = references(item.source_region_ids)
            nodes.append(OKFNode(
                node_id=item.node_id, node_type=item.node_type, corpus_id=request.corpus_id,
                project_id=request.project_id, status=OKFStatus.PROPOSED,
                properties=item.properties, evidence=evidence, provenance=provenance,
                ontology_profile=profile.digest, producer=self.stage, producer_version="1",
            ))
        node_ids = {node.node_id for node in nodes}
        edges = []
        for item in candidate.edges:
            if item.source_id not in node_ids or item.target_id not in node_ids:
                raise NimaError("extracted graph edge references an undeclared node")
            evidence, provenance = references(item.source_region_ids)
            edges.append(OKFEdge(
                edge_id=item.edge_id, relation=item.relation, source_id=GraphIdentity(corpus_id=request.corpus_id, project_id=request.project_id, local_id=item.source_id),
                target_id=GraphIdentity(corpus_id=request.corpus_id, project_id=request.project_id, local_id=item.target_id), corpus_id=request.corpus_id, project_id=request.project_id,
                status=OKFStatus.PROPOSED, properties=item.properties, evidence=evidence,
                provenance=provenance, ontology_profile=profile.digest,
                producer=self.stage, producer_version="1",
            ))
        return OKFDelta(
            delta_id=identity({
                "request": request.model_dump(mode="json"),
                "candidate": candidate.model_dump(mode="json"),
            }),
            base_revision=request.graph_revision, corpus_id=request.corpus_id,
            project_id=request.project_id, ontology_profile=profile.digest,
            upsert_nodes=tuple(nodes), add_edges=tuple(edges),
            provenance=tuple(ref for node in nodes for ref in node.provenance),
            reason="Source-grounded graph extraction proposal.", producer=self.stage,
            producer_version="1", metadata={"unresolved": list(candidate.unresolved),
                                            "diagnostics": list(candidate.diagnostics),
                                            "model_metadata": candidate.model_metadata,
                                            "source_revision": source_revision},
        )

    def _graph_artifact(self, request, delta, candidate, regions, source_revision) -> GraphArtifact:
        data = canonical(delta)
        digest = hashlib.sha256(data).hexdigest()
        return GraphArtifact(envelope=ArtifactEnvelope(
            artifact_id=digest, artifact_kind="graph_candidate", media_type="application/json",
            content_hash=digest, corpus_id=request.corpus_id, project_id=request.project_id,
            source_revision=source_revision,
            source_artifact_ids=tuple(sorted({region["artifact_id"] for region in regions})),
            provenance=request.source_region_ids, diagnostics=candidate.diagnostics, status="proposed",
            content={"ontology_profile": delta.ontology_profile,
                     "unresolved": list(candidate.unresolved),
                     "model_metadata": candidate.model_metadata,
                     "graph_revision": request.graph_revision},
        ), delta=delta)

    @staticmethod
    def _metadata_id(metadata, key):
        # Model metadata is retained in the proposal payload by the extractor;
        # receipts only expose safe scalar provider/model identities.
        value = metadata.get(key)
        return value if isinstance(value, str) and value.strip() and " " not in value else None


__all__ = [
    "ExtractedEdge", "ExtractedNode", "GraphExtractionCandidate",
    "GraphExtractionRequest", "GraphExtractionResult", "GraphExtractionService",
]
