"""Import the private bounded Deep Research canvas and verify inert readback."""
import argparse
import json
from pathlib import Path

import httpx

from flow_io import digest, validate_edges, write_json

ROOT = Path(__file__).resolve().parents[1]
FLOW = ROOT / "examples/langflow_replacement/deep_research_simple.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7860")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    flow = json.loads(FLOW.read_text())
    validate_edges(flow)
    with httpx.Client(base_url=args.url, follow_redirects=True, timeout=180, trust_env=False) as client:
        response = client.get("/api/v1/folders/")
        response.raise_for_status()
        matches = [folder for folder in response.json() if folder["name"] == "NIMA Research Toolbox"]
        if len(matches) != 1:
            raise ValueError("expected one NIMA Research Toolbox folder")
        folder_id = matches[0]["id"]
        response = client.get("/api/v1/flows/")
        response.raise_for_status()
        listed = response.json()
        listed = listed if isinstance(listed, list) else listed.get("flows", listed.get("items", []))
        matches = [item for item in listed if item.get("endpoint_name") == flow["endpoint_name"]]
        if len(matches) > 1:
            raise ValueError("ambiguous Deep Research simple endpoint")
        payload = {key: flow[key] for key in ("name", "description", "data", "endpoint_name")}
        payload.update(folder_id=folder_id, access_type="PRIVATE", mcp_enabled=False, a2a_enabled=False, webhook=False)
        response = client.patch("/api/v1/flows/" + matches[0]["id"], json=payload) if matches else client.post("/api/v1/flows/", json=payload)
        response.raise_for_status()
        identifier = response.json()["id"]
        response = client.get("/api/v1/flows/" + identifier)
        response.raise_for_status()
        live = response.json()
        if live["data"] != flow["data"] or live["folder_id"] != folder_id or live["access_type"] != "PRIVATE":
            raise ValueError("saved/live Deep Research simple canvas differs")
        if any(live[key] for key in ("mcp_enabled", "a2a_enabled", "webhook")):
            raise ValueError("Deep Research simple canvas unexpectedly exposed")
        response = client.post("/api/v1/run/" + identifier,
            json={"input_value": "{}", "input_type": "chat", "output_type": "chat"})
        response.raise_for_status()
        messages = [item["results"]["message"]["text"] for group in response.json()["outputs"]
            for item in group["outputs"] if "message" in item.get("results", {})]
        if not messages:
            raise ValueError("Deep Research simple preview returned no message")
        preview = json.loads(messages[-1])
        if preview.get("research_status") != "complete" or preview.get("graph_commit", {}).get("status") != "no_graph_candidate":
            raise ValueError("Deep Research simple inert preview failed: " + repr(preview)[:500])
        write_json(args.report, {"flow_id": identifier, "folder_id": folder_id,
            "controller_version": flow["nima_tool_manifest"]["controller_version"],
            "saved_data_sha256": digest(flow["data"]), "exact_readback": True,
            "private": True, "mcp_enabled": False, "a2a_enabled": False, "webhook": False,
            "inert_preview": True})
        print(json.dumps({"flow_id": identifier, "exact_readback": True, "inert_preview": True}), flush=True)


if __name__ == "__main__":
    main()
