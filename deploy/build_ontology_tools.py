"""Build both inspectable ontology canvases; stdout only, no publication."""
import copy
import hashlib
import json
import sys

from build_tool_guide import ROOT, COMPONENTS, load_component


def build(tool):
    if tool not in ("load_ontology", "save_ontology"):
        raise ValueError("unknown ontology canvas")
    save = tool == "save_ontology"
    names = ["SaveOntologyFields", "OntologyValidate", "OntologySave"] if save else ["LoadOntologyFields", "OntologyLoad"]
    guide = json.loads((ROOT / "examples/langflow_replacement/tool_guide.json").read_text())
    nodes, snapshots = [], {}
    for name, x in (("ChatInput", 0), ("ChatOutput", (len(names) + 1) * 380)):
        node = copy.deepcopy(next(n for n in guide["data"]["nodes"] if n["data"]["type"] == name))
        identifier = name + "-" + tool
        node.update(id=identifier, position={"x": x, "y": 0})
        node["data"]["id"] = identifier
        node["data"]["node"]["display_name"] = ("Request" if name == "ChatInput" else "Result") + " · " + ("Save Ontology" if save else "Load Ontology")
        if name == "ChatInput":
            node["data"]["node"]["template"]["input_value"]["value"] = "{}"
        nodes.append(node)
    for index, name in enumerate(names, 1):
        component = load_component(name)()
        node = component.to_frontend_node()
        identifier = name + "-nima"
        node.update(id=identifier, type="genericNode", position={"x": index * 380, "y": 0})
        node["data"].update(id=identifier, type=name)
        source = (COMPONENTS / (name + ".py")).read_text()
        node["data"]["node"]["template"]["code"]["value"] = source
        metadata = {"nima_component_id": component.nima_manifest.component_id,
            "contract_version": component.nima_manifest.version,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest()}
        node["data"]["node"]["metadata"] = metadata
        snapshots[name] = metadata
        nodes.append(node)
    connections = [("ChatInput-" + tool, "message", names[0] + "-nima", "mcp_request"),
        (names[0] + "-nima", "request", names[1] + "-nima", "payload")]
    if save:
        connections.append((names[1] + "-nima", "result", names[2] + "-nima", "payload"))
    connections.append((names[-1] + "-nima", "preview", "ChatOutput-" + tool, "input_value"))
    by_id = {node["id"]: node for node in nodes}
    edges = []
    for source, output, target, field in connections:
        left, right = by_id[source], by_id[target]
        port = next(p for p in left["data"]["node"]["outputs"] if p["name"] == output)
        inp = right["data"]["node"]["template"][field]
        sh = {"dataType": left["data"]["type"], "id": source, "name": output, "output_types": port["types"]}
        th = {"fieldName": field, "id": target, "inputTypes": inp.get("input_types", []), "type": inp["type"]}
        edges.append({"id": f"{source}-{output}-{target}-{field}", "source": source, "target": target,
            "sourceHandle": json.dumps(sh).replace('"', "œ"), "targetHandle": json.dumps(th).replace('"', "œ"),
            "data": {"sourceHandle": sh, "targetHandle": th}})
    return {"name": "Save Ontology" if save else "Load Ontology", "is_component": False,
        "endpoint_name": "nima-" + tool.replace("_", "-"),
        "description": ("Validate and explicitly save immutable scoped ontology profiles. Default: validate; writes disabled. Existing registered corpus required for saves."
            if save else "List authorized profiles and load an exact name/version or digest. Read-only; packaged profiles available without a store.") + " No model calls. Canvas visually approved.",
        "data": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.7}},
        "nima_tool_manifest": {"tool_id": tool, "contract_version": "1", "visual_approval": "approved",
            "publication": "not_published_by_builder", "components": snapshots}}


if __name__ == "__main__":
    print(json.dumps(build(sys.argv[1]), indent=2, ensure_ascii=False))
