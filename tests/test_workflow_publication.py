"""A successful flow write must not be repeated after delayed read visibility."""
import json
from types import SimpleNamespace

import httpx
import pytest

from nima_semantica import workflow_installation as workflow


def publisher(monkeypatch, tmp_path, responses):
    template = tmp_path / 'flow.json'
    template.write_text('{}')
    payload = {'name': 'A tool', 'data': {'nodes': [], 'edges': []}}
    monkeypatch.setattr(workflow, 'maintained_flows', lambda: {'tool': template})
    monkeypatch.setattr(workflow, 'configure_flow', lambda *args: (dict(payload), {'role': {'provider': 'anthropic'}}))
    monkeypatch.setattr(workflow, 'validate_flow', lambda flow: True)
    monkeypatch.setattr(workflow, 'ensure_credentials', lambda *args: None)
    monkeypatch.setattr(workflow.time, 'sleep', lambda delay: None)
    calls = []
    def handle(request):
        calls.append((request.method, request.url.path))
        if request.method == 'POST' and request.url.path == '/api/v1/projects/':
            return httpx.Response(201, json={'id': 'folder'})
        if request.method == 'POST' and request.url.path == '/api/v1/flows/':
            return httpx.Response(201, json={'id': 'created-flow'})
        if request.method == 'PATCH':
            pytest.fail('A completed pending write must not be repeated')
        assert request.method == 'GET' and request.url.path == '/api/v1/flows/created-flow'
        value = responses.pop(0)
        return httpx.Response(value, json=payload if value == 200 else {'detail':'not visible'})
    monkeypatch.setattr(workflow, 'api', lambda config: httpx.Client(base_url=config.langflow_url,
        transport=httpx.MockTransport(handle)))
    return SimpleNamespace(langflow_url='http://langflow.local'), tmp_path / 'publication', calls, payload


def test_transient_flow_readback_does_not_repeat_post(monkeypatch, tmp_path):
    config, directory, calls, _ = publisher(monkeypatch, tmp_path, [404, 404, 200])
    result = workflow.publish_flows(config, 'corpus', 'project', directory)
    assert calls.count(('POST', '/api/v1/flows/')) == 1
    assert calls.count(('GET', '/api/v1/flows/created-flow')) == 3
    assert not result['flows']['tool'].get('pending_verification')
    assert result['flows']['tool']['models']['role']['provider'] == 'anthropic'
    assert result['mcp_url'].endswith('/folder/streamable')


@pytest.mark.parametrize('failure', [404, 403])
def test_failed_readback_resumes_saved_identity_without_recreating(monkeypatch, tmp_path, failure):
    responses = [failure] * (5 if failure == 404 else 1)
    config, directory, calls, _ = publisher(monkeypatch, tmp_path, responses)
    with pytest.raises(httpx.HTTPStatusError):
        workflow.publish_flows(config, 'corpus', 'project', directory)
    saved = json.loads((directory / 'publication.json').read_text())
    assert saved['flows']['tool']['pending_verification']
    assert saved['flows']['tool']['id'] == 'created-flow'
    assert 'mcp_url' not in saved
    responses.append(200)
    result = workflow.publish_flows(config, 'corpus', 'project', directory)
    assert calls.count(('POST', '/api/v1/flows/')) == 1
    assert calls.count(('POST', '/api/v1/projects/')) == 1
    assert not result['flows']['tool'].get('pending_verification')


def test_pending_flow_edit_is_not_overwritten(monkeypatch, tmp_path):
    responses = [404] * 5
    config, directory, calls, payload = publisher(monkeypatch, tmp_path, responses)
    with pytest.raises(httpx.HTTPStatusError):
        workflow.publish_flows(config, 'corpus', 'project', directory)
    payload['data'] = {'nodes': ['user-edited'], 'edges': []}
    responses.append(200)
    with pytest.raises(ValueError, match='changed in the editor'):
        workflow.publish_flows(config, 'corpus', 'project', directory)
    assert calls.count(('POST', '/api/v1/flows/')) == 1
    assert json.loads((directory / 'publication.json').read_text())['flows']['tool']['pending_verification']
