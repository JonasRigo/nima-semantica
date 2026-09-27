"""Refresh the existing private Review Research flow with its operator model edge."""
import argparse
import json
from pathlib import Path

import httpx

from flow_io import digest, validate_edges, write_json

ROOT = Path(__file__).resolve().parents[1]
FLOW = ROOT / "examples/langflow_replacement/review_research.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7860")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    flow = json.loads(FLOW.read_text())
    validate_edges(flow)
    with httpx.Client(base_url=args.url, timeout=120, trust_env=False, follow_redirects=True) as client:
        folders = client.get("/api/v1/folders/")
        folders.raise_for_status()
        folder = next(f for f in folders.json() if f["name"] == "NIMA Research Toolbox")
        listing = client.get("/api/v1/flows/")
        listing.raise_for_status()
        listed = listing.json()
        listed = listed if isinstance(listed, list) else listed.get("flows", listed.get("items", []))
        matches = [f for f in listed if f.get("endpoint_name") == flow["endpoint_name"]]
        if len(matches) != 1:
            raise ValueError("expected one existing Review Research flow")
        flow_id = matches[0]["id"]
        payload = {key: flow[key] for key in ("name", "description", "data", "endpoint_name")}
        payload.update(folder_id=folder["id"], access_type="PRIVATE", mcp_enabled=False,
            a2a_enabled=False, webhook=False)
        response = client.patch("/api/v1/flows/" + flow_id, json=payload)
        response.raise_for_status()
        readback = client.get("/api/v1/flows/" + flow_id)
        readback.raise_for_status()
        saved = readback.json()
        if saved["data"] != flow["data"]:
            raise ValueError("live Review Research graph differs from saved canvas")
        if saved["access_type"] != "PRIVATE" or any(saved[key] for key in ("mcp_enabled", "a2a_enabled", "webhook")):
            raise ValueError("Review Research visibility differs")
        preview = client.post("/api/v1/run/" + flow_id,
            json={"input_value":"{}", "input_type":"chat", "output_type":"chat"})
        preview.raise_for_status()
        messages = [item["results"]["message"]["text"] for group in preview.json()["outputs"]
            for item in group["outputs"] if "message" in item.get("results", {})]
        if not messages:
            raise ValueError("preview returned no message")
        content = messages[-1]
        if content.startswith("```json\n") and content.endswith("\n```"):
            content = content[8:-4]
        result = json.loads(content)
        if result.get("data", {}).get("executed") is not False:
            raise ValueError("preview executed unexpectedly")
        report = {"flow_id":flow_id,"folder_id":folder["id"],"model":"openai/gpt-6-luna",
            "data_sha256":digest(flow["data"]),"exact_readback":True,"private":True,
            "inert_preview":True,"mcp_enabled":False,"a2a_enabled":False,"webhook":False}
        write_json(args.report, report)
        print(json.dumps(report))


if __name__ == "__main__":
    main()
