"""Authenticated local PDF worker service, with network-isolated layout and trusted Langflow recognition."""
import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from .assets import verify_assets, normalization_identity
from .langflow_recognition import LangflowRecognition
from .support import sha256_bytes, sha256_file
from .support import PdfError


def create_app(models, data, token):
    models, data = Path(models).resolve(strict=True), Path(data).resolve()
    data.mkdir(parents=True, exist_ok=True)
    app = FastAPI()
    lock = asyncio.Lock()

    def auth(request):
        if not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
            raise HTTPException(401, "authentication required")

    def backend():
        return LangflowRecognition.from_file(data / "recognition.json")

    @app.get("/health")
    def health():
        try:
            selected=backend()
            binding=getattr(selected,"binding",{})
            return {"status":"ready", "service":"nima-semantica-pdf", "recognition":"langflow",
                    "workflow_readiness":binding.get("readiness",{"ready":False,"status":"not_checked"})}
        except Exception:
            return {"status":"not_ready", "service":"nima-semantica-pdf",
                    "diagnostic":"Configure the dedicated PDF vision workflow and check Langflow connectivity"}

    @app.get("/manifest")
    def manifest(request: Request):
        auth(request)
        try:
            selected=backend()
            return normalization_identity(models,selected.manifest)
        except PdfError as error:
            raise HTTPException(503,error.details or "PDF recognition unavailable; run nima setup --verify-workflows") from None
        except Exception:
            raise HTTPException(503,"PDF recognition unavailable; run nima setup --pdf and check Langflow") from None

    async def run_worker(argv, selected, directory, log_path):
        with log_path.open("wb") as log:
            process=await asyncio.create_subprocess_exec(*argv,stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,stderr=log,limit=1_000_000)
            try:
                while line:=await process.stdout.readline():
                    log.write(line);log.flush()
                    try:event=json.loads(line)
                    except (ValueError,UnicodeDecodeError):continue
                    if event.get("kind")=="recognize":
                        results=await asyncio.to_thread(selected.batch,event["requests"],directory)
                        process.stdin.write((json.dumps({"results":results})+"\n").encode())
                        await process.stdin.drain()
                return await process.wait()
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()


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
            try:
                selected = await asyncio.to_thread(backend)
                identity = await asyncio.to_thread(normalization_identity, models, selected.manifest)
            except PdfError as error:
                raise HTTPException(503,error.details or "PDF recognition unavailable; configure its Langflow vision workflow") from None
            except Exception:
                raise HTTPException(503,"PDF recognition unavailable; configure its Langflow vision workflow") from None
            expected=request.headers.get("x-nima-normalization-profile")
            if expected and expected != identity["profile_hash"]:
                raise HTTPException(409,"PDF recognition profile changed; retry preparation with the new profile")
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
            directory.mkdir(parents=True,exist_ok=True)
            from nima_semantica.installation import write_json
            runtime_path=directory/"runtime.json"
            write_json(runtime_path,identity)
            argv = ["bwrap", "--unshare-net", "--die-with-parent", "--ro-bind", "/", "/", "--dev", "/dev",
                "--bind", str(data), str(data), "--tmpfs", "/tmp",
                sys.executable, "-m", "nima_semantica.pdf.worker", str(source), "--models", str(models), "--output", str(data / "parsed"), "--runtime", str(runtime_path)]
            try:
                returncode = await asyncio.wait_for(run_worker(argv,selected,directory,data/(source_hash+".log")),1800)
            except asyncio.TimeoutError:
                raise HTTPException(504,"PDF normalization timed out; checkpoints retained") from None
            except PdfError as error:
                raise HTTPException(502,error.details or "Langflow formula recognition failed; checkpoints retained") from None
            except Exception:
                raise HTTPException(502,"Langflow formula recognition failed; check the vision model and credentials; checkpoints retained") from None
            if returncode or not result_path.is_file():
                raise HTTPException(422,"PDF normalization failed quality checks; diagnostic artifacts retained")
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
