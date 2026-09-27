"""Shared proof-state and historical artifact support used by Draft Lean."""
from .models import ConflictError,canonical,identity
from .proof_contracts import ProofContextRequest
from .proof_state import ProofState,ProofItem,ProofPatch
from .review_tool import ReviewController
from .deep_extraction_tool import RejectedAction
from .graph_analysis import inspect_snapshot_ontology
from .evidence_contracts import require_source_region
from .research_tool_helpers import exact_anchors
from .research_contracts import ProofDAGContext
from .execution_receipts import ExecutionReceiptService
from .providers import strict_json_object
from .artifact_service import ArtifactService



def token(value):return identity(value)[:16]

def read_proof_artifact(store,aid,scope):
    envelope,raw=ArtifactService(store).read(aid,**scope)
    if envelope.artifact_kind!="proof_development" or len(raw)>2_000_000:raise ConflictError("not a bounded proof-development artifact")
    value=strict_json_object(raw.decode())
    req=ProofContextRequest.model_validate(value["request"])
    rid=identity({"stage":"develop_proof","operation_id":req.operation_id,**scope})
    receipt=ExecutionReceiptService(store).get(rid,**scope)
    if not receipt or aid not in receipt.output_ids or receipt.metadata.get("result",{}).get("data",{}).get("result")!=value["data"].get("result"):raise ConflictError("proof artifact lacks matching receipt")
    return value

class ProofController:
    state_type=ProofState
    read_regions=ReviewController.read_regions
    retrieve=ReviewController.retrieve

    def __init__(self,store,request,context,snapshot,ontology):
        self.store,self.request,self.context,self.snapshot,self.ontology=store,request,context,snapshot,ontology
        self.scope=dict(corpus_id=context.corpus_id,project_id=context.project_id);self.snapshot_hash=identity(snapshot)
        self.nodes,profiles,_,_,self.coverage=inspect_snapshot_ontology(snapshot,ontology)
        t=request.target
        if any(ref not in self.nodes for ref in (t.ref,*t.dependencies)):raise ConflictError("target/dependencies outside snapshot")
        self.dependencies={d.dependency_id:d.model_dump(mode="json") for d in request.dependencies}
        self.bound_artifacts={}
        for dep in request.dependencies:
            for aid in dep.artifact_ids:
                env,raw=ArtifactService(store).read(aid,**self.scope)
                if len(raw)>100000:raise RejectedAction("Narrow dependency evidence artifacts to 100000 bytes.")
                self.bound_artifacts[aid]={"envelope":env.model_dump(mode="json"),"content":raw.decode("utf-8")}
        self.dag=None
        if request.proof_dag_record_id:
            record=store.get(request.proof_dag_record_id,**self.scope)
            if not record or record.project_id!=context.project_id or record.kind!="ProofDAGContext":raise ConflictError("proof DAG unavailable")
            self.dag=ProofDAGContext.model_validate(record.content)
            if (self.dag.corpus_id,self.dag.project_id)!=(context.corpus_id,context.project_id):raise ConflictError("proof DAG scope mismatch")
            if self.dag.graph_revision!=request.graph_revision or self.dag.target_node_id!=t.ref or self.dag.status=="superseded":raise ConflictError("proof DAG target/revision mismatch")
            if not set(self.dag.node_ids)<=self.nodes.keys():raise ConflictError("proof DAG nodes unavailable")
            actual={(e.source_id,e.target_id) for e in snapshot.edges if e.relation=="proof_depends_on"}
            if any((d.source_node_id,d.target_node_id) not in actual for d in self.dag.dependencies):raise ConflictError("proof DAG dependency not in snapshot")
            if not set(t.dependencies)<=set(self.dag.node_ids):raise ConflictError("dependency absent from proof DAG")
            if self.dag.source_references or self.dag.formalization_artifact_ids or self.dag.environment_manifest_id:
                # These are not imported as proof certificates or evidence implicitly.
                for aid in self.dag.formalization_artifact_ids:ArtifactService(store).read(aid,**self.scope)
                for ref in self.dag.source_references:
                    if ref.reference_kind!="source_region":raise ConflictError("DAG source must be an exact source region")
                    r=require_source_region(store,ref.target_id,**self.scope)
                    if r.content["artifact_id"]!=ref.content_hash or r.content["source_revision"]!=ref.revision:raise ConflictError("DAG source differs")
        self.priors=[]
        for aid in request.prior_artifact_ids:
            prior=read_proof_artifact(store,aid,self.scope)
            if prior["request"]["target"]!=t.model_dump(mode="json"):raise ConflictError("prior proof target changed")
            self.priors.append({"artifact_id":aid,"historical_graph_revision":prior["request"]["graph_revision"],"status":prior["status"],
                "data":{k:prior["data"][k] for k in ("result","development_history","tests","error") if k in prior["data"]}})
        if len(canonical(self.priors))>200000:raise RejectedAction("Prior history exceeds bound; narrow prior selection.")
        regions=set(request.source_region_ids)|set(t.source_region_ids)|{e.region_id for e in self.nodes[t.ref].evidence}
        if self.dag:regions.update(r.target_id for r in self.dag.source_references)
        if len(regions)>context.max_read_regions:raise RejectedAction("Selected regions exceed operator bound.")
        self.inventory={r:require_source_region(store,r,**self.scope) for r in sorted(regions)}
        if any(len(r.content["text"])>20000 for r in self.inventory.values()):raise RejectedAction("Prepare narrower source regions.")
        self.read={};self.evidence_handles={};self.searches=[];self.steps={};self.history=[];self.tests=[];self.strategy=None;self.analysis=None;self.worker=None
        self.obligations=list(t.unresolved_obligations)
        for p in self.priors:
            self.obligations.extend(p["data"].get("result",{}).get("unresolved_obligations",[]))
        self.obligations=list(dict.fromkeys(self.obligations))
        self.state=self.state_type(store,**self.scope,attempt_id=request.operation_id,task=canonical({"request":request,"snapshot_hash":self.snapshot_hash}).decode(),allow_writes=True)
        self.revision=self.state.view()["revision"]
        self.apply([ProofItem(key="request",kind="request",text="Develop only the exact local target.",anchors=exact_anchors({"task":self.state.task[:16000]})),
            ProofItem(key="coverage",kind="coverage",text="Proof and source correspondence are unverified.",depends_on=("request",),facets={"state":"open"}),
            ProofItem(key="target",kind="target",text=canonical(t).decode(),depends_on=("request",)),
            *[ProofItem(key="dep_"+token(k),kind="dependency",text=canonical(d).decode(),depends_on=("target",),facets={"state":"open"}) for k,d in self.dependencies.items()]])

    def current(self):
        if self.store.graph_revision(**self.scope)!=self.request.graph_revision:raise ConflictError("graph changed during proof development")
        if self.state.view()["revision"]!=self.revision:raise ConflictError("private proof state changed")

    def apply(self,items):
        self.current();self.revision=self.state.apply(ProofPatch(base_revision=self.revision,items=tuple(items)))["revision"]

    def retain_evidence(self,packet,rid,search=False):
        items=[]
        for p in packet["passages"]:
            ref=p["region_id"];r=require_source_region(self.store,ref,**self.scope)
            if r.content["text"]!=p["text"]:raise ConflictError("source changed")
            self.inventory[ref]=r
            items.append(ProofItem(key="evidence_"+token(ref),kind="evidence",text="Exact region "+ref,depends_on=("request",),anchors=exact_anchors({rid:packet["source_text"][:16000]})))
        if items:self.apply(items)
        handles=[]
        for p in packet["passages"]:
            ref=p["region_id"];self.read[ref]={"text":p["text"],"receipt_id":rid}
            r=self.inventory[ref];evidence_id=identity({"proof_evidence_region":ref})
            handle={"evidence_id":evidence_id,"region_id":ref,"artifact_id":r.content["artifact_id"],"source_revision":r.content["source_revision"],"receipt_id":rid}
            self.evidence_handles[evidence_id]=handle;handles.append(handle)
        if search:self.searches.append({"receipt_id":rid,**packet})
        self.analysis=None
        return handles










