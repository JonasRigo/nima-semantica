"""Build the one-canvas, two-pass Deep Research successor without an OSA."""
import copy
import hashlib
import json

from build_tool_guide import ROOT, COMPONENTS, load_component
from nima_semantica.deep_research_passes import VERSION
from nima_semantica.models import identity

POLICY_DIGEST = identity({"version": VERSION, "passes": ("coverage_facets", "initial_queries", "metadata_relevance", "bounded_refinement", "passage_relevance", "regional_graphs", "consolidate", "structured_gap_assessment", "targeted_queries", "reconsolidate"), "continuation": "supplied_graph_starts_at_gap_assessment", "default_evidence": "staged_fast_read_provisional"})


def build():
    guide = json.loads((ROOT / "examples/langflow_replacement/tool_guide.json").read_text())
    nodes = []
    snapshots = {}
    for name, x in (("ChatInput", 0), ("ChatOutput", 2100)):
        node = copy.deepcopy(next(item for item in guide["data"]["nodes"] if item["data"]["type"] == name))
        identifier = name + "-deep_research_simple"
        node.update(id=identifier, position={"x": x, "y": 0})
        node["data"]["id"] = identifier
        node["data"]["node"]["display_name"] = ("Request" if name == "ChatInput" else "Result") + " · Deep Research"
        if name == "ChatInput":
            node["data"]["node"]["template"]["input_value"]["value"] = json.dumps({"research": {"mode": "preview"}})
        nodes.append(node)
    positions = {"DeepResearchPassFields": (340, 0), "StockArxivHTML": (740, -360),
        "PaperDiscovery": (740, 490), "ConductPassDeepResearch": (1210, 0), "ReviewGraphCommit": (1660, 0)}
    for name, (x, y) in positions.items():
        component = load_component(name)()
        node = component.to_frontend_node()
        identifier = name + "-nima"
        node.update(id=identifier, type="genericNode", position={"x": x, "y": y})
        node["data"].update(id=identifier, type=name)
        source = (COMPONENTS / (name + ".py")).read_text()
        node["data"]["node"]["template"]["code"]["value"] = source
        metadata = {"nima_component_id": component.nima_manifest.component_id,
            "contract_version": component.nima_manifest.version,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest()}
        node["data"]["node"]["metadata"] = metadata
        snapshots[name] = metadata
        nodes.append(node)
    connections = [("ChatInput-deep_research_simple", "message", "DeepResearchPassFields-nima", "mcp_request"),
        ("DeepResearchPassFields-nima", "request", "StockArxivHTML-nima", "payload"),
        ("DeepResearchPassFields-nima", "request", "ConductPassDeepResearch-nima", "payload"),
        ("PaperDiscovery-nima", "tool", "StockArxivHTML-nima", "paper_discovery"),
        ("StockArxivHTML-nima", "tool", "ConductPassDeepResearch-nima", "stock_arxiv"),
        ("PaperDiscovery-nima", "tool", "ConductPassDeepResearch-nima", "paper_discovery"),
        ("ConductPassDeepResearch-nima", "result", "ReviewGraphCommit-nima", "payload"),
        ("ReviewGraphCommit-nima", "preview", "ChatOutput-deep_research_simple", "input_value")]
    by_id = {node["id"]: node for node in nodes}
    edges = []
    for source, output, target, field in connections:
        left, right = by_id[source], by_id[target]
        port = next(item for item in left["data"]["node"]["outputs"] if item["name"] == output)
        inp = right["data"]["node"]["template"][field]
        sh = {"dataType": left["data"]["type"], "id": source, "name": output, "output_types": port["types"]}
        th = {"fieldName": field, "id": target, "inputTypes": inp.get("input_types", []), "type": inp["type"]}
        edges.append({"id": f"{source}-{output}-{target}-{field}", "source": source, "target": target,
            "sourceHandle": json.dumps(sh).replace('"', "œ"), "targetHandle": json.dumps(th).replace('"', "œ"),
            "data": {"sourceHandle": sh, "targetHandle": th}})
    return {"name": "Deep Research · Regional Graph Passes", "is_component": False,
        "endpoint_name": "nima-deep-research-simple",
        "description": "Harness-controlled literature review with pinned coverage subquestions, relevance-screened short queries, regional/consolidated provisional graphs and one gap-directed follow-up. Final feedback is a list of questions not covered by the current graph, with graph links for covered facets and controller-derived source inventory; no narrative gap answer. Fast reading stages exact passages without corpus indexing. No automatic scientific admission. Execution disabled by default; visual approval pending.",
        "data": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.48}},
        "nima_tool_manifest": {"tool_id": "deep_research", "contract_version": "11", "visual_approval": "pending",
            "controller_version": VERSION, "policy_digest": POLICY_DIGEST,
            "native_sources_sha256": {name: hashlib.sha256((ROOT / "src/nima_semantica" / (name + ".py")).read_bytes()).hexdigest()
                for name in ("deep_research_passes", "deep_research_fast", "deep_research_fast_pdf", "arxiv_search_fallback", "simple_deep_research", "graph_extraction", "paper_discovery", "literature_acquisition", "source_tools", "project_update", "ontology_profiles")},
            "publication": "not_published_by_builder", "components": snapshots}}


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, ensure_ascii=False))
