"""Read private sessions through their configured owner, never a second writer."""
import asyncio,json,os,sqlite3,sys
from pathlib import Path

async def _call(binding,kind,operation,request):
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client
    from contextlib import AsyncExitStack
    async with AsyncExitStack() as stack:
        remote=binding.get('private_services',{}).get(kind)
        if remote:
            from .private_service import private_token
            import httpx
            client=await stack.enter_async_context(httpx.AsyncClient(headers={'Authorization':'Bearer '+private_token(remote['token_file'])},trust_env=False,timeout=120))
            rd,wr,_=await stack.enter_async_context(streamable_http_client(remote['url'],http_client=client))
        else:
            env=dict(os.environ,NIMA_STORE_ROOT=binding['store_root'])
            rd,wr=await stack.enter_async_context(stdio_client(StdioServerParameters(command=sys.executable,args=['-m','nima_semantica.'+kind+'_mcp','--config',binding[kind+'_config']],env=env)))
        session=await stack.enter_async_context(ClientSession(rd,wr));await session.initialize()
        response=await session.call_tool('nima_'+kind+'_'+operation,{'request':request})
        if response.isError:raise ValueError('Private owner rejected '+operation)
        result=response.structuredContent or json.loads(next(c.text for c in response.content if c.type=='text'))
        if kind=='proof' and 'session_id' in result and 'revision' in result and 'result' not in result:
            result=dict(session_id=result['session_id'],revision=result['revision'],status='completed',result=result)
        if result.get('status') not in ('completed','complete'):raise ValueError('Private read did not complete: '+str(result.get('status')))
        return result

def call_private(binding,kind,operation,request):return asyncio.run(asyncio.wait_for(_call(binding,kind,operation,request),timeout=120))

def session_ids(binding,kind):
    """Discovery is read-only and works alongside a live owner."""
    config=json.loads(Path(binding[kind+'_config']).read_text());path=Path(config['database_path'])
    if not path.exists():return []
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        if kind=='proof':
            scope=json.dumps(dict(corpus_id=binding['corpus_id'],project_id=binding['project_id']),sort_keys=True,separators=(',',':'))
            tables={r[0] for r in db.execute("select name from sqlite_master where type='table'")}
            return [r[0] for r in db.execute('select id from proof_sessions where scope=? order by id',(scope,))] if 'proof_sessions' in tables else []
        columns={r[1] for r in db.execute('pragma table_info(sessions)')}
        if 'session_id' not in columns:return []
        rows=db.execute('select s.session_id,l.binding_json from sessions s join math_lifecycle l on l.session_id=s.session_id order by s.session_id').fetchall()
    found=[]
    for sid,raw in rows:
        value=json.loads(raw)
        if value.get('corpus_id')==binding['corpus_id'] and value.get('project_id')==binding['project_id']:found.append(sid)
    return found

def session_snapshot(binding,sid,kind='math'):
    if not binding.get('private_services',{}).get(kind):
        from .private_snapshot import read_private_snapshot
        return attach_vocabulary(read_private_snapshot(binding,sid,kind),binding)
    exported=call_private(binding,kind,'export',dict(session_id=sid,full_payload=True))
    status=call_private(binding,kind,'status',dict(session_id=sid,full_payload=True))
    if exported['revision']!=status['revision']:raise ValueError('Session changed during report export; repeat after current action finishes')
    vocabulary=call_private(binding,kind,'inspect',dict(session_id=sid,kind='vocabulary',full_payload=True)) if kind=='math' else None
    if vocabulary and vocabulary['revision']!=exported['revision']:raise ValueError('Session changed during vocabulary read')
    receipts={}
    if kind=='math':
        for rid in exported['result'].get('receipt_index',{}):
            value=call_private(binding,kind,'inspect',dict(session_id=sid,kind='receipt',identifier=rid,full_payload=True))
            if value['revision']!=exported['revision']:raise ValueError('Session changed during receipt read')
            receipts[rid]=value['result']
    return attach_vocabulary(dict(export=exported,status=status,vocabulary=vocabulary,receipts=receipts),binding)


def attach_vocabulary(snapshot,binding):
    vocab=(snapshot.get('vocabulary') or {}).get('result',{}).get('vocabulary',{})
    if vocab.get('enabled') and vocab.get('policy',{}).get('snapshot_artifact_id'):
        from .corpus_snapshot import CorpusSnapshot
        from .vocabulary_context import load_snapshot
        from .vocabulary_contracts import VocabularyPolicy
        with CorpusSnapshot(binding['store_root'],corpus_id=binding['corpus_id'],project_id=binding['project_id']) as store:
            full=load_snapshot(store,VocabularyPolicy.model_validate(vocab['policy']),corpus_id=binding['corpus_id'],project_id=binding['project_id'],current=False)
            snapshot['vocabulary_snapshot']=full.model_dump(mode='json')
    return snapshot
