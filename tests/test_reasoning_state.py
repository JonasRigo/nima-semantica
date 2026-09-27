"""Deterministic governance tests, not a measure of extraction or math accuracy."""
import pytest

from nima_semantica.models import ConflictError, VerificationResult
from nima_semantica.reasoning_state import ReasoningState, ReasoningPatch
from nima_semantica.receipts import ExecutionReceipt

TASK = "Compute f with free index s. N is a scalar norm."


def state(store, **kwargs):
    return ReasoningState(store, corpus_id="papers", project_id="project-a", attempt_id="reasoning",
                          task=TASK, allow_writes=True, **kwargs)


def anchored(key="task", kind="obligation", **kwargs):
    return dict(key=key, kind=kind, text=TASK,
                anchors=[dict(source_id="task", start=0, end=len(TASK), quotation=TASK)], **kwargs)


def patch(s, items=(), constraints=()):
    return s.apply(dict(base_revision=s.view()["revision"], items=list(items), constraints=list(constraints)))


def initialize(s):
    return patch(s, [anchored(facets={"free_indices":"s"}),
        dict(key="answer",kind="claim",text="f(s)",depends_on=["task"],facets={"free_indices":"s"})],
        [dict(key="indices",source="task",target="answer",facet="free_indices")])


def receipt(s, *, rid="check", target="answer", outcome="verified", operation="reasoning", status="completed", fingerprint=None):
    value=ExecutionReceipt(receipt_id=rid,operation_id=operation,stage="test_checker",**s.scope,status=status,
        metadata={"reasoning_target": fingerprint or s.fingerprint(target),"outcome":outcome})
    s.receipts.record(value)
    return value


def checker(item, record):
    return VerificationResult(target_id=record.metadata["reasoning_target"],protocol="test_checker",
                              outcome=record.metadata["outcome"],scope="exact represented target")


def test_scope_persistence_okf_and_no_graph_admission(store):
    s=state(store); before=store.graph_revision("papers","project-a")
    view=initialize(s)
    assert state(store).view()==view
    snapshot=s.snapshot()
    assert {n.node_type for n in snapshot.nodes}=={"obligation","claim"}
    assert snapshot.edges[0].relation=="depends_on"
    assert all(n.status=="proposed" for n in snapshot.nodes)
    assert store.graph_revision("papers","project-a")==before
    records=dict(store.records("ReasoningInput",corpus_id="papers"))
    assert next(n for n in snapshot.nodes if n.properties["key"]=="task").provenance[0].target_id in records
    assert next(e for e in snapshot.edges if e.relation=="preserves").properties["facet"]=="free_indices"
    assert not view["correspondence_verified"] and view["unresolved_obligations"]==["task"]


@pytest.mark.parametrize("change",[{"status":"verified"},{"rules":["trust me"]},{"scope":"foreign"}])
def test_model_cannot_assign_authority_or_rules(change):
    with pytest.raises(ValueError):ReasoningPatch.model_validate(dict(base_revision="x",**change))


def test_exact_grounding_and_transaction_rollback(store):
    s=state(store); before=store.revision
    item=anchored(); item["anchors"][0]["quotation"]="invented requirement"
    with pytest.raises(ValueError,match="quotation"):patch(s,[item])
    assert store.revision==before
    item=anchored();item["anchors"]=[]
    with pytest.raises(ValueError,match="anchors"):patch(s,[item])


def test_denied_audit_write(store):
    s=ReasoningState(store,corpus_id="papers",project_id="p",attempt_id="a",task=TASK)
    before=store.revision
    with pytest.raises(PermissionError):initialize(s)
    assert store.revision==before


def test_stale_revision_changed_task_and_foreign_scope(store):
    s=state(store); old=s.view()["revision"];initialize(s)
    with pytest.raises(ConflictError):s.apply(dict(base_revision=old))
    with pytest.raises(ConflictError):ReasoningState(store,**s.scope,attempt_id="reasoning",task="changed").view()
    other=ReasoningState(store,corpus_id="papers",project_id="other",attempt_id="reasoning",task=TASK)
    assert not other.view()["items"] and other.view()["revision"]!=s.view()["revision"]


@pytest.mark.parametrize("deps",[["missing"],["answer"],["task","task"]])
def test_missing_cyclic_duplicate_dependencies(store,deps):
    s=state(store)
    with pytest.raises(ValueError):patch(s,[anchored(),dict(key="answer",kind="claim",text="x",depends_on=deps)])
    assert not s.view()["items"]


@pytest.mark.parametrize("facet,left,right",[
    ("object_type","scalar","matrix"), ("free_indices","s",""),
    ("definition","gamma/(m*tau)","gamma*tau/m"),
    ("environment","lean-pinned-A","lean-pinned-B"),
])
def test_represented_drift_blocks_finalization(store,facet,left,right):
    s=state(store)
    view=patch(s,[anchored(facets={facet:left}),dict(key="answer",kind="claim",text="bad",depends_on=["task"],facets={facet:right})],
        [dict(key="preserve",source="task",target="answer",facet=facet)])
    assert view["violations"]
    with pytest.raises(ValueError,match="constraints"):s.finalization(revision=view["revision"],target="answer",answer="bad")
    # Correcting the proposal does not delete the adverse prior revision.
    view=patch(s,[dict(key="answer",kind="claim",text="fixed",depends_on=["task"],facets={facet:left})])
    assert not view["violations"] and len(store.records(s.KIND))==2
    assert s.finalization(revision=view["revision"],target="answer",answer="fixed")["status"]=="partial"


def test_constraints_cannot_be_dropped_or_redefined(store):
    s=state(store);initialize(s)
    with pytest.raises(ValueError,match="cannot be changed"):
        patch(s,constraints=[dict(key="indices",source="answer",target="answer",facet="free_indices")])
    view=patch(s,[dict(key="answer",kind="claim",text="wrong",depends_on=["task"])])
    assert view["constraints"] and view["violations"]


def test_exact_answer_and_all_task_dependencies_required(store):
    s=state(store);v=initialize(s)
    with pytest.raises(ValueError,match="differs"):s.finalization(revision=v["revision"],target="answer",answer="other")
    v=patch(s,[dict(key="answer",kind="claim",text="f(s)",facets={"free_indices":"s"})])
    with pytest.raises(ValueError,match="omits"):s.finalization(revision=v["revision"],target="answer",answer="f(s)")


def test_successful_execution_is_not_verification(store):
    s=state(store);initialize(s);receipt(s)
    with pytest.raises(ValueError,match="unknown controller"):
        s.attach_check(base_revision=s.view()["revision"],target="answer",receipt_id="check",validator="test_checker")
    assert not s.view()["checks"]


def test_controller_check_and_transitive_invalidation(store):
    s=state(store,validators={"test_checker":checker});initialize(s)
    patch(s,[dict(key="middle",kind="claim",text="m",depends_on=["task"]),
             dict(key="answer",kind="claim",text="f(s)",depends_on=["middle"],facets={"free_indices":"s"})])
    receipt(s)
    v=s.attach_check(base_revision=s.view()["revision"],target="answer",receipt_id="check",validator="test_checker")
    assert not v["checks"][0]["stale"]
    v=patch(s,[anchored(facets={"free_indices":"s","assumption":"changed"})])
    assert v["checks"][0]["stale"]
    with pytest.raises(ValueError,match="current dependencies"):
        s.attach_check(base_revision=v["revision"],target="answer",receipt_id="check",validator="test_checker")
    v=patch(s,[anchored(facets={"free_indices":"s"})])
    assert v["checks"][0]["stale"], "reverting text must not silently revive old revision acceptance"


@pytest.mark.parametrize("kwargs",[dict(operation="foreign"),dict(status="failed"),dict(fingerprint="wrong")])
def test_check_receipt_boundary(store,kwargs):
    s=state(store,validators={"test_checker":checker});initialize(s);receipt(s,**kwargs)
    with pytest.raises(ValueError,match="bound"):
        s.attach_check(base_revision=s.view()["revision"],target="answer",receipt_id="check",validator="test_checker")


def test_refuted_check_cannot_be_overruled_by_accepting_prose(store):
    s=state(store,validators={"test_checker":checker});initialize(s);receipt(s,outcome="refuted")
    v=s.attach_check(base_revision=s.view()["revision"],target="answer",receipt_id="check",validator="test_checker")
    with pytest.raises(ValueError,match="refutations"):s.finalization(revision=v["revision"],target="answer",answer="f(s)")


def test_unexpressed_math_remains_admissible_and_unresolved(store):
    s=state(store);v=patch(s,[anchored(),dict(key="answer",kind="claim",text="An arbitrary topological construction",depends_on=["task"])])
    result=s.finalization(revision=v["revision"],target="answer",answer="An arbitrary topological construction")
    assert not result["mathematically_verified"] and result["unresolved_obligations"]


def test_extraction_is_not_semantic_verification(store):
    s=state(store)
    # An exact quote does not establish that its extracted interpretation is right.
    item=anchored(facets={"object_type":"wrong interpretation"})
    v=patch(s,[item])
    assert not v["correspondence_verified"] and v["authority"]=="proposal_only"


@pytest.mark.parametrize("operation,status",[("reasoning","completed"),("foreign","completed"),("reasoning","failed")])
def test_execution_anchor_binds_to_successful_same_attempt(store,operation,status):
    s=state(store)
    s.receipts.record(ExecutionReceipt(receipt_id="execution",operation_id=operation,stage="comparison_execution",**s.scope,
        status=status,metadata={"output":{"outcome":"executed","exit_code":0,"stdout":"exact observed result"}}))
    item=dict(key="observation",kind="claim",text="proposed interpretation",anchors=[
        dict(source_id="execution",start=0,end=21,quotation="exact observed result")])
    if operation=="reasoning" and status=="completed":
        assert patch(s,[item])["items"]["observation"]
    else:
        with pytest.raises(ValueError):patch(s,[item])


def test_revision_write_is_atomic_with_input_record(store,monkeypatch):
    s=state(store);before=store.revision;original=type(store).put
    def fail(self,record):
        if record.kind=="ReasoningStateRevision":raise RuntimeError("storage failed")
        return original(self,record)
    monkeypatch.setattr(type(store),"put",fail)
    with pytest.raises(RuntimeError):initialize(s)
    assert store.revision==before and not store.records("ReasoningInput")


def test_unique_quote_resolves_without_guessing_offsets(store):
    s=state(store);item=anchored();item["anchors"]=[dict(source_id="task",quotation="free index s")]
    v=patch(s,[item]);anchor=v["items"]["task"]["anchors"][0]
    assert TASK[anchor["start"]:anchor["end"]]=="free index s"


@pytest.mark.parametrize("quotation",[" ","not in the task"])
def test_ambiguous_or_missing_quote_is_not_repaired_silently(store,quotation):
    s=state(store);item=anchored();item["anchors"]=[dict(source_id="task",quotation=quotation)]
    with pytest.raises(ValueError):patch(s,[item])


def test_attempt_namespace_in_export_and_shared_dag_fingerprints(store):
    s=state(store);initialize(s)
    other=ReasoningState(store,**s.scope,attempt_id="another",task=TASK,allow_writes=True);initialize(other)
    assert not {n.node_id for n in s.snapshot().nodes} & {n.node_id for n in other.snapshot().nodes}
    items=[dict(key=f"c{i}",kind="claim",text=str(i),depends_on=[f"c{j}" for j in range(i)]) for i in range(30)]
    patch(s,items)
    assert len(s.fingerprint("c29"))==64
