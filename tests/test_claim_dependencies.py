"""Exact native dependency traversal, limit accounting and audit acceptance."""
from asyncio import CancelledError
import pytest
from test_source_pipeline import store
from nima_semantica.claim_dependencies import TraceDependenciesRequest,TraceDependenciesContext,trace_claim_dependencies
from nima_semantica.graph_service import GraphService
from nima_semantica.graph_commit import OKFCommitApproval
from nima_semantica.okf_contracts import OKFNode,OKFEdge,OKFDelta,GraphIdentity
from nima_semantica.models import ConflictError,Record


def ref(name,project="research"):return GraphIdentity(corpus_id="papers",project_id=project,local_id=name)
def context(**kw):return TraceDependenciesContext(**({"corpus_id":"papers","project_id":"research","allow_audit_writes":True}|kw))
def request(store,**kw):return TraceDependenciesRequest(**({"mode":"trace","operation_id":"trace-1","claim":ref("a"),"graph_revision":store.graph_revision("papers","research")}|kw))
def seed(store,links=(("a","b"),("b","c")),*,statuses=None,edge_status="supported",project="research",relation="requires"):
    names=sorted({"a",*(n for pair in links for n in pair)});statuses=statuses or {}
    nodes=tuple(OKFNode(node_id=n,node_type="claim" if n=="a" else "obligation",corpus_id="papers",project_id=project,
        status=statuses.get(n,"supported" if n=="a" else "unresolved")) for n in names)
    edges=tuple(OKFEdge(edge_id="e"+str(i),relation=relation,source_id=ref(a,project),target_id=ref(b,project),
        corpus_id="papers",project_id=project,status=edge_status) for i,(a,b) in enumerate(links))
    delta=OKFDelta(delta_id="fixture-"+str(store.revision),corpus_id="papers",project_id=project,base_revision=store.graph_revision("papers",project),
        ontology_profile="claim_obligation",upsert_nodes=nodes,add_edges=edges,reason="Scoped dependency fixture")
    GraphService(store).commit_delta(delta,OKFCommitApproval.for_delta(delta,approval_id=delta.delta_id,approved_by="fixture",rationale="Test graph"))


def run(store,**kw):return trace_claim_dependencies(store,request(store),context(**kw))
def ids(items):return [i["local_id"] for i in items]


def test_chain_paths_obligations_replay_and_no_graph_mutation(store):
    seed(store);head=store.graph_revision("papers","research");req=request(store)
    result=run(store);assert result.status=="complete",result
    trace=result.data["trace"]
    assert ids(trace["paths"][0]["nodes"])==["a","b","c"]
    assert ids(trace["paths"][0]["edges"])==["e0","e1"]
    assert ids(trace["structural_leaves"])==["c"]
    assert {v["node"]["local_id"] for v in trace["outstanding_obligations"]}=={"b","c"}
    assert trace["paths_complete"] and trace["cycle_search_complete"] and not trace["cycle_witnesses"]
    assert result.data["project_progress"]["graph_target"]==ref("a").model_dump(mode="json")
    assert result.data["project_progress"]["project_recording"]["status"]=="pending"
    assert store.graph_revision("papers","research")==head
    before=store.revision;assert trace_claim_dependencies(store,req,context())==result and store.revision==before
    with pytest.raises(ConflictError):trace_claim_dependencies(store,req.model_copy(update={"include_proposed":False}),context())


def test_preview_and_denied_writes(store):
    before=store.revision
    assert trace_claim_dependencies(None,TraceDependenciesRequest(),context()).data["executed"] is False
    assert trace_claim_dependencies(store,request(store),context(allow_audit_writes=False)).status=="failed"
    assert store.revision==before


@pytest.mark.parametrize("extra",[{"max_depth":999},{"corpus_id":"foreign"},{"allow_audit_writes":True},{"rules":[]},{"model":"evil"},{"direction":"reverse"}])
def test_public_authority_rejected(extra):
    with pytest.raises(ValueError):TraceDependenciesRequest.model_validate(extra)


def test_diamond_merge_and_parallel_edges_preserve_distinct_paths(store):
    seed(store,(("a","b"),("a","c"),("b","d"),("c","d"),("a","b")))
    trace=run(store).data["trace"]
    assert len(trace["paths"])==3 and all(ids(p["nodes"])[-1]=="d" for p in trace["paths"])
    assert len({tuple(ids(p["edges"])) for p in trace["paths"]})==3
    assert trace["cycle_witnesses"]==[] and ids(trace["structural_leaves"])==["d"]


@pytest.mark.parametrize("links",[(("a","a"),),(("a","b"),("b","c"),("c","b")),(("a","b"),("b","a"),("a","c"))])
def test_cycles_have_exact_closed_edge_witnesses(store,links):
    seed(store,links);trace=run(store).data["trace"]
    assert trace["cycle_witnesses"] and trace["cycle_search_complete"]
    for cycle in trace["cycle_witnesses"]:
        assert cycle["nodes"][0]==cycle["nodes"][-1]
        assert len(cycle["nodes"])==len(cycle["edges"])+1
    assert any(p["terminal"]=="cycle" for p in trace["paths"])


@pytest.mark.parametrize("limits,reason",[({"max_depth":1},"depth_limit"),({"max_visited_nodes":1},"node_limit"),
    ({"max_traversed_edges":0},"edge_limit"),({"max_steps":1},"step_limit")])
def test_cut_branches_are_not_leaves_or_cycle_free_claims(store,limits,reason):
    seed(store);result=run(store,**limits);trace=result.data["trace"]
    assert result.status=="partial" and trace["truncated"] and reason in trace["limits_reached"]
    assert not trace["paths_complete"] and not trace["cycle_search_complete"]
    assert not trace["structural_leaves"] and trace["frontier"]


def test_path_limit_retains_coverage_limit_and_exact_boundary(store):
    seed(store,(("a","b"),("a","c")))
    result=run(store,max_paths=1);assert result.status=="partial"
    assert len(result.data["trace"]["paths"])==1 and "path_limit" in result.data["trace"]["limits_reached"]
    assert result.data["trace"]["reachable_subgraph_complete"] and result.data["trace"]["unexplored_path_prefix_count"]==1
    assert run(store,max_paths=1)==result


def test_exact_budget_boundary_is_complete_when_nothing_is_left(store):
    seed(store,(("a","b"),))
    result=run(store,max_depth=1,max_visited_nodes=2,max_traversed_edges=1,max_paths=1)
    assert result.status=="complete",result


@pytest.mark.parametrize("include",[True,False])
def test_proposed_paths_conditional_and_filtered_branch_not_leaf(store,include):
    seed(store,edge_status="proposed")
    result=trace_claim_dependencies(store,request(store,include_proposed=include),context());trace=result.data["trace"]
    assert result.status=="complete"
    if include:
        assert trace["paths"][0]["conditional"] and len(trace["paths"][0]["proposed_edges"])==2
    else:
        assert trace["paths"][0]["terminal"]=="filtered_boundary" and not trace["structural_leaves"]
        assert trace["excluded_dependencies"][0]["reason"]=="proposed_relation_filtered"


def test_rejected_relation_and_problem_status_are_not_proof(store):
    seed(store,(("a","b"),),edge_status="refuted",statuses={"a":"failed"})
    trace=run(store).data["trace"]
    assert not trace["edges"] and not trace["structural_leaves"]
    assert trace["recorded_problems"][0]["recorded_status"]=="failed"
    assert trace["excluded_dependencies"][0]["reason"]=="recorded_rejected_relation"


@pytest.mark.parametrize("damage",["stale","foreign_revision","foreign_claim","missing_claim","run","target","input_limit"])
def test_failed_preflight_is_audited_without_scope_leak(store,damage):
    stale=request(store).graph_revision;seed(store);kw={};ctx=context()
    if damage=="stale":kw["graph_revision"]=stale
    if damage=="foreign_revision":kw["graph_revision"]=store.graph_revision("papers","private")
    if damage=="foreign_claim":seed(store,project="private");kw["claim"]=ref("a","private")
    if damage=="missing_claim":kw["claim"]=ref("missing")
    if damage=="run":kw["run_id"]="missing"
    if damage=="target":kw["target_record_id"]=store.put(Record(kind="Secret",corpus_id="papers",project_id="private",content={"text":"DO_NOT_LEAK"}))
    if damage=="input_limit":ctx=context(max_nodes=1)
    result=trace_claim_dependencies(store,request(store,**kw),ctx)
    assert result.status=="failed" and "trace" not in result.data and "DO_NOT_LEAK" not in result.model_dump_json()
    assert store.records("DependencyTraceOutcome") and store.records("DependencyTraceProgressProposal")


@pytest.mark.parametrize("error",[ValueError("secret"),CancelledError()])
def test_failed_and_cancelled_traversal_recorded(store,monkeypatch,error):
    seed(store)
    def fail(*a):raise error
    monkeypatch.setattr("nima_semantica.claim_dependencies.traverse_dependencies",fail)
    if isinstance(error,CancelledError):
        with pytest.raises(CancelledError):run(store)
    else:
        result=run(store);assert result.status=="failed" and "secret" not in result.model_dump_json()
    rows=[r for _,r in store.records("ExecutionReceipt") if r.content["stage"]=="trace_claim_dependencies"]
    assert len(rows)==1 and rows[0].content["status"]==("interrupted" if isinstance(error,CancelledError) else "failed")
    assert store.records("DependencyTraceProgressProposal")


def test_timeout_is_partial_and_does_not_invent_leaves(store,monkeypatch):
    seed(store);ticks=iter([0,31])
    monkeypatch.setattr("nima_semantica.claim_dependencies.time.monotonic",lambda:next(ticks,31))
    result=run(store);trace=result.data["trace"]
    assert result.status=="partial" and "timeout" in trace["limits_reached"] and not trace["structural_leaves"]


def test_corpus_project_identity_collision_and_direction(store):
    seed(store,project=None);seed(store)
    trace=trace_claim_dependencies(store,request(store,claim=ref("b",None)),context()).data["trace"]
    assert ids(trace["paths"][0]["nodes"])==["b","c"]
    assert all(n["project_id"] is None for p in trace["paths"] for n in p["nodes"])


def test_non_dependency_support_is_not_followed(store):
    seed(store,(("a","b"),),relation="supports")
    trace=run(store).data["trace"]
    assert ids(trace["structural_leaves"])==["a"] and not trace["edges"]


def test_deep_extraction_candidate_and_exact_evidence_guard(store,monkeypatch):
    from test_deep_extraction import region,actions,Model,request as er,context as ec
    from nima_semantica.deep_extraction_tool import deep_extraction
    r=region(store);extracted=deep_extraction(store,er(r),ec(),model=Model(actions(r)))
    req=request(store,claim=ref("claim"),artifact_id=extracted.artifacts["graph_proposal"])
    result=trace_claim_dependencies(store,req,context());trace=result.data["trace"]
    assert result.status=="complete" and trace["paths"][0]["conditional"]
    assert ids(trace["structural_leaves"])==["obligation"] and trace["input_unresolved"]
    original=store.read_artifact;source=trace["nodes"][0]["node"]["evidence"][0]["artifact_id"]
    monkeypatch.setattr(store,"read_artifact",lambda digest:b"corrupt" if digest==source else original(digest))
    failed=trace_claim_dependencies(store,req.model_copy(update={"operation_id":"corrupt"}),context())
    assert failed.status=="failed" and "trace" not in failed.data


def test_root_leaf_at_depth_zero_is_not_truncated_but_retains_ontology_gaps(store):
    seed(store,())
    result=run(store,max_depth=0,max_paths=1)
    trace=result.data["trace"]
    assert trace["paths_complete"] and not trace["truncated"] and ids(trace["structural_leaves"])==["a"]
    assert result.status=="partial" and trace["coverage_diagnostics"]


def test_graph_drift_invalidates_trace_but_retains_attempt(store,monkeypatch):
    from nima_semantica import claim_dependencies as module
    seed(store);original=module.traverse_dependencies
    def changed(*args):
        result=original(*args);seed(store,(("a","d"),));return result
    monkeypatch.setattr(module,"traverse_dependencies",changed)
    result=run(store)
    assert result.status=="failed" and "trace" not in result.data and store.records("DependencyTraceProgressProposal")


def test_longer_diamond_path_does_not_escape_path_depth_limit(store):
    seed(store,(("a","b"),("a","c"),("b","c"),("c","d")))
    result=run(store,max_depth=2);trace=result.data["trace"]
    assert result.status=="partial" and "path_depth_limit" in trace["limits_reached"]
    assert trace["reachable_subgraph_complete"]
    assert all(len(p["edges"])<=2 for p in trace["paths"])


def test_proposed_endpoint_taints_recorded_relation(store):
    seed(store,(("a","b"),),statuses={"b":"proposed"})
    trace=run(store).data["trace"]
    assert trace["paths"][0]["conditional"] and ids(trace["paths"][0]["proposed_edges"])==["e0"]


def test_large_input_snapshot_with_small_claim_closure_preserves_public_statuses(store):
    links = (("a", "b"), ("b", "c")) + tuple(
        (f"isolated-{i}", f"isolated-{i+1}") for i in range(600))
    seed(store, links)
    before = GraphService(store).read_snapshot(corpus_id="papers", project_id="research")
    assert len(before.nodes) > 256 and len(before.edges) > 512
    result = run(store)
    assert result.status == "complete", result
    trace = result.data["trace"]
    assert ids(trace["paths"][0]["nodes"]) == ["a", "b", "c"]
    assert len(trace["nodes"]) == 3 and len(trace["edges"]) == 2
    assert {item["recorded_status"] for item in trace["outstanding_obligations"]} == {"unresolved"}
    after = GraphService(store).read_snapshot(corpus_id="papers", project_id="research")
    assert before == after
    assert result.data["analysis_scope"] == "stored_corpus_and_project_snapshot"


def test_default_traversal_can_complete_a_closure_above_previous_ceiling(store):
    links = tuple((a, b) for i in range(200)
                  for a, b in (("a", f"branch-{i}"), (f"branch-{i}", f"leaf-{i}")))
    seed(store, links)
    result = run(store)
    trace = result.data["trace"]
    assert result.status == "complete" and not trace["truncated"]
    assert len(trace["nodes"]) == 401 and len(trace["edges"]) == 400
    assert len(trace["paths"]) == 200
    assert trace["reachable_subgraph_complete"] and trace["cycle_search_complete"]


def test_large_trace_still_reports_real_operator_cutoffs(store):
    seed(store, tuple(("a", f"leaf-{i}") for i in range(1100)))
    result = run(store, max_visited_nodes=1024)
    trace = result.data["trace"]
    assert result.status == "partial" and trace["truncated"]
    assert "node_limit" in trace["limits_reached"]
    assert len(trace["nodes"]) == 1024
    assert trace["frontier"] and not trace["reachable_subgraph_complete"]
    assert not trace["paths_complete"] and not trace["cycle_search_complete"]


@pytest.mark.parametrize("field,maximum", [
    ("max_nodes", 16000), ("max_edges", 24000),
    ("max_visited_nodes", 16000), ("max_traversed_edges", 16384),
    ("max_depth", 64), ("max_paths", 2048),
    ("max_steps", 100000), ("timeout_seconds", 120),
])
def test_trace_operator_ceiling_remains_bounded_and_not_public(field, maximum):
    assert getattr(context(**{field: maximum}), field) == maximum
    with pytest.raises(ValueError):
        context(**{field: maximum + 1})
    with pytest.raises(ValueError):
        TraceDependenciesRequest.model_validate({field: maximum})


def test_snapshot_loading_budget_can_still_be_lowered_by_operator(store):
    seed(store)
    result = trace_claim_dependencies(
        store, request(store, operation_id="lower-input-budget"), context(max_nodes=1))
    assert result.status == "failed" and "trace" not in result.data
    assert result.data["error"] == "ValueError"


def test_preview_and_tool_guide_report_operator_limits_without_granting_authority():
    from nima_semantica.claim_dependencies import TRACE_DEFAULT_LIMITS, TRACE_HARD_LIMITS
    from nima_semantica.tool_guide import ToolGuideRequest, tool_guide
    preview = trace_claim_dependencies(None, TraceDependenciesRequest(), context())
    assert {name: preview.data["limits"][name] for name in TRACE_DEFAULT_LIMITS} == TRACE_DEFAULT_LIMITS
    descriptor = tool_guide(ToolGuideRequest(tool="trace_claim_dependencies")).data["tools"][0]
    limits = descriptor["usage"]["operator_limits"]
    assert limits["defaults"] == TRACE_DEFAULT_LIMITS and limits["hard_ceilings"] == TRACE_HARD_LIMITS
    assert set(TRACE_DEFAULT_LIMITS).isdisjoint(descriptor["input_schema"]["properties"])


def test_full_16000_node_traversal_retains_independent_path_budget():
    from nima_semantica.claim_dependencies import traverse_dependencies
    from nima_semantica.okf_contracts import OKFSnapshot, GraphRevision
    from nima_semantica.ontology_services import OntologyService
    revision = GraphRevision(corpus_id="papers", project_id="research", project_revision=0)
    nodes = tuple(OKFNode(node_id=name, node_type="claim", corpus_id="papers", project_id="research",
        status="supported") for name in ("a", *(f"leaf-{i}" for i in range(15999))))
    edges = tuple(OKFEdge(edge_id=f"e{i}", relation="requires", source_id=ref("a"), target_id=ref(f"leaf-{i}"),
        corpus_id="papers", project_id="research", status="supported") for i in range(15999))
    snapshot = OKFSnapshot(snapshot_id="large-traversal", corpus_id="papers", project_id="research",
        graph_revision=revision, ontology_profile="claim_obligation", nodes=nodes, edges=edges)
    req = TraceDependenciesRequest(mode="trace", operation_id="full-traversal", graph_revision=revision, claim=ref("a"))
    trace = traverse_dependencies(snapshot, req, context(max_visited_nodes=16000, max_traversed_edges=16384,
        max_paths=1, timeout_seconds=120), OntologyService())
    assert len(trace["nodes"]) == 16000 and len(trace["edges"]) == 15999
    assert trace["reachable_subgraph_complete"]
    assert not trace["paths_complete"] and "path_limit" in trace["limits_reached"]
    assert not trace["outstanding_obligations"]
