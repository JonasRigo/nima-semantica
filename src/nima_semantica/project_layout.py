"""Versioned human-readable workspace paths, independent of canonical storage."""
from pathlib import Path,PurePosixPath
import hashlib,json
from pydantic import Field,model_validator
from .models import StrictModel,identity
from .storage import atomic_bytes

DEFAULT_PATHS={name:name for name in ('sources','excerpts','ontology','vocabulary','calculations','reports','figures','scripts','receipts','exports','archive')}

class ProjectLayout(StrictModel):
    version:int=Field(default=1,ge=1,le=1)
    paths:dict[str,str]=Field(default_factory=lambda:dict(DEFAULT_PATHS))

    @model_validator(mode='after')
    def relative_paths(self):
        if set(self.paths)!=set(DEFAULT_PATHS):raise ValueError('Layout must define exactly the standard folder roles')
        for value in self.paths.values():
            p=PurePosixPath(value)
            if not value or p.is_absolute() or any(part in ('..','.nima','.git') for part in p.parts) or '\\' in value or value=='.':
                raise ValueError('Layout paths must stay inside the workspace, outside private configuration')
        if len(set(self.paths.values()))!=len(self.paths):raise ValueError('Folder roles must have distinct destinations')
        return self

def resolve_layout(defaults=None,override=None):
    base=ProjectLayout.model_validate(defaults or {}).model_dump()
    if override:
        if set(override)-{'version','paths'}:raise ValueError('Unknown layout fields')
        base.update({k:v for k,v in override.items() if k!='paths'})
        base['paths'].update(override.get('paths',{}))
    return ProjectLayout.model_validate(base)

def folder(workspace,binding,role):
    root=Path(workspace).resolve()
    layout=ProjectLayout.model_validate(binding.get('layout',{}))
    target=root/layout.paths[role]
    if not target.resolve().is_relative_to(root):raise ValueError('Workspace folder follows a symlink outside the project')
    return target

def scaffold(workspace,binding):
    root=Path(workspace).resolve()
    for role in DEFAULT_PATHS:folder(root,binding,role).mkdir(parents=True,exist_ok=True)
    for sub in ('originals','normalized'):(folder(root,binding,'sources')/sub).mkdir(exist_ok=True)
    readme=root/'README.md'
    if not readme.exists():
        rows='\n'.join('- `'+path+'/`: '+role+'.' for role,path in ProjectLayout.model_validate(binding['layout']).paths.items())
        atomic_bytes(readme,('# '+binding['project_id']+'\n\nHuman-readable research documents and reproducible outputs.\nCanonical knowledge and private sessions remain in NIMA storage.\n\n'+rows+'\n\nRead NIMA_PROJECT.md to reconnect to this investigation.\n').encode())

def adoption_preview(workspace,binding,layout):
    root=Path(workspace).resolve();target=root/'.nima/project.json'
    value=resolve_layout(binding.get('layout'),layout).model_dump()
    preview=dict(workspace=str(root),corpus_id=binding['corpus_id'],project_id=binding['project_id'],layout=value,
        before_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),moves=[],note='Adopts folder roles without moving or rewriting existing research files.')
    return dict(preview,preview_hash=identity(preview))

def adopt_layout(preview,expected_hash):
    raw={k:v for k,v in preview.items() if k!='preview_hash'}
    if identity(raw)!=expected_hash or preview['preview_hash']!=expected_hash:raise ValueError('Layout preview hash differs')
    root=Path(preview['workspace']);target=root/'.nima/project.json'
    if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest()!=preview['before_sha256']:raise ValueError('Workspace binding changed after layout preview')
    binding=json.loads(target.read_text())
    if (binding['corpus_id'],binding['project_id'])!=(preview['corpus_id'],preview['project_id']):raise ValueError('Layout scope differs')
    binding['layout']=ProjectLayout.model_validate(preview['layout']).model_dump()
    backup=target.with_name('project.json.'+preview['before_sha256'][:12]+'.bak')
    if not backup.exists():atomic_bytes(backup,target.read_bytes())
    scaffold(root,binding);atomic_bytes(target,(json.dumps(binding,indent=2)+'\n').encode())
    return dict(adopted=True,binding=str(target),backup=str(backup),layout=binding['layout'],moves=[])
