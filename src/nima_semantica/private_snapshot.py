"""Consistent, non-recovering private SQLite snapshots alongside any owner."""
import json,sqlite3
from pathlib import Path
from .private_payloads import expand

def read_private_snapshot(binding,sid,kind='math'):
    config=json.loads(Path(binding[kind+'_config']).read_text());path=Path(config['database_path']).resolve()
    if (config.get('corpus_id','papers'),config['project_id'])!=(binding['corpus_id'],binding['project_id']):raise ValueError('Private configuration scope differs')
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row;db.execute('pragma query_only=ON');db.execute('BEGIN')
        if kind=='math':
            row=db.execute('select * from sessions where session_id=? and project_id=?',(sid,binding['project_id'])).fetchone()
            life=db.execute('select * from math_lifecycle where session_id=?',(sid,)).fetchone()
            if row is None or life is None:raise ValueError('Unknown or foreign private session')
            original=json.loads(life['binding_json'])
            if (original['corpus_id'],original['project_id'])!=(binding['corpus_id'],binding['project_id']):raise ValueError('Private session scope differs')
            from .math_graph_session import GraphSessionBinding,PrivateGraphSessionStore
            state=expand(db,life['scope_json'],json.loads(row['state_json']))
            from .math_task_contract import MathTaskFact
            from .math_indexed_count import IndexedSet
            typed=dict(original)
            typed['task_facts']=tuple(MathTaskFact.model_validate(x) for x in original.get('task_facts',[]))
            typed['indexed_sets']=tuple(IndexedSet.model_validate(x) for x in original.get('indexed_sets',[]))
            original_binding=GraphSessionBinding(**typed)
            if row['binding_hash']!=original_binding.digest():raise ValueError('Corrupt private session binding')
            graph=PrivateGraphSessionStore._restore(original_binding,state)
            vocabulary=db.execute('select payload from math_vocabulary where session_id=?',(sid,)).fetchone()
            vocab=json.loads(vocabulary[0]) if vocabulary else dict(enabled=False,status='disabled')
            status=graph.frontier()
            status.update(lifecycle=life['state'],active_request=life['active'],task=original['task'],required_paths=original['required_paths'])
            closed=db.execute("select o.response_json from math_outcomes o join math_attempts a on a.session_id=o.session_id and a.request_id=o.request_id where a.session_id=? and a.name='close' order by a.revision desc limit 1",(sid,)).fetchone()
            if closed: status['closure']=expand(db,life['scope_json'],json.loads(closed[0]))
            envelope=dict(session_id=sid,revision=row['revision'],status='completed')
            result=dict(export=dict(envelope,result=graph.export()),status=dict(envelope,result=status),vocabulary=dict(envelope,result={'vocabulary':vocab}),receipts=graph.receipts)
        else:
            scope=json.dumps(dict(corpus_id=binding['corpus_id'],project_id=binding['project_id']),sort_keys=True,separators=(',',':'))
            row=db.execute('select * from proof_sessions where id=? and scope=?',(sid,scope)).fetchone()
            if row is None:raise ValueError('Unknown or foreign proof session')
            state=json.loads(row['state']);envelope=dict(session_id=sid,revision=row['revision'],status='completed')
            receipts=[json.loads(r[0]) for r in db.execute('select payload from proof_receipts where session=? order by rowid',(sid,))]
            result=dict(export=dict(envelope,result={'graph':state,'receipts':receipts}),status=dict(envelope,result={'lifecycle':row['lifecycle'],'scope':json.loads(scope)}),vocabulary=None,receipts=receipts)
    result['read_transport']='consistent_read_only_sqlite_snapshot'
    return result
