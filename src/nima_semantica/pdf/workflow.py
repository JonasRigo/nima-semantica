"""Installation-wide private recognition flow and its operator-owned binding."""
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit
import httpx

from nima_semantica.installation import assets, write_json
from nima_semantica.workflow_installation import api, ensure_credentials, wire_model, validate_flow, _published_flow_readback
from .support import canonical_json, sha256_bytes


def install_recognition(config):
    settings = config.pdf_recognition
    if settings is None:
        raise ValueError("PDF recognition needs a dedicated vision model; run nima setup --pdf-model-profile FILE --pdf")
    root = Path(config.data_root) / "pdf-service"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "recognition.json"
    previous = json.loads(path.read_bytes()) if path.is_file() else None
    flow = json.loads((assets()/"examples/pdf_recognition.json").read_text())
    target = next(n for n in flow["data"]["nodes"] if n["data"]["type"] == "FormulaTranscription")
    with api(config) as client:
        for attempt in range(30):
            try:
                if client.get("/health",timeout=2).is_success:
                    break
            except httpx.TransportError:
                pass
            if attempt==29:
                raise ValueError("Langflow is not ready for PDF workflow installation; start it and rerun nima setup --pdf")
            time.sleep(1)
        ensure_credentials(client, config)
        wire_model(flow, target, "model", settings.model, "pdf_recognition")
        validate_flow(flow)
        digest = sha256_bytes(canonical_json(flow["data"]))
        payload = {**flow,"is_component":False,"access_type":"PRIVATE","mcp_enabled":False,
                   "a2a_enabled":False,"webhook":False}
        if previous:
            current = _published_flow_readback(client, previous["flow_id"])
            if sha256_bytes(canonical_json(current.json()["data"])) != previous["installed_digest"]:
                raise ValueError("PDF recognition canvas was edited; retain/export the edit before running setup")
            if previous.get("pending_verification") and digest == previous["installed_digest"]:
                previous.pop("pending_verification")
                write_json(path,previous)
                return previous
            payload.pop("name",None)
            response = client.patch("/api/v1/flows/"+previous["flow_id"],json=payload)
        else:
            response = client.post("/api/v1/flows/",json=payload)
        response.raise_for_status()
        flow_id = response.json()["id"]
        binding = {"schema_version":1,"langflow_url":config.langflow_url,"flow_id":flow_id,
                   "installed_digest":digest,"concurrency":settings.concurrency,
                   "model":settings.model.model_dump(),"api_key_file":"","pending_verification":True,
                   "langflow_api_key_env":config.langflow_api_key_env,
                   "readiness":{"ready":False,"status":"runtime_not_checked"}}
        import sys
        if sys.platform=="darwin" and urlsplit(binding["langflow_url"]).hostname in {"127.0.0.1","localhost"}:
            binding["langflow_url"]=binding["langflow_url"].replace("127.0.0.1","host.docker.internal").replace("localhost","host.docker.internal")
        token = os.environ.get(config.langflow_api_key_env)
        if token:
            secret = root/"langflow.token"
            from nima_semantica.storage import atomic_bytes
            atomic_bytes(secret,token.encode());secret.chmod(0o600)
            binding["api_key_file"]=str(secret)
        # Persist identity before readback so delayed visibility cannot duplicate creation.
        write_json(path,binding)
        readback = _published_flow_readback(client,flow_id)
        if readback.json()["data"] != flow["data"]:
            raise ValueError("Saved PDF recognition canvas differs from its readback")
    binding.pop("pending_verification")
    # Docker Desktop's PDF broker reaches native Langflow via the host gateway.
    import sys
    if sys.platform == "darwin" and urlsplit(binding["langflow_url"]).hostname in {"127.0.0.1","localhost"}:
        binding["langflow_url"] = binding["langflow_url"].replace("127.0.0.1","host.docker.internal").replace("localhost","host.docker.internal")
    write_json(path,binding)
    return binding
