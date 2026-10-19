"""Trusted broker: concurrent private flow calls, immutable crop checkpoints."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx
from .recognition import CropRequest, image_bytes
from .support import PdfError, canonical_json, sha256_bytes, sha256_file


class LangflowRecognition:
    def __init__(self, binding, cache):
        self.binding = binding
        self.cache = Path(cache)
        parsed = urlsplit(binding["langflow_url"])
        if parsed.scheme not in {"http","https"} or parsed.username or parsed.password:
            raise PdfError("Invalid recognition Langflow URL")
        self.url = binding["langflow_url"].rstrip("/")
        token = Path(binding["api_key_file"]).read_text().strip() if binding.get("api_key_file") else ""
        self.headers = {"x-api-key":token} if token else {}
        self.concurrency = binding["concurrency"]
        if type(self.concurrency) is not int or not 1 <= self.concurrency <= 32:
            raise PdfError("Invalid recognition concurrency")
        with httpx.Client(timeout=15,trust_env=False,follow_redirects=False) as client:
            response = client.get(self.url+"/api/v1/flows/"+binding["flow_id"],headers=self.headers)
            if not response.is_success:
                raise PdfError("PDF recognition canvas unavailable; check Langflow authentication and run nima setup --pdf")
            self.digest = sha256_bytes(canonical_json(response.json()["data"]))
        self.manifest = {"backend":"langflow","flow_id":binding["flow_id"],"workflow_digest":self.digest,
                         "implementation_sha256":sha256_file(Path(__file__).with_name("recognition.py")),
                         "configured_model":{k:v for k,v in binding["model"].items() if k != "credential"}}
        self.profile_id = sha256_bytes(canonical_json(self.manifest))

    @classmethod
    def from_file(cls,path):
        path=Path(path)
        if not path.is_file():
            raise PdfError("PDF recognition is not configured; run nima setup --pdf-model-profile FILE --pdf")
        return cls(json.loads(path.read_bytes()),path.parent/"recognition-cache")

    def recognize(self, request):
        request=CropRequest.model_validate(request)
        image_bytes(request)
        path=self.cache/self.profile_id/(request.crop_hash+".json")
        if path.is_file():
            saved=json.loads(path.read_bytes())
            if saved.get("profile_id") != self.profile_id or saved["result"]["crop_hash"] != request.crop_hash:
                raise PdfError("Recognition checkpoint identity mismatch")
            value=saved["result"]
            if sha256_bytes(canonical_json(value)) != saved.get("result_hash"):
                raise PdfError("Recognition checkpoint integrity mismatch")
            return {**value,"id":request.id,"cached":True}
        with httpx.Client(timeout=self.binding["model"].get("request_timeout_seconds",180)+30,
                          trust_env=False,follow_redirects=False) as client:
            current=client.get(self.url+"/api/v1/flows/"+self.binding["flow_id"],headers=self.headers)
            if not current.is_success or sha256_bytes(canonical_json(current.json()["data"])) != self.digest:
                raise PdfError("Recognition workflow changed during normalization; retry with its new identity")
            for attempt in range(3):
                response=client.post(self.url+"/api/v1/run/"+self.binding["flow_id"],headers=self.headers,
                    json={"input_value":request.model_dump_json(),"input_type":"chat","output_type":"chat",
                          "output_component":"ChatOutput-ocr"})
                if response.status_code not in {429,503} or attempt==2:break
                time.sleep(2**attempt)
            if not response.is_success:
                raise PdfError(f"Langflow PDF recognition failed (HTTP {response.status_code}); check the vision model and credential")
            try:
                outputs=response.json()["outputs"]
                rows=[row for group in outputs for row in group["outputs"] if row.get("component_id")=="ChatOutput-ocr"]
                if not rows:rows=[row for group in outputs for row in group["outputs"] if "message" in row.get("results",{})]
                if len(rows)!=1:raise ValueError("ambiguous output")
                value=json.loads(rows[0]["results"]["message"]["text"])
            except (KeyError,TypeError,ValueError) as error:
                raise PdfError("Invalid Langflow recognition response") from error
        if (value.get("id"),value.get("crop_hash")) != (request.id,request.crop_hash) or value.get("status") not in {"transcribed","unreadable"} or not isinstance(value.get("latex"),str):
            raise PdfError("Recognition result does not match its crop")
        value["recognition_profile_id"]=self.profile_id
        from ._ported import _atomic_formula_artifact
        _atomic_formula_artifact(path,canonical_json({"profile_id":self.profile_id,"result":value,
                                                      "result_hash":sha256_bytes(canonical_json(value))}))
        return value

    def batch(self, jobs, owned_root):
        root=Path(owned_root).resolve(); requests=[]
        if not isinstance(jobs,list) or len(jobs)>16:
            raise PdfError("Invalid recognition batch")
        for job in jobs:
            path=Path(job["crop_path"])
            if path.is_symlink() or not path.resolve().is_relative_to(root) or path.suffix!=".png":
                raise PdfError("Recognition crop escapes its job")
            if path.stat().st_size>8_000_000:raise PdfError("Recognition crop exceeds size limit")
            requests.append({k:v for k,v in job.items() if k!="crop_path"}|{"png_base64":base64.b64encode(path.read_bytes()).decode()})
        unique={}
        for request in requests:unique.setdefault(request["crop_hash"],request)
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            recognized=list(pool.map(self.recognize,unique.values()))
        by_hash={value["crop_hash"]:value for value in recognized}
        return [{**by_hash[request["crop_hash"]],"id":request["id"]} for request in requests]
