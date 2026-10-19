"""Build the private PDF recognition canvas without a default model assignment."""
import copy
import json
from build_tool_guide import ROOT, COMPONENTS, load_component


def build():
    guide = json.loads((ROOT / "examples/langflow_replacement/tool_guide.json").read_text())
    nodes = []
    for name, x in (("ChatInput", 0), ("ChatOutput", 800)):
        node = copy.deepcopy(next(n for n in guide["data"]["nodes"] if n["data"]["type"] == name))
        node.update(id=name+"-ocr", position={"x":x,"y":0})
        node["data"]["id"] = node["id"]
        template = node["data"]["node"]["template"]
        template["should_store_message"]["value"] = False
        template["session_id"]["value"] = ""
        template["input_value"]["value"] = "{}" if name == "ChatInput" else ""
        nodes.append(node)
    component = load_component("FormulaTranscription")()
    node = component.to_frontend_node()
    node.update(id="FormulaTranscription-ocr", type="genericNode", position={"x":400,"y":0})
    node["data"].update(id=node["id"], type="FormulaTranscription")
    node["data"]["node"]["template"]["code"]["value"] = (COMPONENTS/"FormulaTranscription.py").read_text()
    nodes.append(node)
    by_id = {n["id"]:n for n in nodes}; edges=[]
    for source, port, target, field in (("ChatInput-ocr","message",node["id"],"request_json"),
                                      (node["id"],"result","ChatOutput-ocr","input_value")):
        left,right=by_id[source],by_id[target]
        output=next(o for o in left["data"]["node"]["outputs"] if o["name"]==port)
        left["data"]["selected_output"]=port
        inp=right["data"]["node"]["template"][field]
        sh={"dataType":left["data"]["type"],"id":source,"name":port,"output_types":output["types"]}
        th={"fieldName":field,"id":target,"inputTypes":inp.get("input_types",[]),"type":inp["type"]}
        edges.append({"id":source+"-"+target,"source":source,"target":target,"data":{"sourceHandle":sh,"targetHandle":th},
            "sourceHandle":json.dumps(sh).replace('"','œ'),"targetHandle":json.dumps(th).replace('"','œ')})
    return {"name":"PDF Formula Recognition","description":"Private PNG-to-LaTeX recognition; operator-selected vision model.",
            "data":{"nodes":nodes,"edges":edges,"viewport":{"x":0,"y":0,"zoom":0.7}}}


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
