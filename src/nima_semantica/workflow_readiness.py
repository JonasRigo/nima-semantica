"""Actual saved-workflow probes, separate from service health and canvas readback."""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import uuid

from .credential_setup import credential_inventory, model_credentials, credential_name, CredentialError
from .installation import assets, write_json
from .models import identity


class WorkflowReadinessError(ValueError):
    def __init__(self, role, profile, reason, *, http_status=None):
        try:
            name = credential_name(profile.credential) if profile.credential and profile.provider != "ollama" else ""
        except CredentialError:
            name = "invalid_credential_name"
        self.diagnostic = {"code": "workflow." + reason, "role": role, "model": profile.model,
            "credential": name,
            "message": f"{role} ({profile.model}), credential {name or '(none)'}: {reason.replace('_', ' ')}. Setup is incomplete."}
        if http_status:
            self.diagnostic["http_status"] = http_status
        super().__init__(self.diagnostic["message"])


def response_error(response, role, profile):
    # Inspect for classification only. Never expose provider bodies or keys.
    auth = response.status_code in {401, 403} or bool(re.search(
        r"\b401\b|\b403\b|missing authentication|authenticationerror|invalid.{0,30}(api.?key|credential)",
        response.text[:1_000_000], re.I))
    return WorkflowReadinessError(role, profile, "authentication_failed" if auth else "runtime_failed", http_status=response.status_code)


def check_bindings(data, role, profile):
    models = [node for node in data.get("nodes", []) if node.get("data", {}).get("type") in {"ConfiguredModel", "ConfiguredEmbeddings"}]
    if not models:
        raise WorkflowReadinessError(role, profile, "credential_binding_mismatch")
    for node in models:
        template = node["data"]["node"]["template"]
        saved = json.loads(template["profile_json"]["value"])
        field = template["api_key"]
        expected = profile.model_dump()
        if node["data"]["type"] == "ConfiguredEmbeddings":
            expected["base_url"] = profile.container_url or profile.base_url
        authenticated = saved["provider"] != "ollama" and bool(saved.get("credential"))
        if (saved != expected or
            field.get("load_from_db") is not authenticated or field.get("value") != (profile.credential if authenticated else "")):
            raise WorkflowReadinessError(role, profile, "credential_binding_mismatch")


def run_saved(client, flow_id, role, profile, payload):
    current = client.get("/api/v1/flows/" + flow_id)
    if not current.is_success:
        raise response_error(current, role, profile)
    check_bindings(current.json()["data"], role, profile)
    response = client.post("/api/v1/run/" + flow_id, json={"input_value": json.dumps(payload),
        "input_type": "chat", "output_type": "chat", "output_component": "ChatOutput-ocr",
        "session_id": "nima-setup-" + uuid.uuid4().hex}, timeout=getattr(profile, "request_timeout_seconds", 180) + 30)
    if not response.is_success:
        raise response_error(response, role, profile)
    try:
        rows = [row for group in response.json()["outputs"] for row in group["outputs"]
                if row.get("component_id") == "ChatOutput-ocr"]
        if len(rows) != 1:
            raise ValueError("ambiguous check output")
        return json.loads(rows[0]["results"]["message"]["text"])
    except (KeyError, TypeError, ValueError):
        classified = response_error(response, role, profile)
        if classified.diagnostic["code"] == "workflow.authentication_failed":
            raise classified from None
        raise WorkflowReadinessError(role, profile, "invalid_runtime_response") from None


# Fixed PNG containing x = 1; usable without the optional PDF/Pillow dependencies.
_FORMULA_PNG = (
    'iVBORw0KGgoAAAANSUhEUgAAAPAAAABgCAIAAACsUWiGAAAFK0lEQVR4nO3cP0hqbxzHcf0ZZWV/hoZCWqKoiCgKl8g5bS7KwjKK'
    'oKS5FodUqKCGiIj+odmQ5FCbFFSKObUaBdVUlP1ZItII//yGAw9i3HO7x5Pit89r8jlXe5577/vKc885Ko3H4xIAKv7L9AIAxISg'
    'gRQEDaQgaCAFQQMpCBpIQdBACoIGUhA0kIKggRQEDaQgaCAFQQMpCBpIQdBACoIGUhA0kIKggRQEDaQgaCAFQQMpCDrdPj4+vF6v'
    '1Wrt6OgoKiqSJrDb7ZleXdbLyfQCfoXX11e/3+/z+Xw+39nZ2efnZ6ZXRBaCToeenp6Dg4NMr+JXwJYDSME7dLrJ5XKVSqVWq9Vq'
    '9dPT0+DgYKZXRAqCTofS0lKtVstFrFKp8vLyuOP7+/sZXRdBCDodnE5nppfwW2APDaQgaCBFSNB2uz3xckBnZ+d3vpP38vJSoVCw'
    'V1VWVr68vAiYHYCHkKANBoPBYGBDt9s9NzfH/5JwONzd3f3+/s4Nc3JydnZ2ysrKBMwOwEPglmN5ebmhoYENTSaT3+/neb7RaAwE'
    'AmxosVja29uFTQ3AQ2DQBQUFLpersLCQG0Yikd7e3j9tIRwOh81mY0OtVjs5OSlsXgB+wv9TWF9fv7KywoZ3d3cDAwNfN9MXFxfj'
    '4+NsqFQqHQ6HVCoVPG+S8vJy6c+4vr4Wa5GQNimd5dDr9cPDw2z4dTMdCoWSts5OpxNbZ/g5qZ62W1paamxsZEOTyXR6esqG4+Pj'
    '5+fnbIitM/y0VIPOz893uVwKhYIbRiIRnU7HbabtdvvW1hZ7pkajwdYZfpoIF1Zqa2tXV1fZkNtMBwIBo9HIDiqVyu3tbRG3zkww'
    'GIz/jOrqatFXCz9NnCuFfX19o6OjbOh2u9va2kKhEDeUyWQ46wzpIdql78XFxaamJjZ8e3tjjy0Wi1qtFmsiAB6iBS2Xy10uV1FR'
    'UdJxjUYzNTUl1iwA/MS8OammpqarqyvxSG5uLnfjh4izAPAQM+ijo6PE0xoSieTz83NtbU3EKQD4iRZ0MBjs7++PxWJJx6enp71e'
    'r1izfIUrhZBInKBjsVh/f//j4yM7IpfLuQfRaFSn0z09PYkyEQA/cYI2m83Hx8dsODExsbm5yYYPDw96vf7rmzeA6EQI+uTkxGKx'
    'sGFra+v8/HxfX9/IyAg7eHh4ODMzk/pcAH+R4uW0YDBYXl7OflpJScnNzQ33S6FQKPE2D5lM5vV6U75+R8re3l7i34XNZsv0irJe'
    'Su/Q3NY5GAyyIxsbG1VVVdzj/Pz83d1dds80t5l+fn5OZUYAfikFbbVaj46O2NBoNCadh66rq0u8Z/r+/l6v18e/8QFEAGGEB+3x'
    'eMxmMxu2tLQsLCx8fZperx8aGmLDg4OD2dlZwZMC/IWwncrj42NFRQX7IcXFxVdXV3968vv7e+IHEGUymc/nE7hFyk6Jd7YIcHt7'
    'm+nfQdYQ8g7NbZ0fHh7YkfX1dZ6bLZM+gBiNRnk+gAiQEgH/CBJ3GhKJZGxs7DuvSvo2b61WG4vFBMyejfAOnTb/HLTH45HJZOzP'
    'urm5ORwOf/O1Sd+0OTMz86+zZykEnTbSOM45ACH4bjsgBUEDKQgaSEHQQAqCBlIQNJCCoIEUBA2kIGggBUEDKQgaSEHQQAqCBlIQ'
    'NJCCoIEUBA2kIGggBUEDKQgaSEHQQAqCBlIQNJCCoIEUBA2kIGggBUEDKQgaSEHQQAqCBlIQNJCCoIEUBA2kIGggBUEDKQgaSEHQ'
    'QAqCBlIQNJDyP5FSlrb112HBAAAAAElFTkSuQmCC'
)


def formula_probe():
    from .pdf.support import sha256_bytes
    data = base64.b64decode(_FORMULA_PNG)
    return {"id": "setup-" + uuid.uuid4().hex, "crop_hash": sha256_bytes(data),
            "png_base64": _FORMULA_PNG}

def verify_recognition(client, binding, profile):
    request = formula_probe()
    result = run_saved(client, binding["flow_id"], "PDF recognition", profile, request)
    if (result.get("id") != request["id"] or result.get("crop_hash") != request["crop_hash"] or
        result.get("status") != "transcribed" or not result.get("latex", "").strip()):
        raise WorkflowReadinessError("PDF recognition", profile, "formula_probe_failed")
    return {"ready": True, "model": profile.model, "credential": profile.credential if profile.provider != "ollama" else "",
        "flow_id": binding["flow_id"], "checked_at": datetime.now(timezone.utc).isoformat(), "probe": "formula_image"}


_CHECK_CODE = '''import json
from lfx.custom import Component
from lfx.io import HandleInput, MessageTextInput, Output
from lfx.schema.message import Message

class SetupModelCheck(Component):
    display_name = "NIMA setup credential check"
    inputs = [MessageTextInput(name="request_json", required=True), HandleInput(name="model", input_types=["LanguageModel"], required=True)]
    outputs = [Output(name="result", method="run_request")]
    async def run_request(self) -> Message:
        response = await self.model.ainvoke("Reply with exactly OK. This is a credential readiness check.")
        if not response.content: raise ValueError("Model returned no response")
        return Message(text=json.dumps({"ready":True}))
'''


def check_flow(config, profile, *, embedding=False):
    from .workflow_installation import wire_model, wire_embeddings, validate_flow
    flow = json.loads((assets() / "examples/pdf_recognition.json").read_text())
    target = next(node for node in flow["data"]["nodes"] if node["data"]["type"] == "FormulaTranscription")
    definition = target["data"]["node"]
    target["data"]["type"] = "SetupModelCheck"
    code = _CHECK_CODE
    if embedding:
        code = code.replace('["LanguageModel"]', '["Embeddings"]').replace(
            'response = await self.model.ainvoke("Reply with exactly OK. This is a credential readiness check.")\n        if not response.content: raise ValueError("Model returned no response")',
            'import asyncio\n        vector = await asyncio.to_thread(self.model.embed_query, "NIMA credential readiness check")\n        if not vector: raise ValueError("Embedding returned no vector")')
        definition["template"]["model"]["input_types"] = ["Embeddings"]
    definition["template"]["code"]["value"] = code
    definition["metadata"] = {"nima_setup_check": True}
    definition["template"].pop("prompt", None)
    definition["field_order"] = ["request_json", "model"]
    flow["name"] = "NIMA setup check " + identity(profile.model_dump())[:16]
    flow["description"] = "Private installation credential readiness check; no corpus access."
    if embedding:
        wire_embeddings(flow, config)
    else:
        wire_model(flow, target, "model", profile, "setup")
    validate_flow(flow)
    return flow


def verify_installation(config, *, include_pdf=True):
    from .workflow_installation import api, ensure_credentials, _published_flow_readback
    path = Path(config.data_root) / "installation/workflow-readiness.json"
    previous = json.loads(path.read_text()) if path.is_file() else {}
    report = {"ready": False, "checks": {}, "flows": previous.get("flows", {}),
              "credentials": credential_inventory(config)}
    write_json(path, report)
    try:
        with api(config) as client:
            ensure_credentials(client, config)
            report["credentials"] = credential_inventory(config)
            profiles = [(role, profile) for role, profile in model_credentials(config) if role != "PDF recognition"]
            checked = {}
            for role, profile in profiles:
                key = identity(profile.model_dump())
                if key in checked:
                    report["checks"][role] = checked[key]
                    continue
                flow = check_flow(config, profile, embedding=role == "embeddings")
                prior = report["flows"].get(key)
                digest = identity(flow["data"])
                if prior:
                    current = _published_flow_readback(client, prior["id"])
                    if identity(current.json()["data"]) != prior["hash"]:
                        raise WorkflowReadinessError(role, profile, "edited_check_workflow")
                payload = {**flow, "is_component": False, "access_type": "PRIVATE", "mcp_enabled": False, "a2a_enabled": False, "webhook": False}
                if not prior or prior["hash"] != digest:
                    response = client.patch("/api/v1/flows/" + prior["id"], json={k:v for k,v in payload.items() if k != "name"}) if prior else client.post("/api/v1/flows/", json=payload)
                    response.raise_for_status()
                    prior = report["flows"][key] = {"id": response.json()["id"], "hash": digest}
                    write_json(path, report)
                    saved = _published_flow_readback(client, prior["id"])
                    if saved.json()["data"] != flow["data"]:
                        raise WorkflowReadinessError(role, profile, "saved_workflow_mismatch")
                result = run_saved(client, prior["id"], role, profile, {"check": "credential"})
                if result.get("ready") is not True:
                    raise WorkflowReadinessError(role, profile, "runtime_failed")
                checked[key] = {"ready": True, "model": profile.model, "credential": profile.credential if profile.provider != "ollama" else "",
                    "flow_id": prior["id"], "checked_at": datetime.now(timezone.utc).isoformat()}
                report["checks"][role] = checked[key]
            if include_pdf and config.pdf_recognition:
                binding_path = Path(config.data_root) / "pdf-service/recognition.json"
                binding = json.loads(binding_path.read_text())
                report["checks"]["PDF recognition"] = verify_recognition(client, binding, config.pdf_recognition.model)
                binding["readiness"] = report["checks"]["PDF recognition"]
                write_json(binding_path, binding)
        report["ready"] = True
        report["credentials"] = credential_inventory(config)
        write_json(path, report)
        return report
    except Exception as error:
        report["error"] = getattr(error, "diagnostic", {"code": "workflow.check_failed", "message": "Credential resolution or saved workflow verification failed; setup is incomplete."})
        write_json(path, report)
        if config.pdf_recognition:
            binding_path = Path(config.data_root) / "pdf-service/recognition.json"
            if binding_path.is_file():
                binding = json.loads(binding_path.read_text())
                binding["readiness"] = {"ready": False, "status": "setup_incomplete", "error": report["error"]}
                write_json(binding_path, binding)
        raise


def invalidate_readiness(config):
    """Keep ownership records while invalidating evidence before reconfiguration."""
    path = Path(config.data_root) / "installation/workflow-readiness.json"
    previous = json.loads(path.read_text()) if path.is_file() else {}
    write_json(path, {"ready": False, "status": "verification_pending", "checks": {},
        "flows": previous.get("flows", {}), "credentials": credential_inventory(config)})
    binding_path = Path(config.data_root) / "pdf-service/recognition.json"
    if binding_path.is_file():
        binding = json.loads(binding_path.read_text())
        binding["readiness"] = {"ready": False, "status": "verification_pending"}
        write_json(binding_path, binding)
