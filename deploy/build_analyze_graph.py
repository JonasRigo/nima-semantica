"""Build the deterministic native Analyze Graph canvas; stdout only."""
import copy
import hashlib
import json

from build_tool_guide import ROOT, COMPONENTS, load_component
from nima_semantica.graph_analysis import VERSION


def build():
    tool = "analyze_graph"
    names = ["GraphAnalysisFields", "AnalyzeGraph", "GraphAnalysisView", "GraphAnalysisPolicy"]
    guide = json.loads((ROOT / "examples/langflow_replacement/tool_guide.json").read_text())
    nodes, snapshots = [], {}
    for name, x in (("ChatInput", 0), ("ChatOutput", 1260)):
        node = copy.deepcopy(next(n for n in guide["data"]["nodes"] if n["data"]["type"] == name))
        identifier = name + "-graphanalysis"
        node.update(id=identifier, position={"x": x, "y": 0})
        node["data"]["id"] = identifier
        node["data"]["node"]["display_name"] = ("Request" if name == "ChatInput" else "Result") + " · Analyze Graph"
        if name == "ChatInput":
            node["data"]["node"]["template"]["input_value"]["value"] = json.dumps({"mode": "preview"})
        nodes.append(node)
    for index, name in enumerate(names, 1):
        component = load_component(name)()
        node = component.to_frontend_node()
        identifier = name + "-nima"
        positions = {"GraphAnalysisFields":(420,0), "AnalyzeGraph":(840,0),
            "GraphAnalysisView":(1260,620), "GraphAnalysisPolicy":(420,620)}
        x,y = positions[name]
        node.update(id=identifier, type="genericNode", position={"x": x, "y": y})
        node["data"].update(id=identifier, type=name)
        source = (COMPONENTS / (name + ".py")).read_text()
        node["data"]["node"]["template"]["code"]["value"] = source
        metadata = {"nima_component_id": getattr(getattr(component,"nima_manifest",None),"component_id",name),
            "contract_version": component.nima_manifest.version,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest()}
        node["data"]["node"]["metadata"] = metadata
        snapshots[name] = metadata
        nodes.append(node)
    connections = [("ChatInput-graphanalysis", "message", "GraphAnalysisFields-nima", "mcp_request"),
        ("GraphAnalysisFields-nima", "request", "AnalyzeGraph-nima", "payload"),
        ("AnalyzeGraph-nima", "result", "GraphAnalysisView-nima", "payload"),
        ("GraphAnalysisPolicy-nima", "result", "AnalyzeGraph-nima", "policy"),
        ("AnalyzeGraph-nima", "preview", "ChatOutput-graphanalysis", "input_value")]
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
    return {"name": "Analyze Graph", "is_component": False,
        "endpoint_name": "nima-analyze-graph",
        "description": "Deterministic scoped graph analysis with exact source/ontology validation, inspectable translation and bounded Semantica dependency reasoning. Conditional hypotheses retain provenance; no scientific admission, LLM or graph mutation. Preview default; visual approval pending.",
        "data": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.6}},
        "nima_tool_manifest": {"tool_id": tool, "contract_version": "1", "visual_approval": "pending",
            "controller_version":VERSION,
            "native_sources_sha256":{name:hashlib.sha256((ROOT/"src/nima_semantica"/(name+".py")).read_bytes()).hexdigest()
                for name in ("graph_analysis","graph_reasoning","graph_service","ontology_services","reasoning_kernel","evidence_contracts")},
            "publication": "not_published_by_builder", "components": snapshots}}


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, ensure_ascii=False))

