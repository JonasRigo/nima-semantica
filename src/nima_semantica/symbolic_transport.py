"""Optional narrow HTTP transport to the existing pinned symbolic worker.

Only operator environment selects the endpoint and credential. No Docker socket,
image, mount, command-line option, host path, or network permission crosses this
API. Generated Python remains confined by SymbolicWorker on the executor host.
"""
import json
import os
from pathlib import Path
import urllib.request

from .calculation import SymbolicExecutionInput, SymbolicWorker
from .models import ConfigurationError, NimaError


class RemoteSymbolicWorker(SymbolicWorker):
    def __init__(self, url, token):
        if not url.startswith(("http://", "https://")) or not token:
            raise ConfigurationError("symbolic worker transport configuration is incomplete")
        self.url, self._token = url.rstrip("/"), token

    def run(self, source, timeout=60):
        payload = SymbolicExecutionInput(source=source, timeout_seconds=timeout)
        request = urllib.request.Request(self.url + "/execute", data=payload.model_dump_json().encode(),
            headers={"Authorization": "Bearer " + self._token, "Content-Type": "application/json"}, method="POST")
        # Do not forward the bearer credential across a server redirect.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout + 45) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("oversized response")
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get("outcome") not in ("executed", "timeout", "output_limit", "code_failed"):
                raise ValueError("invalid worker response")
            import hashlib
            if value.get("source_sha256") != hashlib.sha256(source.encode()).hexdigest():
                raise ValueError("worker source identity mismatch")
            return value
        except Exception:
            raise NimaError("isolated symbolic worker transport failed") from None


def configured_symbolic_worker():
    url = os.environ.get("NIMA_SYMBOLIC_WORKER_URL")
    if not url:
        return SymbolicWorker()
    token_path = os.environ.get("NIMA_SYMBOLIC_WORKER_TOKEN_FILE")
    if not token_path:
        raise ConfigurationError("symbolic worker token file is required")
    return RemoteSymbolicWorker(url, Path(token_path).read_text().strip())
