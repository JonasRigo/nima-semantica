"""Behavioral acceptance for human workspaces, graph chat, reports and Studio."""
import hashlib,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from nima_semantica.models import identity,canonical
from nima_semantica.project_layout import resolve_layout,folder,scaffold,adoption_preview,adopt_layout
from nima_semantica.calculation_report import render_calculation,save_calculation,anchor
from nima_semantica.knowledge_chat import answer_chat
from nima_semantica.pdf.book import merge_batches

def test_layout_defaults_overrides_and_frozen_project(tmp_path):
    defaults=resolve_layout(override={'paths':{'reports':'manuscript'}})
    layout=resolve_layout(defaults.model_dump(),{'paths':{'calculations':'calculation'}})
    binding=dict(corpus_id='papers',project_id='test',layout=layout.model_dump())
    scaffold(tmp_path,binding)
    assert (tmp_path/'manuscript').is_dir() and (tmp_path/'calculation').is_dir()
    (tmp_path/'README.md').write_text('Human edited text')
    scaffold(tmp_path,binding)
    assert (tmp_path/'README.md').read_text()=='Human edited text'
    assert folder(tmp_path,binding,'reports')==tmp_path/'manuscript'
    with pytest.raises(ValueError):resolve_layout(override={'paths':{'reports':'../escape'}})
    (tmp_path/'external').symlink_to(tmp_path.parent,target_is_directory=True)
    binding['layout']['paths']['reports']='external/reports'
    with pytest.raises(ValueError):folder(tmp_path,binding,'reports')

def test_layout_adoption_preserves_files_and_recovers_binding(tmp_path):
    (tmp_path/'.nima').mkdir();path=tmp_path/'.nima/project.json';binding=dict(corpus_id='papers',project_id='test')
    path.write_text(json.dumps(binding));before=path.read_bytes();(tmp_path/'legacy.txt').write_text('keep')
    preview=adoption_preview(tmp_path,binding,{'paths':{'reports':'manuscript'}})
    with pytest.raises(ValueError):adopt_layout(preview,'wrong')
    result=adopt_layout(preview,preview['preview_hash'])
    assert Path(result['backup']).read_bytes()==before
    assert (tmp_path/'legacy.txt').read_text()=='keep'
    assert json.loads(path.read_text())['layout']['paths']['reports']=='manuscript'

def snapshot():
    nodes=[dict(id='task',kind='task',status='task_given',value='Calculate x+1',origin={},depends_on=[]),
        dict(id='x',kind='calculation_operation',operation='symbol',meaning='Given amplitude',value='x',status='provisional',origin={'kind':'assumption'},depends_on=['task']),
        dict(id='out',kind='calculation_output',value='x + 1',status='observed',origin={'output_path':'result'},depends_on=['x']),
        dict(id='failed',kind='experiment',value='failed attempt',status='failed',origin={'receipt_id':'r1'},depends_on=['task'])]
    graph=dict(nodes=nodes,obligations=[dict(root_id='x',status='open',reason='missing assumption support'),dict(root_id='failed',status='open',reason='audit')])
    return dict(export=dict(session_id='s1',revision=3,result=graph),status=dict(result={}),vocabulary=None,receipts={'r1':{'outcome':'code_failed'}})

def test_calculation_document_complete_and_faithful(tmp_path):
    value=snapshot();text,provenance=render_calculation(value)
    assert set(provenance['node_ids'])=={'task','x','out','failed'}
    assert provenance['selected_node_ids']==['task','x','out']
    assert provenance['audit_node_ids']==['failed']
    assert 'Provisional glossary' in text and not provenance['normalization_claimed']
    assert 'x + 1' in text and 'code_failed' in text and 'audit-only' in text
    for n in value['export']['result']['nodes']:assert 'id="'+anchor(n['id'])+'"' in text
    result=save_calculation(value,tmp_path)
    assert result['open_obligations']==2 and json.loads((tmp_path/'graph.json').read_text())==value
    value['export']['result']['nodes'][1]['depends_on']=['missing']
    with pytest.raises(ValueError):render_calculation(value)

def test_book_batch_merge_preserves_page_and_formula_offsets():
    first=dict(text='first',diagnostics=[dict(kind='formula',status='extracted',start=0,end=5,provenance=[{'page':1}]),dict(kind='parser_manifest',artifact_bundle=[])])
    second=dict(text='second',diagnostics=[dict(kind='formula',status='unresolved',start=0,end=6,provenance=[{'page':1}]),dict(kind='parser_manifest',artifact_bundle=[])])
    result=merge_batches([(1,16,first),(17,32,second)],'hash',{'profile_hash':'runtime'})
    assert result['text']=='first\n\nsecond'
    assert result['diagnostics'][1]['start']==7 and result['diagnostics'][1]['provenance'][0]['page']==17
    assert len(result['diagnostics'][-1]['provenance'])==32 and not result['normalization_complete']
    with pytest.raises(ValueError):merge_batches([(2,3,first)],'hash',{})

def test_math_presentation_never_executes_source(tmp_path):
    from nima_semantica.math_presentation import expression_latex
    assert '\\epsilon' in expression_latex('epsilon/4')
    assert expression_latex('__import__("os").system("touch '+str(tmp_path/'bad')+'")') is None
    assert not (tmp_path/'bad').exists()

def test_local_staging_scope_integrity_and_receipted_ingestion(tmp_path,lexical_installation):
    from nima_semantica.local_staging import stage_local,resolve_staged
    from nima_semantica.source_tools import LocalStagedSourceInput,SourceToolContext,PrepareSourcesRequest,prepare_sources
    from nima_semantica.storage import GraphStore
    from nima_semantica.cli import register_corpus
    path=tmp_path/'note.md';path.write_text('A sufficiently detailed local note about the physical question.')
    sid=stage_local(lexical_installation,path,'papers')
    source=LocalStagedSourceInput(name=path.name,staging_id=sid)
    with __import__('contextlib').closing(GraphStore(Path(lexical_installation.data_root)/'corpus')) as store:
        register_corpus(store,'papers')
        context=SourceToolContext(corpus_id='papers',project_id='test',allow_corpus_writes=True,local_staging_root=str(Path(lexical_installation.data_root)/'ingestion-inbox'))
        result=prepare_sources(store,PrepareSourcesRequest(mode='prepare_index',operation_id='local-file',sources=(source,)),context)
        assert result.status=='complete',result
        assert result.data['region_ids']
        before=store.revision
        assert prepare_sources(store,PrepareSourcesRequest(mode='prepare_index',operation_id='local-file',sources=(source,)),context)==result
        assert store.revision==before
        with pytest.raises(ValueError):resolve_staged(source,store,context.model_copy(update={'corpus_id':'foreign'}))
        directory=Path(lexical_installation.data_root)/'ingestion-inbox'/sid
        (directory/'source.bin').write_text('tampered')
        with pytest.raises(ValueError):resolve_staged(source,store,context)

def test_chat_one_call_real_citation_and_history_budget(lexical_installation):
    from nima_semantica.tool_contracts import ToolResult
    data=dict(corpus_id='papers',project_id='test',graph_revision={},projection_id='p',traversal={},truncated=False,
        regions=[dict(text='Exact evidence',evidence={'region_id':'r'},preparation_quality='full')],graph_nodes=[],paths=[])
    found=ToolResult(operation='Retrieve Research Context',status='complete',data=data)
    class Fake:
        def __init__(self,text):self.calls=[];self.text=text
        def invoke(self,value):self.calls.append(value);return SimpleNamespace(content=self.text,usage_metadata={'input_tokens':50,'output_tokens':10})
    model=Fake('The evidence states this [S1].')
    answer=answer_chat(lexical_installation,'Question',found,model=model,history=[{'answer':'x'*2000}],history_tokens=100)
    assert len(model.calls)==1 and answer['citations'][0]['reference']=={'region_id':'r'}
    assert json.loads(model.calls[0][1][1])['history']==[]
    with pytest.raises(ValueError):answer_chat(lexical_installation,'Question',found,model=Fake('Invented [S9]'))

def test_direct_graph_search_without_matching_source(store):
    from conftest import seed_region
    from nima_semantica.cli import register_corpus
    from nima_semantica.graph_projection import GraphProjectionService,GraphProjectionRequest
    from nima_semantica.graph_service import GraphService
    from nima_semantica.okf_contracts import OKFNode
    from nima_semantica.research_retrieval import projected_context,ResearchRetrievalRequest,ResearchRetrievalContext
    register_corpus(store,'papers');seed_region(store,'A source without the query term.')
    graph=GraphService(store)
    # Store a source-independent project concept through the native contract.
    from nima_semantica.models import Record
    node=OKFNode(node_id='special',node_type='claim',properties={'title':'quasiparticle uniqueness','text':'A provisional project claim.'},corpus_id='papers',project_id='test')
    from nima_semantica.okf_contracts import OKFDelta
    from nima_semantica.graph_commit import OKFCommitApproval,commit_okf_delta
    delta=OKFDelta(delta_id='fixture',corpus_id='papers',project_id='test',base_revision=store.graph_revision('papers','test'),ontology_profile='literature_evidence',upsert_nodes=(node,),reason='Fixture')
    commit_okf_delta(store,delta,OKFCommitApproval.for_delta(delta,approval_id='fixture',approved_by='test',rationale='Fixture'))
    GraphProjectionService(store).rebuild(GraphProjectionRequest(corpus_id='papers',project_id='test'))
    found=projected_context(store,ResearchRetrievalRequest(query='quasiparticle uniqueness',knowledge='graph',max_context_tokens=4000),ResearchRetrievalContext(corpus_id='papers',project_id='test'))
    assert found.data['graph_nodes'] and found.data['graph_nodes'][0]['ref']['local_id']=='special'
    assert not found.data['regions'] and found.data['traversal']['estimated_evidence_tokens']<=4000

def test_reporting_template_compiles_all_roles(tmp_path):
    from nima_semantica.reporting import create_report
    binding=dict(corpus_id='papers',project_id='test')
    for kind in ('paper','research','progress','proof'):
        result=create_report(tmp_path,binding,kind,kind)
        source=Path(result['source']).read_text()
        assert 'NIMA_REPORT_' not in source and '\\section{Open obligations' in source

def test_studio_retains_edges_and_refuses_overwriting_edits(store,tmp_path):
    from nima_semantica.cli import register_corpus
    from nima_semantica.studio_export import export_studio
    from nima_semantica.okf_io import import_bundle
    register_corpus(store,'papers')
    snap=store.read_okf_snapshot(corpus_id='papers',project_id='test')
    value=snapshot();value['kind']='math'
    first=export_studio(snap,tmp_path/'okf',sessions=[value]);assert first['concepts']==9
    bundle=import_bundle(tmp_path/'okf');assert len(bundle.concepts)==9
    second=export_studio(snap,tmp_path/'okf',sessions=[value]);assert first==second
    (tmp_path/'okf/index.md').write_text('Human edit')
    with pytest.raises(ValueError):export_studio(snap,tmp_path/'okf',sessions=[value])


def test_managed_skill_upgrade_preserves_human_changes(tmp_path):
    from nima_semantica.skill_installation import managed_skill_plan
    source=tmp_path/'source';source.mkdir();(source/'SKILL.md').write_text('version one')
    project=tmp_path/'project';project.mkdir()
    writes,conflicts=managed_skill_plan(project,{},['codex'],source)
    assert not conflicts
    for p,b in writes.items():p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
    (source/'SKILL.md').write_text('version two')
    writes,conflicts=managed_skill_plan(project,{},['codex'],source)
    assert writes[project/'.agents/skills/SKILL.md']==b'version two' and not conflicts
    (project/'.agents/skills/SKILL.md').write_text('human revision')
    writes,conflicts=managed_skill_plan(project,{},['codex'],source)
    assert conflicts==['.agents/skills/SKILL.md'] and project/'.agents/skills/SKILL.md' not in writes
    (project/'.agents/skills/SKILL.md').unlink();(project/'.agents/skills').rmdir()
    (project/'.agents/skills').symlink_to(source,target_is_directory=True)
    with pytest.raises(ValueError,match='symlink'):managed_skill_plan(project,{},['codex'],source)


def test_private_snapshot_read_only_and_binding_integrity(tmp_path):
    import sqlite3
    from nima_semantica.math_session_service import MathServiceConfig,MathSessionService
    from nima_semantica.private_snapshot import read_private_snapshot
    database=tmp_path/'private.sqlite';config=MathServiceConfig(database_path=str(database),project_id='test',run_id='r')
    service=MathSessionService(config)
    sid=service.open('Compute a provisional result',['answer'])['session_id']
    before=service.read(sid,'export')
    path=tmp_path/'math.json';path.write_text(config.model_dump_json())
    binding={'corpus_id':'papers','project_id':'test','math_config':str(path)}
    view=read_private_snapshot(binding,sid)
    assert all(before['result'][k]==v for k,v in view['export']['result'].items())
    assert service.read(sid,'export')==before
    with pytest.raises(ValueError,match='scope'):read_private_snapshot(dict(binding,project_id='foreign'),sid)
    with sqlite3.connect(database) as db:db.execute("update sessions set binding_hash='corrupt' where session_id=?",(sid,))
    with pytest.raises(ValueError,match='Corrupt'):read_private_snapshot(binding,sid)


def test_studio_existing_and_headless_setup(tmp_path,monkeypatch):
    from nima_semantica.desktop_dependencies import provision_studio
    executable=tmp_path/'studio';executable.touch()
    config=SimpleNamespace(studio_executable=str(executable),data_root=str(tmp_path))
    assert provision_studio(config)['source']=='existing installation'
    monkeypatch.delenv('DISPLAY',raising=False);monkeypatch.delenv('WAYLAND_DISPLAY',raising=False)
    monkeypatch.delenv('NIMA_OKF_STUDIO',raising=False)
    monkeypatch.setattr('nima_semantica.desktop_dependencies.shutil.which',lambda _:None)
    config.studio_executable=''
    assert not provision_studio(config)['available']


def test_graph_chat_visibility_is_applied_before_ranking(store):
    from nima_semantica.cli import register_corpus
    from nima_semantica.okf_contracts import OKFDelta,OKFNode
    from nima_semantica.graph_commit import OKFCommitApproval,commit_okf_delta
    from nima_semantica.graph_projection import GraphProjectionService,GraphProjectionRequest
    from nima_semantica.research_retrieval import projected_context,ResearchRetrievalRequest,ResearchRetrievalContext
    register_corpus(store,'papers')
    for project,nid in [(None,'shared'),('test','selected'),('foreign','secret')]:
        node=OKFNode(node_id=nid,node_type='claim',properties={'title':'regulator uniqueness','text':nid},corpus_id='papers',project_id=project)
        delta=OKFDelta(delta_id=nid,corpus_id='papers',project_id=project,base_revision=store.graph_revision('papers',project),ontology_profile='literature_evidence',upsert_nodes=(node,),reason='Fixture')
        commit_okf_delta(store,delta,OKFCommitApproval.for_delta(delta,approval_id=nid,approved_by='test',rationale='Fixture'))
    GraphProjectionService(store).rebuild(GraphProjectionRequest(corpus_id='papers',project_id='test'))
    for visibility,expected in [('corpus',{'shared'}),('project',{'selected'}),('both',{'shared','selected'})]:
        found=projected_context(store,ResearchRetrievalRequest(query='regulator uniqueness',knowledge='graph',visibility=visibility,max_context_tokens=4000),ResearchRetrievalContext(corpus_id='papers',project_id='test'))
        assert {n['ref']['local_id'] for n in found.data['graph_nodes']}==expected
        assert len(canonical(found.data))<=4000 and not found.data['regions']


def test_owner_answer_uses_current_evidence_and_one_model_call(store,monkeypatch):
    pytest.importorskip('lfx')
    import sys,asyncio
    from contextlib import contextmanager
    from conftest import seed_region
    from nima_semantica.cli import register_corpus
    from nima_semantica.graph_projection import GraphProjectionService,GraphProjectionRequest
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'deploy'))
    from build_tool_guide import load_component
    register_corpus(store,'papers');seed_region(store,'The regulator uniqueness assumption is explicitly provisional.')
    GraphProjectionService(store).rebuild(GraphProjectionRequest(corpus_id='papers',project_id='test'))
    cls=load_component('AnswerKnowledge');module=__import__(cls.__module__,fromlist=['configured_store'])
    @contextmanager
    def access(**kwargs):yield store
    monkeypatch.setattr(module,'configured_store',access)
    class Fake:
        calls=0
        def bind(self,**kwargs):assert kwargs=={'max_tokens':1024};return self
        def invoke(self,messages):self.calls+=1;return SimpleNamespace(content='The assumption is provisional [S1].',usage_metadata={'input_tokens':90,'output_tokens':12})
    model=Fake()
    component=cls().set(corpus_id='papers',project_id='test',allow_model_calls=True,model=model,mcp_request=json.dumps({'query':'regulator uniqueness','knowledge':'sources','mode':'lexical'}))
    result=asyncio.run(component.result_data()).data
    assert result['status']=='complete' and result['data']['model_calls']==1 and model.calls==1
    assert result['data']['citations'][0]['reference']['region_id']


def test_legacy_skill_hash_survives_package_replacement(tmp_path,monkeypatch):
    from nima_semantica.skill_installation import managed_skill_plan
    asset=tmp_path/'assets';(asset/'references').mkdir(parents=True)
    source=tmp_path/'source';source.mkdir();(source/'SKILL.md').write_text('new managed instructions')
    project=tmp_path/'project';target=project/'.agents/skills/SKILL.md';target.parent.mkdir(parents=True);target.write_text('published old instructions')
    baseline={'files':{'SKILL.md':[hashlib.sha256(target.read_bytes()).hexdigest()]}}
    (asset/'references/managed-skill-baselines.json').write_text(json.dumps(baseline))
    monkeypatch.setattr('nima_semantica.installation.assets',lambda:asset)
    writes,conflicts=managed_skill_plan(project,{},['codex'],source)
    assert not conflicts and writes[target]==b'new managed instructions'
    target.write_text('human changed instructions')
    writes,conflicts=managed_skill_plan(project,{},['codex'],source)
    assert conflicts==['.agents/skills/SKILL.md'] and target not in writes


def test_recognized_ocr_is_explicitly_unverified_in_retrieval(store):
    from conftest import seed_region
    from nima_semantica.document_ingestion import DocumentIngestionService
    from nima_semantica.source_corpus import SourceDescriptor
    from nima_semantica.artifact_contracts import ArtifactEnvelope
    from nima_semantica.cli import register_corpus
    from nima_semantica.source_quality import evidence_locator
    register_corpus(store,'papers')
    data=b'Original source fixture';aid=hashlib.sha256(data).hexdigest()
    descriptor=SourceDescriptor(source_id=aid,artifact_id=aid,name='fixture.txt',source_revision=aid,corpus_id='papers',media_type='text/plain')
    envelope=ArtifactEnvelope(artifact_id=aid,artifact_kind='source',media_type='text/plain',content_hash=aid,corpus_id='papers',source_revision=aid,content={})
    from nima_semantica.corpus_registry import CorpusRegistry
    from nima_semantica.registry_contracts import RegistryRevision
    CorpusRegistry(store).register_revision(RegistryRevision(revision_id='fixture',corpus_id='papers',sequence=1,changed_resource_ids=(aid,hashlib.sha256(b'A machine-transcribed formula x=1.').hexdigest())))
    result=DocumentIngestionService(store).ingest(data,descriptor,envelope,registry_revision='fixture',normalized_result=('A machine-transcribed formula x=1.',[dict(kind='formula',status='low_confidence',start=0,end=32)]))
    row=store.get(result.region_ids[0],corpus_id='papers')
    assert row.content['metadata']['math_transcription']=='machine_transcribed_unverified'
    assert evidence_locator(row.content)['math_transcription']=='machine_transcribed_unverified'


def test_studio_displays_source_transcription_qualification(store,tmp_path):
    from nima_semantica.cli import register_corpus
    from nima_semantica.studio_export import export_studio
    register_corpus(store,'papers');snap=store.read_okf_snapshot(corpus_id='papers',project_id='test')
    region=dict(id='r',text='A machine equation.',source_id='book',metadata={'math_transcription':'machine_transcribed_unverified','pdf_pages':[212]})
    export_studio(snap,tmp_path/'book',regions=[region])
    text=(tmp_path/'book/sources/r.md').read_text()
    assert '**Mathematical transcription:** `machine_transcribed_unverified`' in text
    assert 'pdf_pages:' in text and '212' in text
