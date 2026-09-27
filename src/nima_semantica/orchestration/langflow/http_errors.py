"""Bounded backend diagnostics without leaking request bodies or credentials."""
import json
import re

import httpx


def raise_stage_error(response, stage, secrets=()):
    if not response.is_error:
        return
    detail = "backend execution failed"
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
    for secret in secrets:
        if secret:
            detail = detail.replace(secret, "[redacted]")
    detail = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [redacted]", detail)
    detail = "".join(c for c in detail if c >= " " or c == "\n")
    raise httpx.HTTPStatusError(f"{stage}: HTTP {response.status_code}: {detail}",
        request=response.request, response=response)
