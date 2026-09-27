import json

import pytest

from nima_semantica.calculation import CalculationTask
from nima_semantica.mathematical_computation import (
    AlgebraSpec,
    AlgebraSymbol,
    MathematicalComputationRequest,
    MathematicalComputationService,
)
from nima_semantica.models import ConfigurationError
from nima_semantica.storage import GraphStore


class Worker:
    def __init__(self, outcome="executed"):
        self.outcome = outcome
        self.source = None

    def run(self, source, timeout):
        self.source = source
        return {"outcome": self.outcome, "exit_code": 0 if self.outcome == "executed" else 1,
                "stdout": json.dumps({"result": "A*B", "operation": "simplify"})}


def test_service_records_manifest_and_non_commutative_symbols(tmp_path):
    store = GraphStore(tmp_path)
    worker = Worker()
    try:
        request = MathematicalComputationRequest(
            task=CalculationTask(expression="A*B", operation="simplify"),
            algebra=AlgebraSpec(kind="non_commutative", symbols=(AlgebraSymbol(name="A", commutative=False), AlgebraSymbol(name="B", commutative=False)), rules=("A*B != B*A",)),
        )
        result = MathematicalComputationService(store, worker=worker).execute(request)
        assert result.status == "completed"
        assert result.backend_manifest["algebra"] == "non_commutative"
        assert "'A': False" in worker.source and "'B': False" in worker.source
        receipt = store.records("ExecutionReceipt", corpus_id="calculation_preview")[0][1]
        assert receipt.content["metadata"]["backend_manifest"]["rules"] == ["A*B != B*A"]
    finally:
        store.close()


def test_failed_worker_is_receipted(tmp_path):
    store = GraphStore(tmp_path)
    try:
        result = MathematicalComputationService(store, worker=Worker("timeout")).execute(MathematicalComputationRequest())
        assert result.status == "failed"
        assert result.diagnostics == ({"code": "timeout"},)
        assert store.records("ExecutionReceipt", corpus_id="calculation_preview")[0][1].content["status"] == "failed"
    finally:
        store.close()


def test_lean_is_owned_by_lean_service(tmp_path):
    store = GraphStore(tmp_path)
    try:
        with pytest.raises(ConfigurationError):
            MathematicalComputationService(store).execute(MathematicalComputationRequest(task=CalculationTask(backend="lean")))
    finally:
        store.close()
