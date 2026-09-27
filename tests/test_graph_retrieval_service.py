import pytest

from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica import graph_retrieval
from nima_semantica.graph_retrieval import GraphRetrievalRequest, GraphRetrievalService
from nima_semantica.models import ConflictError, Record
from nima_semantica.retrieval_contracts import EmbeddingProjectionManifest, RetrievalContextPacket


def _packet(store, *, corpus_id="papers", project_id="project-a"):
    return RetrievalContextPacket(
        query="find evidence",
        corpus_id=corpus_id,
        project_id=project_id,
        graph_revision=store.graph_revision(corpus_id, project_id),
        projection=EmbeddingProjectionManifest(
            projection_id="projection-1",
            corpus_id=corpus_id,
            project_id=project_id,
            source_revision="source:1",
            provider="local",
            model="embedding-model",
            model_revision="v1",
            dimension=3,
        ),
    )


def test_graph_retrieval_service_rejects_stale_revision(store):
    request = GraphRetrievalRequest(
        request_id="retrieval-1",
        corpus_id="papers",
        project_id="project-a",
        graph_revision=GraphRevision(corpus_id="papers", corpus_revision=999, project_id="project-a", project_revision=0),
        query="find evidence",
    )
    with pytest.raises(ConflictError, match="stale graph revision"):
        GraphRetrievalService(store).execute(request, object())


def test_graph_retrieval_service_returns_typed_read_only_context(store, monkeypatch):
    packet = _packet(store)
    selection = Record(
        kind="SelectionRun",
        corpus_id="papers",
        project_id="project-a",
        content={"context_packet": packet.model_dump(mode="json")},
    )
    monkeypatch.setattr(
        graph_retrieval,
        "retrieve",
        lambda *args, **kwargs: ([{"id": "region-1", "text": "evidence"}], selection),
    )
    result = GraphRetrievalService(store).execute(
        GraphRetrievalRequest(
            request_id="retrieval-1",
            corpus_id="papers",
            project_id="project-a",
            graph_revision=store.graph_revision("papers", "project-a"),
            query="find evidence",
        ),
        object(),
    )

    assert result.request_id == "retrieval-1"
    assert result.context_packet == packet
    assert result.authority == "read_only_retrieval"
    assert result.selected[0]["id"] == "region-1"
