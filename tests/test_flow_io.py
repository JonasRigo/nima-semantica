"""Saved-canvas port, redaction, and real-editor regression checks."""
import copy
import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))
import flow_io as p


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module




def test_foundational_reference_flows_are_saved_and_edge_validated():
    reference = ROOT / "examples/langflow_reference"
    files = sorted(reference.glob("*.json"))
    assert [path.name for path in files] == [
        "graph_commit.json",
        "graph_retrieval_normalizer.json",
        "hypothesis_comparison.json",
        "hypothesis_generation.json",
        "input_normalizer.json",
    ]
    for path in files:
        value = json.loads(path.read_text())
        p.validate_edges(value)
        boundary = path.stem.title().replace("_", "")
        expected = {
            "GraphCommit": "GraphCommit",
            "GraphRetrievalNormalizer": "GraphRetrievalNormalizer",
            "HypothesisComparison": "HypothesisComparison",
            "HypothesisGeneration": "HypothesisGeneration",
            "InputNormalizer": "InputNormalizer",
        }[boundary]
        assert [(node["id"], node["data"]["type"]) for node in value["data"]["nodes"]] == [
            ("Boundary", expected),
            ("Result", "ChatOutput"),
        ]
        assert [
            (edge["source"], edge["data"]["sourceHandle"]["name"], edge["target"], edge["data"]["targetHandle"]["fieldName"])
            for edge in value["data"]["edges"]
        ] == [("Boundary", "preview", "Result", "input_value")]

@pytest.fixture
def flow():
    nodes = []
    for name, inputs, outputs in (
        ("ChatInput", {"input_value": {"type": "str", "value": "{}"}}, [{"name": "message", "types": ["Message"], "method": "message"}]),
        ("ResearchTool", {"input_value": {"type": "other", "input_types": ["Message"], "value": ""},
                          "service_token": {"type": "str", "value": "", "password": True}},
         [{"name": "result", "types": ["Message"], "method": "result"}]),
        ("ChatOutput", {"input_value": {"type": "other", "input_types": ["Message"], "value": ""}}, []),
    ):
        inputs["code"] = {"type": "code", "value": "from nima_semantica.tools import ResearchTool" if name == "ResearchTool" else "native"}
        nodes.append({"id": name, "data": {"type": name, "selected_output": outputs[0]["name"] if outputs else None,
                      "node": {"name": name, "template": inputs, "outputs": outputs}}})
    edges = []
    for source, target, output in (("ChatInput", "ResearchTool", "message"), ("ResearchTool", "ChatOutput", "result")):
        handles = {"sourceHandle": {"id": source, "name": output, "output_types": ["Message"]},
                   "targetHandle": {"id": target, "fieldName": "input_value", "inputTypes": ["Message"], "type": "other"}}
        edges.append({"id": source + target, "source": source, "target": target, "data": handles,
                      **{k: json.dumps(v).replace('"', "œ") for k, v in handles.items()}})
    return {"name": "NIMA Research", "description": "JSON payload contract", "endpoint_name": "research_tool",
            "data": {"nodes": nodes, "edges": edges}}


@pytest.fixture
def palette(flow):
    return {"native": {n["data"]["type"]: copy.deepcopy(n["data"]["node"]) for n in flow["data"]["nodes"] if n["id"] != "ResearchTool"},
            "NIMA Research Tools": {"ResearchTool": copy.deepcopy(flow["data"]["nodes"][1]["data"]["node"])}}


def test_bind_palette_installed_code_and_metadata(flow, palette):
    installed = palette["NIMA Research Tools"]["ResearchTool"]
    installed["template"]["code"]["value"] += "\n# installed revision"
    installed["extension_version"] = "1.0"
    bindings = p.bind_palette(flow, palette)
    assert flow["data"]["nodes"][1]["data"]["node"]["extension_version"] == "1.0"
    assert bindings[1]["category"] == "NIMA Research Tools"
    assert len(bindings[1]["code_sha256"]) == 64


def test_bind_palette_accepts_current_langflow_extension_group_names(flow, palette):
    palette["nima_research_tools"] = palette.pop("NIMA Research Tools")
    bindings = p.bind_palette(flow, palette)
    assert bindings[1]["category"] == "nima_research_tools"


@pytest.mark.parametrize("problem", ["missing", "ambiguous", "input", "output"])
def test_palette_rejects_incompatible_components(flow, palette, problem):
    installed = palette["NIMA Research Tools"]["ResearchTool"]
    if problem == "missing":
        palette.pop("NIMA Research Tools")
    elif problem == "ambiguous":
        palette["NIMA Duplicate"] = {"ResearchTool": installed}
    elif problem == "input":
        installed["template"]["input_value"]["input_types"] = ["Data"]
    else:
        installed["outputs"][0]["types"] = ["Data"]
    with pytest.raises(ValueError):
        p.bind_palette(flow, palette)


@pytest.mark.parametrize("problem", ["dangling", "handle", "port", "hidden", "duplicate", "type"])
def test_edges_reject_editor_breakage(flow, problem):
    edge = flow["data"]["edges"][0]
    if problem == "dangling":
        edge["target"] = "missing"
    elif problem == "handle":
        edge["sourceHandle"] = "{}"
    elif problem == "port":
        flow["data"]["nodes"][0]["data"]["node"]["outputs"] = []
    elif problem == "hidden":
        flow["data"]["nodes"][0]["data"]["selected_output"] = "other"
    elif problem == "duplicate":
        flow["data"]["edges"].append(copy.deepcopy(edge))
    else:
        flow["data"]["nodes"][1]["data"]["node"]["template"]["input_value"]["input_types"] = ["Data"]
    with pytest.raises(ValueError):
        p.validate_edges(flow)


def test_secrets_redacted_without_changing_original(flow, tmp_path):
    template = flow["data"]["nodes"][1]["data"]["node"]["template"]
    template["service_token"]["value"] = "private-secret"
    template["api_key"] = {"value": "another-secret"}
    template["max_output_tokens"] = {"value": 32768}
    public = p.redacted(flow)
    assert "private-secret" not in json.dumps(public) and "another-secret" not in json.dumps(public)
    assert template["service_token"]["value"] == "private-secret"
    assert public["data"]["nodes"][1]["data"]["node"]["template"]["max_output_tokens"]["value"] == 32768
    path = tmp_path / "private.json"
    path.touch(mode=0o644)
    p.write_json(path, flow)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_database_credential_reference_is_not_a_literal_secret():
    assert p.credential_reference("api_key", {"load_from_db": True, "value": "PROVIDER_KEY"})
    assert not p.credential_reference("api_key", {"value": "literal-secret"})
