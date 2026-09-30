"""Configure and install explicit Langflow canvases from a maintained inventory."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import time

import httpx

from .installation import assets, write_json


def maintained_flows():
    root = assets() / "examples" / "langflow_replacement"
    from .tool_guide import public_tool_catalog
    # Tool Guide is the maintained public inventory; graph services use stdio MCP.
    result = {}
    for item in public_tool_catalog():
        if item.tool_id in {"calculate_mathematics", "develop_proof", "conduct_proof", "draft_proof"}:
            continue
        path = root / Path(item.canvas_path).name
        if item.tool_id == "deep_research":
            path = root / "deep_research_simple.json"
        if path.is_file():
            result[item.tool_id] = path
        else:
            raise ValueError(f"missing maintained canvas: {path}")
    return result


def handles(flow):
    for node in flow["data"]["nodes"]:
        template = node["data"]["node"]["template"]
        for field, spec in template.items():
            if isinstance(spec, dict) and "LanguageModel" in spec.get("input_types", []):
                yield node, field


def model_inventory():
    return {name: [f"{node['id']}.{field}" for node, field in handles(json.loads(path.read_text()))]
            for name, path in maintained_flows().items()}


def wire_embeddings(flow, installation):
    profile = installation.embedding.model_copy(update={"base_url": installation.embedding.container_url or installation.embedding.base_url})
    manifest = {"provider": profile.provider, "model": profile.model, "revision": profile.revision,
                "parameters": {}, "dimension": profile.dimension, "normalization": "l2"}
    for target in list(flow["data"]["nodes"]):
        template = target["data"]["node"]["template"]
        for field, spec in template.items():
            if not isinstance(spec, dict) or "Embeddings" not in spec.get("input_types", []):
                continue
            node = json.loads((assets() / "examples/configured_embeddings_node.json").read_text())
            mid = "ConfiguredEmbeddings-" + target["id"]
            node.update(id=mid, type="genericNode", position={"x": 0, "y": -500})
            node["data"].update(id=mid, type="ConfiguredEmbeddings", selected_output="embeddings")
            for output in node["data"]["node"]["outputs"]:
                output["hidden"] = False
            node["data"]["node"]["template"]["profile_json"]["value"] = profile.model_dump_json()
            credential = node["data"]["node"]["template"]["api_key"]
            credential["value"] = profile.credential if profile.provider != "ollama" else ""
            credential["load_from_db"] = profile.provider != "ollama" and bool(profile.credential)
            flow["data"]["nodes"].append(node)
            flow["data"]["edges"] = [e for e in flow["data"]["edges"] if not (e["target"] == target["id"] and e["data"]["targetHandle"]["fieldName"] == field)]
            sh = {"dataType": "ConfiguredEmbeddings", "id": mid, "name": "embeddings", "output_types": ["Embeddings"]}
            th = {"fieldName": field, "id": target["id"], "inputTypes": spec["input_types"], "type": spec["type"]}
            flow["data"]["edges"].append({"id": f"{mid}-{field}", "source": mid, "target": target["id"],
                "sourceHandle": json.dumps(sh).replace('"', "œ"), "targetHandle": json.dumps(th).replace('"', "œ"), "data": {"sourceHandle": sh, "targetHandle": th}})
            if "manifest_json" in template:
                template["manifest_json"]["value"] = json.dumps(manifest)


def wire_model(flow, target, field, profile, role):
    template = json.loads((assets() / "examples/langflow_replacement/review_research.json").read_text())
    model = copy.deepcopy(next(n for n in template["data"]["nodes"] if n["data"]["type"] == "OpenAIModel"))
    native = profile.provider in {"openai", "openrouter", "compatible", "anthropic", "gemini", "ollama"}
    kind = "ConfiguredModel" if native else "OpenAIModel"
    if native:
        model = json.loads((assets() / "examples/configured_embeddings_node.json").read_text())
        definition = model["data"]["node"]
        definition["base_classes"] = ["LanguageModel"]
        definition["template"]["code"]["value"] = (assets() / "deploy/langflow_components/nima_tools/ConfiguredModel.py").read_text()
        definition["template"]["profile_json"]["value"] = profile.model_dump_json()
        definition["template"]["profile_json"]["display_name"] = "Model profile"
        definition["template"]["api_key"]["display_name"] = "Model credential"
        definition["outputs"] = [{**definition["outputs"][0], "name":"model_output", "display_name":"Language Model",
            "method":"build_model", "types":["LanguageModel"], "selected":"LanguageModel", "hidden":False}]
        definition["metadata"] = {"nima_component_id":"configured_model", "contract_version":"1"}
        definition["field_order"] = ["profile_json", "api_key"]
        model["data"].update(type=kind, selected_output="model_output")
    mid = kind + "-nima-" + hashlib.sha256(role.encode()).hexdigest()[:12]
    model["type"] = "genericNode"
    model["id"] = mid
    model["data"]["id"] = mid
    model["data"]["node"]["display_name"] = "Tool model · " + profile.provider
    model["data"]["node"]["description"] = "Configured model: " + profile.model
    model["position"] = {"x": target.get("position", {}).get("x", 0) - 350, "y": target.get("position", {}).get("y", 0) - 250}
    values = model["data"]["node"]["template"]
    if not native:
        for key, value in {"model_name": profile.model, "openai_api_base": profile.container_url or profile.base_url,
                           "max_tokens": profile.max_tokens, "model_kwargs": profile.parameters, "max_retries": 0}.items():
            values[key]["value"] = value
    authenticated = profile.provider != "ollama" and bool(profile.credential)
    values["api_key"]["value"] = profile.credential if authenticated else ("" if native else "local")
    values["api_key"]["load_from_db"] = authenticated
    flow["data"]["edges"] = [e for e in flow["data"]["edges"]
                              if not (e["target"] == target["id"] and e.get("data", {}).get("targetHandle", {}).get("fieldName") == field)]
    flow["data"]["nodes"].append(model)
    source_handle = {"dataType": kind, "id": mid, "name": "model_output", "output_types": ["LanguageModel"]}
    target_handle = {"fieldName": field, "id": target["id"], "inputTypes": target["data"]["node"]["template"][field]["input_types"], "type": "other"}
    edge = {"id": f"{mid}-{target['id']}-{field}", "source": mid, "target": target["id"],
            "data": {"sourceHandle": source_handle, "targetHandle": target_handle},
            "sourceHandle": json.dumps(source_handle).replace('"', "œ"), "targetHandle": json.dumps(target_handle).replace('"', "œ")}
    flow["data"]["edges"].append(edge)


def configure_flow(flow, name, installation, corpus, project):
    flow = copy.deepcopy(flow)
    # Remove previous model nodes after discarding their model-input edges below.
    old_models = {n["id"] for n in flow["data"]["nodes"] if n["data"]["type"] in {"OpenAIModel", "ConfiguredModel"}}
    flow["data"]["nodes"] = [n for n in flow["data"]["nodes"] if n["id"] not in old_models]
    flow["data"]["edges"] = [e for e in flow["data"]["edges"] if e["source"] not in old_models and e["target"] not in old_models]
    assignments = {}
    for node, field in list(handles(flow)):
        role = f"{name}:{node['id']}.{field}"
        profile = installation.overrides.get(role, installation.overrides.get(name, installation.llm))
        wire_model(flow, node, field, profile, role)
        manifest = {"provider": profile.provider, "model": profile.model, "revision": profile.model,
                    "parameters": {"max_tokens": profile.max_tokens, **profile.parameters,
                        "context_window": profile.context_window, "request_timeout_seconds": profile.request_timeout_seconds}}
        template = node["data"]["node"]["template"]
        if "model_manifest_json" in template:
            template["model_manifest_json"]["value"] = json.dumps(manifest)
        assignments[role] = profile.model_dump()
    for node in flow["data"]["nodes"]:
        template = node["data"]["node"]["template"]
        values = {"corpus_id": corpus, "project_id": project, "pdf_url": installation.pdf_container_url or installation.pdf_url,
                  "pdf_token_file": installation.pdf_token_file,
                  "discovery_providers_json": json.dumps(installation.discovery.discovery_providers),
                  "max_discoveries": installation.discovery.max_discoveries,
                  "acquisition_policy_json": installation.acquisition.model_dump_json(),
                  "allow_fast_read": installation.allow_fast_read}
        for field, value in values.items():
            if field in template:
                template[field]["value"] = value
        # Model and audit capabilities are operator-configured at installation.
        # Graph commits retain their separate exact approval requirements.
        for field in ("allow_model_calls", "allow_audit_writes", "allow_attempt_writes", "allow_artifact_writes", "allow_corpus_writes", "allow_writes", "allow_projection_writes", "allow_graph_writes"):
            if field in template:
                template[field]["value"] = True
        if node["data"]["type"].endswith("Retrieval") and "enabled" in template:
            template["enabled"]["value"] = True
        if node["data"]["type"] == "LeanDraftVerification":
            template["enabled"]["value"] = bool(installation.lean_url)
        if node["data"]["type"] == "LeanSearch":
            template["enabled"]["value"] = installation.allow_lean_search
            template["allow_query_disclosure"]["value"] = installation.allow_lean_search
        if "allow_substantiation" in template:
            template["allow_substantiation"]["value"] = True
        if "allow_pdf" in template:
            template["allow_pdf"]["value"] = bool(installation.pdf_url)
        if "allow_execution" in template:
            template["allow_execution"]["value"] = bool(installation.lean_url if node["data"]["type"] == "VerifyLean" else installation.symbolic_url)
        if "allow_embeddings" in template:
            template["allow_embeddings"]["value"] = installation.embedding is not None
    if installation.embedding is not None:
        wire_embeddings(flow, installation)
    from .tool_guide import public_tool_catalog
    flow["name"] = next((item.name for item in public_tool_catalog() if item.tool_id == name), name)
    return flow, assignments


def validate_flow(flow):
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location("nima_flow_io", assets() / "deploy/flow_io.py")
    module = module_from_spec(spec); spec.loader.exec_module(module)
    module.validate_edges(flow)
    node_ids = {n["id"] for n in flow["data"]["nodes"]}
    if len(node_ids) != len(flow["data"]["nodes"]):
        raise ValueError("duplicate node IDs")
    for node, field in handles(flow):
        incoming = [e for e in flow["data"]["edges"] if e["target"] == node["id"] and e["data"]["targetHandle"]["fieldName"] == field]
        if len(incoming) != 1:
            raise ValueError(f"unconnected or ambiguous model input: {node['id']}.{field}")
    return True


def api(installation):
    token = os.environ.get(installation.langflow_api_key_env)
    return httpx.Client(base_url=installation.langflow_url.rstrip("/"),
                        headers={"x-api-key": token} if token else {}, timeout=120)


def ensure_credentials(client, installation):
    import getpass
    import sys
    response = client.get("/api/v1/variables/"); response.raise_for_status()
    existing = {v["name"] for v in response.json() if v.get("has_value", True)}
    names = {p.credential for p in [installation.llm, *installation.overrides.values()] if p.provider != "ollama" and p.credential}
    if installation.embedding and installation.embedding.provider != "ollama" and installation.embedding.credential:
        names.add(installation.embedding.credential)
    for name in sorted(names - existing):
        value = os.environ.get(name)
        if not value and sys.stdin.isatty():
            value = getpass.getpass(f"Credential for {name} (stored privately in Langflow): ")
        if not value:
            raise ValueError(f"Set {name} in the environment or create that Credential in Langflow")
        response = client.post("/api/v1/variables/", json={"name": name, "value": value, "type": "Credential", "default_fields": []})
        response.raise_for_status()


def _published_flow_readback(client, identifier):
    """Retry only a just-written flow's transient visibility, never its write."""
    for delay in (0, 0.2, 0.5, 1.0, 2.0):
        if delay:
            time.sleep(delay)
        response = client.get(f"/api/v1/flows/{identifier}")
        if response.status_code != 404:
            break
    response.raise_for_status()
    return response


def publish_flows(installation, corpus, project, directory):
    directory = Path(directory)
    state_path = directory / "publication.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"flows": {}}
    with api(installation) as client:
        ensure_credentials(client, installation)
        if not state.get("folder_id"):
            response = client.post("/api/v1/projects/", json={"name": f"NIMA {corpus}/{project}", "description": "Project-bound NIMA toolbox"})
            response.raise_for_status(); state["folder_id"] = response.json()["id"]
            write_json(state_path, state)
        for name, path in maintained_flows().items():
            flow, assignments = configure_flow(json.loads(path.read_text()), name, installation, corpus, project)
            validate_flow(flow)
            payload = {key: flow[key] for key in ("name", "description", "data") if key in flow}
            payload.update(folder_id=state["folder_id"], is_component=False, access_type="PRIVATE", mcp_enabled=True, a2a_enabled=False, webhook=False)
            previous = state["flows"].get(name)
            if previous:
                pending = previous.get("pending_verification", False)
                current = (_published_flow_readback(client, previous["id"]) if pending else
                           client.get(f"/api/v1/flows/{previous['id']}"))
                current.raise_for_status()
                current_hash = hashlib.sha256(json.dumps(current.json()["data"], sort_keys=True).encode()).hexdigest()
                if current_hash != previous["hash"]:
                    raise ValueError(f"Live flow {name} changed in the editor; export and reconcile before updating")
                if pending:
                    previous.pop("pending_verification")
                    write_json(state_path, state)
                    if current.json()["data"] == payload["data"]:
                        # Resume verification of the exact successful write;
                        # do not POST another flow or repeat a completed PATCH.
                        continue
                # Langflow assigns globally unique names (including suffixes
                # for another project's toolbox). Preserve that saved name.
                payload.pop("name", None)
                response = client.patch(f"/api/v1/flows/{previous['id']}", json=payload)
            else:
                response = client.post("/api/v1/flows/", json=payload)
            response.raise_for_status()
            identifier = response.json()["id"]
            state["flows"][name] = {"id": identifier,
                "hash": hashlib.sha256(json.dumps(payload["data"], sort_keys=True).encode()).hexdigest(),
                "models": assignments, "pending_verification": True}
            write_json(state_path, state)
            readback = _published_flow_readback(client, identifier)
            actual = readback.json()["data"]
            if actual != payload["data"]:
                raise ValueError(f"Saved flow {name} differs from its readback")
            state["flows"][name] = {"id": identifier, "hash": hashlib.sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest(), "models": assignments}
            write_json(state_path, state)
    state["mcp_url"] = installation.langflow_url.rstrip("/") + f"/api/v1/mcp/project/{state['folder_id']}/streamable"
    write_json(state_path, state)
    return state
