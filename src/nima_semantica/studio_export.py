"""Complete, scope-preserving OKF presentation for the existing Studio reader."""
from pathlib import Path
from urllib.parse import quote
import hashlib,json,posixpath,re,shutil,subprocess
import yaml
from .models import identity
from .storage import atomic_bytes

def segment(value):return quote(str(value),safe='-_').replace('.','%2E')
def display_math(text):
    parts=re.split(r'(```.*?```)',text,flags=re.S)
    for i in range(0,len(parts),2):
        parts[i]=re.sub(r'\\\[(.*?)\\\]',lambda m:'\n$$\n'+m.group(1).strip()+'\n$$\n',parts[i],flags=re.S)
        parts[i]=re.sub(r'\\\((.*?)\\\)',lambda m:'$'+m.group(1)+'$',parts[i],flags=re.S)
    return ''.join(parts)

def export_studio(snapshot,directory,*,regions=(),vocabulary=None,sessions=()):
    """All inputs are exact scoped snapshots; canonical state is never mutated."""
    from .calculation_report import exact
    root=Path(directory).resolve();root.mkdir(parents=True,exist_ok=True)
    if (root/'.git').exists():raise ValueError('Use a generated export folder, not a repository root')
    previous=root/'.nima-generated.json'
    if any(root.iterdir()) and not previous.exists():raise ValueError('Existing output is not a NIMA presentation export')
    staged={};concepts={};edges=[];scope=dict(corpus_id=snapshot.corpus_id,project_id=snapshot.project_id)
    def asset(name,value):staged['assets/'+name+'.json']=(json.dumps(value,indent=2,ensure_ascii=False)+'\n').encode()
    def link(a,b,label):
        edges.append((a,b));return '['+str(label).replace('[','(').replace(']',')').replace('\n',' ')+']('+posixpath.relpath(b+'.md',posixpath.dirname(a) or '.')+')'
    def concept(key,kind,title,body,tags,metadata):
        if key in concepts:raise ValueError('Duplicate presentation identity')
        fm=dict(type=kind,title=str(title)[:512],status='deprecated' if 'historical-source' in tags else 'draft',tags=tags,nima=metadata)
        staged[key+'.md']=('---\n'+yaml.safe_dump(fm,sort_keys=False,allow_unicode=True)+'---\n\n# '+str(title)+'\n\n'+display_math(body)+'\n').encode()
        concepts[key]=dict(title=title,type=kind,tags=tags)
    asset('project-snapshot',snapshot.model_dump(mode='json'))
    project='overview/project';corpus='overview/corpus'
    concept(corpus,'Corpus',scope['corpus_id'],'Visible corpus scope.\n\n'+link(corpus,project,scope['project_id'] or 'Corpus knowledge'),['overview','scope-corpus'],scope)
    concept(project,'Project',scope['project_id'] or scope['corpus_id'],f'Graph revision: `{snapshot.graph_revision.model_dump(mode="json")}`.\n\n'+link(project,corpus,'Corpus'),['overview','scope-project'],scope)
    keymap={n.ref.key:'knowledge/'+('corpus/' if n.project_id is None else 'project/')+segment(n.node_id) for n in snapshot.nodes}
    source_map={r['id']:'sources/'+segment(r['id']) for r in regions}
    active={e.region_id for n in snapshot.nodes for e in n.evidence}
    for region in regions:
        if region.get('corpus_id',scope['corpus_id'])!=scope['corpus_id'] or region.get('project_id') not in (None,scope['project_id']):raise ValueError('Foreign source region')
        key=source_map[region['id']];used=region['id'] in active
        quality={k:v for k,v in region.get('metadata',{}).items() if k in ('preparation_quality','math_transcription','pdf_pages','pdf_page_precision','normalization_record_id')}
        warning=('\n\n**Mathematical transcription:** `'+str(quality['math_transcription'])+'`. Check the original source before relying on its equations.') if quality.get('math_transcription') else ''
        asset('source-'+segment(region['id']),region)
        text=''.join(c if ord(c)>=32 or c in '\n\t' else f'[U+{ord(c):04X}]' for c in region['text'])
        concept(key,'Source Evidence',region.get('name',region.get('source_id',region['id'])),
            ('Source evidence used by the current graph.' if used else 'Searchable source region; not cited by the current graph. Historical preparation retains its original qualification.')+'\n\n'+link(key,corpus,'Corpus')+warning+'\n\n'+text,
            ['layer-sources','reviewed-source' if used else 'source-region'],{**scope,'project_id':region.get('project_id'),'region_id':region['id'],'source_id':region.get('source_id'),'artifact_id':region.get('artifact_id'),**quality})
    for n in snapshot.nodes:
        key=keymap[n.ref.key];parts=[str(n.properties.get('text',n.properties.get('statement',''))),f'**NIMA status:** `{n.status.value}`.']
        for e in snapshot.edges:
            if e.source_id==n.ref:parts+=['- `'+e.relation+'` → '+link(key,keymap[e.target_id.key],e.target_id.local_id)]
        for evidence in n.evidence:
            if evidence.region_id not in source_map:raise ValueError('Knowledge evidence is missing from the presentation')
            parts+=['Evidence: '+link(key,source_map[evidence.region_id],evidence.region_id)]
        parts+=['Project: '+link(key,project,'Investigation')]
        concept(key,n.node_type,n.properties.get('title',n.node_id),'\n\n'.join(parts),['layer-knowledge','scope-corpus' if n.project_id is None else 'scope-project'],{**scope,'project_id':n.project_id,'original_id':n.node_id,'status':n.status.value})
    if vocabulary:
        asset('vocabulary',vocabulary);parent='overview/vocabulary'
        concept(parent,'Vocabulary','Normalized vocabulary','Snapshot: `'+str(vocabulary.get('content_hash'))+'`.\n\n'+link(parent,project,'Project'),['overview','layer-vocabulary'],{**scope,'content_hash':vocabulary.get('content_hash')})
        for entry in vocabulary.get('entries',[]):
            key='vocabulary/'+segment(entry['entry_id']);body=entry['definition']+'\n\n'+link(key,parent,'Vocabulary')
            for evidence in entry.get('evidence',[]):
                if evidence['region_id'] in source_map:body+='\n\nEvidence: '+link(key,source_map[evidence['region_id']],evidence['region_id'])
            concept(key,'Normalized Term',entry['term'],body,['layer-vocabulary'],{**scope,**entry})
    for session in sessions:
        kind=session.get('kind','math');exported=session['export'];sid=exported['session_id'];graph=exported['result']
        if kind=='proof':graph=graph.get('graph',graph)
        raw_nodes=graph.get('nodes',[]);nodes={n['id']:n for n in raw_nodes} if isinstance(raw_nodes,list) else raw_nodes
        prefix='private/'+kind+'/'+segment(sid);parent=prefix+'/overview';mapping={i:prefix+'/'+segment(i) for i in nodes}
        asset(kind+'-'+segment(sid),session)
        concept(parent,'Calculation Session' if kind=='math' else 'Proof Session',kind+' session '+sid,'Private working state.\n\n'+link(parent,project,'Project')+'\n\n'+exact(session.get('status',{})),['overview','layer-'+kind],{**scope,'session_id':sid,'revision':exported['revision']})
        for i,n in nodes.items():
            key=mapping[i];body=exact(n.get('value',n.get('statement',n)))+'\n\n## Dependencies\n\n'
            for dep in n.get('depends_on',[]):
                if dep not in mapping:raise ValueError('Private dependency is missing')
                body+='- '+link(key,mapping[dep],dep)+'\n'
            body+='\nSession: '+link(key,parent,'Overview')+'\n\n## Exact node\n\n'+exact(n)
            origin=n.get('origin',{})
            if origin.get('region_id') in source_map:body+='\n\nEvidence: '+link(key,source_map[origin['region_id']],'Source region')
            label=n.get('meaning') or origin.get('output_path') or n.get('operation') or n.get('kind') or i
            concept(key,n.get('kind','Private Node').replace('_',' ').title(),str(label)+' · '+i,body,['layer-'+kind,'scope-private'],{**scope,'session_id':sid,'original_id':i,'status':n.get('status','unqualified')})
        obligations=graph.get('obligations',[])
        if isinstance(obligations,dict):obligations=[dict(o,root_id=k) for k,o in obligations.items()]
        for o in obligations:
            i=o.get('root_id') or o.get('id');key=prefix+'/obligations/'+segment(i)
            body=exact(o)+'\n\n'+link(key,parent,'Session')
            if i in mapping:body+=' · '+link(key,mapping[i],'Affected operation')
            concept(key,'Open Obligation',str(i)+' · '+o.get('reason','obligation'),body,['layer-obligations','scope-private'],{**scope,'session_id':sid,**o})
    if any(b not in concepts for _,b in edges):raise ValueError('Broken presentation endpoint')
    index='---\nokf_version: "0.2"\n---\n\n# NIMA investigation\n\n'+'\n'.join('- ['+str(v['title']).replace('[','(').replace(']',')').replace('\n',' ')[:220]+']('+k+'.md)' for k,v in concepts.items())+'\n'
    staged['index.md']=index.encode()
    manifest=dict(**scope,graph_revision=snapshot.graph_revision.model_dump(mode='json'),concepts=len(concepts),links=len(set(edges)),files={name:hashlib.sha256(data).hexdigest() for name,data in staged.items()})
    old=json.loads(previous.read_text()) if previous.exists() else {'files':{}}
    # Refuse to overwrite user edits or remove files not owned by this exporter.
    for name,digest in old['files'].items():
        path=root/name
        if not path.resolve().is_relative_to(root) or path.is_symlink():raise ValueError('Unsafe generated path')
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Generated export was edited: '+name)
    for name,data in staged.items():
        target=root/name
        if not target.resolve().is_relative_to(root):raise ValueError('Export path escaped output directory')
        atomic_bytes(target,data)
    for name in set(old['files'])-staged.keys():(root/name).unlink(missing_ok=True)
    atomic_bytes(previous,(json.dumps(manifest,indent=2)+'\n').encode())
    return dict(manifest,directory=str(root))

def open_studio(directory,executable=None):
    import os
    candidate=executable or os.environ.get('NIMA_OKF_STUDIO') or shutil.which('okf-viewer')
    if not candidate:raise ValueError('OKF bundle is exported. Install OKF Studio or configure studio_executable / NIMA_OKF_STUDIO, then open this folder.')
    resolved=shutil.which(str(candidate))
    if not resolved or not Path(resolved).is_file():
        raise ValueError('OKF bundle is exported. Studio must be an executable file; for macOS use OKF Studio.app/Contents/MacOS/okf-viewer, or the source-build executable. Check studio_executable / NIMA_OKF_STUDIO.')
    try:
        process=subprocess.Popen([resolved,'open',str(Path(directory).resolve())],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    except OSError as error:
        raise ValueError('OKF bundle is exported. Studio could not launch: '+str(error)) from error
    return process.pid
