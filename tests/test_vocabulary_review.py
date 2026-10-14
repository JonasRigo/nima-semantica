"""Advisory judgments preserve new concepts, evidence and unresolved dissent."""
import pytest
from test_source_pipeline import store
from nima_semantica.vocabulary_context import publish_snapshot, vocabulary_context
from nima_semantica.vocabulary_review import assess_vocabulary
from nima_semantica.vocabulary_contracts import VocabularyExtension, compatible_dump
from nima_semantica.proposal_service import ProposalService
from nima_semantica.models import ConflictError, identity
from test_vocabulary_context import snapshot


def extension(snap, **extra):
    return VocabularyExtension(proposal_id="new-concept", corpus_id="papers", project_id="research",
        base_snapshot_hash=snap.content_hash, task_id="task", kind="definition_challenge", term="alternative gap",
        definition="A new, explicitly provisional distinction.", motivation="Test a different conceptual interpretation.",
        related_entry_ids=("gap",), speculative_basis="Task-grounded hypothesis; evidence remains unresolved.",
        open_questions=("Determine whether this distinction is physically useful.",), **extra)


def test_provisional_extension_and_disagreement_remain_usable_and_revisioned(store):
    snap = snapshot(store); policy = publish_snapshot(store, snap, corpus_id="papers", project_id="research")
    packet = vocabulary_context(store, corpus_id="papers", project_id="research", configured=policy)
    proposed = extension(snap)
    report = assess_vocabulary(store, packet, corpus_id="papers", project_id="research", target_id="target",
        argument="Explore an unfamiliar alternative.", extensions=(proposed,), assessment="Definition challenge remains unresolved.")
    assert report["extensions"][0]["disposition"] == "proposed" and not report["semantic_verification"]
    service = ProposalService(store); head = store.graph_revision("papers", "research")
    first = service.persist_vocabulary(proposed, corpus_id="papers", project_id="research")
    assert service.persist_vocabulary(proposed, corpus_id="papers", project_id="research") == first
    revision = proposed.model_copy(update={"revision": 1, "supersedes": first, "definition": "A revised provisional distinction."})
    second = service.persist_vocabulary(revision, corpus_id="papers", project_id="research")
    assert first != second and store.get(first).content["definition"] == proposed.definition
    assert store.graph_revision("papers", "research") == head
    with pytest.raises(ConflictError):
        service.persist_vocabulary(revision.model_copy(update={"definition": "Divergent"}), corpus_id="papers", project_id="research")


@pytest.mark.parametrize("kind", ["meaning_shift", "unsupported_equivalence", "missing_qualifier", "changed_assumption", "status_inflation", "unresolved_definition"])
def test_existing_call_findings_bind_exact_argument_and_remain_advisory(store, kind):
    snap = snapshot(store); policy = publish_snapshot(store, snap, corpus_id="papers", project_id="research")
    packet = vocabulary_context(store, corpus_id="papers", project_id="research", configured=policy)
    report = assess_vocabulary(store, packet, corpus_id="papers", project_id="research", target_id="claim",
        argument="The gap is automatically verified.", observations=({"kind": kind, "explanation": "The selected meaning or status needs justification.", "entry_ids": ["gap"]},))
    finding = report["findings"][0]
    assert finding["argument_hash"] == identity("The gap is automatically verified.")
    assert finding["authority"] == "advisory" and finding["assessment"] == "unresolved"
    assert report["status"] == "unresolved" and not report["scientific_admission"]


def test_unknown_word_is_not_an_automatic_rejection(store):
    snap = snapshot(store); policy = publish_snapshot(store, snap, corpus_id="papers", project_id="research")
    packet = vocabulary_context(store, corpus_id="papers", project_id="research", configured=policy)
    report = assess_vocabulary(store, packet, corpus_id="papers", project_id="research", target_id="claim", argument="Investigate newconceptXYZ.")
    assert report["findings"] == [] and report["status"] == "unresolved"


def test_hypothesis_calls_receive_context_without_extra_invocations(store):
    from test_hypothesis_tool import seed, request, context, actions, proposal
    from test_deep_extraction import Model
    from nima_semantica.hypothesis_tool import generate_hypotheses
    seed(store)
    snap = snapshot(store); policy = publish_snapshot(store, snap, corpus_id="papers", project_id="research")
    plain = Model(actions())
    disabled = generate_hypotheses(store, request(store, operation_id="off", normalized_reasoning=False), context(normalized_reasoning=policy), model=plain)
    guided = Model([proposal(vocabulary_assessment="A provisional conjecture; definitions remain distinct."), *actions()[1:]])
    enabled = generate_hypotheses(store, request(store, operation_id="on"), context(normalized_reasoning=policy), model=guided)
    assert disabled.status == enabled.status == "partial"
    assert len(plain.calls) == len(guided.calls) == 3
    assert "attached project vocabulary" not in plain.calls[0]["messages"][0]["content"]
    assert "attached project vocabulary" in guided.calls[0]["messages"][0]["content"]
    import json
    assert "vocabulary_findings" not in json.dumps(plain.calls[0]["tools"])
    assert "vocabulary_findings" in json.dumps(guided.calls[0]["tools"])
    assert enabled.data["normalized_reasoning"]["snapshot_hash"] == snap.content_hash
    assert not enabled.data["result"]["proposals"][0]["model_metadata"]["candidate"]["vocabulary_review"]["semantic_verification"]


def test_omitted_controls_preserve_legacy_request_fingerprint():
    from nima_semantica.hypothesis_contracts import GenerateHypothesesRequest, HypothesisContext
    request = GenerateHypothesesRequest(); context = HypothesisContext(corpus_id="papers", project_id="research")
    assert "normalized_reasoning" not in compatible_dump(request)
    assert "normalized_reasoning" not in compatible_dump(context)


def test_private_proof_submission_records_pinned_advisory_findings(store, tmp_path):
    from nima_semantica.proof_mcp import ProofConfig, ProofService
    snap = snapshot(store); policy = publish_snapshot(store, snap, corpus_id="papers", project_id="research")
    service = ProofService(ProofConfig(database_path=str(tmp_path/"proof.sqlite"), corpus_id="papers",
        project_id="research", store_root=str(store.root), normalized_reasoning=policy))
    opened = service.invoke("open", {"request_id": "start", "target": "Investigate a gap distinction"})
    sid = opened["session_id"]
    node = service.invoke("record_node", {"session_id": sid, "request_id": "step", "expected_revision": 0,
        "kind": "step", "statement": "A provisional comparison", "depends_on": ["target"]})
    argument = "A spectral gap and transport threshold still require separate definitions."
    submitted = service.invoke("submit", {"session_id": sid, "request_id": "submit", "expected_revision": node["revision"],
        "conclusion_node": node["result"]["node_id"], "argument": argument,
        "vocabulary_assessment": "Equivalence remains unresolved.",
        "vocabulary_findings": [{"kind": "unsupported_equivalence", "explanation": "Distinct definitions remain necessary.", "entry_ids": ["gap"]}]})
    report = submitted["result"]["vocabulary_review"]
    assert report["snapshot_hash"] == snap.content_hash and report["argument_hash"] == identity(argument)
    assert report["findings"][0]["authority"] == "advisory" and not report["semantic_verification"]
    assert submitted["scientific_status"] == "uncertified"


def test_existing_save_workflow_publishes_vocabulary_and_replays_proposals(store):
    from nima_semantica.research_analysis import SaveAnalysisRequest, SaveAnalysisContext, save_research_analysis
    snap = snapshot(store)
    context = SaveAnalysisContext(corpus_id="papers", project_id="research", allow_artifact_writes=True)
    request = SaveAnalysisRequest(mode="save_vocabulary", operation_id="snapshot", graph_revision=snap.graph_revision,
        vocabulary_snapshot=snap)
    saved = save_research_analysis(store, request, context)
    assert saved.status == "complete" and not saved.data["scientific_admission"]
    assert save_research_analysis(store, request, context) == saved
    proposed = SaveAnalysisRequest(mode="propose_vocabulary", operation_id="proposal", graph_revision=snap.graph_revision,
        vocabulary_extension=extension(snap))
    result = save_research_analysis(store, proposed, context)
    assert save_research_analysis(store, proposed, context) == result
    assert store.get(result.data["record_id"]).kind == "VocabularyExtension"
    assert store.graph_revision("papers", "research") == snap.graph_revision
    denied = save_research_analysis(store, request, context.model_copy(update={"allow_artifact_writes": False}))
    assert denied.status == "failed"
