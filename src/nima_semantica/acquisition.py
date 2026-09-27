"""Bounded archival acquisition using Semantica's guarded HTTP primitive."""
import time
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests

from .models import NimaError


def acquire(url, policy, *, approved_domain=None):
    from semantica.ingest.ssrf import request_with_ssrf_guard
    allowed = set(policy.domains)
    if approved_domain:
        allowed.add(approved_domain)
    started = time.monotonic()

    class RestrictedSession(requests.Session):
        def send(self, request, **kwargs):
            parsed = urlsplit(request.url)
            if parsed.scheme not in ("http", "https") or parsed.hostname not in allowed or parsed.username or parsed.password:
                raise NimaError("acquisition target is outside approved domains")
            return super().send(request, **kwargs)

    session = RestrictedSession()
    session.trust_env = False

    def fetch(target, maximum):
        response = request_with_ssrf_guard("GET", target, session=session, allow_private_ips=False,
            allow_private_ips_on_redirect=False, max_redirects=policy.max_redirects,
            timeout=policy.timeout_seconds, stream=True, headers={"User-Agent": "NIMA-Semantica/0.1"})
        with response:
            if response.status_code == 404:
                return b"", response
            response.raise_for_status()
            chunks, total = [], 0
            for chunk in response.iter_content(65536):
                total += len(chunk)
                if total > maximum or time.monotonic() - started > policy.timeout_seconds:
                    raise NimaError("acquisition size or time limit exceeded")
                chunks.append(chunk)
            return b"".join(chunks), response

    try:
        parsed = urlsplit(url)
        robot_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        robots, _ = fetch(robot_url, min(policy.max_response_bytes, 100_000))
        parser = RobotFileParser()
        parser.parse(robots.decode("utf-8", errors="replace").splitlines())
        if robots and not parser.can_fetch("NIMA-Semantica", url):
            raise NimaError("robots policy disallows acquisition")
        data, response = fetch(url, policy.max_response_bytes)
        if not data:
            raise NimaError("acquisition returned no source")
        name = PurePosixPath(urlsplit(response.url).path).name or "source.html"
        if "." not in name:
            name += ".html"
        return data, name, {"resolved_url": response.url, "status_code": response.status_code,
                            "content_type": response.headers.get("content-type", ""), "bytes": len(data)}
    finally:
        session.close()
