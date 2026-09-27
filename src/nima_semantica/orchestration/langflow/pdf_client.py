"""Lightweight client for NIMA-Semantica's independent PDF service."""
from pathlib import Path
from urllib.parse import urlsplit
import httpx

from nima_semantica.models import ConfigurationError, NimaError
from .http_errors import raise_stage_error


class PdfNormalizerClient:
    def __init__(self, url: str, token_file: str):
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            raise ConfigurationError("invalid PDF service URL")
        self.url = url.rstrip("/")
        self.token_file = Path(token_file)

    def health(self):
        token = self.token_file.read_text().strip()
        with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
            try:
                response = client.get(self.url + "/health")
            except httpx.ConnectError as error:
                raise httpx.ConnectError("PDF worker unavailable; start the configured nima-pdf service and check /health",
                    request=error.request) from error
        raise_stage_error(response, "PDF worker readiness", (token,))
        if response.json().get("status") != "ready":
            raise NimaError("PDF worker is not ready; inspect its managed service status")
        return True

    def normalize(self, data: bytes):
        self.health()
        token = self.token_file.read_text().strip()
        with httpx.Client(timeout=1830, trust_env=False, follow_redirects=False) as client:
            try:
                response = client.post(self.url + "/normalize", content=data,
                    headers={"authorization": "Bearer " + token, "content-type": "application/pdf"})
            except httpx.ConnectError as error:
                raise httpx.ConnectError("PDF worker unavailable; start the configured nima-pdf service and check /health",
                    request=error.request) from error
        raise_stage_error(response, "PDF normalization", (token,))
        result = response.json()
        if not isinstance(result.get("text"), str) or not isinstance(result.get("diagnostics"), list):
            raise NimaError("invalid PDF worker response")
        return result
