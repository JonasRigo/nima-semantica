"""Deterministic source-to-required-premise traversal; graph structure is not proof."""
from asyncio import CancelledError
from collections import deque
import time
from typing import Literal
from pydantic import Field,StrictBool,StrictInt,model_validator
from .models import StrictModel,ConflictError,canonical,identity
from .okf_contracts import GraphIdentifier,GraphIdentity,GraphRevision,Digest
from .graph_analysis import load_snapshot,inspect_snapshot_ontology,dependency_disposition,persist_graph_outcome,TRUSTED_STATUSES
from .ontology_services import OntologyService
from .corpus_registry import CorpusRegistry
from .research_run_service import ResearchRunService
from .execution_receipts import ExecutionReceiptService
from .receipts import ExecutionReceipt
from .tool_contracts import ToolResult

VERSION="trace-claim-dependencies-v1"
POLICY={"direction":"source_requires_target","relation_selection":"ontology.necessary_dependency",
    "recorded_edge_statuses":sorted(TRUSTED_STATUSES),"endpoint_policy":"Proposed endpoints make the dependency conditional.",
    "leaf":"no recorded necessary-dependency outgoing edges",
    "proposed":"included only when requested; retained as conditional paths",
    "rejected":"failed/refuted/contradicted relations excluded and reported",
    "limits":"Boundaries and unenumerated paths never become leaves or a cycle-free claim.",
    "authority":"analysis_only"}
POLICY_DIGEST=identity({"version":VERSION,"policy":POLICY})


class TraceDependenciesRequest(StrictModel):
    mode: Literal["preview","trace"]="preview"
    operation_id: GraphIdentifier | None=None
    run_id: GraphIdentifier | None=None
    graph_revision: GraphRevision | None=None
    claim: GraphIdentity | None=None
    artifact_id: Digest | None=None
    include_proposed: StrictBool=True
    target_record_id: GraphIdentifier | None=None
    parent_record_id: GraphIdentifier | None=None

    @model_validator(mode="after")
    def pinned(self):
        if self.mode!="preview" and (not self.operation_id or self.graph_revision is None or self.claim is None):
            raise ValueError("trace requires operation ID, exact graph revision and full claim identity")
        return self


class TraceDependenciesContext(StrictModel):
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None=None
    allow_audit_writes: StrictBool=False
    max_nodes: StrictInt=Field(default=256,ge=1,le=256)
    max_edges: StrictInt=Field(default=512,ge=0,le=512)
    max_visited_nodes: StrictInt=Field(default=128,ge=1,le=256)
    max_traversed_edges: StrictInt=Field(default=256,ge=0,le=512)
    max_depth: StrictInt=Field(default=32,ge=0,le=64)
    max_paths: StrictInt=Field(default=128,ge=1,le=2048)
    max_steps: StrictInt=Field(default=10000,ge=1,le=100000)
    timeout_seconds: StrictInt=Field(default=30,ge=1,le=120)


def traverse_dependencies(snapshot,request,context,ontology):
    started=time.monotonic();deadline=started+context.timeout_seconds
    nodes,profiles,node_profiles,edge_profiles,coverage=inspect_snapshot_ontology(snapshot,ontology)
    root=request.claim
    if root not in nodes:raise ConflictError("claim is absent from the authorized snapshot")
    outgoing={ref:[] for ref in nodes};recorded={ref:[] for ref in nodes};excluded={ref:[] for ref in nodes}
    dispositions={}
    for e in sorted(snapshot.edges,key=lambda e:canonical(e.ref)):
        if not edge_profiles[e.ref][1].necessary_dependency:continue
        recorded[e.source_id].append(e);disposition=dependency_disposition(e,nodes);dispositions[e.ref]=disposition
        if disposition=="excluded" or disposition=="proposed" and not request.include_proposed:
            excluded[e.source_id].append({"edge":e.ref.model_dump(mode="json"),"target":e.target_id.model_dump(mode="json"),
                "reason":"recorded_rejected_relation" if disposition=="excluded" else "proposed_relation_filtered"})
        else:outgoing[e.source_id].append(e)
    steps=0;limits=set();frontier=[]
    def consume():
        nonlocal steps
        if time.monotonic()>=deadline:limits.add("timeout");return False
        if steps>=context.max_steps:limits.add("step_limit");return False
        steps+=1;return True
    def boundary(ref,reason,edge=None):
        limits.add(reason)
        item={"node":ref.model_dump(mode="json"),"reason":reason}
        if edge:item["edge"]=edge.ref.model_dump(mode="json");item["target"]=edge.target_id.model_dump(mode="json")
        frontier.append(item)
    visited={root:0};queue=deque([root]);selected={};adj={ref:[] for ref in nodes};expanded=set()
    while queue:
        ref=queue.popleft()
        if not consume():
            frontier.extend({"node":v.model_dump(mode="json"),"reason":"work_limit"} for v in (ref,*queue));break
        if outgoing[ref] and visited[ref]>=context.max_depth:
            boundary(ref,"depth_limit");continue
        completed=True
        for e in outgoing[ref]:
            if not consume():boundary(ref,"work_limit",e);completed=False;break
            if len(selected)>=context.max_traversed_edges:
                boundary(ref,"edge_limit",e);completed=False;continue
            if e.target_id not in visited:
                if len(visited)>=context.max_visited_nodes:
                    boundary(ref,"node_limit",e);completed=False;continue
                visited[e.target_id]=visited[ref]+1;queue.append(e.target_id)
            selected[e.ref]=e;adj[ref].append(e)
        if completed:expanded.add(ref)
        if "timeout" in limits or "step_limit" in limits:
            frontier.extend({"node":v.model_dump(mode="json"),"reason":"work_limit"} for v in queue);break
    reachable_complete=not limits
    paths=[];cycles={};stack=[((root,),())];paths_complete=True
    def path_record(ns,es,terminal):
        conditional=[e.ref.model_dump(mode="json") for e in es if dispositions[e.ref]=="proposed"]
        return {"nodes":[r.model_dump(mode="json") for r in ns],"edges":[e.ref.model_dump(mode="json") for e in es],
            "terminal":terminal,"conditional":bool(conditional),"proposed_edges":conditional}
    while stack:
        if len(paths)>=context.max_paths:limits.add("path_limit");paths_complete=False;break
        if not consume():paths_complete=False;break
        ns,es=stack.pop();ref=ns[-1]
        if ref in ns[:-1]:
            start=ns.index(ref);cycle_edges=es[start:];keys=[canonical(e.ref).decode() for e in cycle_edges]
            rotation=min(tuple(keys[i:]+keys[:i]) for i in range(len(keys)))
            cycles[rotation]=path_record(ns[start:],cycle_edges,"cycle")
            paths.append(path_record(ns,es,"cycle"));continue
        if adj[ref] and len(es)>=context.max_depth:
            paths.append(path_record(ns,es,"depth_boundary"));limits.add("path_depth_limit");paths_complete=False;continue
        if not adj[ref]:
            terminal="limit_boundary" if ref not in expanded else "filtered_boundary" if recorded[ref] else "structural_leaf"
            paths.append(path_record(ns,es,terminal));continue
        # Retain an explicit path to partially expanded branch points as well
        # as their explored descendants. They must not look fully covered.
        if ref not in expanded:
            paths.append(path_record(ns,es,"limit_boundary"))
        stack.extend(((*ns,e.target_id),(*es,e)) for e in reversed(adj[ref]))
    truncated=bool(limits)
    ordered=sorted(visited,key=canonical)
    obligations=[];problems=[];leaves=[];missing_evidence=[]
    for ref in ordered:
        n=nodes[ref];role=next(t.role for t in node_profiles[ref].node_types if t.name.casefold()==n.node_type)
        item={"node":ref.model_dump(mode="json"),"recorded_status":n.status.value}
        if role=="obligation" and n.status.value!="verified":obligations.append(item)
        if n.status.value in ("failed","refuted","contradicted"):problems.append(item)
        if not recorded[ref]:leaves.append(ref.model_dump(mode="json"))
        if not n.evidence:missing_evidence.append({"kind":"node","ref":ref.model_dump(mode="json")})
    missing_evidence.extend({"kind":"edge","ref":e.ref.model_dump(mode="json")} for e in selected.values() if not e.evidence)
    return {"claim":root.model_dump(mode="json"),"policy":POLICY,"snapshot_id":snapshot.snapshot_id,"snapshot_hash":identity(snapshot),
        "graph_revision":snapshot.graph_revision.model_dump(mode="json"),"ontology_digests":sorted(profiles),"coverage_diagnostics":coverage,
        "nodes":[{"node":nodes[ref].model_dump(mode="json"),"shortest_depth":visited[ref]} for ref in ordered],
        "edges":[e.model_dump(mode="json") for e in sorted(selected.values(),key=lambda e:canonical(e.ref))],
        "paths":paths,"cycle_witnesses":list(cycles.values()),"structural_leaves":leaves,
        "outstanding_obligations":obligations,"recorded_problems":problems,"no_attached_evidence":missing_evidence,
        "excluded_dependencies":[item for ref in ordered for item in excluded[ref]],"frontier":frontier,
        "reachable_subgraph_complete":reachable_complete,
        "unexplored_path_prefix_count":len(stack),"path_frontier_omitted_count":max(0,len(stack)-32),
        "unexplored_path_prefixes":[path_record(ns,es,"unexplored_prefix") for ns,es in stack[-32:]],
        "traversal_complete":not truncated,"paths_complete":paths_complete and not truncated,
        "cycle_search_complete":paths_complete and not truncated,"truncated":truncated,"limits_reached":sorted(limits),"steps":steps,
        "input_unresolved":snapshot.metadata.get("unresolved",[]),"input_diagnostics":snapshot.metadata.get("diagnostics",[]),
        "meaning":"Leaves and cycles concern recorded, selected relations only. Missing evidence or unverified status is not proof of absent substantiation."}


def trace_claim_dependencies(store,request,context):
    request=TraceDependenciesRequest.model_validate(request.model_dump(mode="json"))
    context=TraceDependenciesContext.model_validate(context.model_dump(mode="json"))
    if request.mode=="preview":return ToolResult(operation="Trace Claim Dependencies",status="complete",data={"executed":False,"request":request.model_dump(mode="json"),"policy":POLICY})
    if store is None or not context.allow_audit_writes:return ToolResult(operation="Trace Claim Dependencies",status="failed",diagnostics=({"code":"audit_or_store_unavailable"},))
    scope=dict(corpus_id=context.corpus_id,project_id=context.project_id);receipts=ExecutionReceiptService(store)
    rid=identity({"stage":"trace_claim_dependencies","operation_id":request.operation_id,**scope})
    fingerprint=identity({"version":VERSION,"policy":POLICY_DIGEST,"request":request,"context":context})
    previous=receipts.replay(rid,request_hash=fingerprint,**scope)
    if previous:return ToolResult.model_validate(previous.metadata["result"])
    data={"version":VERSION,"policy_digest":POLICY_DIGEST,"scientific_acceptance":False,"source_fidelity_verified":False,
        "analysis_scope":"candidate_only" if request.artifact_id else "stored_corpus_and_project_snapshot","limits":context.model_dump(mode="json")}
    interrupted=None
    try:
        if not CorpusRegistry(store).corpus(context.corpus_id):raise ValueError("registered corpus required")
        if request.run_id and ResearchRunService(store).get_run(request.run_id,**scope) is None:raise ConflictError("run outside scope")
        for ref in (request.target_record_id,request.parent_record_id):
            record=store.get(ref,**scope) if ref else None
            if ref and (record is None or record.project_id!=context.project_id):raise ConflictError("progress reference outside scope")
        ontology=OntologyService.from_store(store,**scope)
        snapshot=load_snapshot(store,request,context,ontology)
        trace=traverse_dependencies(snapshot,request,context,ontology)
        if store.graph_revision(**scope)!=request.graph_revision:raise ConflictError("graph changed during traversal")
        data["trace"]=trace
        status="partial" if trace["truncated"] or trace["coverage_diagnostics"] else "complete"
    except (Exception,CancelledError,KeyboardInterrupt) as exc:
        interrupted=exc if isinstance(exc,(CancelledError,KeyboardInterrupt)) else None
        status="failed";data["error"]=type(exc).__name__
    terminal="interrupted" if interrupted else "failed" if status=="failed" else "partial" if status=="partial" else "completed"
    with store.joined_transaction():
        artifact,progress=persist_graph_outcome(store,request,context,data,terminal,[rid],version=VERSION,
            record_prefix="DependencyTrace",artifact_kind="dependency_trace",target=request.claim.model_dump(mode="json"))
        data["project_progress"]=progress
        result=ToolResult(operation="Trace Claim Dependencies",status=status,data=data,receipt_ids=(rid,),artifacts={"trace":artifact},
            note="Read-only graph traversal; no mathematical verification, proof completion, scheduling or graph admission. Project progress recording remains pending an approved commit.")
        receipts.record(ExecutionReceipt(receipt_id=rid,operation_id=request.operation_id,stage="trace_claim_dependencies",**scope,
            run_id=request.run_id,graph_revision=request.graph_revision,status=terminal,error=data.get("error"),tool_version=VERSION,
            output_ids=(artifact,),metadata={"request_hash":fingerprint,"result":result.model_dump(mode="json")}))
    if interrupted:raise interrupted
    return result
