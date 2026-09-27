"""Namespaced, explicitly installed custom Langflow workflows."""
import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator
from jsonschema import Draft202012Validator

from .installation import project_binding, project_path, write_json
from .workflow_installation import api, configure_flow, validate_flow, ensure_credentials


class WorkflowManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    name: str = Field(max_length=28, pattern=r"^[a-z][a-z0-9_-]+\.[a-z][a-z0-9_-]+$")
    version: str = Field(min_length=1)
    canvas: str
    description: str = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    input_schema: dict
    output_schema: dict

    @field_validator("input_schema", "output_schema")
    @classmethod
    def schema_valid(cls, value):
        Draft202012Validator.check_schema(value)
        return value


def handle(args, config):
    path = Path(args.file).resolve()
    manifest = WorkflowManifest.model_validate_json(path.read_text())
    canvas = (path.parent / manifest.canvas).resolve()
    if not canvas.is_relative_to(path.parent) or not canvas.is_file():
        raise ValueError("canvas must be a file inside the workflow package")
    flow = json.loads(canvas.read_text())
    configured, assignments = configure_flow(flow, manifest.name, config, getattr(args, "corpus", "preview"), getattr(args, "project", "preview"))
    available = {name for node in configured["data"]["nodes"] for name in node["data"]["node"]["template"] if name.startswith("allow_")}
    if set(manifest.capabilities) - available:
        raise ValueError("Workflow declares capabilities absent from its canvas")
    # Installation enables only explicitly declared custom-flow capabilities.
    for node in configured["data"]["nodes"]:
        for name, field in node["data"]["node"]["template"].items():
            if name.startswith("allow_") and isinstance(field, dict):
                field["value"] = name in manifest.capabilities
    validate_flow(configured)
    if args.action == "validate":
        print(json.dumps({"valid": True, "name": manifest.name, "models": assignments}, indent=2)); return 0
    binding = project_binding(config, args.corpus, args.project)
    directory = project_path(config, args.corpus, args.project)
    publication = json.loads((directory / "publication.json").read_text())
    installed = directory / "workflows" / (manifest.name + ".json")
    digest = hashlib.sha256(canvas.read_bytes() + path.read_bytes()).hexdigest()
    if installed.exists():
        previous = json.loads(installed.read_text())
        if previous["digest"] != digest:
            raise ValueError("Workflow already installed with different content; retain a new version/name or reconcile explicitly")
        print(json.dumps(previous, indent=2)); return 0
    with api(config) as client:
        ensure_credentials(client, config)
        payload = {"name": manifest.name, "description": manifest.description, "data": configured["data"],
                   "folder_id": publication["folder_id"], "is_component": False, "access_type": "PRIVATE", "mcp_enabled": True, "a2a_enabled": False, "webhook": False}
        response = client.post("/api/v1/flows/", json=payload); response.raise_for_status()
        flow_id = response.json()["id"]
        readback = client.get(f"/api/v1/flows/{flow_id}"); readback.raise_for_status()
        if readback.json()["data"] != payload["data"]:
            raise ValueError("Custom flow readback differs")
    result = {"name": manifest.name, "flow_id": flow_id, "digest": digest, "manifest": manifest.model_dump(), "models": assignments}
    write_json(installed, result)
    print(json.dumps(result, indent=2)); return 0
