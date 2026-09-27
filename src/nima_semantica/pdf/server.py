"""Authenticated local PDF worker service, independent of NIMA-AGI and Langflow."""
import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from .assets import verify_assets
from .support import sha256_bytes, sha256_file


def create_app(models, data, token):
    models, data = Path(models).resolve(strict=True), Path(data).resolve()
    data.mkdir(parents=True, exist_ok=True)
    app = FastAPI()
    lock = asyncio.Lock()

    def auth(request):
        if not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
            raise HTTPException(401, "authentication required")

    @app.get("/health")
    def health():
        return {"status": "ready", "service": "nima-semantica-pdf"}

    @app.post("/normalize")
    async def normalize(request: Request):
        auth(request)
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 50_000_000:
                raise HTTPException(413, "PDF exceeds upload limit")
        if not content.startswith(b"%PDF-"):
            raise HTTPException(422, "PDF header missing")
        if lock.locked():
            raise HTTPException(429, "PDF worker busy; retry later")
        async with lock:
            source_hash = sha256_bytes(content)
            source = data / (source_hash + ".pdf")
            if source.exists() and sha256_file(source) != source_hash:
                raise HTTPException(409, "source cache integrity failure")
            if not source.exists():
                from ._ported import _atomic_formula_artifact
                _atomic_formula_artifact(source, bytes(content))
            identity = await asyncio.to_thread(verify_assets, models)
            directory = data / "parsed" / source_hash / identity["profile_hash"]
            result_path = directory / "result.json"
            hash_path = directory / "result.sha256"
            if result_path.is_file() and hash_path.is_file():
                if sha256_file(result_path) != hash_path.read_text().strip():
                    raise HTTPException(409, "normalized cache integrity failure")
                cached = json.loads(result_path.read_bytes())
                recorded = next(d for d in cached["diagnostics"] if d["kind"] == "parser_manifest")
                if recorded["runtime"] != identity or recorded["source_sha256"] != source_hash:
                    raise HTTPException(409, "normalized cache identity mismatch")
                return cached
            argv = ["bwrap", "--unshare-net", "--die-with-parent", "--ro-bind", "/", "/", "--dev", "/dev",
                "--bind", str(data), str(data), "--tmpfs", "/tmp",
                sys.executable, "-m", "nima_semantica.pdf.worker", str(source), "--models", str(models), "--output", str(data / "parsed")]
            try:
                completed = await asyncio.to_thread(subprocess.run, argv, capture_output=True, timeout=1800, check=False)
            except subprocess.TimeoutExpired as exc:
                (data / (source_hash + ".timeout.log")).write_bytes((exc.stdout or b"") + (exc.stderr or b""))
                raise HTTPException(504, "PDF normalization timed out; partial evidence retained") from None
            (data / (source_hash + ".log")).write_bytes(completed.stdout + completed.stderr)
            if completed.returncode or not result_path.is_file():
                raise HTTPException(422, "PDF normalization failed quality checks; diagnostic artifacts retained")
            hash_path.write_text(sha256_file(result_path))
            return json.loads(result_path.read_bytes())

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()
    args.data.mkdir(parents=True, exist_ok=True)
    token_file = args.data / "token"
    if not token_file.exists():
        with token_file.open("x") as stream:
            os.chmod(token_file, 0o600)
            stream.write(secrets.token_urlsafe(32))
    token = token_file.read_text().strip()
    if len(token) < 32:
        raise ValueError("PDF service token is too short")
    verify_assets(args.models)
    import uvicorn
    uvicorn.run(create_app(args.models, args.data, token), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
