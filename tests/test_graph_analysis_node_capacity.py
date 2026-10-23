"""Node-budget bounds and native snapshot gate, without a scientific-store write."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import nima_semantica.graph_analysis as ga


def test_operator_node_ceiling_and_default():
    assert ga.AnalyzeGraphContext(corpus_id="corpus").max_nodes == 16000
    assert ga.AnalyzeGraphContext(corpus_id="corpus", max_nodes=16000).max_nodes == 16000
    with pytest.raises(ValidationError):
        ga.AnalyzeGraphContext(corpus_id="corpus", max_nodes=16001)
    with pytest.raises(ValidationError):
        ga.AnalyzeGraphRequest(max_nodes=16000)


@pytest.mark.parametrize("count", [8001, 10001, 16000])
def test_native_snapshot_gate_accepts_expanded_budget(monkeypatch, count):
    snapshot = SimpleNamespace(nodes=range(count), edges=())
    monkeypatch.setattr(ga, "GraphService", lambda *a, **k: SimpleNamespace(read_snapshot=lambda **k: snapshot))
    monkeypatch.setattr(ga, "validate_snapshot_evidence", lambda *a: SimpleNamespace(valid=True))
    revision = ga.GraphRevision(corpus_id="corpus", project_id="beyond-iid-v2", corpus_revision=0, project_revision=47)
    store = SimpleNamespace(graph_revision=lambda **k: revision)
    request = ga.AnalyzeGraphRequest(mode="strict", operation_id="node-capacity-check", graph_revision=revision)
    context = ga.AnalyzeGraphContext(corpus_id="corpus", project_id="beyond-iid-v2", max_nodes=16000)
    assert ga.load_snapshot(store, request, context, None) is snapshot


@pytest.mark.parametrize("count,budget", [(16001, 16000), (8001, 8000)])
def test_native_snapshot_gate_still_rejects_over_budget(monkeypatch, count, budget):
    snapshot = SimpleNamespace(nodes=range(count), edges=())
    monkeypatch.setattr(ga, "GraphService", lambda *a, **k: SimpleNamespace(read_snapshot=lambda **k: snapshot))
    revision = ga.GraphRevision(corpus_id="corpus", project_id="beyond-iid-v2", corpus_revision=0, project_revision=47)
    store = SimpleNamespace(graph_revision=lambda **k: revision)
    request = ga.AnalyzeGraphRequest(mode="strict", operation_id="node-capacity-check", graph_revision=revision)
    context = ga.AnalyzeGraphContext(corpus_id="corpus", project_id="beyond-iid-v2", max_nodes=budget)
    with pytest.raises(ValueError, match="never silently truncate"):
        ga.load_snapshot(store, request, context, None)
