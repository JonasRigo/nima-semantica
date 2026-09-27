"""Build the inspectable read-only evidence reader; stdout only."""
import copy
import hashlib
import json

from build_tool_guide import ROOT, COMPONENTS, load_component


def build():
    tool = "read_evidence"
    names = ["EvidenceReadFields", "ReadEvidence"]
    guide = json.loads((ROOT / "examples/langflow_replacement/tool_guide.json").read_text())
    nodes, snapshots = [], {}
    for name, x in (("ChatInput", 0), ("ChatOutput", 1260)):
        node = copy.deepcopy(next(n for n in guide["data"]["nodes"] if n["data"]["type"] == name))
        identifier = name + "-evidence"
        node.update(id=identifier, position={"x": x, "y": 0})
        node["data"]["id"] = identifier
        node["data"]["node"]["display_name"] = ("Request" if name == "ChatInput" else "Result") + " · Evidence"
        if name == "ChatInput":
            node["data"]["node"]["template"]["input_value"]["value"] = json.dumps({"region_id": "557bdbcc15fbac649d974c5bd7ac85b9f59c3e60d69853b57c1fd36dfa8dd273"})
        nodes.append(node)
    for index, name in enumerate(names, 1):
        component = load_component(name)()
        node = component.to_frontend_node()
        identifier = name + "-nima"
        node.update(id=identifier, type="genericNode", position={"x": index * 420, "y": 0})
        node["data"].update(id=identifier, type=name)
        source = (COMPONENTS / (name + ".py")).read_text()
        node["data"]["node"]["template"]["code"]["value"] = source
        metadata = {"nima_component_id": component.nima_manifest.component_id,
            "contract_version": component.nima_manifest.version,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest()}
        node["data"]["node"]["metadata"] = metadata
        snapshots[name] = metadata
        nodes.append(node)
    connections = [("ChatInput-evidence", "message", "EvidenceReadFields-nima", "mcp_request"),
        ("EvidenceReadFields-nima", "request", "ReadEvidence-nima", "payload"),
        ("ReadEvidence-nima", "preview", "ChatOutput-evidence", "input_value")]
    by_id = {n["id"]: n for n in nodes}
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
    return {"name": "Read Evidence", "is_component": False,
        "endpoint_name": "nima-read-evidence",
        "description": "Authenticated published-artifact and exact source-region reads with bounded direct provenance and inert rendering. No writes or model calls. Canvas visually approved.",
        "data": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.6}},
        "nima_tool_manifest": {"tool_id": tool, "contract_version": "1", "visual_approval": "approved",
            "publication": "not_published_by_builder", "components": snapshots}}


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, ensure_ascii=False))
