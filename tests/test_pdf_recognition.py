import asyncio
import base64
from io import BytesIO
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from nima_semantica.pdf.recognition import transcribe
from nima_semantica.pdf.langflow_recognition import LangflowRecognition
from nima_semantica.pdf.support import canonical_json, sha256_bytes, PdfError
from nima_semantica.model_runtime import check_context
from nima_semantica.installation import ModelProfile


def crop():
    buffer=BytesIO();Image.new('RGB',(100,40),'white').save(buffer,format='PNG')
    data=buffer.getvalue()
    return {'id':'formula','crop_hash':sha256_bytes(data),'png_base64':base64.b64encode(data).decode()}


@pytest.mark.asyncio
async def test_image_block_and_unreadable_response():
    calls=[]
    class Model:
        async def ainvoke(self,messages):
            calls.append(messages)
            return SimpleNamespace(content='UNREADABLE',response_metadata={},usage_metadata=None)
    result=await transcribe(crop(),Model())
    assert result['status']=='unreadable'
    assert calls[0][1].content[0]['type']=='image'
    assert 'base64' in calls[0][1].content[0]


def test_context_uses_image_dimensions_and_rejects_oversized_payload():
    from langchain_core.messages import HumanMessage
    request=crop()
    profile=ModelProfile(provider='ollama',model='fixture',max_tokens=128,context_window=4096)
    check_context(profile,[HumanMessage(content=[{'type':'image','base64':request['png_base64'],'mime_type':'image/png'}])],{})
    with pytest.raises(ValueError,match='bounded'):
        check_context(profile,[{'type':'image','base64':'A'*11_000_001}],{})


def backend(tmp_path,monkeypatch,mutate=None):
    data={'nodes':[],'edges':[]};calls=[]
    def handle(request):
        if request.method=='GET':return httpx.Response(200,json={'data':data})
        value=json.loads(json.loads(request.content)['input_value'])
        calls.append(value)
        answer={'id':value['id'],'crop_hash':value['crop_hash'],'status':'transcribed','latex':'x^2'}
        if mutate:answer=mutate(answer)
        return httpx.Response(200,json={'outputs':[{'outputs':[{'component_id':'ChatOutput-ocr',
            'results':{'message':{'text':json.dumps(answer)}}}]}]})
    original=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    selected=LangflowRecognition({'langflow_url':'http://langflow.test','flow_id':'flow',
        'concurrency':4,'model':{'model':'vision','request_timeout_seconds':30}},tmp_path/'cache')
    return selected,calls,data


def test_cache_correlation_and_canvas_change(tmp_path,monkeypatch):
    selected,calls,data=backend(tmp_path,monkeypatch)
    request=crop();assert selected.recognize(request)['latex']=='x^2'
    request['id']='second'
    assert selected.recognize(request)['id']=='second' and len(calls)==1
    data['nodes']=['changed']
    other=crop();other['crop_hash']='f'*64
    # Integrity failures are detected before any model call.
    with pytest.raises(ValueError,match='hash'):selected.recognize(other)
    selected.cache=tmp_path/'empty'
    with pytest.raises(PdfError,match='changed'):selected.recognize(request)


def test_wrong_crop_rejected(tmp_path,monkeypatch):
    selected,_,_=backend(tmp_path,monkeypatch,lambda a:{**a,'crop_hash':'0'*64})
    with pytest.raises(PdfError,match='match'):selected.recognize(crop())


def test_broker_refuses_foreign_files(tmp_path,monkeypatch):
    selected,_,_=backend(tmp_path,monkeypatch)
    path=tmp_path/'outside.png';path.write_bytes(b'png')
    with pytest.raises(PdfError,match='escapes'):
        selected.batch([{'id':'x','crop_path':str(path),'crop_hash':'f'*64}],tmp_path/'job')


def test_repeated_crops_share_one_recognition_without_losing_ids(tmp_path,monkeypatch):
    selected,calls,_=backend(tmp_path,monkeypatch)
    request=crop();path=tmp_path/'crop.png';path.write_bytes(base64.b64decode(request['png_base64']))
    jobs=[{'id':str(i),'crop_path':str(path),'crop_hash':request['crop_hash']} for i in range(6)]
    results=selected.batch(jobs,tmp_path)
    assert len(calls)==1
    assert [r['id'] for r in results]==[str(i) for i in range(6)]
