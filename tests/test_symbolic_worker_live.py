"""Opt-in local Docker isolation tests, never run generated code on the host."""

import json
import os

import pytest

from nima_semantica.calculation import SymbolicWorker

pytestmark = pytest.mark.skipif(
    os.environ.get("NIMA_LIVE_SYMBOLIC") != "1",
    reason="requires built isolated symbolic worker image",
)


def test_nonroot_nnp_capabilities_filesystem_and_network():
    source = """import json, os, socket
status = dict(line.split(':',1) for line in open('/proc/self/status') if ':' in line)
try:
    open('/etc/nima-write-probe','w').close()
    writable = True
except OSError:
    writable = False
try:
    socket.create_connection(('1.1.1.1',53),timeout=0.5).close()
    network = True
except OSError:
    network = False
print(json.dumps({'uid':os.getuid(),'nnp':status['NoNewPrivs'].strip(),'caps':status['CapEff'].strip(),'writable':writable,'network':network}))
"""
    evidence = SymbolicWorker().run(source)
    assert evidence["outcome"] == "executed"
    value = json.loads(evidence["stdout"])
    assert value["uid"] != 0 and value["nnp"] == "1" and int(value["caps"], 16) == 0
    assert not value["network"] and not value["writable"]


def test_timeout_includes_children():
    evidence = SymbolicWorker().run(
        "import subprocess,time; subprocess.Popen(['sleep','100']); time.sleep(100)", 2
    )
    assert evidence["outcome"] == "timeout"
    assert evidence["elapsed_seconds"] < 20


def test_output_limit_and_failed_code():
    worker = SymbolicWorker()
    evidence = worker.run("print('x' * 2000000)")
    assert evidence["outcome"] == "output_limit"
    assert (
        len(evidence["stdout"].encode()) + len(evidence["stderr"].encode()) <= worker.output_limit
    )
    assert worker.run("raise ValueError('intentional failure')")["outcome"] == "code_failed"
