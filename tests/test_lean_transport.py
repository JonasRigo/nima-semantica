"""Narrow Lean transport integrity and authentication; no model or public URLs."""
import base64
import copy
import importlib.util
from http.server import HTTPServer
from pathlib import Path
import threading
import urllib.error
import urllib.request
import pytest

from nima_semantica.lean_transport import RemoteLeanVerifier,execute_packet,request_payload,CollectedArtifacts
from nima_semantica.lean_project import LeanProjectRequest
from nima_semantica.lean_verification_service import LeanVerificationService
from test_verify_lean_tool import Verifier


def native():return LeanProjectRequest({"Submission":"theorem target : True := True.intro"},("target",),("Submission",))


def remote(monkeypatch,mutate=None):
    worker=Verifier()
    def call(self,path,payload=None):
        if path=="/manifest":return LeanVerificationService(None,worker).environment_manifest()
        result=execute_packet(worker,payload)
        if mutate:mutate(result)
        return result
    monkeypatch.setattr(RemoteLeanVerifier,"_request",call)
    return RemoteLeanVerifier("http://worker","not-a-real-token")


def test_exact_artifact_transfer(store,monkeypatch):
    result=remote(monkeypatch).verify(store,native())
    assert result.certification_verified and store.read_artifact(result.evidence_artifact)
    for name,digest in result.source_artifacts.items():assert store.read_artifact(digest).decode()==native().sources[name]


@pytest.mark.parametrize("mutate",[
    lambda r:r.update(request_hash="wrong"),lambda r:r["environment"].update(toolchain_sha256="0"*64),
    lambda r:r["artifacts"].clear(),lambda r:r["result"].update(correspondence_verified=True),
    lambda r:r["result"].update(certification_verified="true"),lambda r:r["result"].update(declaration_types={"wrong":"type"}),
    lambda r:r["result"].update(declaration_type_fingerprints={"target":"0"*64}),
    lambda r:r["result"].update(source_artifacts={}),
    lambda r:r["artifacts"].update({next(iter(r["artifacts"])):base64.b64encode(b"tampered").decode()})])
def test_corrupt_response_rejected(store,monkeypatch,mutate):
    with pytest.raises((ValueError,KeyError)):remote(monkeypatch,mutate).verify(store,native())


def test_worker_rejects_environment_override_and_paths():
    worker=Verifier();payload={**request_payload(native()),"environment":LeanVerificationService(None,worker).environment_manifest()}
    with pytest.raises(ValueError):execute_packet(worker,{**payload,"command":"shell"})
    wrong=copy.deepcopy(payload);wrong["environment"]["trusted_imports"]=["Cheat"]
    with pytest.raises(ValueError):execute_packet(worker,wrong)
    assert not worker.calls


def test_http_authentication_framing_and_remote_roundtrip(store):
    path=Path(__file__).resolve().parents[1]/"deploy/serve_lean_worker.py"
    spec=importlib.util.spec_from_file_location("test_lean_server",path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    worker=Verifier();server=HTTPServer(("127.0.0.1",0),module.handler(worker,"test-token"))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url="http://127.0.0.1:"+str(server.server_port)
    try:
        with pytest.raises(urllib.error.HTTPError) as error:urllib.request.urlopen(url+"/manifest")
        assert error.value.code==403
        result=RemoteLeanVerifier(url,"test-token").verify(store,native())
        assert result.certification_verified and len(worker.calls)==1
        # Send only malformed framing headers. The server rejects them before
        # reading a body; concurrently sending chunks can race its close and
        # produce a TCP reset on Darwin instead of exposing the HTTP 400.
        import http.client
        connection=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=5)
        try:
            connection.putrequest("POST","/verify")
            connection.putheader("Authorization","Bearer test-token")
            connection.putheader("Transfer-Encoding","chunked")
            connection.putheader("Content-Length","2")
            connection.endheaders()
            response=connection.getresponse()
            assert response.status==400 and len(worker.calls)==1
            response.read()
        finally:
            connection.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
