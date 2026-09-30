"""Agent-facing operating guidance shared by discovery and documentation."""

# These describe recovery, not mandatory pipelines or autonomous dispatch.
GUIDANCE = {
    "tool_guide": "Use an empty request to list tools; select tool by exact name or ID for its schema and examples.",
    "research_run": "Inspect using next_run_revision and next_receipt_id while more_transitions or more_attempts is true. Re-read current run revision before transitions.",
    "load_ontology": "List with {}; load a returned name/version or digest. A missing profile requires selecting or explicitly saving an ontology, not inventing an identity.",
    "save_ontology": "Validate first if useful; save an immutable profile explicitly. Resolve version conflicts by inspecting the existing profile, not overwriting it.",
    "prepare_and_index_sources": "Prepare explicit sources in bounded groups. Inspect each source outcome and failed_stage; resume only failed work with a new operation ID. Index readiness does not certify source fidelity.",
    "inspect_corpus": "Follow returned pagination using offset/limit and the expected store revision. If the revision changes, restart inventory discovery rather than combining inconsistent pages.",
    "retrieve_research_context": "Choose lexical/vector/hybrid explicitly. Inspect readiness when projections or embeddings are unavailable. Narrow or reformulate queries when context limits truncate results; ranked retrieval is not exhaustive pagination.",
    "read_evidence": "Read returned exact region or artifact references. Preserve hashes, offsets and quality labels. Follow provenance links explicitly; a bounded link list is not complete lineage.",
    "calculate_mathematics": "Discover every nima_math_* operation through nima_math_tools and atomic operations through nima_math_operations. Continue the same session with current revisions; inspect rejected actions before changing work.",
    "search_for_counterexamples": "Supply the scoped statement and domain. Exhausted search budgets mean no witness was found within that search, not that a proof exists. Change scope or request operator budget changes deliberately.",
    "verify_lean": "Supply pinned sources, imports and targets. Repair from verifier diagnostics without silently changing the theorem or environment. Oversized coherent proof submissions need operator review, not arbitrary splitting.",
    "deep_extraction": "Plan with source_id, then submit each returned regional batch unchanged. Track coverage and artifacts; re-plan with a new plan_attempt_id only for deliberate new attempts. For coherent multi-batch/paper graphs use consolidation_plan with saved artifact_ids and exact graph_revision, inspect all relevant pages/evidence, then consolidate with plan_hash, explicit same-type identity merges and ontology-valid links. No label-based auto-merging or graph commits. Continue with the consolidated artifact plus new candidates, not overlapping originals. Preserve uncertainty and preparation quality; connectivity is not completeness.",
    "analyze_graph": "Select an existing scoped revision and analysis mode. If analysis exceeds bounds, use an explicitly selected smaller graph candidate artifact or request an operator budget change; this request has no seed selector. Do not infer global conclusions from partial analysis.",
    "trace_claim_dependencies": "Select a scoped claim and revision. Report traversal boundaries and unresolved dependencies. Independently traced subgraphs do not automatically establish complete closure.",
    "substantiate_graph_snapshot": "Select existing targets and exact evidence. Split independent target groups within the schema limits, retaining shared revision and per-target findings. Missing support is limited to examined scope.",
    "update_project_graph": "Preview does not persist a proposal or commit. Prepare an exact delta or reference a saved proposal artifact, then obtain exact scoped approval before commit. Re-prepare after revision conflicts; never split an approved delta implicitly.",
    "hypothesis_generation": "Choose the mode and its required evidence or existing target. Results are proposals. Continue with explicitly selected follow-up questions, not an automatic fixed number of hypothesis rounds.",
    "compare_research_objects": "Use caller-supplied objects for private/unadmitted work, or real scoped graph identities for graph modes. Do not split a joint comparison into independent groups and claim the same result.",
    "deep_research": "Choose research scope and bounded passes. Preserve temporary-read provenance and remaining questions; further passes are agent decisions. Temporary fast reads do not become prepared corpus evidence automatically.",
    "review_research": "Review independent target groups within request limits. Preserve each target's findings, evidence and outstanding obligations; partial target coverage is not a complete paper review.",
    "save_research_analysis": "Persist the explicitly selected report and evidence, then read its artifact back. Resolve missing substantiation prerequisites rather than dropping adverse findings or their uncertainty.",
    "draft_proof": "Discover nima_proof_* operations through nima_proof_tools. Continue private session state with current revisions and explicit obligations; exporting or submitting private work does not admit project claims.",
    "conduct_proof": "Discover nima_proof_* operations through nima_proof_tools. Let the objective determine further steps, not a fixed round count. Retain failed attempts and obligations; project changes require separate approval.",
    "draft_lean": "Pin the target and environment. Use retrieval and verifier feedback for repairs within operator budgets; report unresolved goals when exhausted. A draft is not verified merely because generation completed.",
}


def schema_limits(schema):
    """Report exact declared bounds, including definitions, without resolving cycles."""
    found = []
    def walk(value, path):
        if isinstance(value, dict):
            bounds = {k: value[k] for k in ("minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum") if k in value}
            if bounds:
                found.append({"schema_path": path, "bounds": bounds})
            for key, child in value.items():
                if isinstance(child, (dict, list)):
                    walk(child, path + [key])
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, path + [index])
    walk(schema, [])
    return found


def usage(tool_id, schema):
    private = tool_id in {"calculate_mathematics", "draft_proof", "conduct_proof"}
    from .request_diagnostics import MODE_REQUIREMENTS
    return {"transport": "typed request object" if private else "JSON object serialized in input_value",
        "guidance": GUIDANCE[tool_id], "request_limits": schema_limits(schema),
        "example_policy": "Examples illustrate validated request shapes. Replace placeholder identities, artifact hashes, target content and revisions with real scoped values; recompute content_revision when target content changes. Examples grant no execution or commit permission.",
        "mode_requirements": MODE_REQUIREMENTS.get(schema.get("title"), "Use the required fields, enums and descriptions in input_schema; identity/scope prerequisites are checked before execution."),
        "limit_policy": "Schema bounds apply per call, not to the total number of calls. Do not silently truncate. Split only independent work; preserve coherent operations and approval boundaries.",
        "operator_policy": "Scope, permissions, model configuration and execution budgets remain operator-owned. Requests cannot grant themselves capabilities.",
        "retry_policy": "Replay uncertain delivery with identical arguments and ID. Inspect status/receipts before retrying interrupted work. Use a new ID for changed work or a deliberate new attempt; stop repeated unchanged failures and diagnose.",
        "completion_policy": "Retain artifacts and receipts across calls. Report failed/deferred scope explicitly; partial work is not full coverage."}


def private_catalog(tools):
    return {"transport": "typed request object", "operations": {
        name: {"description": description, "input_schema": schema.model_json_schema(),
               "request_limits": schema_limits(schema.model_json_schema())}
        for name, (schema, description) in tools.items()},
        "continuation": "Use status/frontier/inspect to recover session state. Mutations use the current revision and a unique request_id. Replay identical deliveries; do not automatically rerun interrupted execution.",
        "limits": "Per-call limits are not a fixed total step or batch count. Keep dependent work in the same session; do not split atomic or jointly validated operations blindly."}
