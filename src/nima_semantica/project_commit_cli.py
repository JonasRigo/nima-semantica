"""Trusted operator approval, journaled delivery and native project graph commits.

Approval travels in operator context, never in the public MCP request. A live
owner receives invocation-only Langflow tweaks; saved workflows are read-only.
"""
from contextlib import contextmanager
from asyncio import CancelledError
import fcntl
import json
import os
from pathlib import Path
from types import SimpleNamespace

from .corpus_snapshot import CorpusSnapshot
from .graph_commit import OKFCommitApproval
from .installation import identifier, project_path, store_path, write_json
from .models import ConflictError, identity
from .okf_contracts import OKFDelta
from .owner_operations import native_result
from .project_update import UpdateProjectContext, UpdateProjectRequest, update_project_graph
from .storage import GraphStore


def register_command(project):
    command = project.add_parser('commit', help='Review and explicitly approve an exact project graph delta')
    command.add_argument('project')
    command.add_argument('--corpus', required=True)
    command.add_argument('--path', default='.')
    selection = command.add_mutually_exclusive_group(required=True)
    selection.add_argument('--delta', help='Exact prepared OKFDelta JSON file')
    selection.add_argument('--resume', help='Resume a retained operator operation ID')
    command.add_argument('--approve-delta-hash', help='Explicit approval of this exact canonical delta hash')
    command.add_argument('--approved-by', help='Operator identity retained in the native approval')
    command.add_argument('--rationale')
    command.add_argument('--operation-id')
    command.add_argument('--open', action='store_true', help='Refresh the export and open OKF Studio')
    command.add_argument('--no-export', action='store_true', help='Skip the Studio export')


def _json_file(path, limit=8_000_000):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError('Expected a bounded regular JSON file')
    return json.loads(path.read_bytes())


def _summary(delta):
    return dict(delta_id=delta.delta_id, delta_hash=identity(delta),
                corpus_id=delta.corpus_id, project_id=delta.project_id,
                base_revision=delta.base_revision.model_dump(mode='json'),
                changes=dict(upsert_nodes=len(delta.upsert_nodes), add_edges=len(delta.add_edges),
                             remove_nodes=len(delta.remove_node_ids), remove_edges=len(delta.remove_edge_ids)))


@contextmanager
def _journal_lock(directory, operation_id):
    # User-controlled IDs are hashed, and approvals/journals are private files.
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        raise ValueError('Refusing a symlink journal directory')
    path = directory / (identity(operation_id) + '.json')
    fd = os.open(path.with_suffix('.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield path
    finally:
        os.close(fd)


def _save(path, journal):
    write_json(path, journal)
    # Persist the rename before any potentially uncertain commit delivery.
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _owner_context(client, config, scope):
    publication = _json_file(project_path(config, scope['corpus_id'], scope['project_id']) / 'publication.json')
    flow_id = publication['flows']['update_project_graph']
    # Publication entries normally contain objects, older registrations used IDs.
    if isinstance(flow_id, dict):
        flow_id = flow_id['id']
    response = client.get('/api/v1/flows/' + identifier(flow_id))
    response.raise_for_status()
    flow = response.json()
    if flow.get('folder_id') != publication['folder_id']:
        raise ConflictError('Published update flow folder differs from project registration')
    nodes = flow['data']['nodes']
    matches = [n for n in nodes if n['data'].get('type') == 'UpdateProjectGraph']
    if len(matches) != 1:
        raise ConflictError('Expected exactly one project update operator')
    node = matches[0]
    template = node['data']['node']['template']
    values = {key: value.get('value') for key, value in template.items() if isinstance(value, dict)}
    if any(values.get(key) != value for key, value in scope.items()):
        raise ConflictError('Published update operator scope differs')
    fields = set(UpdateProjectContext.model_fields) - {'approval'}
    context = UpdateProjectContext(**{key: values[key] for key in fields if key in values})
    return flow_id, node['id'], context


@contextmanager
def _executor(config, scope, actor, expected=None):
    """Acquire the exclusive local lease or call the existing authenticated owner."""
    try:
        store = GraphStore(store_path(config))
    except ConflictError as exc:
        if str(exc) != 'another service owns this corpus':
            raise
    else:
        try:
            context = UpdateProjectContext(**scope, actor=actor, allow_graph_writes=True,
                                          allow_audit_writes=True, allow_projection_writes=True)
            if expected is not None and context != expected:
                raise ConflictError('Operator policy changed; refusing a divergent resume')
            yield context, lambda request, ctx: update_project_graph(store, request, ctx)
        finally:
            store.close()
        return
    from .workflow_installation import api
    with api(config) as client:
        flow_id, node_id, configured = _owner_context(client, config, scope)
        context = configured.model_copy(update={'actor': actor, 'approval': None})
        if expected is not None and context != expected:
            raise ConflictError('Owner policy changed; refusing a divergent resume')
        def invoke(request, ctx):
            response = client.post('/api/v1/run/' + flow_id, timeout=1200,
                json=dict(input_value=request.model_dump_json(), input_type='chat', output_type='chat',
                          session_id='nima-operator-' + identity(request.operation_id),
                          tweaks={node_id: dict(actor=ctx.actor,
                              approval_json=ctx.approval.model_dump_json() if ctx.approval else 'null')}))
            response.raise_for_status()
            return native_result(SimpleNamespace(isError=False, structuredContent=response.json(), content=[]),
                                 'Update Project Graph')
        yield context, invoke


def _export(result, args, config):
    if args.no_export or not result.get('committed'):
        return result
    from .release_cli import graph_command
    try:
        result['studio'] = graph_command(SimpleNamespace(project=args.project, corpus=args.corpus,
            path=args.path, output=None, open=args.open, no_private=True, session=None), config)
        result['studio_status'] = 'complete'
    except Exception as exc:
        result.update(studio_status='failed', studio_error=type(exc).__name__)
    return result


def commit_command(args, config):
    from .release_cli import workspace_binding
    identifier(args.corpus); identifier(args.project)
    workspace_binding(config, args.path, args.corpus, args.project)
    if args.no_export and args.open:
        raise ValueError('--open requires an export')
    scope = dict(corpus_id=args.corpus, project_id=args.project)
    directory = project_path(config, args.corpus, args.project) / 'graph-commits'
    if args.resume:
        if any((args.approve_delta_hash, args.approved_by, args.rationale, args.operation_id)):
            raise ValueError('Resume uses the retained request and approval; do not supply replacements')
        operation_id = identifier(args.resume)
    else:
        delta = OKFDelta.model_validate(_json_file(args.delta, 4_000_000))
        if (delta.corpus_id, delta.project_id) != (args.corpus, args.project):
            raise ConflictError('Delta scope differs from the selected project')
        operation_id = identifier(args.operation_id or 'operator-' + identity(delta))
        if args.approve_delta_hash is not None:
            if args.approve_delta_hash != identity(delta):
                raise ConflictError('Approval hash differs from the exact delta')
            if not args.approved_by or not args.rationale or not args.rationale.strip():
                raise ValueError('Approval requires --approved-by and --rationale')
            identifier(args.approved_by)
        elif args.approved_by or args.rationale:
            raise ValueError('Operator identity and rationale require --approve-delta-hash')
        with CorpusSnapshot(store_path(config), **scope) as store:
            previous = store.get_okf_operation(delta.delta_id, **scope)
            if previous:
                if previous['delta_hash'] != identity(delta):
                    raise ConflictError('Delta ID was committed with a different hash')
                return _export(dict(_summary(delta), state='already_committed', committed=True,
                                    commit=previous['result'], approval=previous['approval']), args, config)
            # Read-only native preparation retains ontology, evidence and revision checks.
            prepared = update_project_graph(store, UpdateProjectRequest(mode='prepare', operation_id=operation_id,
                graph_revision=delta.base_revision, delta=delta), UpdateProjectContext(**scope))
        if prepared.status != 'complete':
            return dict(_summary(delta), state='rejected', committed=False, native=prepared.model_dump(mode='json'))
        if args.approve_delta_hash is None:
            return dict(_summary(delta), state='prepared', committed=False, approval_granted=False,
                        required_approval=prepared.data['required_approval'])
    with _journal_lock(directory, operation_id) as path:
        if args.resume:
            journal = _json_file(path)
        elif path.exists():
            raise ConflictError('Operation journal already exists; use --resume ' + operation_id)
        else:
            journal = None
        if journal is not None:
            request = UpdateProjectRequest.model_validate(journal['request'])
            ctx = UpdateProjectContext.model_validate(journal['context'])
            delta = request.delta
            if request.operation_id != operation_id or request.mode != 'commit' or delta is None:
                raise ConflictError('Journal request identity differs')
            if (ctx.corpus_id, ctx.project_id) != (args.corpus, args.project):
                raise ConflictError('Journal scope differs')
            approval = OKFCommitApproval.for_delta(delta, **{
                key: getattr(ctx.approval, key) for key in ('approval_id', 'approved_by', 'rationale', 'approved_at')})
            if approval != ctx.approval or ctx.actor != approval.approved_by:
                raise ConflictError('Journal approval binding differs')
            if journal.get('result') is not None:
                return _export(dict(journal['result']), args, config)
        else:
            request = UpdateProjectRequest(mode='commit', operation_id=operation_id,
                                           graph_revision=delta.base_revision, delta=delta)
            ctx = None
        actor = ctx.actor if ctx else args.approved_by
        expected = ctx.model_copy(update={'approval': None}) if ctx else None
        with _executor(config, scope, actor, expected) as (policy, invoke):
            if not policy.allow_graph_writes or not policy.allow_audit_writes:
                raise ConflictError('Configured owner does not authorize graph and audit writes')
            if ctx is None:
                approval = OKFCommitApproval.for_delta(delta, approval_id='approval-' + identity(operation_id),
                    approved_by=actor, rationale=args.rationale)
                ctx = policy.model_copy(update={'approval': approval})
                journal = dict(schema_version=1, request=request.model_dump(mode='json'),
                               context=ctx.model_dump(mode='json'), result=None)
            journal['state'] = 'dispatching'
            _save(path, journal)
            try:
                outcome = invoke(request, ctx)
            except (Exception, KeyboardInterrupt, CancelledError) as exc:
                journal['state'] = 'delivery_unknown'
                _save(path, journal)
                return dict(_summary(delta), state='delivery_unknown', committed=None,
                            error=type(exc).__name__, operation_id=operation_id, journal=str(path),
                            resume='nima project commit ' + args.project + ' --corpus ' + args.corpus +
                                   ' --resume ' + operation_id)
            result = dict(_summary(delta), state='committed' if outcome.status == 'complete' else outcome.status,
                          committed=outcome.data.get('committed', False), operation_id=operation_id,
                          journal=str(path), native=outcome.model_dump(mode='json'))
            journal.update(state=result['state'], result=result)
            _save(path, journal)
    return _export(result, args, config)
