"""Import candidate math canvases privately and verify exact readback/preview."""
import argparse
import json
from pathlib import Path
import httpx

from flow_io import digest, validate_edges, write_json

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7860")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    records = []
    with httpx.Client(base_url=args.url, follow_redirects=True, timeout=120, trust_env=False) as client:
        folders = client.get("/api/v1/folders/")
        folders.raise_for_status()
        matches = [f for f in folders.json() if f["name"] == "NIMA Research Toolbox"]
        if len(matches) != 1:
            raise ValueError("expected one NIMA Research Toolbox collection")
        folder_id = matches[0]["id"]
        listing = client.get("/api/v1/flows/")
        listing.raise_for_status()
        existing = listing.json()
        if not isinstance(existing, list):
            existing = existing.get("flows", existing.get("items", []))
        for file in sorted((ROOT / "examples/langflow_replacement/math_mcp").glob("*.json")):
            flow = json.loads(file.read_text())
            validate_edges(flow)
            payload = {key: flow[key] for key in ("name", "description", "data", "endpoint_name")}
            payload.update(folder_id=folder_id, access_type="PRIVATE", mcp_enabled=False, a2a_enabled=False, webhook=False)
            candidates = [f for f in existing if f.get("endpoint_name") == flow["endpoint_name"]]
            if len(candidates) > 1:
                raise ValueError("ambiguous existing endpoint")
            if candidates:
                response = client.patch("/api/v1/flows/" + candidates[0]["id"], json=payload)
            else:
                response = client.post("/api/v1/flows/", json=payload)
            response.raise_for_status()
            identifier = response.json()["id"]
            response = client.get("/api/v1/flows/" + identifier)
            response.raise_for_status()
            live = response.json()
            assert live["data"] == flow["data"], "live/saved graph mismatch"
            assert live["folder_id"] == folder_id and live["access_type"] == "PRIVATE"
            assert not live["mcp_enabled"] and not live["a2a_enabled"] and not live["webhook"]
            preview = client.post("/api/v1/run/" + identifier, json={"input_value": "{}", "input_type": "chat", "output_type": "chat"})
            preview.raise_for_status()
            output = preview.json()
            messages = [item["results"]["message"]["text"] for group in output["outputs"] for item in group["outputs"] if "message" in item.get("results", {})]
            assert messages, "no preview message"
            value = json.loads(messages[-1])
            assert value.get("executed") is False or (file.stem == "operations" and "operations" in value)
            records.append({"operation": file.stem, "flow_id": identifier, "folder_id": folder_id,
                "data_sha256": digest(flow["data"]), "exact_readback": True, "inert_preview": True})
            print(file.stem + ": imported and previewed", flush=True)
    write_json(args.report, {"contract_version": "math-session-v1", "visual_approval": "pending", "flows": records})


if __name__ == "__main__":
    main()
