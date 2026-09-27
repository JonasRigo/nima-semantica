"""Operator-configured local or authenticated remote pinned Lean verifier."""
import base64
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import urllib.request

from .lean_project import LeanProjectVerifier, LeanProjectRequest, LeanProjectResult, PinnedDirectory
from .lean_verification_service import LeanVerificationService
from .models import ConfigurationError, NimaError, identity
from .providers import strict_json_object

MAX_RESPONSE = 64 * 1024 * 1024


def local_lean_verifier(path):
    config = strict_json_object(Path(path).read_text())
    if set(config) - {"toolchain","libraries","trusted_imports","timeout","memory_mb","output_bytes","artifact_bytes"}:
        raise ValueError("unknown Lean configuration field")
    def pin(value):
        if set(value) != {"path","sha256"}:raise ValueError("invalid directory pin")
        return PinnedDirectory(Path(value["path"]),value["sha256"])
    options = {k:config[k] for k in ("trusted_imports","timeout","memory_mb","output_bytes","artifact_bytes") if k in config}
    return LeanProjectVerifier(pin(config["toolchain"]),libraries=tuple(pin(v) for v in config.get("libraries",[])),**options)


def request_payload(request):
    request.validate()
    return {"sources":request.sources,"module_order":list(request.sources),"targets":list(request.targets),"imports":list(request.imports)}


class CollectedArtifacts:
    """Bounded per-call transport buffer; contains no caller-selected paths."""
    def __init__(self):
        self.artifacts={}; self.size=0

    def artifact(self,data):
        digest=hashlib.sha256(data).hexdigest()
        if digest not in self.artifacts:
            if self.size+len(data)>40*1024*1024:raise ValueError("Lean transport artifact limit")
            self.artifacts[digest]=base64.b64encode(data).decode();self.size+=len(data)
        return digest


def execute_packet(verifier, payload):
    if set(payload)!={"sources","module_order","targets","imports","environment"}:
        raise ValueError("invalid Lean worker payload")
    order=payload["module_order"]
    if not isinstance(order,list) or len(order)!=len(set(order)) or set(order)!=set(payload["sources"]):
        raise ValueError("invalid module order")
    request=LeanProjectRequest({n:payload["sources"][n] for n in order},tuple(payload["targets"]),tuple(payload["imports"]))
    request.validate()
    manifest=LeanVerificationService(None,verifier).environment_manifest()
    if payload["environment"]!=manifest:raise ValueError("worker environment differs from requested pin")
    artifacts=CollectedArtifacts()
    result=verifier.verify(artifacts,request)
    if manifest!=LeanVerificationService(None,verifier).environment_manifest():raise ValueError("worker environment changed")
    return {"request_hash":identity(request_payload(request)),"environment":manifest,
        "result":asdict(result),"artifacts":artifacts.artifacts}


class RemoteLeanVerifier:
    def __init__(self,url,token):
        if not url.startswith(("http://","https://")) or not token:raise ConfigurationError("invalid Lean endpoint configuration")
        self.url,self._token=url.rstrip("/"),token
        manifest=self._request("/manifest")
        required={"configured","protocol","toolchain_sha256","library_sha256","trusted_imports","timeout_seconds","memory_mb","output_bytes","artifact_bytes","allowed_axioms"}
        if set(manifest)!=required or manifest["protocol"]!="lean-project-kernel-replay-v3" or manifest["configured"] is not True:
            raise ConfigurationError("invalid Lean worker manifest")
        import re
        if any(not isinstance(p,str) or not re.fullmatch(r"[0-9a-f]{64}",p) for p in [manifest["toolchain_sha256"],*manifest["library_sha256"]]):
            raise ConfigurationError("unpinned Lean worker")
        if not isinstance(manifest["timeout_seconds"],(int,float)) or not 0<manifest["timeout_seconds"]<=300:
            raise ConfigurationError("invalid Lean worker timeout")
        self.toolchain=SimpleNamespace(sha256=manifest["toolchain_sha256"])
        self.libraries=tuple(SimpleNamespace(sha256=p) for p in manifest["library_sha256"])
        self.trusted_imports=tuple(manifest["trusted_imports"])
        self.timeout=manifest["timeout_seconds"];self.memory_mb=manifest["memory_mb"]
        self.output_bytes=manifest["output_bytes"];self.artifact_bytes=manifest["artifact_bytes"]
        if LeanVerificationService(None,self).environment_manifest()!=manifest:raise ConfigurationError("unsupported Lean manifest policy")
        self.manifest=manifest

    def _request(self,path,payload=None):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        request=urllib.request.Request(self.url+path,data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization":"Bearer "+self._token,"Content-Type":"application/json"})
        try:
            with urllib.request.build_opener(NoRedirect).open(request,timeout=getattr(self,"timeout",15)+45) as response:
                raw=response.read(MAX_RESPONSE+1)
            if len(raw)>MAX_RESPONSE:raise ValueError("oversized response")
            return strict_json_object(raw.decode())
        except Exception:
            raise NimaError("pinned Lean worker transport failed") from None

    def verify(self,store,request):
        payload=request_payload(request)
        response=self._request("/verify",{**payload,"environment":self.manifest})
        if set(response)!={"request_hash","environment","result","artifacts"} or response["request_hash"]!=identity(payload) or response["environment"]!=self.manifest:
            raise ValueError("Lean response binding mismatch")
        result=LeanProjectResult(**response["result"])
        for field in ("compilation_succeeded","inspection_succeeded","axioms_accepted","certification_verified","kernel_replay_succeeded","correspondence_verified"):
            if type(getattr(result,field)) is not bool:raise ValueError("non-boolean Lean result")
        if result.correspondence_verified:raise ValueError("worker cannot certify source correspondence")
        decoded={}
        for digest,value in response["artifacts"].items():
            blob=base64.b64decode(value,validate=True)
            if hashlib.sha256(blob).hexdigest()!=digest:raise ValueError("Lean artifact hash mismatch")
            decoded[digest]=blob
        expected={n:hashlib.sha256(s.encode()).hexdigest() for n,s in request.sources.items()}
        if result.source_artifacts!=expected:raise ValueError("Lean source identity mismatch")
        refs={result.evidence_artifact,*result.source_artifacts.values(),*result.compiled_artifacts.values()}
        if not refs<=decoded.keys():raise ValueError("missing Lean evidence bytes")
        evidence=strict_json_object(decoded[result.evidence_artifact].decode())
        if evidence["sources"]!=expected or evidence["targets"]!=list(request.targets) or evidence["imports"]!=list(request.imports):
            raise ValueError("Lean evidence target/source mismatch")
        for field in ("compilation_succeeded","inspection_succeeded","axioms_accepted","certification_verified","kernel_replay_succeeded","correspondence_verified","declaration_types","declaration_type_fingerprints"):
            if evidence.get(field)!=getattr(result,field):raise ValueError("Lean evidence/result disagreement")
        if (evidence["toolchain"]!=self.manifest["toolchain_sha256"] or evidence["libraries"]!=self.manifest["library_sha256"]
                or evidence["trusted_imports"]!=self.manifest["trusted_imports"] or evidence["allowed_axioms"]!=self.manifest["allowed_axioms"]):
            raise ValueError("Lean evidence/environment disagreement")
        for digest,blob in decoded.items():
            if store.artifact(blob)!=digest:raise ValueError("artifact persistence mismatch")
        return result


def configured_lean_verifier():
    url=os.environ.get("NIMA_LEAN_WORKER_URL")
    if url:
        token=os.environ.get("NIMA_LEAN_WORKER_TOKEN_FILE")
        if not token:raise ConfigurationError("Lean worker token file is required")
        return RemoteLeanVerifier(url,Path(token).read_text().strip())
    config=os.environ.get("NIMA_LEAN_CONFIG")
    if not config:raise ConfigurationError("operator Lean worker or pinned configuration is required")
    return local_lean_verifier(config)
