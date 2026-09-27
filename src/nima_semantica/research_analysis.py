"""Deterministic immutable research bundles, without scientific admission."""
from asyncio import CancelledError
from typing import Literal
from pydantic import Field, StrictBool, StrictInt, model_validator
from .models import StrictModel, Record, canonical, identity, ConflictError
from .okf_contracts import GraphIdentifier, GraphRevision, Digest, EvidenceReference
from .providers import ModelManifest, strict_json_object, validate_manifest
from .artifact_contracts import ArtifactEnvelope
from .artifact_service import ArtifactService
from .corpus_registry import CorpusRegistry
from .registry_contracts import RegistryRevision
from .execution_receipts import ExecutionReceiptService
from .receipts import ExecutionReceipt
from .evidence_provenance import EvidenceProvenanceService
from .research_run_service import ResearchRunService
from .graph_analysis import persist_graph_outcome
from .tool_contracts import ToolResult

VERSION="save-research-analysis-v1"


class AnalysisSection(StrictModel):
    section_id: GraphIdentifier
    category: Literal["summary","literature","claim","hypothesis","obligation","relation","ontology_query","comparison","proof_attempt","verification","review","failed_check","unresolved_check"]
    text: str=Field(min_length=1,max_length=20000)
    evidence_indices: tuple[StrictInt,...]=Field(default=(),max_length=32)
    artifact_ids: tuple[Digest,...]=Field(default=(),max_length=32)
    authority: Literal["caller_authored_unverified"]="caller_authored_unverified"


class ResearchAnalysisBundle(StrictModel):
    title: str=Field(min_length=1,max_length=2000)
    sections: tuple[AnalysisSection,...]=Field(default=(),max_length=128)
    evidence: tuple[EvidenceReference,...]=Field(default=(),max_length=128)
    artifact_ids: tuple[Digest,...]=Field(default=(),max_length=64)
    receipt_ids: tuple[GraphIdentifier,...]=Field(default=(),max_length=128)
    model_manifests: tuple[ModelManifest,...]=Field(default=(),max_length=16)
    workflow: str=Field(default="harness-authored analysis",max_length=2000)
    limitations: tuple[str,...]=Field(min_length=1,max_length=32)

    @model_validator(mode="after")
    def bounded(self):
        if not self.title.strip() or any(not s.strip() or len(s)>4000 for s in self.limitations):raise ValueError("nonempty title and bounded limitations required")
        if not self.sections and not self.artifact_ids:raise ValueError("analysis requires sections or artifacts")
        for values in (self.artifact_ids,self.receipt_ids,tuple(s.section_id for s in self.sections)):
            if len(set(values))!=len(values):raise ValueError("duplicate analysis reference")
        for s in self.sections:
            if any(i<0 or i>=len(self.evidence) for i in s.evidence_indices):raise ValueError("unknown evidence index")
            if not set(s.artifact_ids)<=set(self.artifact_ids):raise ValueError("section artifact is not in bundle")
        for manifest in self.model_manifests:validate_manifest(manifest)
        if len(canonical(self))>1_000_000:raise ValueError("analysis input exceeds bound")
        return self


class SaveAnalysisRequest(StrictModel):
    mode: Literal["preview","save"]="preview"
    operation_id: GraphIdentifier | None=None
    run_id: GraphIdentifier | None=None
    graph_revision: GraphRevision | None=None
    bundle: ResearchAnalysisBundle | None=None
    target_record_id: GraphIdentifier | None=None
    parent_record_id: GraphIdentifier | None=None

    @model_validator(mode="after")
    def pinned(self):
        if self.mode=="save" and (not self.operation_id or self.graph_revision is None or self.bundle is None):raise ValueError("save requires operation, graph revision and analysis bundle")
        return self


class SaveAnalysisContext(StrictModel):
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    allow_artifact_writes: StrictBool=False
    max_bytes: StrictInt=Field(default=5_000_000,ge=1000,le=5_000_000)


def review_publication_check(store, artifact_id, envelope, captured, revision, scope):
    """Recheck structured adverse findings against original receipt-bound reviews."""
    from .review_contracts import ReviewRequest
    if envelope.artifact_kind!="review_assessment":return None
    req=ReviewRequest.model_validate(captured.get("request",{}))
    if req.graph_revision!=revision:raise ConflictError("review revision differs from publication")
    receipts=ExecutionReceiptService(store)
    receipt=receipts.get(identity({"stage":"review_research","operation_id":req.operation_id,**scope}),**scope)
    if receipt is None or artifact_id not in receipt.output_ids or receipt.graph_revision!=revision:raise ConflictError("review has no matching publication receipt")
    if captured.get("status")!=receipt.status or any(captured.get(k)!=v for k,v in scope.items()):raise ConflictError("review status or scope differs")
    saved=receipt.metadata.get("result",{})
    if captured.get("data",{}).get("result")!=saved.get("data",{}).get("result"):raise ConflictError("review artifact and receipt disagree")
    result=captured.get("data",{}).get("result")
    if result is None:
        return {"artifact_id":artifact_id,"status":captured.get("status"),"publishable_assessments":False,"reason":"attempt did not submit; retained as history"}
    if receipt.status!="partial" or not result.get("finalized"):raise ConflictError("review submission is not authenticated")
    if result.get("targets")!=[t.model_dump(mode="json") for t in req.targets]:raise ConflictError("review target content differs")
    by_id={t.target_id:t for t in req.targets}
    for assessment in result["assessments"]:
        target=by_id.get(assessment.get("target_id"))
        if target is None:raise ConflictError("unknown review target")
        if assessment.get("content_revision")!=target.content_revision:raise ConflictError("review content revision differs")
        finding=assessment.get("missing_support_finding")
        if not finding:continue
        if assessment["assessment"]=="plausible":raise ConflictError("missing support cannot be plausible")
        if assessment["assessment"]=="unresolved":continue
        nested=next((n for n in result["substantiations"] if n["operation_id"]==assessment.get("substantiation_operation_id") and n["target_id"]==target.target_id),None)
        if nested is None or nested["status"] not in ("completed","partial"):raise ConflictError("missing matching substantiation")
        if nested.get("content_revision")!=target.content_revision:raise ConflictError("substantiation content revision differs")
        nested_req=nested["request"]
        if nested_req["graph_revision"]!=revision.model_dump(mode="json"):raise ConflictError("substantiation revision differs")
        selected=nested_req["targets"][0]
        if selected["finding"]!=finding or selected["ref"]!=target.ref.model_dump(mode="json"):raise ConflictError("substantiation finding or target differs")
        nested_result=nested["result"]
        nested_id=identity({"stage":"substantiate_graph_snapshot","operation_id":nested["operation_id"],**scope})
        nested_receipt=receipts.get(nested_id,**scope)
        if not nested_receipt or nested_receipt.metadata.get("result")!=nested_result or nested_receipt.graph_revision!=revision or nested_receipt.status!=nested["status"]:raise ConflictError("unauthenticated substantiation result")
        values=nested_result.get("data",{}).get("result",{}).get("assessments",[])
        if not values or values[0].get("target")!=selected or values[0].get("snapshot_hash")!=result["snapshot_hash"] or values[0].get("source_substantiation")!="support_not_located":raise ConflictError("substantiation does not support scoped adverse finding")
    return {"artifact_id":artifact_id,"status":captured["status"],"publishable_assessments":True,"unqualified_absence_supported":False}


def validate_bundle(store,request,context):
    scope=dict(corpus_id=context.corpus_id,project_id=context.project_id)
    if not CorpusRegistry(store).corpus(context.corpus_id):raise ConflictError("registered corpus required")
    if store.graph_revision(**scope)!=request.graph_revision:raise ConflictError("analysis graph revision is stale or foreign")
    if request.run_id and ResearchRunService(store).get_run(request.run_id,**scope) is None:raise ConflictError("run outside scope")
    for key in (request.target_record_id,request.parent_record_id):
        record=store.get(key,**scope) if key else None
        if key and (record is None or record.project_id!=context.project_id):raise ConflictError("progress record outside project")
    bundle=request.bundle;receipts=ExecutionReceiptService(store)
    captured_receipts=[]
    for rid in bundle.receipt_ids:
        receipt=receipts.get(rid,**scope) or receipts.get(rid,corpus_id=context.corpus_id,project_id=None)
        if receipt is None:raise ConflictError("receipt outside scope")
        captured_receipts.append(receipt.model_dump(mode="json"))
    provenance=EvidenceProvenanceService(store)
    for e in bundle.evidence:
        if not provenance.validate_reference(e,**scope,target_id=e.region_id).valid:raise ConflictError("invalid exact evidence")
    service=ArtifactService(store);artifacts=[];checks=[];total=len(canonical(bundle))
    for artifact_id in bundle.artifact_ids:
        env,blob=service.read(artifact_id,**scope);total+=len(blob)
        if total>context.max_bytes:raise ValueError("complete bundle exceeds byte bound")
        for record_id in env.provenance:
            if store.get(record_id,**scope) is None:raise ConflictError("artifact provenance unavailable")
        captured=strict_json_object(blob.decode()) if env.artifact_kind=="review_assessment" else None
        gate=review_publication_check(store,artifact_id,env,captured,request.graph_revision,scope)
        if gate:checks.append(gate)
        # Complete immutable bytes are retained by digest; no truncated projections.
        artifacts.append({"envelope":env.model_dump(mode="json"),"bytes":len(blob)})
    return {"version":VERSION,**scope,"operation_id":request.operation_id,"run_id":request.run_id,
        "graph_revision":request.graph_revision.model_dump(mode="json"),"bundle":bundle.model_dump(mode="json"),
        "artifact_inventory":artifacts,"receipts":captured_receipts,"review_publication_checks":checks,
        "authority":"caller_authored_report","scientific_admission":False,"source_entailment_verified":False,
        "limitations":"Exact references checked; prose and caller-supplied categories are unverified. Original failed/unresolved attempts retain their status."}


def render_analysis(report):
    """Safe deterministic projection; all structured bundle fields remain visible."""
    return {"bundle":canonical(report),"markdown":ArtifactService._render_bytes(report["bundle"]["title"],report,"markdown")}


def save_research_analysis(store,request,context):
    request=SaveAnalysisRequest.model_validate(request.model_dump(mode="json"))
    context=SaveAnalysisContext.model_validate(context.model_dump(mode="json"))
    if request.mode=="preview":return ToolResult(operation="Save Research Analysis",status="complete",data={"executed":False,"request":request.model_dump(mode="json"),"version":VERSION})
    if store is None or not context.allow_artifact_writes:return ToolResult(operation="Save Research Analysis",status="failed",diagnostics=({"code":"artifact_writes_or_store_unavailable"},))
    scope=dict(corpus_id=context.corpus_id,project_id=context.project_id);receipts=ExecutionReceiptService(store)
    rid=identity({"stage":"save_research_analysis","operation_id":request.operation_id,**scope});fingerprint=identity({"version":VERSION,"request":request,"context":context})
    previous=receipts.replay(rid,request_hash=fingerprint,**scope)
    if previous:return ToolResult.model_validate(previous.metadata["result"])
    data={"version":VERSION,"scientific_admission":False};artifacts={};interrupted=None
    def finish(data,artifacts,status,interrupted):
        terminal="interrupted" if interrupted else "completed" if status=="complete" else "failed"
        with store.joined_transaction():
            outcome,progress=persist_graph_outcome(store,request,context,data,terminal,[rid],version=VERSION,record_prefix="ResearchAnalysis",artifact_kind="research_analysis_outcome")
            data["project_progress"]=progress;published={**artifacts,"outcome":outcome}
            result=ToolResult(operation="Save Research Analysis",status=status,data=data,receipt_ids=(rid,*data.get("publication_receipt_ids",())),artifacts=published,
                note="Immutable caller-authored report. Persistence does not certify science; project recording requires separate approval.")
            receipts.record(ExecutionReceipt(receipt_id=rid,operation_id=request.operation_id,stage="save_research_analysis",**scope,run_id=request.run_id,
                graph_revision=request.graph_revision,status=terminal,error=data.get("error"),tool_version=VERSION,output_ids=tuple(published.values()),metadata={"request_hash":fingerprint,"result":result.model_dump(mode="json")}))
        return result

    try:
        with store.joined_transaction():
            report=validate_bundle(store,request,context)
            if len(canonical(report))>context.max_bytes:raise ValueError("validated bundle exceeds byte bound")
            rendered=render_analysis(report)
            if sum(map(len,rendered.values()))>context.max_bytes:raise ValueError("rendered bundle exceeds byte bound")
            artifacts={k:identity_bytes(v) for k,v in rendered.items()}
            heads=[RegistryRevision.model_validate(r.content) for _,r in store.records("SystemRegistryRevision",corpus_id=context.corpus_id)]
            head=max(heads,key=lambda r:r.sequence) if heads else None
            revision=RegistryRevision(revision_id=identity((VERSION,artifacts)),corpus_id=context.corpus_id,sequence=head.sequence+1 if head else 0,
                parent_revision=head.revision_id if head else None,changed_resource_ids=tuple(artifacts.values()))
            registry=CorpusRegistry(store);registry.register_revision(revision)
            record_id=store.put(Record(kind="ResearchAnalysisBundle",**scope,content=report))
            for kind,blob in rendered.items():
                refs=tuple(dict.fromkeys([*request.bundle.artifact_ids,*(e.artifact_id for e in request.bundle.evidence),*([artifacts["bundle"]] if kind=="markdown" else [])]))
                ArtifactService(store).publish(blob,ArtifactEnvelope(artifact_id=artifacts[kind],content_hash=artifacts[kind],artifact_kind="research_analysis_"+kind,
                    media_type="application/json" if kind=="bundle" else "text/markdown",**scope,source_artifact_ids=refs,provenance=(record_id,),status="proposed"),registry_revision=revision.revision_id)
            data.update(record_id=record_id,artifacts=artifacts,review_publication_checks=report["review_publication_checks"],
                publication_receipt_ids=[identity({"stage":"artifact_publication","operation_id":f"artifact-publish:{aid}:{revision.revision_id}"}) for aid in artifacts.values()])
            status="complete"
            result=finish(data,artifacts,status,None)
    except (Exception,CancelledError,KeyboardInterrupt) as exc:
        interrupted=exc if isinstance(exc,(CancelledError,KeyboardInterrupt)) else None
        status="failed";artifacts={};data={"version":VERSION,"scientific_admission":False,"error":type(exc).__name__}
    if status=="failed":result=finish(data,artifacts,status,interrupted)
    if interrupted:raise interrupted
    return result


def identity_bytes(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()
