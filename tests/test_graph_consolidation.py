"""Cross-paper identity, provenance, scope, replay and graph admission acceptance."""
import pytest
from nima_semantica.deep_extraction_tool import deep_extraction
from nima_semantica.extraction_contracts import DeepExtractionRequest
from nima_semantica.okf_contracts import OKFDelta
from nima_semantica.artifact_service import ArtifactService
from nima_semantica.project_update import update_project_graph, UpdateProjectRequest, UpdateProjectContext
from test_deep_extraction import context, region, request, Model, actions
from test_source_pipeline import store


def extract(store, text, ordinal):
    r = region(store, text)
    result = deep_extraction(store, request(r, operation_id=f"extract-{ordinal}"), context(), model=Model(actions(r)))
    assert result.artifacts.get("graph_proposal"), result
    return result.artifacts["graph_proposal"]


def call(store, artifacts, mode="consolidation_plan", operation_id="consolidate", **kwargs):
    selection = {"artifact_ids": artifacts, **kwargs}
    return deep_extraction(store, DeepExtractionRequest(mode=mode, operation_id=operation_id if mode=="consolidate" else None,
        graph_revision=store.graph_revision("papers", "research"), consolidation=selection), context())


def test_twenty_sources_consolidate_not_label_merge(store):
    artifacts = [extract(store, f"Paper {i}: A claim requires an obligation.", i) for i in range(20)]
    before = store.graph_revision("papers", "research")
    plan = call(store, artifacts, limit=128)
    assert plan.status == "complete", plan.model_dump_json()
    assert plan.data["total_nodes"] == 40
    assert plan.data["structure"]["connected_components"] == 20
    nodes = plan.data["nodes"]
    common = [n["node_id"] for n in nodes if n["node_type"] == "obligation"]
    result = call(store, artifacts, "consolidate", plan_hash=plan.data["plan_hash"], merges=[{
        "node_ids": common, "canonical_id": common[0], "rationale": "Each fixture explicitly names the same obligation."}])
    assert result.artifacts.get("graph_proposal"), result
    assert result.data["node_count"] == 21 and result.data["structure"]["connected_components"] == 1
    assert store.graph_revision("papers", "research") == before
    _, blob = ArtifactService(store).read(result.artifacts["graph_proposal"], corpus_id="papers", project_id="research")
    delta = OKFDelta.model_validate_json(blob)
    merged = next(n for n in delta.upsert_nodes if n.node_id == common[0])
    assert len(merged.evidence) == 20 and len(merged.properties["consolidation_variants"]) == 20
    from nima_semantica.graph_analysis import analyze_graph, AnalyzeGraphRequest, AnalyzeGraphContext
    analysis = analyze_graph(store, AnalyzeGraphRequest(mode="strict", operation_id="consolidated-proposal-analysis",
        graph_revision=before, artifact_id=result.artifacts["graph_proposal"]),
        AnalyzeGraphContext(corpus_id="papers", project_id="research", allow_audit_writes=True))
    assert analysis.status == "complete", analysis.model_dump_json()
    assert analysis.data["input_unresolved"] == delta.metadata["consolidation"]["unresolved"]
    assert analysis.data["input_unresolved"]
    replay = call(store, artifacts, "consolidate", plan_hash=plan.data["plan_hash"], merges=[{
        "node_ids": common, "canonical_id": common[0], "rationale": "Each fixture explicitly names the same obligation."}])
    assert replay == result
    prep = update_project_graph(store, UpdateProjectRequest(mode="prepare", operation_id="prep", graph_revision=before,
        artifact_id=result.artifacts["graph_proposal"]), UpdateProjectContext(corpus_id="papers", project_id="research"))
    assert prep.status == "complete", prep


def test_plan_pagination_and_rejection(store):
    a = extract(store, "Paper A claim requires an obligation.", 0)
    b = extract(store, "Paper B claim requires an obligation.", 1)
    planned = call(store, [a,b], limit=1)
    assert planned.status == "complete", planned.model_dump_json()
    keys = []
    while True:
        keys.extend(n["node_id"] for n in planned.data["nodes"])
        if not planned.data["next_request"]:break
        planned = deep_extraction(store, DeepExtractionRequest.model_validate(planned.data["next_request"]), context())
    assert len(keys) == len(set(keys)) == 4
    bad = call(store, [a,b], "consolidate", plan_hash="0"*64)
    assert bad.status == "failed" and not bad.artifacts
    mixed = call(store, [a,b], limit=128)
    n = mixed.data["nodes"]
    bad = call(store, [a,b], "consolidate", plan_hash=mixed.data["plan_hash"], merges=[{
        "node_ids":[n[0]["node_id"],next(v["node_id"] for v in n if v["node_type"] != n[0]["node_type"])],
        "canonical_id":n[0]["node_id"],"rationale":"Invalid mixed types"}])
    assert bad.status == "failed" and not bad.artifacts


def test_incremental_lineage_and_overlapping_inputs(store):
    a = extract(store, "Paper A claim requires an obligation.", 0)
    b = extract(store, "Paper B claim requires an obligation.", 1)
    p = call(store, [a])
    saved = call(store, [a], "consolidate", plan_hash=p.data["plan_hash"])
    candidate = saved.artifacts["graph_proposal"]
    p = call(store, [candidate, b])
    assert p.status == "complete", p
    assert p.data["total_nodes"] == 4
    assert {n["node_id"] for n in call(store, [candidate]).data["nodes"]} <= {n["node_id"] for n in p.data["nodes"]}
    rejected = call(store, [candidate, a])
    assert rejected.status == "failed" and "Overlapping" in rejected.diagnostics[0]["message"]


def test_planning_is_read_only_and_preserves_distinct_equal_labels(store):
    a = extract(store, "Paper A claim requires an obligation.", 0)
    b = extract(store, "Paper B claim requires an obligation.", 1)
    before = store.revision
    p = call(store, [a, b])
    assert p.data["total_nodes"] == 4 and store.revision == before
    denied = deep_extraction(store, DeepExtractionRequest(mode="consolidate", operation_id="denied",
        graph_revision=store.graph_revision("papers", "research"), consolidation={"artifact_ids":[a,b],"plan_hash":p.data["plan_hash"]}),
        context(allow_audit_writes=False, allow_model_calls=False, model_manifest=None))
    assert denied.status == "failed" and store.revision == before
    saved = call(store, [a,b], "consolidate", plan_hash=p.data["plan_hash"])
    assert saved.data["node_count"] == 4 and saved.data["structure"]["connected_components"] == 2


def test_links_grounding_and_ontology_guards(store):
    a = extract(store, "Paper A claim requires an obligation.", 0)
    b = extract(store, "Paper B claim requires an obligation.", 1)
    p = call(store, [a,b])
    nodes = p.data["nodes"]
    claim = next(n for n in nodes if n["node_type"] == "claim")
    target = next(n for n in nodes if n["node_type"] == "obligation" and n["origins"][0]["artifact_id"] != claim["origins"][0]["artifact_id"])
    link = dict(source_id=claim["node_id"], target_id=target["node_id"], relation="requires",
        region_ids=[claim["evidence"][0]["region_id"]], rationale="Fixture explicitly establishes this requirement.")
    invalid = call(store, [a,b], "consolidate", plan_hash=p.data["plan_hash"], links=[link | {"region_ids":["missing"]}])
    assert invalid.status == "failed" and not invalid.artifacts
    invalid = call(store, [a,b], "consolidate", plan_hash=p.data["plan_hash"], links=[link | {"relation":"invented"}])
    assert invalid.status == "failed" and not invalid.artifacts
    valid = call(store, [a,b], "consolidate", plan_hash=p.data["plan_hash"], links=[link])
    assert valid.artifacts and valid.data["structure"]["connected_components"] == 1


def test_foreign_run_and_candidate_are_rejected_without_model_calls(store):
    a = extract(store, "Paper A claim requires an obligation.", 0)
    req = DeepExtractionRequest(mode="consolidation_plan", graph_revision=store.graph_revision("papers", "research"),
        run_id="not-registered", consolidation={"artifact_ids":[a]})
    before = store.revision
    result = deep_extraction(store, req, context())
    assert result.status == "failed" and store.revision == before
    result = deep_extraction(store, req.model_copy(update={"run_id":None,"graph_revision":store.graph_revision("papers", "foreign")}), context(project_id="foreign"))
    assert result.status == "failed" and store.revision == before


def test_large_candidate_commit_then_complete_analysis(store):
    from nima_semantica.okf_contracts import OKFNode
    from nima_semantica.artifact_contracts import ArtifactEnvelope
    from nima_semantica.deep_extraction_tool import _revision
    from nima_semantica.models import identity, canonical
    from nima_semantica.graph_commit import OKFCommitApproval
    from nima_semantica.graph_analysis import analyze_graph, AnalyzeGraphRequest, AnalyzeGraphContext
    scope = dict(corpus_id="papers", project_id="research")
    revision = store.graph_revision(**scope)
    delta = OKFDelta(delta_id="large-fixture", base_revision=revision, **scope, ontology_profile="literature_evidence@1.0.0",
        upsert_nodes=tuple(OKFNode(node_id=f"n{i}", node_type="claim", **scope,
            ontology_profile="literature_evidence@1.0.0", properties={"text":"x"*4000}) for i in range(1100)),
        reason="Operator-authorized large graph regression; no scientific acceptance.")
    digest = identity(delta)
    assert len(canonical(delta)) > 4_000_000
    _revision(store, "papers", "large-fixture-registry", (digest,))
    ArtifactService(store).publish(canonical(delta), ArtifactEnvelope(artifact_id=digest, content_hash=digest,
        artifact_kind="graph_candidate", media_type="application/json", **scope, status="proposed"), registry_revision="large-fixture-registry")
    prep = update_project_graph(store, UpdateProjectRequest(mode="prepare", operation_id="large-prepare",
        graph_revision=revision, artifact_id=digest), UpdateProjectContext(**scope))
    assert prep.status == "complete", prep.diagnostics
    approval = OKFCommitApproval.for_delta(delta, approval_id="large-approval", approved_by="test", rationale="Test fixture only.")
    committed = update_project_graph(store, UpdateProjectRequest(mode="commit", operation_id="large-commit",
        graph_revision=revision, artifact_id=digest), UpdateProjectContext(**scope, actor="test", allow_graph_writes=True, allow_audit_writes=True, approval=approval))
    assert committed.data["project_recording"]["status"] == "committed", committed
    analysis = analyze_graph(store, AnalyzeGraphRequest(mode="strict", operation_id="large-analysis", graph_revision=store.graph_revision(**scope)),
        AnalyzeGraphContext(**scope, allow_audit_writes=True))
    assert analysis.status == "complete", analysis.data.get("error")
    assert analysis.data["node_count"] == 1100
    assert analysis.data["structure"]["connected_components"] == 1100
    assert analysis.data["reasoning"]["inference"]["complete"] is True


def test_incremental_merge_keeps_existing_project_identity(store):
    from nima_semantica.graph_commit import OKFCommitApproval
    from nima_semantica.graph_service import GraphService
    a = extract(store, "Paper A claim requires an obligation.", 0)
    p = call(store, [a])
    first = call(store, [a], "consolidate", plan_hash=p.data["plan_hash"])
    _, blob = ArtifactService(store).read(first.artifacts["graph_proposal"], corpus_id="papers", project_id="research")
    delta = OKFDelta.model_validate_json(blob)
    GraphService(store).commit_delta(delta, OKFCommitApproval.for_delta(delta, approval_id="admit-first", approved_by="test", rationale="Isolated fixture."))
    b = extract(store, "Paper B claim requires an obligation.", 1)
    p = call(store, [b])
    old = next(n for n in p.data["nodes"] if n["node_type"] == "obligation" and n["existing_project_node"])
    new = next(n for n in p.data["nodes"] if n["node_type"] == "obligation" and not n["existing_project_node"])
    merge = dict(node_ids=[old["node_id"],new["node_id"]], canonical_id=new["node_id"], rationale="Same fixture obligation.")
    rejected = call(store, [b], "consolidate", operation_id="add-second", plan_hash=p.data["plan_hash"], merges=[merge])
    assert rejected.status == "failed" and "existing project identity" in rejected.diagnostics[0]["message"]
    saved = call(store, [b], "consolidate", operation_id="add-second", plan_hash=p.data["plan_hash"], merges=[merge | {"canonical_id":old["node_id"]}])
    assert saved.artifacts and saved.data["node_count"] == 3
    _, blob = ArtifactService(store).read(saved.artifacts["graph_proposal"], corpus_id="papers", project_id="research")
    final = OKFDelta.model_validate_json(blob)
    assert old["node_id"] in {n.node_id for n in final.upsert_nodes}
    assert new["node_id"] not in {n.node_id for n in final.upsert_nodes}
    assert len(next(n for n in final.upsert_nodes if n.node_id == old["node_id"]).evidence) == 2


@pytest.mark.parametrize("reference_kind", ["endpoint", "parent"])
def test_corpus_reference_cannot_be_renamed_as_same_named_project_node(store, reference_kind):
    from nima_semantica.deep_extraction_tool import _revision
    from nima_semantica.models import identity, canonical
    a = extract(store, "Paper A claim requires an obligation.", 0)
    env, blob = ArtifactService(store).read(a, corpus_id="papers", project_id="research")
    delta = OKFDelta.model_validate_json(blob)
    target = delta.add_edges[0].target_id.model_copy(update={"project_id": None})
    if reference_kind == "endpoint":
        delta = delta.model_copy(update={"add_edges": (delta.add_edges[0].model_copy(update={"target_id": target}),)})
    else:
        delta = delta.model_copy(update={"upsert_nodes": (delta.upsert_nodes[0].model_copy(update={"parents": (target,)}), *delta.upsert_nodes[1:])})
    digest = identity(delta)
    registry = "cross-scope-" + reference_kind
    _revision(store, "papers", registry, (digest,))
    ArtifactService(store).publish(canonical(delta), env.model_copy(update={"artifact_id": digest, "content_hash": digest}), registry_revision=registry)
    result = call(store, [digest])
    assert result.status == "failed" and "self-contained" in result.diagnostics[0]["message"]


def test_link_output_budget_is_enforced(store, monkeypatch):
    import nima_semantica.graph_consolidation as module
    a = extract(store, "Paper A claim requires an obligation.", 0)
    p = call(store, [a])
    monkeypatch.setattr(module, "MAX_EDGES", 1)
    claim = next(n for n in p.data["nodes"] if n["node_type"] == "claim")
    target = next(n for n in p.data["nodes"] if n["node_type"] == "obligation")
    result = call(store, [a], "consolidate", plan_hash=p.data["plan_hash"], links=[dict(
        source_id=claim["node_id"], target_id=target["node_id"], relation="requires",
        region_ids=[claim["evidence"][0]["region_id"]], rationale="An additional separately interpreted relation exceeds this test budget.")])
    assert result.status == "failed" and "exceeds" in result.diagnostics[0]["message"]
