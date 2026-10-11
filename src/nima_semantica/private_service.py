"""One private database owner serving authenticated loopback MCP clients."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hmac
import json
import os
from pathlib import Path
import secrets
import stat

from . import __version__
from .storage import atomic_bytes


class OwnerOccupied(RuntimeError):
    pass


@contextmanager
def database_owner(path):
    """Acquire before initialization; recovery belongs exclusively to this owner."""
    path = Path(str(path) + ".owner.lock")
    if path.is_symlink():
        raise ValueError("owner lock must not be a symlink")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OwnerOccupied("private database has an active owner; connect to its managed HTTP service. Standalone stdio supports one owner only") from None
        lock.seek(0); lock.truncate()
        json.dump({"pid": os.getpid(), "uid": os.getuid(), "nima_version": __version__}, lock)
        lock.flush(); os.fsync(lock.fileno())
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def private_token(path, *, create=False):
    path = Path(path)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w") as stream:
                stream.write(secrets.token_urlsafe(48))
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("service token must be a private regular file (0600)")
    value = path.read_text().strip()
    if len(value) < 32:
        raise ValueError("service token is too short")
    return value


class BearerGate:
    """Authenticate every HTTP request, including discovery and reconnect."""
    def __init__(self, app, token):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            values = [v for k, v in scope.get("headers", ()) if k.lower() == b"authorization"]
            if len(values) != 1 or not hmac.compare_digest(values[0], ("Bearer " + self.token).encode()):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b"authentication required"})
                return
        await self.app(scope, receive, send)


def http_app(server, service, token):
    from starlette.responses import JSONResponse
    server.settings.stateless_http = True
    server.settings.json_response = True

    @server.custom_route("/health", methods=["GET"])
    async def health(request):
        return JSONResponse({"nima_version": __version__, "ready": True,
            "scope": {"corpus_id": service.config.corpus_id, "project_id": service.config.project_id},
            "owner_pid": os.getpid(), "owner_uid": os.getuid(), "transport": "authenticated-loopback-http"})

    return BearerGate(server.streamable_http_app(), token)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=["proof", "math"], required=True)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--host", choices=["127.0.0.1", "::1"], default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    # Parse before opening the database, but do not initialize/recover without ownership.
    if args.kind == "proof":
        from .proof_mcp import ProofConfig as Config, ProofService as Service, create_server
    else:
        from .math_session_service import MathServiceConfig as Config
        from .math_mcp import ConfiguredMathService as Service, create_server
    config = Config.model_validate_json(args.config.read_text())
    token = private_token(args.token_file, create=True)
    import uvicorn
    with database_owner(config.database_path):
        service = Service(config)
        service.recover_interrupted()
        uvicorn.run(http_app(create_server(service), service, token), host=args.host, port=args.port,
                    access_log=False)


if __name__ == "__main__":
    main()
