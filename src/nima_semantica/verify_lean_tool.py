"""Deterministic public Lean checking: no model, retrieval, repair or graph admission."""
from asyncio import CancelledError
import hashlib
from typing import Literal

from pydantic import Field, StrictBool, model_validator

from .artifact_contracts import ArtifactEnvelope
from .artifact_service import ArtifactService
from .corpus_registry import CorpusRegistry
from .evidence_contracts import require_source_region
from .execution_receipts import ExecutionReceiptService
from .lean_project import LeanProjectRequest
from .lean_verification_service import LeanVerificationRequest, LeanVerificationService
from .models import StrictModel, Record, ConflictError, identity, canonical
from .okf_contracts import GraphIdentifier, GraphRevision
from .receipts import ExecutionReceipt
from .registry_contracts import RegistryRevision
from .research_run_service import ResearchRunService
from .tool_contracts import ToolResult

VERSION = "verify-lean-v2"


class VerifyLeanRequest(StrictModel):
    mode: Literal["preview", "verify"] = "preview"
    operation_id: GraphIdentifier | None = None
    run_id: GraphIdentifier | None = None
    sources: dict[str, str] = Field(default_factory=lambda:{"Submission":"theorem target : True := True.intro"}, min_length=1, max_length=32)
    module_order: tuple[str, ...] = Field(default=(), max_length=32)
    targets: tuple[str, ...] = Field(default=("target",), min_length=1, max_length=64)
    imports: tuple[str, ...] = Field(default=("Submission",), min_length=1, max_length=32)
    expected_declaration_type_fingerprints: dict[str, str] = Field(default_factory=dict, max_length=64,
        description="Optional exact lean-expr-alpha-sha256-v2 fingerprints; mismatch cannot be reported as accepted.")
    proof_target_id: GraphIdentifier | None = None
    parent_node_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=64)
    definition_node_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=64)
    source_region_ids: tuple[GraphIdentifier, ...] = Field(default=(), max_length=32)
    graph_revision: GraphRevision | None = None

    @model_validator(mode="after")
    def validate_request(self):
        if self.mode == "verify" and self.operation_id is None:
            raise ValueError("verification requires an operation_id")
        if not self.module_order:
            object.__setattr__(self,"module_order",tuple(self.sources))
        if len(set(self.module_order)) != len(self.module_order) or set(self.module_order) != set(self.sources):
            raise ValueError("module_order must contain each submitted module exactly once")
        LeanProjectRequest(self.ordered_sources(), self.targets, self.imports).validate()
        if (not set(self.expected_declaration_type_fingerprints) <= set(self.targets)
                or any(len(v)!=64 or any(c not in "0123456789abcdef" for c in v) for v in self.expected_declaration_type_fingerprints.values())):
            raise ValueError("expected declaration fingerprints must be lowercase SHA-256 values for selected targets")
        if (self.proof_target_id or self.parent_node_ids or self.definition_node_ids) and self.graph_revision is None:
            raise ValueError("project references require a graph revision")
        return self

    def ordered_sources(self):
        return {name:self.sources[name] for name in self.module_order}


class VerifyLeanContext(StrictModel):
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    allow_execution: StrictBool = False
    allow_audit_writes: StrictBool = False


def _outcome(store, request, context, data, status, receipt_ids):
    scope = dict(corpus_id=context.corpus_id, project_id=context.project_id)
    blob = canonical({"version":VERSION,"request":request,"status":status,"data":data,"receipt_ids":receipt_ids,**scope})
    artifact = store.artifact(blob)
    record_id = store.put(Record(kind="LeanVerificationOutcome", **scope,
        content={"operation_id":request.operation_id,"artifact_id":artifact,"status":status}))
    registry = CorpusRegistry(store)
    if registry.corpus(context.corpus_id):
        revisions = [RegistryRevision.model_validate(r.content) for _,r in store.records("SystemRegistryRevision",corpus_id=context.corpus_id)]
        head = max(revisions,key=lambda r:r.sequence) if revisions else None
        revision = RegistryRevision(revision_id=identity((VERSION,artifact,"publication")),corpus_id=context.corpus_id,
            sequence=head.sequence+1 if head else 0,parent_revision=head.revision_id if head else None,changed_resource_ids=(artifact,))
        registry.register_revision(revision)
        ArtifactService(store).publish(blob,ArtifactEnvelope(artifact_id=artifact,content_hash=artifact,
            artifact_kind="lean_verification_outcome",media_type="application/json",provenance=(record_id,),**scope),
            registry_revision=revision.revision_id)
    progress = {"authority":"proposal_only",**scope,"operation_id":request.operation_id,
        "proof_target_id":request.proof_target_id,"parent_node_ids":list(request.parent_node_ids),
        "definition_node_ids":list(request.definition_node_ids),"expected_graph_revision":request.graph_revision.model_dump(mode="json") if request.graph_revision else None,
        "outcome_record_id":record_id,"outcome_artifact_id":artifact,"attempt_status":status,"receipt_ids":receipt_ids,
        "scientific_admission":False,"parent_proof_completed":False,
        "project_recording":{"status":"pending","commit_receipt_id":None}}
    proposal = store.put(Record(kind="LeanVerificationProgressProposal",**scope,content=progress,parents=(record_id,)))
    return artifact,{"record_id":proposal,**progress}


def verify_lean(store, request, context, *, verifier=None):
    request = VerifyLeanRequest.model_validate(request.model_dump(mode="json"))
    context = VerifyLeanContext.model_validate(context.model_dump(mode="json"))
    hashes = {n:hashlib.sha256(s.encode()).hexdigest() for n,s in request.ordered_sources().items()}
    if request.mode == "preview":
        return ToolResult(operation="Verify Lean",status="complete",data={"executed":False,"request":request.model_dump(mode="json"),
            "source_sha256":hashes,"correspondence_verified":False,"version":VERSION})
    if not context.allow_execution or not context.allow_audit_writes or store is None:
        return ToolResult(operation="Verify Lean",status="failed",diagnostics=({"code":"execution_audit_or_store_unavailable"},))
    scope = dict(corpus_id=context.corpus_id,project_id=context.project_id)
    rid = identity({"stage":"verify_lean","operation_id":request.operation_id,**scope})
    receipts = ExecutionReceiptService(store)
    # Resolve only operator configuration, including its immutable environment identity.
    configuration_error = None
    try:
        if verifier is None:
            from .lean_transport import configured_lean_verifier
            verifier = configured_lean_verifier()
        environment = LeanVerificationService(store,verifier).environment_manifest()
    except (Exception,CancelledError,KeyboardInterrupt) as exc:
        configuration_error = exc
        environment = {"configured":False}
    request_hash = identity({"version":VERSION,"request":request,"context":context,"environment":environment})
    previous = receipts.replay(rid,request_hash=request_hash,**scope)
    if previous:
        return ToolResult.model_validate(previous.metadata["result"])
    data = {"version":VERSION,"source_sha256":hashes,"module_order":list(request.module_order),
        "environment_manifest":environment,"correspondence_verified":False,"executed":False}
    children=[]; interrupted=None; error=None
    try:
        if request.graph_revision and request.graph_revision != store.graph_revision(**scope):
            raise ConflictError("stale or foreign graph revision")
        if request.run_id and ResearchRunService(store).get_run(request.run_id,**scope) is None:
            raise ConflictError("run outside authorized scope")
        for ref in (*request.parent_node_ids,*request.definition_node_ids,*((request.proof_target_id,) if request.proof_target_id else ())):
            if store.get(ref,**scope) is None:
                raise ConflictError("proof reference outside authorized scope")
        for ref in request.source_region_ids:
            require_source_region(store,ref,**scope)
        if configuration_error:
            raise configuration_error
        native = LeanVerificationRequest(**scope,operation_id=identity((rid,"native")),run_id=request.run_id,
            sources=request.ordered_sources(),targets=request.targets,imports=request.imports,
            graph_revision=request.graph_revision,proof_target_id=request.proof_target_id,
            parent_node_ids=request.parent_node_ids,definition_node_ids=request.definition_node_ids,source_region_ids=request.source_region_ids)
        children.append(identity({"stage":"lean_verification","operation_id":native.operation_id}))
        checked = LeanVerificationService(store,verifier).execute(native)
        data.update(executed=True,verification=checked.model_dump(mode="json"))
        result = checked.project_result
        if result:
            if result["source_artifacts"] != hashes:
                raise ValueError("verifier source binding mismatch")
            for name,digest in hashes.items():
                if store.read_artifact(digest) != request.sources[name].encode():
                    raise ValueError("source bytes changed")
            if checked.status == "verified" and set(result["declaration_types"]) != set(request.targets):
                raise ValueError("verified target set mismatch")
        mismatches = [n for n,t in request.expected_declaration_type_fingerprints.items()
            if result.get("declaration_type_fingerprints",{}).get(n)!=t]
        data["expected_type_mismatches"] = mismatches
        data["expected_declaration_type_fingerprint_format"] = "lean-expr-alpha-sha256-v2"
        data["formal_verification_accepted"] = checked.status == "verified" and not mismatches
        status = "complete" if data["formal_verification_accepted"] else "partial" if checked.status != "failed" else "failed"
    except (Exception,CancelledError,KeyboardInterrupt) as exc:
        interrupted = exc if isinstance(exc,(CancelledError,KeyboardInterrupt)) else None
        error = type(exc).__name__
        data.update(error=error,formal_verification_accepted=False)
        status = "failed"
    terminal = "interrupted" if interrupted else "failed" if status=="failed" else "completed" if status=="complete" else "partial"
    with store.joined_transaction():
        artifact,progress = _outcome(store,request,context,data,terminal,[rid,*children])
        data["project_progress"] = progress
        result = ToolResult(operation="Verify Lean",status=status,data=data,artifacts={"outcome":artifact},receipt_ids=(rid,*children),
            note="Formal acceptance concerns exact returned declaration types in the pinned environment only. Source correspondence and project admission are not certified.")
        receipts.record(ExecutionReceipt(receipt_id=rid,operation_id=request.operation_id,stage="verify_lean",**scope,
            run_id=request.run_id,graph_revision=request.graph_revision,status=terminal,error=error,tool_version=VERSION,
            output_ids=(artifact,),metadata={"request_hash":request_hash,"result":result.model_dump(mode="json")}))
    if interrupted:raise interrupted
    return result
