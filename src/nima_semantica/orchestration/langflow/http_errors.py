"""Bounded backend diagnostics without leaking request bodies or credentials."""
import json
import re

import httpx


def raise_stage_error(response, stage, secrets=()):
    if not response.is_error:
        return
    detail = "backend execution failed"
    candidate = None
    try:
        value = response.json()
        candidate = value.get("detail", value.get("error"))
        if isinstance(candidate, str):
            detail = candidate[:2000]
        elif isinstance(candidate, list):
            detail = json.dumps([{k: row[k] for k in ("loc", "type", "msg") if k in row}
                for row in candidate[:16] if isinstance(row, dict)])[:2000]
    except (ValueError, AttributeError):
        pass
    if isinstance(candidate, dict) and candidate.get("code") in {
        "workflow.authentication_failed", "workflow.langflow_authentication_failed", "workflow.credential_binding_mismatch"}:
        from types import SimpleNamespace
        from nima_semantica.credential_setup import credential_name, CredentialError
        from nima_semantica.workflow_readiness import WorkflowReadinessError
        try:
            name = credential_name(candidate.get("credential", ""))
        except CredentialError:
            name = "invalid_credential_name"
        model = candidate.get("model", "configured vision model")
        if not isinstance(model, str) or len(model) > 256 or any(c.isspace() for c in model) or model.startswith("sk-"):
            model = "configured vision model"
        raise WorkflowReadinessError("PDF recognition", SimpleNamespace(model=model,credential=name,provider="compatible"),
            candidate["code"].removeprefix("workflow."),http_status=response.status_code)
    for secret in secrets:
        if secret:
            detail = detail.replace(secret, "[redacted]")
    detail = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [redacted]", detail)
    detail = "".join(c for c in detail if c >= " " or c == "\n")
    raise httpx.HTTPStatusError(f"{stage}: HTTP {response.status_code}: {detail}",
        request=response.request, response=response)
