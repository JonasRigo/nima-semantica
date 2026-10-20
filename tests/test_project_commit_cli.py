"""Operator delivery exercises native commits, scope guards and receipt replay."""
from contextlib import closing
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nima_semantica.corpus_registry import CorpusRegistry
from nima_semantica.corpus_snapshot import CorpusSnapshot
from nima_semantica.graph_projection import GraphProjectionService
from nima_semantica.installation import Installation, project_path, store_path, write_json
from nima_semantica.models import ConflictError, identity
from nima_semantica.okf_contracts import OKFDelta, OKFNode
from nima_semantica.project_commit_cli import commit_command
from nima_semantica.project_update import UpdateProjectContext, UpdateProjectRequest, update_project_graph
from nima_semantica.registry_contracts import CorpusDescriptor
from nima_semantica.storage import GraphStore


@pytest.fixture
def example(tmp_path):
    config = Installation(data_root=str(tmp_path / 'data'),
                          llm=dict(provider='compatible', base_url='http://unused', model='unused'))
    root = tmp_path / 'workspace'
    write_json(root / '.nima/project.json', dict(corpus_id='papers', project_id='research'))
    with closing(GraphStore(store_path(config))) as store:
        CorpusRegistry(store).register_corpus(CorpusDescriptor(corpus_id='papers', name='Example', corpus_revision='1'))
        delta = OKFDelta(delta_id='approved-example', corpus_id='papers', project_id='research',
            base_revision=store.graph_revision('papers', 'research'), ontology_profile='claim_obligation',
            reason='Reviewed example', upsert_nodes=(OKFNode(node_id='example', node_type='claim',
                corpus_id='papers', project_id='research', properties={'title': 'Example hypothesis'}),))
    path = tmp_path / 'delta.json'
    path.write_text(delta.model_dump_json())
    args = SimpleNamespace(project='research', corpus='papers', path=str(root), delta=str(path),
        resume=None, approve_delta_hash=None, approved_by=None, rationale=None, operation_id='operator-example',
        open=False, no_export=True)
    return config, args, delta


def approve(args, delta):
    return SimpleNamespace(**(vars(args) | dict(approve_delta_hash=identity(delta), approved_by='rigo',
                                              rationale='Human reviewed this exact example')))


def snapshot(config):
    return CorpusSnapshot(store_path(config), corpus_id='papers', project_id='research')


def test_prepare_is_read_only_and_full_commit_and_repeated_delta_are_idempotent(example):
    config, args, delta = example
    with snapshot(config) as store:
        before = store.revision
    prepared = commit_command(args, config)
    assert prepared['state'] == 'prepared' and not prepared['approval_granted']
    with snapshot(config) as store:
        assert store.revision == before
    assert not (project_path(config, 'papers', 'research') / 'graph-commits').exists()
    args = approve(args, delta)
    args.no_export = False
    result = commit_command(args, config)
    assert result['state'] == 'committed' and result['studio_status'] == 'complete', result
    assert result['studio']['knowledge_graph']['project_nodes'] == 1
    journal = Path(result['journal'])
    assert journal.stat().st_mode & 0o777 == 0o600
    with snapshot(config) as store:
        before = store.revision
        assert store.graph_revision('papers', 'research').project_revision == 1
    repeated = commit_command(args, config)
    assert repeated['state'] == 'already_committed'
    assert repeated['commit'] == result['native']['data']['commit']
    with snapshot(config) as store:
        assert store.revision == before and len(store.records('GraphCommitReceipt')) == 1


@pytest.mark.parametrize('damage', ['hash', 'scope', 'actor', 'stale', 'public_approval'])
def test_invalid_approval_or_selection_cannot_commit(example, damage):
    config, args, delta = example
    args = approve(args, delta)
    if damage == 'hash':
        args.approve_delta_hash = '0' * 64
    elif damage == 'scope':
        args.project = 'foreign'
    elif damage == 'actor':
        args.approved_by = ''
    elif damage == 'stale':
        with closing(GraphStore(store_path(config))) as store:
            other = delta.model_copy(update={'delta_id': 'advance'})
            from nima_semantica.graph_commit import OKFCommitApproval
            ctx = UpdateProjectContext(corpus_id='papers', project_id='research', actor='rigo',
                allow_graph_writes=True, allow_audit_writes=True,
                approval=OKFCommitApproval.for_delta(other, approval_id='advance', approved_by='rigo', rationale='Advance'))
            assert update_project_graph(store, UpdateProjectRequest(mode='commit', operation_id='advance',
                graph_revision=delta.base_revision, delta=other), ctx).data['committed']
        assert commit_command(args, config)['state'] == 'rejected'
        return
    else:
        with pytest.raises(ValueError):
            UpdateProjectRequest.model_validate({'approval': {'approved': True}})
        return
    with pytest.raises((ValueError, ConflictError)):
        commit_command(args, config)
    with snapshot(config) as store:
        assert store.graph_revision('papers', 'research').project_revision == 0


class Response:
    def __init__(self, data): self.data = data
    def raise_for_status(self): pass
    def json(self): return self.data


class Owner:
    """HTTP-shaped owner runs the actual native workflow with a held writer lease."""
    def __init__(self, store, config, *, lost_response=False, allow=True):
        self.store, self.calls, self.lost_response = store, [], lost_response
        context = UpdateProjectContext(corpus_id='papers', project_id='research', actor='harness',
            allow_graph_writes=allow, allow_audit_writes=allow, allow_projection_writes=True)
        self.flow = dict(folder_id='folder-1', data=dict(nodes=[dict(id='update-1', data=dict(
            type='UpdateProjectGraph', node=dict(template={
                k: dict(value=v) for k, v in context.model_dump(mode='json').items() if k != 'approval'} | {
                    'approval_json': dict(value='null')})))]))
        self.original = copy.deepcopy(self.flow)
        write_json(project_path(config, 'papers', 'research') / 'publication.json',
                   dict(folder_id='folder-1', flows=dict(update_project_graph=dict(id='flow-1'))))
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def get(self, path):
        assert path == '/api/v1/flows/flow-1'
        self.calls.append(('GET', path))
        return Response(copy.deepcopy(self.flow))
    def post(self, path, *, json, timeout):
        assert path == '/api/v1/run/flow-1' and timeout == 1200
        self.calls.append(('POST', copy.deepcopy(json)))
        assert set(json['tweaks']['update-1']) == {'actor', 'approval_json'}
        request = UpdateProjectRequest.model_validate_json(json['input_value'])
        values = {k: v['value'] for k, v in self.flow['data']['nodes'][0]['data']['node']['template'].items()}
        tweaks = json['tweaks']['update-1']
        from nima_semantica.graph_commit import OKFCommitApproval
        values.pop('approval_json')
        ctx = UpdateProjectContext(**(values | dict(actor=tweaks['actor'],
            approval=OKFCommitApproval.model_validate_json(tweaks['approval_json']))))
        result = update_project_graph(self.store, request, ctx)
        if self.lost_response:
            self.lost_response = False
            raise TimeoutError('Response lost after durable commit')
        return Response(dict(outputs=[dict(results=dict(message=dict(text='```json\n' +
                        result.model_dump_json() + '\n```')))]))


def test_owner_timeout_resume_preserves_exact_approval_and_never_edits_flow(example, monkeypatch):
    config, args, delta = example
    args = approve(args, delta)
    with closing(GraphStore(store_path(config))) as store:
        owner = Owner(store, config, lost_response=True)
        monkeypatch.setattr('nima_semantica.workflow_installation.api', lambda _: owner)
        unknown = commit_command(args, config)
        assert unknown['state'] == 'delivery_unknown' and unknown['committed'] is None
        retained = json.loads(Path(unknown['journal']).read_text())
        args.resume, args.delta = args.operation_id, None
        args.operation_id = args.approve_delta_hash = args.approved_by = args.rationale = None
        result = commit_command(args, config)
        assert result['state'] == 'committed', result
        assert store.graph_revision('papers', 'research').project_revision == 1
        assert len(store.records('GraphCommitReceipt')) == 1
        posts = [body for method, body in owner.calls if method == 'POST']
        assert len(posts) == 2 and posts[0] == posts[1]
        assert retained['context'] == json.loads(Path(unknown['journal']).read_text())['context']
        assert owner.flow == owner.original
        assert commit_command(args, config)['state'] == 'committed'
        assert len([method for method, _ in owner.calls if method == 'POST']) == 2


@pytest.mark.parametrize('damage', ['gate', 'scope', 'folder'])
def test_owner_configuration_cannot_be_overridden(example, monkeypatch, damage):
    config, args, delta = example
    with closing(GraphStore(store_path(config))) as store:
        owner = Owner(store, config, allow=damage != 'gate')
        if damage == 'scope':
            owner.flow['data']['nodes'][0]['data']['node']['template']['project_id']['value'] = 'foreign'
        if damage == 'folder': owner.flow['folder_id'] = 'foreign'
        monkeypatch.setattr('nima_semantica.workflow_installation.api', lambda _: owner)
        with pytest.raises(ConflictError): commit_command(approve(args, delta), config)
        assert not any(method == 'POST' for method, _ in owner.calls)
        assert store.graph_revision('papers', 'research').project_revision == 0


def test_projection_or_studio_failure_retains_committed_state(example, monkeypatch):
    config, args, delta = example
    monkeypatch.setattr(GraphProjectionService, 'rebuild', lambda *a, **k: (_ for _ in ()).throw(ValueError('failed')))
    monkeypatch.setattr('nima_semantica.release_cli.graph_command', lambda *a: (_ for _ in ()).throw(OSError('unavailable')))
    args = approve(args, delta); args.no_export = False
    result = commit_command(args, config)
    assert result['state'] == 'partial' and result['committed'] and result['studio_status'] == 'failed'
    with snapshot(config) as store:
        assert store.graph_revision('papers', 'research').project_revision == 1


def test_cli_parser_registers_operator_command(example, capsys):
    from nima_semantica.cli import main
    config, args, delta = example
    path = Path(args.path).parent / 'installation.json'
    write_json(path, config.model_dump(mode='json'))
    assert main(['--config', str(path), 'project', 'commit', 'research', '--corpus', 'papers',
                 '--path', args.path, '--delta', args.delta, '--no-export']) == 0
    assert json.loads(capsys.readouterr().out)['state'] == 'prepared'


def test_changed_delta_with_committed_id_is_rejected(example):
    config, args, delta = example
    assert commit_command(approve(args, delta), config)['committed']
    changed = delta.model_copy(update={'reason': 'Different reviewed content'})
    Path(args.delta).write_text(changed.model_dump_json())
    with pytest.raises(ConflictError, match='different hash'):
        commit_command(approve(args, changed), config)


def test_resume_rejects_changed_owner_policy_without_dispatch(example, monkeypatch):
    config, args, delta = example
    args = approve(args, delta)
    with closing(GraphStore(store_path(config))) as store:
        owner = Owner(store, config, lost_response=True)
        monkeypatch.setattr('nima_semantica.workflow_installation.api', lambda _: owner)
        assert commit_command(args, config)['state'] == 'delivery_unknown'
        args.resume, args.delta = args.operation_id, None
        args.operation_id = args.approve_delta_hash = args.approved_by = args.rationale = None
        owner.flow['data']['nodes'][0]['data']['node']['template']['max_changes']['value'] = 100
        with pytest.raises(ConflictError, match='policy changed'):
            commit_command(args, config)
        assert len([method for method, _ in owner.calls if method == 'POST']) == 1


def test_journal_is_exclusive_and_symlink_destination_is_rejected(tmp_path):
    from nima_semantica.project_commit_cli import _journal_lock, _save
    directory = tmp_path / 'journals'
    with _journal_lock(directory, 'same-operation') as path:
        with pytest.raises(BlockingIOError):
            with _journal_lock(directory, 'same-operation'):
                pytest.fail('Concurrent operation acquired the journal lock')
        target = tmp_path / 'private'
        target.write_text('original')
        path.symlink_to(target)
        with pytest.raises(ValueError):
            _save(path, {})
        assert target.read_text() == 'original'
