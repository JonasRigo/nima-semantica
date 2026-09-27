"""Build the inspectable source preparation pipeline; stdout only."""
import copy
import hashlib
import json

from build_tool_guide import ROOT, COMPONENTS, load_component


def build():
    tool = "prepare_and_index_sources"
    names = ["SourceFields", "PrepareSources", "EmbedSourceRegions", "BuildSourceProjection"]
    guide = json.loads((ROOT / "examples/langflow_replacement/tool_guide.json").read_text())
    nodes, snapshots = [], {}
    for name, x in (("ChatInput", 0), ("ChatOutput", 2100)):
        node = copy.deepcopy(next(n for n in guide["data"]["nodes"] if n["data"]["type"] == name))
        identifier = name + "-sources"
        node.update(id=identifier, position={"x": x, "y": 0})
        node["data"]["id"] = identifier
        node["data"]["node"]["display_name"] = ("Request" if name == "ChatInput" else "Result") + " · Sources"
        if name == "ChatInput":
            node["data"]["node"]["template"]["input_value"]["value"] = "{}"
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
    connections = [("ChatInput-sources", "message", "SourceFields-nima", "mcp_request"),
        ("SourceFields-nima", "request", "PrepareSources-nima", "payload"),
        ("PrepareSources-nima", "result", "EmbedSourceRegions-nima", "payload"),
        ("EmbedSourceRegions-nima", "result", "BuildSourceProjection-nima", "payload"),
        ("BuildSourceProjection-nima", "preview", "ChatOutput-sources", "input_value")]
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
    return {"name": "Prepare and Index Sources", "is_component": False,
        "endpoint_name": "nima-prepare-and-index-sources",
        "description": "Preview or explicitly prepare corpus-wide source bytes and exact regions, optionally embed with an operator-connected model, and publish revision-bound projections. Default: no-write preview. PDF worker and embedding calls disabled. Visually approved.",
        "data": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.6}},
        "nima_tool_manifest": {"tool_id": tool, "contract_version": "1", "visual_approval": "approved",
            "publication": "not_published_by_builder", "components": snapshots}}


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, ensure_ascii=False))
