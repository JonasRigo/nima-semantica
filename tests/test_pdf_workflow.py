import json
from pathlib import Path

import httpx
import pytest

from nima_semantica.installation import Installation, ModelProfile, PdfRecognitionSettings
from nima_semantica.pdf import workflow


def test_private_workflow_model_assignment_and_readback_recovery(tmp_path,monkeypatch):
    config=Installation(data_root=str(tmp_path),llm=ModelProfile(model='main'),
        pdf_recognition=PdfRecognitionSettings(model=ModelProfile(model='vision')))
    calls=[]; saved={}; unavailable=[True]
    def handle(request):
        calls.append((request.method,request.url.path))
        if request.url.path=='/health':return httpx.Response(200,json={'status':'ok'})
        if request.method=='POST':
            saved.update(json.loads(request.content))
            return httpx.Response(201,json={'id':'private-flow'})
        assert request.method=='GET'
        return httpx.Response(404 if unavailable[0] else 200,json=saved)
    monkeypatch.setattr(workflow,'api',lambda config:httpx.Client(base_url='http://langflow.test',transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(workflow,'ensure_credentials',lambda *args:None)
    import nima_semantica.workflow_installation as shared
    monkeypatch.setattr(shared.time,'sleep',lambda *args:None)
    with pytest.raises(httpx.HTTPStatusError):workflow.install_recognition(config)
    binding=tmp_path/'pdf-service/recognition.json'
    assert json.loads(binding.read_text())['pending_verification']
    unavailable[0]=False
    result=workflow.install_recognition(config)
    assert not result.get('pending_verification')
    assert sum(method=='POST' for method,_ in calls)==1
    assert saved['access_type']=='PRIVATE' and saved['mcp_enabled'] is False
    models=[node for node in saved['data']['nodes'] if node['data']['type']=='ConfiguredModel']
    profile=json.loads(models[0]['data']['node']['template']['profile_json']['value'])
    assert profile['model']=='vision'


def test_pdf_setup_requires_explicit_independent_profile(tmp_path):
    config=Installation(data_root=str(tmp_path),llm=ModelProfile(model='main'))
    with pytest.raises(ValueError,match='dedicated vision'):workflow.install_recognition(config)
