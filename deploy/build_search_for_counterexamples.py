"""Build the inspectable native counterexample agent; stdout only."""
import copy
import hashlib
import json

from build_tool_guide import ROOT, COMPONENTS, load_component
from nima_semantica.counterexample_contracts import VERSION
from nima_semantica.math_reasoning import POLICY_DIGEST


def build():
    tool = "search_for_counterexamples"
    names = ["CounterexampleFields", "SearchForCounterexamples", "CounterexampleOntology", "CounterexampleStateView", "CounterexampleRetrieval"]
    guide = json.loads((ROOT / "examples/langflow_replacement/tool_guide.json").read_text())
    nodes, snapshots = [], {}
    for name, x in (("ChatInput", 0), ("ChatOutput", 1260)):
        node = copy.deepcopy(next(n for n in guide["data"]["nodes"] if n["data"]["type"] == name))
        identifier = name + "-counterexample"
        node.update(id=identifier, position={"x": x, "y": 0})
        node["data"]["id"] = identifier
        node["data"]["node"]["display_name"] = ("Request" if name == "ChatInput" else "Result") + " · Counterexample Search"
        if name == "ChatInput":
            node["data"]["node"]["template"]["input_value"]["value"] = json.dumps({"mode": "preview"})
        nodes.append(node)
    for index, name in enumerate(names, 1):
        component = load_component(name)()
        node = component.to_frontend_node()
        identifier = name + "-nima"
        positions = {"CounterexampleFields":(420,0), "SearchForCounterexamples":(840,0),
            "CounterexampleOntology":(420,620), "CounterexampleStateView":(1260,620), "CounterexampleRetrieval":(0,620)}
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
    connections = [("ChatInput-counterexample", "message", "CounterexampleFields-nima", "mcp_request"),
        ("CounterexampleFields-nima", "request", "SearchForCounterexamples-nima", "payload"),
        ("CounterexampleOntology-nima", "result", "SearchForCounterexamples-nima", "policy"),
        ("CounterexampleRetrieval-nima", "result", "SearchForCounterexamples-nima", "retrieval_policy"),
        ("SearchForCounterexamples-nima", "result", "CounterexampleStateView-nima", "payload"),
        ("SearchForCounterexamples-nima", "preview", "ChatOutput-counterexample", "input_value")]
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
    return {"name": "Search for Counterexamples", "is_component": False,
        "endpoint_name": "nima-search-for-counterexamples",
        "description": "Single counterexample agent with optional exact retrieval, private search state, fixed isolated integer search and independent witness validation. Explicit search limits and pending project progress. Execution disabled; visual approval pending.",
        "data": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.6}},
        "nima_tool_manifest": {"tool_id": tool, "contract_version": "1", "visual_approval": "pending",
            "controller_version":VERSION,"policy_digest":POLICY_DIGEST,
            "native_sources_sha256":{name:hashlib.sha256((ROOT/"src/nima_semantica"/(name+".py")).read_bytes()).hexdigest()
                for name in ("counterexample_contracts","counterexample_kernel","counterexample_tool","math_retrieval","math_reasoning","reasoning_state","reasoning_kernel","verification","verification_service","research_additions")},
            "publication": "not_published_by_builder", "components": snapshots}}


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, ensure_ascii=False))
