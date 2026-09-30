import json
import sys
from types import SimpleNamespace

import pytest

from nima_semantica.cli import main
from nima_semantica.installation import Installation, ModelProfile, EmbeddingProfile
from nima_semantica.model_setup import native_chat_model
from nima_semantica.workflow_installation import configure_flow, maintained_flows, validate_flow, ensure_credentials


@pytest.mark.parametrize('provider', ['openai','openrouter','anthropic','gemini','ollama'])
def test_provider_defaults_do_not_route_to_openrouter(provider):
    profile = ModelProfile(provider=provider, model='fixture')
    assert ('openrouter.ai' in profile.base_url) is (provider == 'openrouter')
    if provider == 'ollama':
        assert profile.credential == ''


def test_compatible_requires_explicit_endpoint():
    with pytest.raises(ValueError, match='explicit base_url'):
        ModelProfile(provider='compatible', model='fixture')


def test_scripted_all_local_setup_has_independent_models_and_no_credentials(tmp_path, monkeypatch):
    from nima_semantica import setup_services
    monkeypatch.setattr(setup_services, 'probe_embedding', lambda model, base_url: dict(
        model=model, base_url=base_url, provider='ollama', revision='pinned', dimension=1024))
    path = tmp_path/'config.json'
    assert main(['--config',str(path),'setup','--non-interactive','--provider','ollama',
        '--model','local-chat','--base-url','http://localhost:11435','--data-root',str(tmp_path/'data'),
        '--embedding-provider','ollama','--embedding-model','local-embedding',
        '--embedding-base-url','http://localhost:11436']) == 0
    value = Installation.model_validate_json(path.read_text())
    assert value.llm.model == 'local-chat' and value.llm.credential == ''
    assert value.embedding.model == 'local-embedding'
    assert value.embedding.base_url == 'http://localhost:11436'
    class Client:
        def get(self, path):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: [])
        def post(self, *a, **kw):
            pytest.fail('local setup must not request a cloud credential')
    ensure_credentials(Client(), value)


def test_interactive_local_compatible_llm_and_embeddings(tmp_path, monkeypatch):
    from nima_semantica import workflow_installation
    monkeypatch.setattr(workflow_installation, 'model_inventory', lambda: {})
    answers = iter(['compatible','cheap-local','http://localhost:1234/v1','-',
                    '', '',
                    'compatible','local-embed','http://localhost:8080/v1','revision-1','768','-'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(answers))
    path = tmp_path/'config.json'
    assert main(['--config',str(path),'setup','--data-root',str(tmp_path/'data')]) == 0
    config = Installation.model_validate_json(path.read_text())
    assert config.llm.base_url == 'http://localhost:1234/v1' and not config.llm.credential
    assert config.embedding.base_url == 'http://localhost:8080/v1' and not config.embedding.credential


@pytest.mark.parametrize('provider,module,klass,key', [
    ('anthropic','langchain_anthropic','ChatAnthropic','anthropic_api_url'),
    ('gemini','langchain_google_genai','ChatGoogleGenerativeAI','base_url'),
    ('ollama','langchain_ollama','ChatOllama','base_url')])
def test_native_protocol_routes_without_cloud_fallback(monkeypatch, provider, module, klass, key):
    calls = []
    class Model:
        def __init__(self, **kwargs):
            calls.append(kwargs)
        def bind_tools(self, tools, **kwargs):
            return kwargs
    monkeypatch.setitem(sys.modules, module, SimpleNamespace(**{klass:Model}))
    profile = ModelProfile(provider=provider, model='fixture', base_url='http://fixture:8080')
    client = native_chat_model(profile, 'fixture-secret' if provider != 'ollama' else None)
    assert calls[0][key] == 'http://fixture:8080'
    assert calls[0]['model'] == 'fixture'
    options = client.bind_tools([], tool_choice='required', parallel_tool_calls=False)
    assert options['tool_choice'] == ('any' if provider == 'anthropic' else 'required')
    assert ('parallel_tool_calls' in options) is (provider == 'anthropic')


@pytest.mark.parametrize('provider',['openai','openrouter','compatible','anthropic','gemini','ollama'])
def test_native_provider_node_wiring_and_reconfiguration(tmp_path, provider):
    config = Installation(data_root=str(tmp_path), llm=ModelProfile(provider=provider, model='fixture',base_url='https://models.example/v1'))
    flow = json.loads(maintained_flows()['review_research'].read_text())
    configured, _ = configure_flow(flow, 'review_research', config, 'papers','project')
    assert validate_flow(configured)
    nodes = [n for n in configured['data']['nodes'] if n['data']['type']=='ConfiguredModel']
    assert len(nodes) == 1
    assert nodes[0]['data']['node']['base_classes'] == ['LanguageModel']
    template = nodes[0]['data']['node']['template']
    assert json.loads(template['profile_json']['value'])['provider'] == provider
    assert template['api_key']['load_from_db'] is (provider != 'ollama')
    again, _ = configure_flow(configured, 'review_research', config, 'papers','project')
    assert len([n for n in again['data']['nodes'] if n['data']['type']=='ConfiguredModel']) == 1


@pytest.mark.parametrize('provider', ['openrouter', 'compatible'])
def test_compatible_route_uses_chat_completions_not_model_name_inference(monkeypatch, provider):
    pytest.importorskip('langchain_openai')
    import httpx
    from langchain_openai import ChatOpenAI
    import langchain_openai
    calls=[]
    def reply(request):
        payload=json.loads(request.content)
        calls.append((request.url.path,payload))
        assert request.url.path == '/v1/chat/completions'
        assert 'seed' not in payload
        return httpx.Response(200,json={'id':'fixture','choices':[{'index':0,'message':{'role':'assistant','content':'ok'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})
    class MockChat(ChatOpenAI):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(reply)))
    monkeypatch.setattr(langchain_openai,'ChatOpenAI',MockChat)
    try:
        import lfx.base.models.provider_ssrf as ssrf
    except ImportError:
        pass
    else:
        monkeypatch.setattr(ssrf,'openai_compatible_client_kwargs',lambda *a,**kw:{})
    model=native_chat_model(ModelProfile(provider=provider,model='gpt-6-luna',base_url='https://fixture.example/v1'), 'fake-key')
    assert model.invoke('fixture').content == 'ok'
    assert len(calls)==1


def test_native_provider_cannot_override_credential_routing():
    profile = ModelProfile(provider='ollama',model='local',parameters={'base_url':'https://cloud.example'})
    with pytest.raises(ValueError, match='routing'):
        native_chat_model(profile)


def test_republication_preserves_server_assigned_name(tmp_path, monkeypatch):
    import hashlib
    from nima_semantica import workflow_installation as workflow
    data = {'nodes': [], 'edges': []}
    state = {'folder_id':'folder','flows':{'tool_guide':{'id':'flow',
        'hash':hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()}}}
    (tmp_path/'publication.json').write_text(json.dumps(state))
    source = tmp_path/'flow.json'
    source.write_text('{}')
    monkeypatch.setattr(workflow, 'maintained_flows', lambda: {'tool_guide':source})
    monkeypatch.setattr(workflow, 'configure_flow', lambda *a: ({'name':'Tool Guide','data':data}, {}))
    monkeypatch.setattr(workflow, 'validate_flow', lambda *a: True)
    monkeypatch.setattr(workflow, 'ensure_credentials', lambda *a: None)
    class Client:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, path):
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {'id':'flow','name':'Tool Guide (1)','data':data})
        def patch(self, path, json):
            assert 'name' not in json
            return self.get(path)
    monkeypatch.setattr(workflow, 'api', lambda *a: Client())
    workflow.publish_flows(Installation(data_root=str(tmp_path),llm=ModelProfile(provider='ollama',model='local')), 'corpus','project',tmp_path)


@pytest.mark.parametrize('provider', ['anthropic','gemini','ollama'])
def test_real_sdk_constructs_and_binds_tools_without_network(provider):
    pytest.importorskip({'anthropic':'langchain_anthropic','gemini':'langchain_google_genai','ollama':'langchain_ollama'}[provider])
    model = native_chat_model(ModelProfile(provider=provider, model='fixture'),
                              'fixture-not-a-real-key' if provider != 'ollama' else None)
    bound = model.bind_tools([{'type':'function','function':{'name':'answer',
        'description':'Return a result','parameters':{'type':'object','properties':{'text':{'type':'string'}},'required':['text']}}}],
        tool_choice='required',parallel_tool_calls=False)
    assert bound is not None


@pytest.mark.parametrize('metadata', [{'stop_reason':'max_tokens'}, {'finish_reason':'MAX_TOKENS'}, {'done_reason':'length'}])
def test_native_truncation_is_never_accepted(metadata):
    from nima_semantica.providers import completion_envelope, ModelManifest
    result = completion_envelope('{"valid":true}', ModelManifest(provider='fixture',model='fixture',revision='1',parameters={}),
        {'input_tokens':1,'output_tokens':1}, metadata)
    assert result['error'] == 'truncated' and result['result'] == {}
