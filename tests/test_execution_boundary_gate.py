"""Native service boundary acceptance; no paid models or live corpus writes."""
import asyncio
from types import SimpleNamespace

import pytest

from conftest import seed_region
from nima_semantica.calculation import CalculationTask, SymbolicCheckInput, SymbolicWorker
from nima_semantica.execution_receipts import ExecutionReceiptService
from nima_semantica.graph_commit import OKFCommitApproval, commit_okf_delta
from nima_semantica.graph_extraction import GraphExtractionRequest, GraphExtractionService
from nima_semantica.lean_verification_service import LeanVerificationRequest, LeanVerificationService
from nima_semantica.mathematical_computation import MathematicalComputationRequest, MathematicalComputationService
from nima_semantica.models import ConflictError, NimaError, Record
from nima_semantica.okf_contracts import OKFDelta, OKFNode
from nima_semantica.storage import GraphStore
from nima_semantica.verification_service import VerificationRequest, VerificationService


def invocation(store, kind, callback, monkeypatch=None):
    task = CalculationTask(corpus_id="papers", project_id="project-a")
    if kind == "math":
        service = MathematicalComputationService(store, worker=SimpleNamespace(run=callback))
        request = MathematicalComputationRequest(task=task, operation_id="attempt")
    elif kind == "verification":
        service = VerificationService(store, symbolic_worker=SimpleNamespace(check=callback))
        request = VerificationRequest(operation="symbolic_check", corpus_id="papers", project_id="project-a",
                                      symbolic=SymbolicCheckInput(task=task, candidate="x**3/3"), operation_id="attempt")
    elif kind == "lean":
        service = LeanVerificationService(store, SimpleNamespace(verify=callback))
        request = LeanVerificationRequest(sources={"Submission": "theorem target : True := True.intro"},
            targets=("target",), imports=("Submission",), corpus_id="papers", project_id="project-a", operation_id="attempt")
    elif kind == "reasoning":
        from nima_semantica.graph_reasoning import GraphReasoningRequest, GraphReasoningService
        from test_graph_reasoning_service import logical_graph
        service = GraphReasoningService(store, backend=callback)
        request = GraphReasoningRequest(graph=logical_graph(), corpus_id="papers", project_id="project-a",
            graph_revision=store.graph_revision("papers", "project-a"), operation_id="attempt")
    elif kind == "literature":
        from nima_semantica.literature_acquisition import LiteratureAcquisitionRequest, LiteratureAcquisitionService
        from nima_semantica.models import AcquisitionPolicy
        monkeypatch.setattr("nima_semantica.literature_acquisition.acquire", callback)
        service = LiteratureAcquisitionService(store)
        request = LiteratureAcquisitionRequest(corpus_id="papers", project_id="project-a",
            url="https://example.org/paper", policy=AcquisitionPolicy(enabled=True, domains=("example.org",)), idempotency_key="attempt")
    elif kind == "embedding":
        from nima_semantica.embedding_index import EmbeddingIndexRequest, EmbeddingIndexService
        from test_embedding_index_service import MANIFEST
        region = seed_region(store)
        request = EmbeddingIndexRequest(corpus_id="papers", project_id="project-a", region_ids=(region.id,),
            manifest=MANIFEST, idempotency_key="attempt")
        service = EmbeddingIndexService(store)
        return lambda value=request: service.embed_and_index(value, SimpleNamespace(embed=callback)), request
    else:
        region = seed_region(store)
        service = GraphExtractionService(store)
        request = GraphExtractionRequest(corpus_id="papers", project_id="project-a",
            graph_revision=store.graph_revision("papers", "project-a"), registry_revision="registry:1",
            ontology_profile="claim_obligation", source_region_ids=(region.id,), question="Extract claims", idempotency_key="attempt")
        return lambda value=request: service.execute(value, callback), request
    return lambda value=request: service.execute(value), request


@pytest.mark.parametrize("kind", ["math", "verification", "lean", "extraction", "reasoning", "literature", "embedding"])
@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError, KeyboardInterrupt])
def test_failed_and_cancelled_attempts_survive_reopen(store, kind, error_type, monkeypatch):
    calls = []
    def fail(*args):
        calls.append(1)
        raise error_type("private provider message")
    execute, _ = invocation(store, kind, fail, monkeypatch)
    if error_type is RuntimeError:
        assert execute().status == "failed"
    else:
        with pytest.raises(error_type):
            execute()
    assert calls == [1]
    records = store.records("ExecutionReceipt", corpus_id="papers", project_id="project-a")
    assert len(records) == 1
    receipt = records[0][1].content
    assert receipt["status"] == ("failed" if error_type is RuntimeError else "interrupted")
    assert "private provider message" not in str(receipt)
    assert execute().status in ("failed", "interrupted")
    assert calls == [1]
    assert store.graph_revision("papers", "project-a").project_revision == 0
    store.close()
    reopened = GraphStore(store.root)
    try:
        saved = ExecutionReceiptService(reopened).get(receipt["receipt_id"], corpus_id="papers", project_id="project-a")
        assert saved is not None
        assert ExecutionReceiptService(reopened).get(receipt["receipt_id"], corpus_id="papers", project_id="project-b") is None
    finally:
        reopened.close()


@pytest.mark.parametrize("kind", ["math", "verification", "lean", "extraction", "reasoning", "literature", "embedding"])
def test_changed_request_cannot_reuse_attempt_id(store, kind, monkeypatch):
    calls = []
    def fail(*args):
        calls.append(1)
        raise RuntimeError("failure")
    execute, request = invocation(store, kind, fail, monkeypatch)
    assert execute().status == "failed"
    assert execute().status == "failed"
    field = "profile" if kind == "embedding" else "motivating_gap" if kind == "literature" else "run_id"
    changed = request.model_copy(update={field: "different-run"})
    with pytest.raises(ConflictError):
        execute(changed)
    assert len(calls) == 1


@pytest.mark.parametrize("revision_scope", ["project-b", "project-a"])
def test_math_rejects_wrong_or_stale_revision_before_worker(store, revision_scope):
    def forbidden(*args):
        pytest.fail("worker invoked before revision validation")
    revision = store.graph_revision("papers", revision_scope)
    if revision_scope == "project-a":
        revision = revision.model_copy(update={"project_revision": 9})
    request = MathematicalComputationRequest(task=CalculationTask(corpus_id="papers", project_id="project-a"), graph_revision=revision)
    assert MathematicalComputationService(store, worker=SimpleNamespace(run=forbidden)).execute(request).status == "failed"


@pytest.mark.parametrize("kind", ["lean", "extraction"])
@pytest.mark.parametrize("invalid", ["wrong-kind", "unregistered-source", "foreign-project"])
def test_invalid_sources_never_reach_worker(store, kind, invalid):
    def forbidden(*args):
        pytest.fail("invalid source reached worker")
    execute, request = invocation(store, kind, forbidden)
    if invalid == "foreign-project":
        region = seed_region(store, project_id="project-b")
    elif invalid == "wrong-kind":
        region = Record(kind="NotASourceRegion", corpus_id="papers", content={"text": "fake"})
        store.put(region)
    else:
        artifact = store.artifact(b"Unregistered source.")
        region = Record(kind="SourceRegion", corpus_id="papers", content={"source_id": "missing", "corpus_id": "papers",
            "artifact_id": artifact, "source_revision": artifact, "start": 0, "end": 20, "ordinal": 0, "text": "Unregistered source."})
        store.put(region)
    result = execute(request.model_copy(update={"source_region_ids": (region.id,)}))
    assert result.status == "failed"


@pytest.mark.parametrize("alteration", ["missing", "changed-delta", "foreign-project"])
def test_unapproved_graph_writes_leave_no_mutation(store, alteration):
    delta = OKFDelta(delta_id="delta", corpus_id="papers", project_id="project-a",
        base_revision=store.graph_revision("papers", "project-a"), ontology_profile="claim_obligation",
        upsert_nodes=(OKFNode(node_id="claim", node_type="claim", corpus_id="papers", project_id="project-a"),), reason="proposed")
    approval = OKFCommitApproval.for_delta(delta, approval_id="approval", approved_by="operator", rationale="reviewed")
    if alteration == "missing":
        approval = None
    elif alteration == "changed-delta":
        delta = delta.model_copy(update={"reason": "not approved"})
    else:
        approval = approval.model_copy(update={"project_id": "project-b"})
    before = store.revision
    with pytest.raises((ValueError, NimaError)):
        commit_okf_delta(store, delta, approval)
    assert store.revision == before
    assert store.read_okf_snapshot(corpus_id="papers", project_id="project-a").nodes == ()


def test_symbolic_worker_missing_runtime_never_executes_on_host(monkeypatch, tmp_path):
    import nima_semantica.calculation as module
    sentinel = tmp_path / "must-not-exist"
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("docker unavailable")
    monkeypatch.setattr(module.subprocess, "run", unavailable)
    with pytest.raises(NimaError, match="unavailable"):
        SymbolicWorker().run(f"open({str(sentinel)!r}, 'w').close()")
    assert not sentinel.exists()


@pytest.mark.parametrize("tamper", ["quotation", "content_hash", "source_revision", "locator"])
def test_invalid_provenance_commit_is_atomic(store, tamper):
    from nima_semantica.okf_contracts import EvidenceReference
    region = seed_region(store)
    reference = EvidenceReference(corpus_id="papers", region_id=region.id,
        artifact_id=region.content["artifact_id"], content_hash=region.content["artifact_id"],
        source_revision=region.content["source_revision"], quotation=region.content["text"])
    changes = {"quotation": "Invented", "content_hash": "0" * 64, "source_revision": "stale", "locator": {"start": 99}}
    reference = reference.model_copy(update={tamper: changes[tamper]})
    if tamper == "content_hash":
        reference = reference.model_copy(update={"artifact_id": changes[tamper]})
    delta = OKFDelta(delta_id="invalid-evidence", corpus_id="papers", project_id="project-a",
        base_revision=store.graph_revision("papers", "project-a"), ontology_profile="claim_obligation",
        upsert_nodes=(OKFNode(node_id="claim", node_type="claim", corpus_id="papers", project_id="project-a", evidence=(reference,)),), reason="proposed")
    approval = OKFCommitApproval.for_delta(delta, approval_id="approval", approved_by="operator", rationale="fixture")
    before = store.revision
    with pytest.raises(ConflictError, match="invalid evidence"):
        commit_okf_delta(store, delta, approval)
    assert store.revision == before
    assert not store.records("GraphCommitReceipt", corpus_id="papers")


def test_embedding_replay_binds_vector_bytes(store):
    from nima_semantica.embedding_index import EmbeddingIndexRequest, EmbeddingIndexService
    from test_embedding_index_service import MANIFEST
    region = seed_region(store)
    request = EmbeddingIndexRequest(corpus_id="papers", region_ids=(region.id,), manifest=MANIFEST, idempotency_key="vectors")
    service = EmbeddingIndexService(store)
    assert service.index(request, [[1.0, 0.0]]).status == "completed"
    with pytest.raises(ConflictError):
        service.index(request, [[0.0, 1.0]])


def test_lean_attempt_and_receipt_roll_back_together(store, monkeypatch):
    def fail(*args):
        raise RuntimeError("worker failure")
    execute, _ = invocation(store, "lean", fail)
    def unavailable(*args, **kwargs):
        raise OSError("injected receipt persistence failure")
    monkeypatch.setattr(ExecutionReceiptService, "record", unavailable)
    with pytest.raises(OSError):
        execute()
    assert not store.records("LeanProofAttempt", corpus_id="papers")


def test_embedding_provider_success_is_receipted_and_not_repeated(store):
    from nima_semantica.embedding_index import EmbeddingIndexRequest, EmbeddingIndexService
    from test_embedding_index_service import MANIFEST
    region = seed_region(store)
    calls = []
    def embed(profile, texts):
        calls.append(texts)
        return [[1.0, 0.0]], MANIFEST
    request = EmbeddingIndexRequest(corpus_id="papers", region_ids=(region.id,), manifest=MANIFEST, idempotency_key="provider")
    service = EmbeddingIndexService(store)
    first = service.embed_and_index(request, SimpleNamespace(embed=embed))
    assert first.status == "completed"
    assert service.embed_and_index(request, SimpleNamespace(embed=embed)) == first
    assert calls == [[region.content["text"]]]
    assert len(store.records("ExecutionReceipt", corpus_id="papers")) == 1
