"""Build the native Research Run canvas; stdout only, no live publication."""
import copy
import hashlib
import json

from build_tool_guide import ROOT, COMPONENTS, load_component


def build():
    guide = json.loads((ROOT / "examples/langflow_replacement/tool_guide.json").read_text())
    nodes = []
    for name, identifier, x, y in (("ChatInput", "ChatInput-research-run", 0, 0),
                                  ("ChatOutput", "ChatOutput-research-run", 1100, 0)):
        node = copy.deepcopy(next(n for n in guide["data"]["nodes"] if n["data"]["type"] == name))
        node.update(id=identifier, position={"x": x, "y": y})
        node["data"]["id"] = identifier
        if name == "ChatInput":
            node["data"]["node"]["template"]["input_value"]["value"] = "{}"
        nodes.append(node)
    snapshots = {}
    for name, x in (("ResearchRunFields", 330), ("ResearchRunTool", 710)):
        component = load_component(name)()
        node = component.to_frontend_node()
        identifier = name + "-nima"
        node.update(id=identifier, type="genericNode", position={"x": x, "y": 0})
        node["data"].update(id=identifier, type=name)
        source = (COMPONENTS / (name + ".py")).read_text()
        node["data"]["node"]["template"]["code"]["value"] = source
        metadata = {"nima_component_id": component.nima_manifest.component_id,
            "contract_version": component.nima_manifest.version,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest()}
        node["data"]["node"]["metadata"] = metadata
        snapshots[name] = metadata
        nodes.append(node)
    by_id = {n["id"]: n for n in nodes}
    edges = []
    for source, output, target, field in (
        ("ChatInput-research-run", "message", "ResearchRunFields-nima", "mcp_request"),
        ("ResearchRunFields-nima", "request", "ResearchRunTool-nima", "payload"),
        ("ResearchRunTool-nima", "preview", "ChatOutput-research-run", "input_value"),
    ):
        left, right = by_id[source], by_id[target]
        port = next(p for p in left["data"]["node"]["outputs"] if p["name"] == output)
        inp = right["data"]["node"]["template"][field]
        sh = {"dataType":left["data"]["type"], "id":source, "name":output, "output_types":port["types"]}
        th = {"fieldName":field, "id":target, "inputTypes":inp.get("input_types", []), "type":inp["type"]}
        edges.append({"id":f"{source}-{output}-{target}-{field}", "source":source, "target":target,
            "sourceHandle":json.dumps(sh).replace('"', "œ"), "targetHandle":json.dumps(th).replace('"', "œ"),
            "data":{"sourceHandle":sh, "targetHandle":th}})
    return {"name":"Research Run", "description":"Create, transition, or inspect append-only research runs. Operator-owned scope and explicit audit-write toggle. No scheduling, model calls, or scientific approval. Default: inspect; writes disabled. Requires an existing NIMA_STORE_ROOT. Canvas visually approved.",
        "endpoint_name":"nima-research-run", "is_component":False,
        "data":{"nodes":nodes, "edges":edges, "viewport":{"x":0,"y":0,"zoom":0.8}},
        "nima_tool_manifest":{"tool_id":"research_run", "contract_version":"1", "visual_approval":"approved",
            "publication":"not_published_by_builder", "components":snapshots,
            "authority":"audit_record_write_only", "receipt_policy":"emitted on authorized write attempts; inspect and denied writes do not persist"}}


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, ensure_ascii=False))
