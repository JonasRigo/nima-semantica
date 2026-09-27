"""Private review OSA state; shared immutable revision and Semantica machinery."""
from typing import Literal
from pydantic import Field
from .models import StrictModel,identity
from .ontology_profiles import OntologyProfile,NodeType,RelationType
from .reasoning_state import ReasoningState,ReasoningItem
from .math_reasoning import PREDICATES,RULES
from .reasoning_kernel import Atom,Assertion,ContextPolicy,GraphSnapshot,infer

KINDS=("request","target","evidence","coverage","assessment","analysis","context","obligation","check","assumption")
PROFILE=OntologyProfile(name="review_attempt",version="1.0.0",
    node_types=tuple(NodeType(name=k,description="Controller-owned review "+k) for k in KINDS),
    relation_types=(RelationType(name="depends_on",source_types=KINDS,target_types=KINDS,
        description="Revision-bound review dependency",necessary_dependency=True),),
    required_node_types=("request","coverage"),
    instructions="Retain source coverage, exact quotation checks, assessment revisions and unresolved review issues. Never certify source fidelity or publish private state.")
POLICY_DIGEST=identity({"profile":PROFILE,"predicates":PREDICATES,"rules":RULES})


class ReviewItem(ReasoningItem):
    kind: Literal["request","target","evidence","coverage","assessment","analysis","context","obligation","check","assumption"]


class ReviewPatch(StrictModel):
    base_revision: str
    items: tuple[ReviewItem,...] = Field(min_length=1,max_length=64)
    constraints: tuple = Field(default=(),max_length=0)


class ReviewState(ReasoningState):
    KIND="ReviewAttemptRevision"
    profile=PROFILE
    item_model=ReviewItem
    patch_model=ReviewPatch
    task_grounded_kinds=("request",)

    def _input_record(self):
        record=super()._input_record()
        return record.model_copy(update={"kind":"ReviewAttemptInput","content":{**record.content,"policy_digest":POLICY_DIGEST}})

    def _source(self,source_id):
        if source_id=="task":return self.task
        receipt=self.receipts.get(source_id,**self.scope)
        if (receipt is None or receipt.operation_id!=self.attempt_id or receipt.status!="completed"
                or receipt.stage not in ("review_research_read_regions","review_research_retrieve_context")):
            raise ValueError("source is not a successful same-attempt read")
        return receipt.metadata["output"]["source_text"]

    def _snapshot(self,items,revision,constraints=None):
        if any(v["kind"] not in KINDS for v in items.values()):raise ValueError("unknown review state kind")
        if items and not set(PROFILE.required_node_types)<={v["kind"] for v in items.values()}:
            raise ValueError("review state lacks request/coverage")
        return {"revision":revision,"items":items,"edges":[{"source":k,"relation":"depends_on","target":d}
            for k,v in items.items() for d in v["depends_on"]],"policy_digest":POLICY_DIGEST,"publishable":False}

    def _view(self,head):
        view=super()._view(head)
        ids={k:"n"+identity(k)[:24] for k in head["items"]};facts=[]
        def fact(p,*keys):facts.append(Assertion(atom=Atom(predicate=p,arguments=tuple(ids[k] for k in keys)),
            origin="assumed",justification="Controller-observed review metadata, not scientific truth."))
        for key,item in head["items"].items():
            fact("Proposed",key)
            for dep in item["depends_on"]:fact("Depends",key,dep)
            if item["facets"].get("state")=="open":fact("Open",key)
            if item["facets"].get("state")=="blocked":fact("Blocked",key)
        report=infer(GraphSnapshot(graph_id="review_attempt",context_id="private_attempt",
            policy=ContextPolicy(allow_assumptions=True),entities={v:"Item" for v in ids.values()},
            predicates=PREDICATES,assertions=tuple(facts),rules=RULES))
        if not report.complete:raise ValueError("incomplete private-state inference")
        reverse={v:k for k,v in ids.items()}
        consequences={p:sorted(reverse[a.arguments[0]] for a in report.atoms if a.predicate==p) for p in ("Open","Blocked")}
        return {**view,"authority":"internal_attempt_state","publishable":False,"policy_digest":POLICY_DIGEST,
            "inference":report.model_dump(mode="json"),"consequences":consequences,"source_fidelity_verified":False}
