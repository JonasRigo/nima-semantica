"""Deterministic, explicitly approved project commits and progress recording."""
from asyncio import CancelledError
from typing import Literal
from pydantic import Field, StrictBool, StrictInt, model_validator
from .models import StrictModel, Record, ConflictError, canonical, identity
from .okf_contracts import GraphIdentifier, GraphIdentity, GraphRevision, Digest, OKFDelta, OKFNode, OKFReference
from .graph_commit import OKFCommitApproval
from .graph_service import GraphService
from .graph_projection import GraphProjectionService, GraphProjectionRequest
from .ontology_services import OntologyService, OntologyRegistry
from .ontology_profiles import OntologyProfile, NodeType
from .ontology_tools import save_ontology, SaveOntologyRequest, OntologyContext
from .artifact_service import ArtifactService
from .corpus_registry import CorpusRegistry
from .evidence_contracts import validate_delta_evidence
from .execution_receipts import ExecutionReceiptService
from .receipts import ExecutionReceipt
from .research_run_service import ResearchRunService
from .tool_contracts import ToolResult

VERSION="update-project-graph-v1"
PROGRESS_PROFILE=OntologyProfile(name="project_progress",version="1.0.0",
    node_types=(NodeType(name="Attempt",description="Recorded workflow attempt and its immutable outcome; not scientific acceptance."),),
    instructions="Record scoped attempts, retained targets, outcomes and receipt links. Observed means history was recorded, never that its scientific claims were verified.")
PROGRESS_KINDS={"CounterexampleProgressProposal":"CounterexampleOutcome","LeanVerificationProgressProposal":"LeanVerificationOutcome",
    "ExtractionProgressProposal":"ExtractionOutcome","GraphAnalysisProgressProposal":"GraphAnalysisOutcome",
    "DependencyTraceProgressProposal":"DependencyTraceOutcome","SubstantiationProgressProposal":"SubstantiationOutcome",
    "HypothesisProgressProposal":"HypothesisOutcome","ComparisonProgressProposal":"ComparisonOutcome","DeepResearchProgressProposal":"DeepResearchOutcome",
    "ReviewProgressProposal":"ReviewOutcome","ResearchAnalysisProgressProposal":"ResearchAnalysisOutcome",
    "ProofDevelopmentProgressProposal":"ProofDevelopmentOutcome",
    "LeanDraftProgressProposal":"LeanDraftOutcome"}
POLICY_DIGEST=identity({"version":VERSION,"progress_profile":PROGRESS_PROFILE,"proposal_kinds":PROGRESS_KINDS,
    "authority":"operator-approved exact project delta; no corpus writes or inferred edits"})


class CorrectionBinding(StrictModel):
    assessment_artifact_id: Digest
    target_indices: tuple[StrictInt,...] = Field(min_length=1,max_length=16)


class UpdateProjectRequest(StrictModel):
    mode: Literal["preview","prepare","commit","rebuild_projection"]="preview"
    operation_id: GraphIdentifier | None=None
    run_id: GraphIdentifier | None=None
    graph_revision: GraphRevision | None=None
    delta: OKFDelta | None=None
    artifact_id: Digest | None=None
    progress_proposal_ids: tuple[GraphIdentifier,...] = Field(default=(),max_length=32)
    record_historical_progress: StrictBool=False
    correction_bindings: tuple[CorrectionBinding,...] = Field(default=(),max_length=16)

    @model_validator(mode="after")
    def selection(self):
        selected=sum((self.delta is not None,self.artifact_id is not None,bool(self.progress_proposal_ids)))
        if self.mode!="preview" and (not self.operation_id or self.graph_revision is None):raise ValueError("operation and exact graph revision required")
        if self.mode in ("prepare","commit") and selected!=1:raise ValueError("select exactly one delta, graph candidate artifact or progress batch")
        if self.mode=="rebuild_projection" and (selected or self.correction_bindings):raise ValueError("projection rebuild cannot carry a delta or corrections")
        if len(set(self.progress_proposal_ids))!=len(self.progress_proposal_ids):raise ValueError("duplicate progress proposal")
        if self.correction_bindings and self.delta is None:raise ValueError("corrections require explicit harness-authored delta")
        if self.record_historical_progress and not self.progress_proposal_ids:raise ValueError("historical recording applies only to progress")
        if len(canonical(self))>4_000_000:raise ValueError("request exceeds complete-read bound")
        return self


class UpdateProjectContext(StrictModel):
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier
    actor: GraphIdentifier="harness"
    allow_graph_writes: StrictBool=False
    allow_audit_writes: StrictBool=False
    allow_projection_writes: StrictBool=False
    approval: OKFCommitApproval | None=None
    max_changes: StrictInt=Field(default=512,ge=1,le=2048)
    max_regions: StrictInt=Field(default=100000,ge=1,le=1000000)
    max_tokens: StrictInt=Field(default=1000000,ge=1,le=10000000)


def _scope(context):return dict(corpus_id=context.corpus_id,project_id=context.project_id)


def _progress(store,ref,request,context):
    scope=_scope(context);record=store.get(ref,**scope)
    if record is None or record.project_id!=context.project_id or record.kind not in PROGRESS_KINDS:raise ConflictError("unknown or foreign progress proposal")
    p=record.content
    if (p.get("corpus_id"),p.get("project_id"))!=(context.corpus_id,context.project_id) or p.get("authority")!="proposal_only" or p.get("scientific_admission") is not False:
        raise ConflictError("invalid progress authority or scope")
    observed=p.get("expected_graph_revision")
    if observed is not None:
        revision=GraphRevision.model_validate(observed)
        if (revision.corpus_id,revision.project_id)!=(context.corpus_id,context.project_id):raise ConflictError("foreign attempt revision")
        if revision!=request.graph_revision and not request.record_historical_progress:raise ConflictError("historical progress requires explicit selection and fresh delta approval")
    outcome=store.get(p.get("outcome_record_id",""),**scope)
    artifact=p.get("outcome_artifact_id",p.get("artifact_id"))
    if outcome is None or outcome.project_id!=context.project_id or outcome.kind!=PROGRESS_KINDS[record.kind]:raise ConflictError("progress outcome binding differs")
    if outcome.content.get("artifact_id")!=artifact or outcome.content.get("operation_id")!=p.get("operation_id") or outcome.content.get("status")!=p.get("attempt_status"):
        raise ConflictError("outcome identity/status differs")
    env,blob=ArtifactService(store).read(artifact,**scope)
    if env.project_id!=context.project_id or outcome.id not in env.provenance:raise ConflictError("outcome artifact provenance differs")
    from .providers import strict_json_object
    captured=strict_json_object(blob.decode())
    if captured.get("status")!=p.get("attempt_status") or captured.get("request",{}).get("operation_id")!=p.get("operation_id"):
        raise ConflictError("captured attempt differs")
    receipts=ExecutionReceiptService(store)
    if not p.get("receipt_ids"):raise ConflictError("attempt receipts required")
    if p["receipt_ids"]!=captured.get("receipt_ids"):raise ConflictError("progress receipts differ from captured outcome")
    captured_request=captured.get("request",{})
    captured_revision=captured_request.get("graph_revision") or captured.get("data",{}).get("graph_revision")
    if captured_revision is not None and observed!=captured_revision:raise ConflictError("progress revision differs from captured attempt")
    expected_target=captured_request.get("claim") if record.kind=="DependencyTraceProgressProposal" else captured_request.get("targets") if record.kind in ("SubstantiationProgressProposal","ReviewProgressProposal") else captured_request.get("target") if record.kind=="ComparisonProgressProposal" else captured_request.get("graph_targets") if record.kind=="DeepResearchProgressProposal" else None
    if record.kind in ("ProofDevelopmentProgressProposal","LeanDraftProgressProposal"):expected_target=captured_request.get("target")
    if p.get("graph_target")!=expected_target:raise ConflictError("progress graph target differs from captured attempt")
    for key in ("target_record_id","parent_record_id","proof_target_id","parent_node_ids","definition_node_ids"):
        if key in p and p[key]!=captured_request.get(key):raise ConflictError("progress target differs from captured request")
    for rid in p["receipt_ids"]:
        receipt=receipts.get(rid,**scope)
        if receipt is None or receipt.project_id!=context.project_id:raise ConflictError("attempt receipt outside scope")
    for key in ("target_record_id","parent_record_id","proof_target_id"):
        target=store.get(p[key],**scope) if p.get(key) else None
        if p.get(key) and (target is None or target.project_id!=context.project_id):raise ConflictError("progress target outside project")
    for key in ("parent_node_ids","definition_node_ids"):
        for target in p.get(key,[]):
            if store.get(target,**scope) is None:raise ConflictError("progress reference unavailable")
    if any(r.content.get("proposal_id")==ref for _,r in store.records("ProjectProgressCommit",**scope)):
        raise ConflictError("progress already committed; use the original operation receipt")
    known={n.ref for n in store.read_okf_snapshot(**scope).nodes}
    graph_target=p.get("graph_target")
    targets=graph_target if isinstance(graph_target,list) else [graph_target] if graph_target else []
    links=[];unresolved=[]
    for selected in targets:
        if selected.get("kind","node")!="node":continue
        target=GraphIdentity.model_validate(selected.get("ref",selected))
        if target.corpus_id!=context.corpus_id or target.project_id not in (None,context.project_id):raise ConflictError("history graph target outside scope")
        if target in known:links.append(target)
        else:unresolved.append(target.model_dump(mode="json"))
    node=OKFNode(node_id="attempt-"+identity(ref),node_type="attempt",**scope,status="observed",ontology_profile=PROGRESS_PROFILE.digest,
        properties={"progress_proposal_id":ref,"progress":p,"scientific_acceptance":False,"parent_proof_completed":False,"unresolved_graph_targets":unresolved},
        parents=tuple(dict.fromkeys(links)),
        provenance=(OKFReference(reference_kind="record",target_id=ref,**scope),
            OKFReference(reference_kind="record",target_id=outcome.id,**scope),
            OKFReference(reference_kind="artifact",target_id=artifact,content_hash=artifact,**scope)),producer="update_project_graph",producer_version=VERSION)
    return node


def _corrections(store,request,delta,context):
    bindings=[];touched={n.ref for n in delta.upsert_nodes}|set(delta.remove_node_ids)
    edges={e.ref for e in delta.add_edges}|set(delta.remove_edge_ids)
    for binding in request.correction_bindings:
        env,blob=ArtifactService(store).read(binding.assessment_artifact_id,**_scope(context))
        if env.project_id!=context.project_id or env.artifact_kind!="substantiation_assessment":raise ConflictError("foreign or wrong assessment artifact")
        from .providers import strict_json_object
        outcome=strict_json_object(blob.decode());result=outcome.get("data",{}).get("result",{})
        if not result.get("finalized") or result.get("graph_revision")!=request.graph_revision.model_dump(mode="json"):raise ConflictError("assessment is absent or stale")
        if len(set(binding.target_indices))!=len(binding.target_indices):raise ConflictError("duplicate correction target")
        for i in binding.target_indices:
            if i<0 or i>=len(result["assessments"]):raise ConflictError("unknown correction target")
            a=result["assessments"][i]
            from .okf_contracts import GraphIdentity
            ref=GraphIdentity.model_validate(a["target"]["ref"])
            if a["recommendation"] not in ("revise","withdraw") or ref not in (touched if a["target"]["kind"]=="node" else edges):
                raise ConflictError("correction does not bind an explicit changed target")
        bindings.append(binding.model_dump(mode="json"))
    if (bindings or "substantiation_corrections" in delta.metadata) and delta.metadata.get("substantiation_corrections")!=bindings:raise ConflictError("approved delta must include exact substantiation correction bindings")


def prepare_update(store,request,context):
    scope=_scope(context)
    if not CorpusRegistry(store).corpus(context.corpus_id):raise ConflictError("registered corpus required")
    if store.graph_revision(**scope)!=request.graph_revision:raise ConflictError("stale or foreign graph revision")
    if request.run_id and ResearchRunService(store).get_run(request.run_id,**scope) is None:raise ConflictError("foreign run")
    if request.progress_proposal_ids:
        nodes=tuple(_progress(store,ref,request,context) for ref in request.progress_proposal_ids)
        delta=OKFDelta(delta_id=request.operation_id,base_revision=request.graph_revision,**scope,ontology_profile=PROGRESS_PROFILE.digest,
            upsert_nodes=nodes,reason="Record explicitly selected workflow attempts without scientific promotion.",
            metadata={"progress_proposal_ids":list(request.progress_proposal_ids),"record_historical_progress":request.record_historical_progress})
    elif request.artifact_id:
        env,blob=ArtifactService(store).read(request.artifact_id,**scope)
        if env.project_id!=context.project_id or env.artifact_kind!="graph_candidate" or env.status!="proposed":raise ConflictError("expected authorized proposed graph candidate")
        if len(blob)>4_000_000:raise ValueError("candidate exceeds complete-read bound")
        delta=OKFDelta.model_validate_json(blob)
    else:delta=request.delta
    if (delta.corpus_id,delta.project_id)!=(context.corpus_id,context.project_id) or delta.base_revision!=request.graph_revision:raise ConflictError("delta scope/revision differs")
    if sum(len(v) for v in (delta.upsert_nodes,delta.add_edges,delta.remove_node_ids,delta.remove_edge_ids))>context.max_changes:raise ValueError("delta exceeds operator bound")
    if not request.progress_proposal_ids and (delta.ontology_profile==PROGRESS_PROFILE.digest or delta.metadata.get("progress_proposal_ids")):
        raise ConflictError("use the progress proposal selector to record history")
    _corrections(store,request,delta,context)
    ontology=OntologyService.from_store(store,**scope)
    if request.progress_proposal_ids:
        profiles=tuple(ontology.resolve(p.digest) for p in ontology.list_profiles())
        if not any(p.digest==PROGRESS_PROFILE.digest for p in profiles):profiles=(*profiles,PROGRESS_PROFILE)
        ontology=OntologyService(OntologyRegistry(profiles))
    # Same prospective graph, endpoint, ontology and evidence checks as commit,
    # without writing a preview commit or manufacturing approval.
    profile=ontology.resolve(delta.ontology_profile or "")
    if profile.digest==PROGRESS_PROFILE.digest and not request.progress_proposal_ids:raise ConflictError("progress history requires a native proposal")
    existing=store.read_okf_snapshot(**scope)
    history={n.ref for n in existing.nodes if n.ontology_profile==PROGRESS_PROFILE.digest}
    if history & ({n.ref for n in delta.upsert_nodes}|set(delta.remove_node_ids)):raise ConflictError("committed progress history is append-only")
    for obj in (*delta.upsert_nodes,*delta.add_edges):
        if obj.ontology_profile and ontology.resolve(obj.ontology_profile).digest!=profile.digest:raise ConflictError("object ontology differs")
    bound=delta.model_copy(update={"ontology_profile":profile.digest,
        "upsert_nodes":tuple(n.model_copy(update={"ontology_profile":profile.digest}) for n in delta.upsert_nodes),
        "add_edges":tuple(e.model_copy(update={"ontology_profile":profile.digest}) for e in delta.add_edges)})
    nodes,edges=store.prospective_graph(bound)
    for n in nodes.values():
        p=ontology.resolve(n.ontology_profile)
        if n.node_type not in {t.name.casefold() for t in p.node_types}:raise ConflictError("unknown node type")
    for e in edges.values():
        p=ontology.resolve(e.ontology_profile);r=next((r for r in p.relation_types if r.name.casefold()==e.relation),None)
        if r is None or nodes[e.source_id].node_type not in {x.casefold() for x in r.source_types} or nodes[e.target_id].node_type not in {x.casefold() for x in r.target_types}:raise ConflictError("invalid relation endpoints")
        if any(nodes[ref].ontology_profile!=p.digest for ref in (e.source_id,e.target_id)):raise ConflictError("cross-profile edge")
    if not validate_delta_evidence(store,bound).valid:raise ConflictError("invalid evidence")
    return delta


def update_project_graph(store,request,context):
    request=UpdateProjectRequest.model_validate(request.model_dump(mode="json"));context=UpdateProjectContext.model_validate(context.model_dump(mode="json"))
    if request.mode=="preview":return ToolResult(operation="Update Project Graph",status="complete",data={"executed":False,"policy_digest":POLICY_DIGEST,"request":request.model_dump(mode="json")})
    if store is None:return ToolResult(operation="Update Project Graph",status="unavailable")
    if request.mode=="prepare":
        try:
            with store.joined_transaction():delta=prepare_update(store,request,context)
            return ToolResult(operation="Update Project Graph",status="complete",data={"executed":False,"delta":delta.model_dump(mode="json"),"delta_hash":identity(delta),
                "required_approval":{"delta_id":delta.delta_id,"delta_hash":identity(delta),"base_revision":delta.base_revision.model_dump(mode="json"),**_scope(context)},
                "progress_ontology":PROGRESS_PROFILE.model_dump(mode="json") if request.progress_proposal_ids else None,"approval_granted":False})
        except Exception as exc:return ToolResult(operation="Update Project Graph",status="failed",diagnostics=({"code":type(exc).__name__},))
    if not context.allow_audit_writes or (request.mode=="commit" and (not context.allow_graph_writes or context.approval is None)) or (request.mode=="rebuild_projection" and not context.allow_projection_writes):
        return ToolResult(operation="Update Project Graph",status="failed",diagnostics=({"code":"write_or_approval_not_authorized"},))
    scope=_scope(context);receipts=ExecutionReceiptService(store)
    rid=identity({"stage":"update_project_graph","operation_id":request.operation_id,**scope});fingerprint=identity({"version":VERSION,"request":request,"context":context})
    previous=receipts.replay(rid,request_hash=fingerprint,**scope)
    if previous:return ToolResult.model_validate(previous.metadata["result"])
    checkpoints=[r.content for _,r in store.records("ProjectUpdateCommit",**scope) if r.project_id==context.project_id and r.content.get("operation_id")==request.operation_id]
    if checkpoints and (len(checkpoints)!=1 or checkpoints[0]["request_hash"]!=fingerprint):raise ConflictError("divergent interrupted commit replay")
    data={"committed":False,"project_recording":{"status":"pending","commit_receipt_id":None},"projection":{"status":"not_requested"}};children=[];interrupted=None
    try:
        if request.mode=="commit":
            if checkpoints:
                from .graph_commit import OKFCommitResult
                native=OKFCommitResult.model_validate(checkpoints[0]["commit"])
                delta_hash=context.approval.delta_hash
            else:
                with store.joined_transaction():
                    delta=prepare_update(store,request,context)
                    approval=context.approval
                    if approval.approved_by!=context.actor or approval.delta_hash!=identity(delta) or approval.delta_id!=delta.delta_id or approval.base_revision!=delta.base_revision or (approval.corpus_id,approval.project_id)!=(context.corpus_id,context.project_id):
                        raise ConflictError("operator approval does not match actor and exact scoped delta")
                    if request.progress_proposal_ids:
                        saved=save_ontology(store,SaveOntologyRequest(mode="save",operation_id=identity((rid,"ontology")),profile=PROGRESS_PROFILE.model_dump(mode="json")),OntologyContext(**scope,actor=context.actor,allow_writes=True))
                        if saved.status!="complete":raise ConflictError("progress ontology could not be saved")
                    native=GraphService(store,ontology=OntologyService.from_store(store,**scope)).commit_delta(delta,approval)
                    for ref in request.progress_proposal_ids:
                        store.put(Record(kind="ProjectProgressCommit",**scope,content={"proposal_id":ref,"delta_id":delta.delta_id,
                            "commit_receipt_id":native.receipt_id,"final_revision":native.final_revision.model_dump(mode="json"),"node_id":"attempt-"+identity(ref)},parents=(ref,native.receipt_id)))
                    # Recover committed identity after process interruption before projection/publication.
                    store.put(Record(kind="ProjectUpdateCommit",**scope,content={"operation_id":request.operation_id,"request_hash":fingerprint,"commit":native.model_dump(mode="json")}))
                delta_hash=identity(delta)
            data.update(committed=True,commit=native.model_dump(mode="json"),delta_hash=delta_hash,
                project_recording={"status":"committed","commit_receipt_id":native.receipt_id,"proposal_ids":list(request.progress_proposal_ids)})
            revision=native.final_revision
        else:
            revision=request.graph_revision
            if revision!=store.graph_revision(**scope):raise ConflictError("projection revision stale or foreign")
            if not CorpusRegistry(store).corpus(context.corpus_id):raise ConflictError("registered corpus required")
            if request.run_id and ResearchRunService(store).get_run(request.run_id,**scope) is None:raise ConflictError("foreign run")
        if context.allow_projection_writes:
            projection=GraphProjectionService(store).rebuild(GraphProjectionRequest(**scope,graph_revision=revision,
                idempotency_key=identity((rid,"projection",revision)),max_regions=context.max_regions,max_tokens=context.max_tokens))
            children.append(projection.receipt_id);data["projection"]=projection.model_dump(mode="json")
            status="complete" if projection.status=="completed" else "partial"
        else:
            data["projection"]={"status":"pending","reason":"projection writes not authorized; rebuild explicitly before retrieval"};status="partial"
    except (Exception,CancelledError,KeyboardInterrupt) as exc:
        interrupted=exc if isinstance(exc,(CancelledError,KeyboardInterrupt)) else None
        data["error"]=type(exc).__name__;status="partial" if data["committed"] else "failed"
        if data["committed"]:data["projection"]={"status":"interrupted" if interrupted else "failed"}
        else:data["project_recording"]={"status":"failed","commit_receipt_id":None}
    result=ToolResult(operation="Update Project Graph",status=status,data=data,receipt_ids=(rid,*children),
        note="Commit approval authorizes exactly this project delta, not scientific certification or corpus promotion. Projection failure never undoes a committed graph. Original progress proposals stay immutable; ProjectProgressCommit records resolve pending history.")
    terminal="interrupted" if interrupted else "failed" if status=="failed" else "partial" if status=="partial" else "completed"
    with store.joined_transaction():
        outcome=store.put(Record(kind="ProjectUpdateOutcome",**scope,content={"operation_id":request.operation_id,"result":result.model_dump(mode="json")}))
        receipts.record(ExecutionReceipt(receipt_id=rid,operation_id=request.operation_id,stage="update_project_graph",**scope,run_id=request.run_id,
            graph_revision=request.graph_revision,status=terminal,error=data.get("error"),tool_version=VERSION,output_ids=(outcome,),
            metadata={"request_hash":fingerprint,"result":result.model_dump(mode="json")}))
    if interrupted:raise interrupted
    return result
