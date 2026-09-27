"""Build the inspectable native comparison ontology-state agent; stdout only."""
import copy
import hashlib
import json

from build_tool_guide import ROOT, COMPONENTS, load_component
from nima_semantica.comparison_contracts import VERSION
from nima_semantica.comparison_state import POLICY_DIGEST


def build():
    tool = "compare_research_objects"
    names = ["ComparisonFields", "CompareResearchObjects", "ComparisonOntology", "ComparisonStateView", "ComparisonRetrieval"]
    guide = json.loads((ROOT / "examples/langflow_replacement/tool_guide.json").read_text())
    nodes, snapshots = [], {}
    for name, x in (("ChatInput", 0), ("ChatOutput", 1260)):
        node = copy.deepcopy(next(n for n in guide["data"]["nodes"] if n["data"]["type"] == name))
        identifier = name + "-comparison"
        node.update(id=identifier, position={"x": x, "y": 0})
        node["data"]["id"] = identifier
        node["data"]["node"]["display_name"] = ("Request" if name == "ChatInput" else "Result") + " · Compare Research Objects"
        if name == "ChatInput":
            node["data"]["node"]["template"]["input_value"]["value"] = json.dumps({"mode": "preview"})
        nodes.append(node)
    for index, name in enumerate(names, 1):
        component = load_component(name)()
        node = component.to_frontend_node()
        identifier = name + "-nima"
        positions = {"ComparisonFields":(420,0), "CompareResearchObjects":(840,0),
            "ComparisonOntology":(420,620), "ComparisonStateView":(1260,620), "ComparisonRetrieval":(0,620)}
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
    connections = [("ChatInput-comparison", "message", "ComparisonFields-nima", "mcp_request"),
        ("ComparisonFields-nima", "request", "CompareResearchObjects-nima", "payload"),
        ("ComparisonOntology-nima", "result", "CompareResearchObjects-nima", "policy"),
        ("ComparisonRetrieval-nima", "result", "CompareResearchObjects-nima", "retrieval_policy"),
        ("CompareResearchObjects-nima", "result", "ComparisonStateView-nima", "payload"),
        ("CompareResearchObjects-nima", "preview", "ChatOutput-comparison", "input_value")]
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
    return {"name": "Compare Research Objects", "is_component": False,
        "endpoint_name": "nima-compare-research-objects",
        "description": "Ontology-state comparison agent: pinned objects and criteria, exact evidence and optional retrieval, represented dependency checks, all-pair findings and pending progress. Harness retains selection authority. Private state is never admitted. Execution disabled; visual approval pending.",
        "data": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.6}},
        "nima_tool_manifest": {"tool_id": tool, "contract_version": "1", "visual_approval": "pending",
            "controller_version":VERSION,"policy_digest":POLICY_DIGEST,
            "native_sources_sha256":{name:hashlib.sha256((ROOT/"src/nima_semantica"/(name+".py")).read_bytes()).hexdigest()
                for name in ("comparison_contracts","comparison_state","comparison_tool","workflow_contracts","proposal_service","graph_analysis","claim_dependencies","evidence_contracts","math_retrieval","math_reasoning","reasoning_state","reasoning_kernel")},
            "publication": "not_published_by_builder", "components": snapshots}}


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, ensure_ascii=False))
