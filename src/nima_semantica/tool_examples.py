"""Schema-validated execution examples; IDs are placeholders, not stored evidence."""
from importlib import import_module
from .models import identity


def execution_example(tool_id):
    revision = {"corpus_id": "papers", "project_id": "research", "corpus_revision": 0, "project_revision": 0}
    ref = {"corpus_id": "papers", "project_id": "research", "local_id": "selected-claim"}
    common = {"operation_id": "example-attempt-1", "graph_revision": revision}
    target = {"target_id": "selected-claim", "ref": ref, "statement": "The selected claim.",
        "argument": "The selected argument.", "domain": "Selected domain", "quantifier": "forall",
        "assumptions": [], "dependencies": [], "unresolved_obligations": [], "source_region_ids": []}
    target["content_revision"] = identity(target)
    cases = {
        "analyze_graph": ("graph_analysis", "AnalyzeGraphRequest", common | {"mode": "strict"}),
        "trace_claim_dependencies": ("claim_dependencies", "TraceDependenciesRequest", common | {"mode": "trace", "claim": ref}),
        "hypothesis_generation": ("hypothesis_contracts", "GenerateHypothesesRequest", common | {"mode": "generate", "source_region_ids": ["prepared-region-id"]}),
        "substantiate_graph_snapshot": ("substantiation_contracts", "SubstantiationRequest", common | {"mode": "substantiate", "targets": [{"ref": ref, "finding": "Support for this selected claim was not located in the examined evidence."}]}),
        "review_research": ("review_contracts", "ReviewRequest", common | {"mode": "argument_review", "targets": [target]}),
        "update_project_graph": ("project_update", "UpdateProjectRequest", common | {"mode": "prepare", "artifact_id": "0" * 64}),
        "save_research_analysis": ("research_analysis", "SaveAnalysisRequest", common | {"mode": "save", "bundle": {
            "title": "Scoped research report", "sections": [{"section_id": "summary", "category": "summary", "text": "Caller-authored summary."}], "limitations": ["Not independently verified."]}}),
        "compare_research_objects": ("comparison_contracts", "CompareObjectsRequest", common | {"mode": "mathematical_objects",
            "objects": [{"object_id": name, "inline": {"kind": "mathematical_object", "statement": statement, "domain": "integers"}}
                        for name, statement in (("a", "x squared"), ("b", "x"))],
            "criteria": [{"criterion_id": "equality", "description": "Under what assumptions are the expressions equal?"}]}),
        "deep_research": ("deep_research_passes", "PassResearchRequest", {"research": common | {"mode": "research", "questions": [{"question_id": "q1", "question": "What assumptions support the selected claim?"}]}}),
        "prepare_and_index_sources": ("source_tools", "PrepareSourcesRequest", {"mode": "prepare_index", "operation_id": "prepare-1", "sources": [{"name": "paper.txt", "text": "A source statement."}], "index_mode": "lexical"}),
        "deep_extraction": ("extraction_contracts", "DeepExtractionRequest", {"mode": "plan", "source_id": "prepared-source-id", "ontology_profile": "literature_review@1.0.0"}),
    }
    if tool_id == "draft_lean":
        target.pop("content_revision")
        target.update(granularity="lemma", definitions=[])
        target["content_revision"] = identity(target)
        cases[tool_id] = ("lean_draft_contracts", "DraftLeanRequest", common | {"mode": "draft", "target": target, "environment_digest": "0" * 64})
    if tool_id not in cases:
        return None
    module, name, payload = cases[tool_id]
    model = getattr(import_module("nima_semantica." + module), name)
    model.model_validate(payload)
    return payload
