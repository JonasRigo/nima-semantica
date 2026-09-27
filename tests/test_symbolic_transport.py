"""Narrow worker transport: unauthorized and arbitrary Docker options rejected."""
import hashlib
import json
from pathlib import Path
import sys
import threading
from http.server import HTTPServer
import urllib.request
import urllib.error
from types import SimpleNamespace
import pytest
from nima_semantica.symbolic_transport import RemoteSymbolicWorker
from nima_semantica.models import NimaError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"deploy"))
from serve_symbolic_worker import handler


@pytest.fixture
def server():
    calls=[]
    def run(source,timeout):
        calls.append((source,timeout))
        return {"outcome":"executed","exit_code":0,"stdout":"1\n","source_sha256":hashlib.sha256(source.encode()).hexdigest()}
    service=HTTPServer(("127.0.0.1",0),handler(SimpleNamespace(run=run),"fixture-token"))
    thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start()
    yield f"http://127.0.0.1:{service.server_port}",calls
    service.shutdown();service.server_close();thread.join()


def test_authenticated_execution(server):
    url,calls=server
    assert RemoteSymbolicWorker(url,"fixture-token").run("print(1)")["stdout"]=="1\n"
    assert calls==[("print(1)",60)]


def test_unauthorized_never_executes(server):
    url,calls=server
    with pytest.raises(NimaError):RemoteSymbolicWorker(url,"wrong").run("print(1)")
    assert not calls


@pytest.mark.parametrize("extra",[{"image":"other"},{"mount":"/"},{"network":"host"},{"timeout_seconds":121}])
def test_no_docker_controls(server,extra):
    url,calls=server
    request=urllib.request.Request(url+"/execute",data=json.dumps({"source":"print(1)"}|extra).encode(),headers={"Authorization":"Bearer fixture-token"})
    with pytest.raises(urllib.error.HTTPError) as e:urllib.request.urlopen(request)
    assert e.value.code==400 and not calls
