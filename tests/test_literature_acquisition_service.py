from nima_semantica.literature_acquisition import (
    LiteratureAcquisitionRequest,
    LiteratureAcquisitionService,
)
from nima_semantica.models import AcquisitionPolicy
from nima_semantica.storage import GraphStore


def test_literature_service_persists_provenance_and_replays(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "nima_semantica.literature_acquisition.acquire",
        lambda url, policy: (b"source", "paper.txt", {"resolved_url": url, "status_code": 200}),
    )
    store = GraphStore(tmp_path)
    try:
        request = LiteratureAcquisitionRequest(
            corpus_id="papers", url="https://example.org/paper.txt",
            policy=AcquisitionPolicy(enabled=True, domains=("example.org",)),
            idempotency_key="literature-replay",
        )
        service = LiteratureAcquisitionService(store)
        first = service.execute(request)
        second = service.execute(request)
        assert first.status == second.status == "completed"
        assert first.result == second.result
        assert len(store.records("AcquisitionAttempt", corpus_id="papers")) == 1
        assert len(store.records("ExecutionReceipt", corpus_id="papers")) == 1
    finally:
        store.close()


def test_literature_service_records_policy_failures(tmp_path):
    store = GraphStore(tmp_path)
    try:
        result = LiteratureAcquisitionService(store).execute(
            LiteratureAcquisitionRequest(
                corpus_id="papers", url="https://example.org/paper.txt",
                policy=AcquisitionPolicy(enabled=False, domains=("example.org",)),
            )
        )
        assert result.status == "failed"
        assert result.diagnostics == ({"code": "NimaError"},)
        assert len(store.records("ExecutionReceipt", corpus_id="papers")) == 1
    finally:
        store.close()
