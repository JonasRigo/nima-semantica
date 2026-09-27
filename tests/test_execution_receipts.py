import pytest

from nima_semantica.okf_contracts import GraphRevision, GraphIdentity
from nima_semantica.execution_receipts import ExecutionReceiptService
from nima_semantica.models import ConflictError
from nima_semantica.receipts import ExecutionReceipt
from nima_semantica.storage import GraphStore


def execution_receipt(status="completed", metadata=None):
    return ExecutionReceipt(
        receipt_id="receipt-1", operation_id="operation-1", stage="retrieval",
        corpus_id="papers", project_id="project-a", graph_revision=GraphRevision(corpus_id="papers", corpus_revision=0, project_id="project-a", project_revision=0),
        input_ids=("input-1",), status=status, metadata=metadata or {},
    )


def test_execution_receipt_service_persists_and_replays_attempts(tmp_path):
    store = GraphStore(tmp_path)
    try:
        service = ExecutionReceiptService(store)
        record_id = service.record(execution_receipt())
        assert service.record(execution_receipt()) == record_id
        loaded = service.get("receipt-1", corpus_id="papers", project_id="project-a")
        assert loaded is not None and loaded.graph_revision == execution_receipt().graph_revision
        assert store.read_okf_snapshot(corpus_id="papers", project_id="project-a").nodes == ()
    finally:
        store.close()


def test_execution_receipt_service_rejects_divergent_replay_and_stale_revision(tmp_path):
    store = GraphStore(tmp_path)
    try:
        service = ExecutionReceiptService(store)
        service.record(execution_receipt())
        with pytest.raises(ConflictError, match="different metadata"):
            service.record(execution_receipt(metadata={"attempt": 2}))
        with pytest.raises(ConflictError, match="stale store revision"):
            service.record(
                ExecutionReceipt(
                    receipt_id="receipt-2", operation_id="operation-2", stage="retrieval",
                    corpus_id="papers", project_id="project-a",
                ),
                expected_store_revision="sqlite:0",
            )
    finally:
        store.close()
