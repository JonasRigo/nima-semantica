"""Scoped file staging for ingestion through an already running corpus owner."""
import hashlib,json,re
from pathlib import Path
from .models import identity
from .storage import atomic_bytes

def stage_local(config,path,corpus_id,project_id=None):
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise ValueError('Ingestion requires a regular file')
    data=path.read_bytes()
    if not data or len(data)>20_000_000:raise ValueError('Local source exceeds the 20 MB limit')
    descriptor=dict(corpus_id=corpus_id,project_id=project_id,name=path.name,sha256=hashlib.sha256(data).hexdigest())
    staging_id=identity(descriptor);root=Path(config.data_root)/'ingestion-inbox'/staging_id
    root.mkdir(parents=True,exist_ok=True)
    if root.is_symlink() or not root.resolve().is_relative_to((Path(config.data_root)/'ingestion-inbox').resolve()):raise ValueError('Unsafe staging destination')
    for name,value in (('source.bin',data),('manifest.json',(json.dumps(descriptor,sort_keys=True)+'\n').encode())):
        target=root/name
        if target.is_symlink():raise ValueError('Unsafe staged file')
        if target.exists() and target.read_bytes()!=value:raise ValueError('Staging identity conflict')
        if not target.exists():atomic_bytes(target,value)
    return staging_id

def resolve_staged(source,store,context):
    if not context.local_staging_root:raise ValueError('Local staging is not enabled by the corpus owner')
    root=Path(context.local_staging_root).resolve();directory=root/source.staging_id
    if not directory.resolve().is_relative_to(root) or directory.is_symlink():raise ValueError('Unsafe staging directory')
    if any((directory/name).is_symlink() for name in ('manifest.json','source.bin')):raise ValueError('Unsafe staged file')
    descriptor=json.loads((directory/'manifest.json').read_text());scope_project=context.project_id if context.source_scope=='project' else None
    if descriptor['corpus_id']!=context.corpus_id or descriptor['project_id']!=scope_project or descriptor['name']!=source.name or identity(descriptor)!=source.staging_id:raise ValueError('Staged source scope or identity differs')
    data=(directory/'source.bin').read_bytes()
    if not data or len(data)>20_000_000 or hashlib.sha256(data).hexdigest()!=descriptor['sha256']:raise ValueError('Staged source integrity differs')
    from .models import Record
    from .source_tools import AcquiredSourceInput
    artifact=store.artifact(data)
    attempt=store.put(Record(kind='AcquisitionAttempt',corpus_id=context.corpus_id,project_id=context.project_id,
        content=dict(status='completed',artifact_id=artifact,name=source.name,staging_only=True,origin='explicit_local_file')))
    return AcquiredSourceInput(name=source.name,acquired_name=source.name,artifact_id=artifact,acquisition_id=attempt)

def ingest_through_owner(args,config):
    from .installation import project_binding,list_projects
    from .owner_operations import call_owner
    corpus=args.corpus;project=args.project
    if project:binding=project_binding(config,corpus,project)
    else:
        candidates=[p for p in list_projects(config,corpus) if p.get('mcp_url')]
        if not candidates:raise ValueError('Register a project toolbox for this corpus before using its active owner')
        binding=project_binding(config,corpus,candidates[0]['project_id'])
    staging=stage_local(config,args.file,corpus,project)
    if getattr(args,"ocr_output_dir",None):
        from .ocr import destinations
        source=Path(args.file)
        if source.read_bytes().startswith(b"%PDF-"):
            digest=hashlib.sha256(source.read_bytes()).hexdigest()
            destinations(Path(args.ocr_output_dir)/(source.stem+"-"+digest[:12]+".md"),
                force=getattr(args,"overwrite_ocr",False),source=source)
    from .models import identity
    operation='cli-staged-'+identity(dict(staging_id=staging,index_mode=args.index_mode,mode=args.mode))
    if args.retry:
        import uuid
        operation+='-retry-'+uuid.uuid4().hex
    request=dict(mode='prepare_index',operation_id=operation,index_mode=args.index_mode,preparation_mode=args.mode,
        index_scope='project' if project else 'corpus',source_scope='project' if project else 'corpus',sources=[dict(name=Path(args.file).name,staging_id=staging)])
    if args.projection_max_tokens:request['projection_max_tokens']=args.projection_max_tokens
    result=call_owner(binding['mcp_url'],'Prepare and Index Sources',request,timeout=3600)
    if result.status not in ('complete','partial') or not result.data.get('index_ready'):
        return result
    from .cli import refresh_project_projection
    projects=[project] if project else [p['project_id'] for p in list_projects(config,corpus)]
    for selected in projects:refresh_project_projection(config,corpus,selected,args.projection_max_tokens)
    return result
