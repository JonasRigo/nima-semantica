"""Safe public request diagnostics: never echo input values or validator contexts."""
from pydantic import ValidationError
from .tool_contracts import ToolResult

# Cross-field validators are not represented by JSON Schema. Keep their public
# prerequisites explicit rather than forwarding arbitrary exception messages.
MODE_REQUIREMENTS = {
    "DeepExtractionRequest": "regional requires operation_id and 1–32 source_region_ids; optional source_id checks membership. plan requires source_id only, optionally plan_attempt_id. document modes require source_id and operation_id. consolidation_plan/consolidate require graph_revision and consolidation.artifact_ids, not source selection. consolidate also requires operation_id and consolidation.plan_hash from planning; merges/links belong to consolidate only. Project links require graph_revision.",
    "AnalyzeGraphRequest": "Execution requires operation_id and the exact graph_revision from Inspect Corpus; artifact_id optionally selects a saved graph candidate.",
    "TraceDependenciesRequest": "trace requires operation_id, exact graph_revision, and claim with corpus_id, optional project_id and local_id.",
    "SubstantiationRequest": "substantiate requires operation_id, graph_revision and nonempty targets containing exact ref and finding. Targets and regions must be unique.",
    "ReviewRequest": "Non-preview modes require operation_id, graph_revision and exact targets with content_revision. proof_assessment also requires each target's argument. Do not invent target hashes or graph identities.",
    "ReviewTarget": "content_revision must equal the canonical identity of the target without content_revision; preserve the exact selected statement, domain, references and argument.",
    "UpdateProjectRequest": "prepare/commit require operation_id, graph_revision and exactly one of delta, artifact_id or progress_proposal_ids. rebuild_projection requires operation_id and graph_revision without a delta. Public input cannot grant commit approval.",
    "GenerateHypothesesRequest": "Non-preview modes require operation_id and graph_revision. restate also requires prior_hypothesis_ids. Scope lists must be unique.",
    "CompareObjectsRequest": "Non-preview modes require operation_id, graph_revision, at least two objects and explicit criteria. Proof comparisons require the exact common target; each object selects exactly one binding.",
    "SaveAnalysisRequest": "save requires operation_id, graph_revision and bundle with sections or artifacts and explicit limitations.",
    "DraftLeanRequest": "draft/repair require operation_id, graph_revision, exact target and environment_digest. Target dependency inventory must match supplied dependencies; repair requires prior attempts as specified in the schema.",
    "ResearchRunRequest": "create requires creation and operation_id; transition requires transition and operation_id. inspect accepts run_id and pagination only, not write controls. Payload must match the selected mode.",
    "PassResearchRequest": "Continue with input_graph_artifact_id without initial search_phrases; research carries the scoped request and operation ID.",
}


def is_request_rejection(value):
    return (isinstance(value, dict) and value.get("status") == "failed"
        and isinstance(value.get("data"), dict) and value["data"].get("executed") is False
        and any(isinstance(d, dict) and str(d.get("code", "")).startswith("request.invalid")
                for d in value.get("diagnostics", ())))


def invalid_request(operation, exc):
    diagnostics = []
    if isinstance(exc, ValidationError):
        for error in exc.errors(include_input=False, include_context=True, include_url=False):
            kind = error["type"]
            details = {key: value for key, value in error.get("ctx", {}).items()
                       if key in {"min_length", "max_length", "actual_length", "ge", "gt", "le", "lt"}
                       and isinstance(value, (int, float))}
            next_action = {
                "extra_forbidden": "Remove this unrecognized field; operator permissions and model configuration are not public request fields.",
                "missing": "Supply this required field using the tool's schema and the selected mode's prerequisites.",
                "too_long": "Reduce this call to the published bound. Use planning/pagination for independent work; do not truncate or split an atomic operation.",
                "string_too_long": "Reduce the request text within the published bound without silently discarding required evidence; otherwise request operator guidance.",
            }.get(kind, "Consult this tool's input schema and mode requirements; correct the indicated field before retrying.")
            diagnostics.append({"code": "request.invalid_field", "field": list(error["loc"]),
                "constraint": kind,
                "bounds": details,
                "message": "The request field does not satisfy the published contract.",
                "next_action": next_action})
    else:
        diagnostics.append({"code": "request.invalid_json", "field": [],
            "message": "Expected a JSON object within the published transport size limit.",
            "next_action": "For Langflow serialize the request object in input_value; for mathematics/proof pass the typed request object. Consult tool discovery for examples."})
    return ToolResult(operation=operation, status="failed", data={"executed": False},
        diagnostics=tuple(diagnostics + ([{"code": "request.mode_requirements", "message": MODE_REQUIREMENTS[exc.title]}]
            if isinstance(exc, ValidationError) and exc.title in MODE_REQUIREMENTS else []))).model_dump(mode="json")


def request_server(title, prefix, contracts):
    """Validate before FastMCP's generic formatter can echo rejected secret values.

    Keep the registered typed schemas and successful response conversion intact.
    Only pre-dispatch validation is intercepted, never service/internal failures.
    """
    import json
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.exceptions import ToolError

    class RequestServer(FastMCP):
        async def call_tool(self, name, arguments):
            operation = name.removeprefix(prefix)
            if name.startswith(prefix) and operation in contracts:
                try:
                    if not isinstance(arguments, dict) or set(arguments) != {"request"}:
                        raise TypeError("typed request envelope required")
                    contracts[operation][0].model_validate(arguments["request"])
                except (ValidationError, TypeError) as exc:
                    raise ToolError(json.dumps(invalid_request(name, exc))) from None
            return await super().call_tool(name, arguments)
    return RequestServer(title)
