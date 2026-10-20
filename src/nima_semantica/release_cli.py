"""Workspace, graph, chat and report command adapters over standard services."""
import json
from pathlib import Path
from .installation import identifier,project_binding,configuration_path,store_path,write_json
from .project_layout import folder

def workspace_binding(config,path,corpus=None,project=None):
    root=Path(path).resolve();local=root/'.nima/project.json'
    if local.exists():
        binding=json.loads(local.read_text())
        if corpus and binding['corpus_id']!=corpus or project and binding['project_id']!=project:raise ValueError('Workspace scope differs from command')
    elif corpus and project:binding=project_binding(config,corpus,project)
    else:raise ValueError('Select a bound workspace or provide corpus and project')
    return root,binding

def graph_command(args,config):
    from .corpus_snapshot import CorpusSnapshot
    from .studio_export import export_studio,open_studio
    from .private_client import session_ids,session_snapshot
    root,binding=workspace_binding(config,args.path,args.corpus,args.project)
    with CorpusSnapshot(store_path(config),corpus_id=args.corpus,project_id=args.project) as store:
        snapshot=store.read_okf_snapshot(corpus_id=args.corpus,project_id=args.project)
        names={r.content['source_id']:r.content.get('name',r.id) for _,r in store.iter_records(kind='Document')}
        regions=[dict(r.content,id=r.id,project_id=r.project_id,name=names.get(r.content['source_id'],r.id)) for _,r in store.iter_records(kind='SourceRegion')]
        vocabulary=None;policy=binding.get('normalized_reasoning',{})
        if policy.get('snapshot_artifact_id'):
            from .vocabulary_context import load_snapshot
            from .vocabulary_contracts import VocabularyPolicy
            vocabulary=load_snapshot(store,VocabularyPolicy.model_validate(policy),corpus_id=args.corpus,project_id=args.project,current=False).model_dump(mode='json')
    sessions=[]
    if not args.no_private:
        for kind in ('math','proof'):
            for sid in session_ids(binding,kind):
                if args.session and sid not in args.session:continue
                sessions.append(dict(session_snapshot(binding,sid,kind),kind=kind))
        if args.session and set(args.session)-{s['export']['session_id'] for s in sessions}:raise ValueError('Requested session is not visible in this project')
    destination=Path(args.output) if args.output else folder(root,binding,'exports')/'studio-complete'
    result=export_studio(snapshot,destination,regions=regions,vocabulary=vocabulary,sessions=sessions)
    result['private_sessions']=len(sessions)
    if args.open:result['studio_pid']=open_studio(destination,config.studio_executable)
    return {k:v for k,v in result.items() if k!='files'}

def calculation_command(args,config):
    from .private_client import session_snapshot
    from .calculation_report import save_calculation
    root,binding=workspace_binding(config,args.path)
    snapshot=session_snapshot(binding,args.session)
    destination=Path(args.output) if args.output else folder(root,binding,'calculations')/args.session
    return save_calculation(snapshot,destination)

def layout_command(args,config):
    from .project_layout import adoption_preview,adopt_layout
    root,binding=workspace_binding(config,args.path,args.corpus,args.project)
    override=json.loads(Path(args.layout).read_text())
    if args.apply:
        preview=json.loads(Path(args.preview_file).read_text())
        if preview['workspace']!=str(root) or preview['layout']!=__import__('nima_semantica.project_layout',fromlist=['resolve_layout']).resolve_layout(binding.get('layout'),override).model_dump():raise ValueError('Layout differs from saved preview')
        return adopt_layout(preview,args.apply)
    preview=adoption_preview(root,binding,override)
    if args.preview_file:write_json(args.preview_file,preview)
    return preview

def chat_command(args,config):
    from .knowledge_chat import retrieve_chat,answer_chat
    history=[]
    def turn(question):
        import os
        if not args.retrieve_only and config.llm.credential and not os.environ.get(config.llm.credential):
            from .knowledge_chat import answer_through_owner
            answer=answer_through_owner(config,question,corpus_id=args.corpus,project_id=args.project,knowledge=args.knowledge,visibility=args.scope,context_tokens=args.context_tokens,answer_tokens=args.answer_tokens,mode=args.mode,history=history)
            history.append(dict(question=question,answer=answer['answer']))
            return answer
        result=retrieve_chat(config,question,corpus_id=args.corpus,project_id=args.project,knowledge=args.knowledge,visibility=args.scope,context_tokens=args.context_tokens,mode=args.mode,history=history,answer_tokens=args.answer_tokens)
        if args.retrieve_only:return result.model_dump(mode='json')
        answer=answer_chat(config,question,result,history=history,context_tokens=args.context_tokens,answer_tokens=args.answer_tokens)
        history.append(dict(question=question,answer=answer['answer']))
        return answer
    if args.question:return turn(args.question)
    while True:
        try:question=input('nima> ').strip()
        except (EOFError,KeyboardInterrupt):break
        if question in ('/quit','/exit'):break
        if question:
            result=turn(question)
            print(json.dumps(result,indent=2,ensure_ascii=False) if args.json else result.get('answer',json.dumps(result,ensure_ascii=False)))
    return None

def register_commands(commands,project):
    graph=project.add_parser('graph',help='Export the complete scoped investigation for OKF Studio')
    graph.add_argument('project');graph.add_argument('--corpus',required=True);graph.add_argument('--path',default='.')
    graph.add_argument('--output');graph.add_argument('--open',action='store_true');graph.add_argument('--session',action='append');graph.add_argument('--no-private',action='store_true')
    chat=commands.add_parser('chat',help='Ask corpus, project or graph questions with bounded evidence')
    chat.add_argument('--corpus',required=True);chat.add_argument('--project');chat.add_argument('--scope',choices=['corpus','project','both'])
    chat.add_argument('--knowledge',choices=['sources','graph','both'],default='both');chat.add_argument('--mode',choices=['lexical','vector','hybrid'])
    chat.add_argument('--question');chat.add_argument('--json',action='store_true');chat.add_argument('--retrieve-only',action='store_true')
    chat.add_argument('--context-tokens',type=int,help='Evidence token budget, up to the installation chat ceiling')
    chat.add_argument('--answer-tokens',type=int,help='Reserved answer tokens, up to the installation chat ceiling')
    math=commands.add_parser('math').add_subparsers(dest='action',required=True)
    report=math.add_parser('report',help='Write a complete private calculation derivation and provenance audit')
    report.add_argument('session');report.add_argument('--path',default='.');report.add_argument('--output')

    report_commands=commands.add_parser('report',help='Shared SciPost-style papers and reports').add_subparsers(dest='action',required=True)
    report_commands.add_parser('doctor')
    for action in ('init','build'):
        sub=report_commands.add_parser(action);sub.add_argument('name');sub.add_argument('--path',default='.')
        if action=='init':sub.add_argument('--kind',choices=['paper','research','progress','proof'],default='research')
