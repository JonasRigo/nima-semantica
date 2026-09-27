"""Deterministic, revision-bound analysis of represented OKF graph structure.

Logical consequences describe the graph under its declared dependencies, not
the truth of its prose. No properties are interpreted as executable logic.
"""
from asyncio import CancelledError
import hashlib
from typing import Literal
from pydantic import Field, StrictBool, StrictInt, model_validator

from .artifact_contracts import ArtifactEnvelope
from .artifact_service import ArtifactService
from .corpus_registry import CorpusRegistry
from .evidence_contracts import validate_snapshot_evidence
from .execution_receipts import ExecutionReceiptService
from .graph_service import GraphService
from .graph_reasoning import GraphReasoningRequest, GraphReasoningService
from .models import StrictModel, Record, ConflictError, canonical, identity
from .okf_contracts import GraphIdentifier, GraphRevision, Digest, OKFDelta, OKFSnapshot
from .ontology_services import OntologyService
from .reasoning_kernel import Atom, Assertion, Predicate, HornRule, GraphSnapshot, ContextPolicy
from .receipts import ExecutionReceipt
from .registry_contracts import RegistryRevision
from .research_run_service import ResearchRunService
from .tool_contracts import ToolResult

VERSION="analyze-graph-v1"
JUSTIFICATION="Deterministic analysis of represented graph metadata and explicitly declared dependency semantics; not mathematical certification."
TRUSTED_STATUSES=frozenset(("observed","supported","verified","promoted"))


class AnalyzeGraphRequest(StrictModel):
    mode: Literal["preview","strict","hypothesizing"] = "preview"
    operation_id: GraphIdentifier | None = None
    run_id: GraphIdentifier | None = None
    graph_revision: GraphRevision | None = None
    artifact_id: Digest | None = None
    target_record_id: GraphIdentifier | None = None
    parent_record_id: GraphIdentifier | None = None

    @model_validator(mode="after")
    def pinned(self):
        if self.mode!="preview" and (not self.operation_id or self.graph_revision is None):
            raise ValueError("analysis requires an operation ID and exact graph revision")
        return self


class AnalyzeGraphContext(StrictModel):
    corpus_id: GraphIdentifier
    project_id: GraphIdentifier | None = None
    allow_audit_writes: StrictBool = False
    max_nodes: StrictInt = Field(default=128,ge=1,le=256)
    max_edges: StrictInt = Field(default=256,ge=0,le=512)
    max_facts: StrictInt = Field(default=2000,ge=1,le=10000)
    max_matches: StrictInt = Field(default=10000,ge=1,le=100000)
    max_rounds: StrictInt = Field(default=64,ge=1,le=256)
    timeout_seconds: StrictInt = Field(default=30,ge=1,le=120)


def _rule(name,premises,conclusion):
    def atom(value):return Atom(predicate=value[0],arguments=tuple(value[1:]))
    return HornRule(name=name,premises=tuple(map(atom,premises)),conclusion=atom(conclusion),
        origin="assumed",justification=JUSTIFICATION)


RULES=(
    _rule("dependency_path",[("Depends","?x","?y")],("Reachable","?x","?y")),
    _rule("transitive_dependency",[("Reachable","?x","?y"),("Depends","?y","?z")],("Reachable","?x","?z")),
    _rule("dependency_cycle",[("Reachable","?x","?x")],("DependencyCycle","?x")),
    _rule("open_dependency",[("Depends","?x","?y"),("OpenObligation","?y")],("HasOpenDependency","?x")),
    _rule("propagate_open_dependency",[("Depends","?x","?y"),("HasOpenDependency","?y")],("HasOpenDependency","?x")),
    _rule("problem_dependency",[("Depends","?x","?y"),("RecordedProblem","?y")],("HasProblemDependency","?x")),
    _rule("propagate_problem_dependency",[("Depends","?x","?y"),("HasProblemDependency","?y")],("HasProblemDependency","?x")),
)
POLICY_DIGEST=identity({"version":VERSION,"rules":RULES,"trusted_statuses":sorted(TRUSTED_STATUSES)})


def load_snapshot(store,request,context,ontology):
    scope=dict(corpus_id=context.corpus_id,project_id=context.project_id)
    if store.graph_revision(**scope)!=request.graph_revision:raise ConflictError("stale or foreign graph revision")
    if request.artifact_id is None:
        snapshot=GraphService(store,ontology=ontology).read_snapshot(**scope,revision=request.graph_revision)
    else:
        envelope=ArtifactService(store).resolve(request.artifact_id,**scope)
        if (envelope.corpus_id,envelope.project_id)!=(context.corpus_id,context.project_id):
            raise ConflictError("candidate scope must exactly match analysis scope")
        if envelope.artifact_kind!="graph_candidate" or envelope.status!="proposed":
            raise ValueError("analysis accepts registered proposed graph candidates, not arbitrary artifacts")
        raw=store.read_artifact(request.artifact_id)
        if hashlib.sha256(raw).hexdigest()!=request.artifact_id:raise ValueError("candidate bytes differ from pinned hash")
        if len(raw)>4_000_000:raise ValueError("candidate exceeds complete-read limit")
        delta=OKFDelta.model_validate_json(raw)
        if delta.base_revision!=request.graph_revision or (delta.corpus_id,delta.project_id)!=(context.corpus_id,context.project_id):
            raise ConflictError("candidate revision or scope differs")
        if delta.remove_node_ids or delta.remove_edge_ids:raise ValueError("candidate analysis requires a self-contained additive proposal")
        if any(item.status.value!="proposed" for item in (*delta.upsert_nodes,*delta.add_edges)):
            raise ValueError("a proposed artifact cannot self-admit graph objects")
        snapshot=OKFSnapshot(snapshot_id=request.artifact_id,**scope,graph_revision=request.graph_revision,
            nodes=delta.upsert_nodes,edges=delta.add_edges,ontology_profile=delta.ontology_profile,metadata=delta.metadata)
    if len(snapshot.nodes)>context.max_nodes or len(snapshot.edges)>context.max_edges:
        raise ValueError("graph exceeds authorized analysis bounds; no silent truncation")
    if not validate_snapshot_evidence(store,snapshot).valid:raise ValueError("invalid graph evidence")
    return snapshot


def inspect_snapshot_ontology(snapshot,ontology):
    """Shared read-only vocabulary, endpoint and coverage validation for graph tools."""
    nodes={n.ref:n for n in snapshot.nodes};profiles={};node_profiles={};edge_profiles={};coverage=[]
    if snapshot.ontology_profile:
        profile=ontology.resolve(snapshot.ontology_profile);profiles[profile.digest]=profile
    for n in snapshot.nodes:
        profile=ontology.resolve(n.ontology_profile or snapshot.ontology_profile or "")
        if n.node_type not in {t.name.casefold() for t in profile.node_types}:raise ValueError("unknown node type")
        profiles[profile.digest]=profile;node_profiles[n.ref]=profile
    for e in snapshot.edges:
        profile=ontology.resolve(e.ontology_profile or snapshot.ontology_profile or "")
        relation=next((r for r in profile.relation_types if r.name.casefold()==e.relation),None)
        if relation is None:raise ValueError("unknown relation")
        if any(node_profiles[ref].digest!=profile.digest for ref in (e.source_id,e.target_id)):
            raise ValueError("cross-profile relations require explicit alignment")
        if nodes[e.source_id].node_type not in {x.casefold() for x in relation.source_types} or nodes[e.target_id].node_type not in {x.casefold() for x in relation.target_types}:
            raise ValueError("relation endpoint type mismatch")
        profiles[profile.digest]=profile;edge_profiles[e.ref]=(profile,relation)
    for digest,p in profiles.items():
        present={n.node_type for n in snapshot.nodes if node_profiles[n.ref].digest==digest}
        relations={e.relation for e in snapshot.edges if edge_profiles[e.ref][0].digest==digest}
        coverage.extend({"code":"required_node_type_missing","profile":digest,"name":k} for k in p.required_node_types if k.casefold() not in present)
        coverage.extend({"code":"required_relation_missing","profile":digest,"name":k} for k in p.required_relation_types if k.casefold() not in relations)
    return nodes,profiles,node_profiles,edge_profiles,coverage


def dependency_disposition(edge,nodes):
    """Relation eligibility only; recorded statuses never certify truth."""
    if edge.status.value in ("refuted","failed","contradicted"):return "excluded"
    if edge.status.value in TRUSTED_STATUSES and all(nodes[ref].status.value!="proposed" for ref in (edge.source_id,edge.target_id)):
        return "recorded"
    return "proposed"


def translate_snapshot(snapshot,ontology,context):
    """Translate typed metadata only, with reversible full-scope identities."""
    nodes,profiles,node_profiles,edge_profiles,coverage=inspect_snapshot_ontology(snapshot,ontology)
    entities={};bindings={};assertions=[];attribution={};predicates={};vocabulary={}
    def entity(kind,ref):
        key=("n" if kind=="node" else "e")+identity(ref)[:40]
        entities[key]="Node" if kind=="node" else "Edge"
        bindings[key]={"kind":kind,"ref":ref.model_dump(mode="json")}
        return key
    nids={n.ref:entity("node",n.ref) for n in snapshot.nodes}
    eids={e.ref:entity("edge",e.ref) for e in snapshot.edges}
    def fact(name,args,types,items,origin="assumed"):
        predicates[name]=Predicate(name=name,argument_types=types)
        assertion=Assertion(atom=Atom(predicate=name,arguments=args),origin=origin,justification=JUSTIFICATION if origin=="assumed" else "Proposed dependency; only eligible in hypothesizing mode.")
        if assertion.id not in attribution:assertions.append(assertion)
        sources=[{"kind":"node" if hasattr(i,"node_id") else "edge","ref":i.ref.model_dump(mode="json"),
            "status":i.status.value,"evidence":[e.model_dump(mode="json") for e in i.evidence]} for i in items]
        attribution[assertion.id]=list({identity(s):s for s in [*attribution.get(assertion.id,[]),*sources]}.values())
    findings=[]
    for n in snapshot.nodes:
        p=node_profiles[n.ref];nid=nids[n.ref]
        typename="Node_"+identity((p.digest,n.node_type))[:32]
        vocabulary[typename]={"ontology_digest":p.digest,"node_type":n.node_type}
        fact(typename,(nid,),("Node",),(n,))
        fact("Status_"+n.status.value,(nid,),("Node",),(n,))
        role=next(t.role for t in p.node_types if t.name.casefold()==n.node_type)
        if role=="obligation" and n.status.value!="verified":
            fact("OpenObligation",(nid,),("Node",),(n,))
            findings.append({"code":"obligation_not_recorded_verified","node":n.ref.model_dump(mode="json"),"status":n.status.value})
        if n.status.value in ("contradicted","refuted","failed"):
            fact("RecordedProblem",(nid,),("Node",),(n,))
        if not n.evidence:findings.append({"code":"no_attached_evidence","kind":"node","ref":n.ref.model_dump(mode="json"),"meaning":"No attached evidence in this snapshot; not a claim that no substantiation exists."})
    excluded=[]
    for e in snapshot.edges:
        p,relation=edge_profiles[e.ref];name="Edge_"+identity((p.digest,e.relation))[:32]
        vocabulary[name]={"ontology_digest":p.digest,"relation":e.relation,"necessary_dependency":relation.necessary_dependency}
        fact(name,(eids[e.ref],nids[e.source_id],nids[e.target_id]),("Edge","Node","Node"),(e,))
        if not e.evidence:findings.append({"code":"no_attached_evidence","kind":"edge","ref":e.ref.model_dump(mode="json"),"meaning":"No attached evidence in this snapshot; not a claim that no substantiation exists."})
        if relation.necessary_dependency:
            items=(e,nodes[e.source_id],nodes[e.target_id])
            disposition=dependency_disposition(e,nodes)
            if disposition=="excluded":
                excluded.append(e.ref.model_dump(mode="json"));continue
            # The edge is the asserted relation. Endpoint status does not erase
            # an obligation; a proposed endpoint still makes the path conditional.
            origin="assumed" if disposition=="recorded" else "proposed"
            fact("Depends",(nids[e.source_id],nids[e.target_id]),("Node","Node"),items,origin)
    for name,arity in (("Depends",2),("Reachable",2),("DependencyCycle",1),("OpenObligation",1),
                       ("HasOpenDependency",1),("RecordedProblem",1),("HasProblemDependency",1)):
        predicates[name]=Predicate(name=name,argument_types=("Node",)*arity)
    graph=GraphSnapshot(graph_id="okf_analysis",context_id="scoped_metadata",entities=entities,
        predicates=tuple(predicates.values()),assertions=tuple(assertions),rules=RULES,
        policy=ContextPolicy(allow_assumptions=True,max_facts=context.max_facts,max_matches=context.max_matches,
            max_rounds=context.max_rounds,timeout_seconds=context.timeout_seconds))
    return graph,{"entities":bindings,"vocabulary":vocabulary,"assertion_sources":attribution,
        "rules":{r.id:r.model_dump(mode="json") for r in RULES},"ontology_digests":sorted(profiles),
        "coverage_diagnostics":coverage,"findings":findings,"excluded_dependency_edges":excluded}


def persist_graph_outcome(store,request,context,data,status,receipt_ids,*,version=VERSION,
                          record_prefix="GraphAnalysis",artifact_kind="graph_analysis",target=None):
    """Shared graph-tool audit publication; never admission into a research graph."""
    scope=dict(corpus_id=context.corpus_id,project_id=context.project_id)
    blob=canonical({"version":version,"request":request,"data":data,"status":status,"receipt_ids":receipt_ids,**scope})
    artifact=store.artifact(blob)
    outcome=store.put(Record(kind=record_prefix+"Outcome",**scope,content={"operation_id":request.operation_id,"artifact_id":artifact,"status":status}))
    if CorpusRegistry(store).corpus(context.corpus_id):
        heads=[RegistryRevision.model_validate(r.content) for _,r in store.records("SystemRegistryRevision",corpus_id=context.corpus_id)]
        head=max(heads,key=lambda r:r.sequence) if heads else None
        rev=RegistryRevision(revision_id=identity((version,artifact)),corpus_id=context.corpus_id,
            sequence=head.sequence+1 if head else 0,parent_revision=head.revision_id if head else None,changed_resource_ids=(artifact,))
        CorpusRegistry(store).register_revision(rev)
        ArtifactService(store).publish(blob,ArtifactEnvelope(artifact_id=artifact,content_hash=artifact,artifact_kind=artifact_kind,
            media_type="application/json",provenance=(outcome,),**scope),registry_revision=rev.revision_id)
    progress={"authority":"proposal_only",**scope,"operation_id":request.operation_id,"outcome_record_id":outcome,
        "artifact_id":artifact,"target_record_id":request.target_record_id,"parent_record_id":request.parent_record_id,
        "expected_graph_revision":request.graph_revision.model_dump(mode="json"),"attempt_status":status,"receipt_ids":receipt_ids,
        "project_recording":{"status":"pending","commit_receipt_id":None},"scientific_admission":False}
    if target is not None:progress["graph_target"]=target
    ref=store.put(Record(kind=record_prefix+"ProgressProposal",**scope,content=progress,parents=(outcome,)))
    return artifact,{"record_id":ref,**progress}


def analyze_graph(store,request,context):
    request=AnalyzeGraphRequest.model_validate(request.model_dump(mode="json"))
    context=AnalyzeGraphContext.model_validate(context.model_dump(mode="json"))
    if request.mode=="preview":return ToolResult(operation="Analyze Graph",status="complete",data={"executed":False,"request":request.model_dump(mode="json"),"policy_digest":POLICY_DIGEST})
    if store is None or not context.allow_audit_writes:return ToolResult(operation="Analyze Graph",status="failed",diagnostics=({"code":"audit_or_store_unavailable"},))
    scope=dict(corpus_id=context.corpus_id,project_id=context.project_id);receipts=ExecutionReceiptService(store)
    rid=identity({"stage":"analyze_graph","operation_id":request.operation_id,**scope})
    fingerprint=identity({"version":VERSION,"policy":POLICY_DIGEST,"request":request,"context":context})
    previous=receipts.replay(rid,request_hash=fingerprint,**scope)
    if previous:return ToolResult.model_validate(previous.metadata["result"])
    data={"version":VERSION,"policy_digest":POLICY_DIGEST,"scientific_acceptance":False,"source_fidelity_verified":False,
        "analysis_scope":"candidate_only" if request.artifact_id else "stored_corpus_and_project_snapshot"}
    children=[];interrupted=None
    try:
        if not CorpusRegistry(store).corpus(context.corpus_id):raise ValueError("registered corpus required")
        if request.run_id and ResearchRunService(store).get_run(request.run_id,**scope) is None:raise ConflictError("run outside scope")
        for ref in (request.target_record_id,request.parent_record_id):
            if ref and (store.get(ref,**scope) is None or store.get(ref,**scope).project_id!=context.project_id):raise ConflictError("progress reference outside scope")
        ontology=OntologyService.from_store(store,**scope)
        snapshot=load_snapshot(store,request,context,ontology)
        graph,translation=translate_snapshot(snapshot,ontology,context)
        data.update(snapshot_id=snapshot.snapshot_id,snapshot_hash=identity(snapshot),graph_revision=request.graph_revision.model_dump(mode="json"),
            node_count=len(snapshot.nodes),edge_count=len(snapshot.edges),translation=translation,logical_graph=graph.model_dump(mode="json"),
            input_unresolved=snapshot.metadata.get("unresolved",[]),input_diagnostics=snapshot.metadata.get("diagnostics",[]))
        native=GraphReasoningRequest(graph=graph,**scope,graph_revision=request.graph_revision,mode=request.mode,
            operation_id=identity((rid,"reasoning")),run_id=request.run_id,input_ids=(snapshot.snapshot_id,))
        service=GraphReasoningService(store)
        child=identity({"stage":"graph_reasoning","operation_id":native.operation_id})
        try:result=service.execute(native)
        finally:
            if receipts.get(child,**scope):children.append(child)
        if store.graph_revision(**scope)!=request.graph_revision:raise ConflictError("graph changed during analysis")
        data["reasoning"]=result.model_dump(mode="json")
        # Native 'admitted' means admitted to this inference context only.
        data["conclusions"]=[{"atom":c.atom.model_dump(mode="json"),"entities":[translation["entities"][a] for a in c.atom.arguments],
            "support_ids":list(c.support_ids),"hypothesis_ids":list(c.hypothesis_ids),"conditional":conditional,
            "authority":"analysis_only"} for conditional,items in ((False,result.admitted_conclusions),(True,result.conditional_conclusions)) for c in items]
        data["excluded_proposed_assertions"]=[a.id for a in graph.assertions if a.origin=="proposed"] if request.mode=="strict" else []
        status="failed" if result.status=="failed" else "partial" if result.status=="partial" or translation["coverage_diagnostics"] else "complete"
    except (Exception,CancelledError,KeyboardInterrupt) as exc:
        interrupted=exc if isinstance(exc,(CancelledError,KeyboardInterrupt)) else None
        status="failed";data["error"]=type(exc).__name__
        data.pop("reasoning",None);data.pop("conclusions",None)
    terminal="interrupted" if interrupted else "failed" if status=="failed" else "completed" if status=="complete" else "partial"
    with store.joined_transaction():
        artifact,progress=persist_graph_outcome(store,request,context,data,terminal,[rid,*children]);data["project_progress"]=progress
        result=ToolResult(operation="Analyze Graph",status=status,data=data,receipt_ids=(rid,*children),artifacts={"analysis":artifact},
            note="Completion describes bounded graph analysis, not mathematical truth or source fidelity. Prose/properties are not executable logic; no conclusions are committed. Missing attached support is not evidence of absence.")
        receipts.record(ExecutionReceipt(receipt_id=rid,operation_id=request.operation_id,stage="analyze_graph",**scope,
            run_id=request.run_id,graph_revision=request.graph_revision,status=terminal,error=data.get("error"),tool_version=VERSION,
            output_ids=(artifact,),metadata={"request_hash":fingerprint,"result":result.model_dump(mode="json")}))
    if interrupted:raise interrupted
    return result
