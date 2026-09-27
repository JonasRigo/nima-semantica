"""Serialize an adapted copy; stdout only, never modifies the reference flow."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "references/Graph_Extraction_Strcutured_Output.json"


def ontology_namespace_fix(source):
    """Approved adapted-copy-only fix for Langflow's dynamic model namespace."""
    anchor = "        descriptor = {\n"
    if source.count(anchor) != 1:
        raise ValueError("reference ontology changed; review the compatibility fix")
    return source.replace(anchor,
        '        model.model_rebuild(_types_namespace={"Literal": Literal, "Any": Any})\n\n' + anchor)


def load_component(name):
    path = ROOT / f"deploy/langflow_components/nima_workflows/{name}.py"
    spec = importlib.util.spec_from_file_location(f"nima_example_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, name)


def adapter_node(name, x, y):
    component = load_component(name)()
    node = component.to_frontend_node()
    identifier = f"{name}-nima-adapter"
    node.update(id=identifier, type="genericNode", position={"x": x, "y": y})
    node["data"].update(id=identifier, type=name)
    source = (ROOT / f"deploy/langflow_components/nima_workflows/{name}.py").read_text()
    node["data"]["node"]["template"]["code"]["value"] = source
    node["data"]["node"]["metadata"] = {
        "nima_component_id": component.nima_manifest.component_id,
        "contract_version": component.nima_manifest.version,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
    }
    return node


def connect(nodes, source, output, target, field):
    left, right = nodes[source], nodes[target]
    port = next(o for o in left["data"]["node"]["outputs"] if o["name"] == output)
    inp = right["data"]["node"]["template"][field]
    sh = {"dataType": left["data"]["type"], "id": source, "name": output, "output_types": port["types"]}
    th = {"fieldName": field, "id": target, "inputTypes": inp.get("input_types", []), "type": inp["type"]}
    return {"id": f"nima-{source}-{output}-{target}-{field}", "source": source, "target": target,
            "sourceHandle": json.dumps(sh).replace('"', "œ"), "targetHandle": json.dumps(th).replace('"', "œ"),
            "data": {"sourceHandle": sh, "targetHandle": th}}


def build(route="file"):
    if route not in ("file", "retrieval"):
        raise ValueError("unknown example route")
    original = json.loads(REFERENCE.read_text())
    flow = copy.deepcopy(original)
    flow.pop("id", None)
    flow["name"] = f"NIMA imported graph extraction ({route})"
    flow["description"] = "Adapted reference, not a published tool. Configure source, model, and NIMA store before execution."
    data = flow["data"]
    for name, x, y in (("InputNormalizer", 0, 0), ("GraphRetrievalNormalizer", 0, 450), ("OutputNormalizer", 2000, 0)):
        data["nodes"].append(adapter_node(name, x, y))
    nodes = {n["id"]: n for n in data["nodes"]}
    compatibility = []
    for node in data["nodes"]:
        if "OntologyManager" not in node["data"]["type"]:
            continue
        field = node["data"]["node"]["template"]["code"]
        original_source = field["value"]
        field["value"] = ontology_namespace_fix(original_source)
        compatibility.append({"node_id": node["id"], "fix": "pydantic_types_namespace",
            "original_sha256": hashlib.sha256(original_source.encode()).hexdigest(),
            "adapted_sha256": hashlib.sha256(field["value"].encode()).hexdigest()})
    inp, retrieval, out = (f"{name}-nima-adapter" for name in ("InputNormalizer", "GraphRetrievalNormalizer", "OutputNormalizer"))
    nodes[inp]["data"]["node"]["template"]["corpus_id"]["value"] = "papers"
    nodes[inp]["data"]["node"]["template"]["project_id"]["value"] = "imported-flow"
    nodes[inp]["data"]["node"]["template"]["ontology_profile"]["value"] = "claim_obligation"
    nodes[out]["data"]["node"]["template"]["preset"]["value"] = "graph_extraction"
    data["edges"] = [e for e in data["edges"] if not (e["source"] == "File-CEQh8" and e["target"] == "ParserComponent-3Hm0b")]
    entry = ("File-CEQh8", "advanced_dataframe") if route == "file" else ("ChatInput-nPDyc", "message")
    bindings = [(*entry, inp, "input_value"),
                (inp, "context", retrieval, "context"),
                ("ChatInput-nPDyc", "message", retrieval, "query"),
                (inp if route == "file" else retrieval, "rows", "ParserComponent-3Hm0b", "input_data"),
                (inp if route == "file" else retrieval, "context" if route == "file" else "context_out", out, "context"),
                ("ext:graph_extraction:StructuredOutputComponent@extra-foE8l", "structured_output", out, "input_value")]
    data["edges"].extend(connect(nodes, *binding) for binding in bindings)
    flow["nima_adapter_manifest"] = {
        "schema_version": 1, "route": route, "reference_sha256": hashlib.sha256(REFERENCE.read_bytes()).hexdigest(),
        "preserved_node_ids": [n["id"] for n in original["data"]["nodes"]],
        "compatibility_fixes": compatibility,
        "boundary_replacement": {"source": "File-CEQh8", "target": "ParserComponent-3Hm0b"},
        "authority": "proposal_only", "published": False,
    }
    return flow


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="New directory for generated examples; existing directories are refused")
    args = parser.parse_args()
    flows = {route: build(route) for route in ("file", "retrieval")}
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=False)
        for route, flow in flows.items():
            (args.output_dir / f"graph_extraction_{route}.json").write_text(json.dumps(flow, indent=2, ensure_ascii=False) + "\n")
        print(f"Generated {len(flows)} reference-preserving examples in {args.output_dir}")
    else:
        print(json.dumps(flows, ensure_ascii=False))
