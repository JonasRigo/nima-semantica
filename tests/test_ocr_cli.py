import hashlib
import json
from pathlib import Path

import pytest

from nima_semantica.cli import main
from nima_semantica.installation import Installation, ModelProfile, PdfRecognitionSettings, write_json
from nima_semantica.ocr import write_exports
from nima_semantica.orchestration.langflow.pdf_client import PdfNormalizerClient


@pytest.fixture
def configured(tmp_path,monkeypatch):
    config=Installation(data_root=str(tmp_path/'data'),llm=ModelProfile(model='fixture'),
        pdf_recognition=PdfRecognitionSettings(model=ModelProfile(model='vision-fixture')),
        pdf_url='http://pdf.test',pdf_token_file=str(tmp_path/'token'))
    path=tmp_path/'installation.json';write_json(path,config.model_dump())
    pdf=tmp_path/'paper with spaces.pdf';pdf.write_bytes(b'%PDF-fixture')
    state={'calls':0,'profile':'1'*64,'unresolved':False}
    monkeypatch.setattr(PdfNormalizerClient,'profile',lambda self:{'profile_hash':state['profile']})
    def normalize(self,data):
        state['calls']+=1
        return {'text':'Exact PDF evidence: $x^2 + y^2 = 1$.\n'*12,
            'diagnostics':[{'kind':'parser_manifest','runtime':{'profile_hash':state['profile']},
                'source_sha256':hashlib.sha256(data).hexdigest(),'provenance':[{'page':1}],
                'mathematics':{'unresolved_formula_count':int(state['unresolved'])}}],
            'normalization_complete':not state['unresolved']}
    monkeypatch.setattr(PdfNormalizerClient,'normalize',normalize)
    return path,pdf,state,config


def test_standalone_paths_sidecar_and_no_corpus_write(configured,tmp_path,capsys):
    config,pdf,state,installation=configured
    output=tmp_path/'new directory'/'result.md'
    assert main(['--config',str(config),'ocr',str(pdf),'-o',str(output)])==0
    saved=json.loads(capsys.readouterr().out)
    assert Path(saved['document'])==output
    evidence=json.loads(output.with_suffix('.ocr.json').read_text())
    assert evidence['text']==output.read_text()
    assert evidence['source_sha256']==hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert state['calls']==1
    assert not (Path(installation.data_root)/'corpus').exists()


def test_preflight_overwrite_and_input_collision(configured,tmp_path,capsys):
    config,pdf,state,_=configured
    output=tmp_path/'result.md';output.write_text('existing')
    args=['--config',str(config),'ocr',str(pdf),'-o',str(output)]
    assert main(args)==2
    assert state['calls']==0 and output.read_text()=='existing'
    assert main(args+['--force'])==0
    assert state['calls']==1
    assert main(['--config',str(config),'ocr',str(pdf),'-o',str(pdf),'--force'])==2
    assert pdf.read_bytes()==b'%PDF-fixture'


def test_partial_saved_and_service_failure_keeps_exports(configured,tmp_path,monkeypatch,capsys):
    config,pdf,state,_=configured
    output=tmp_path/'partial.md';state['unresolved']=True
    args=['--config',str(config),'ocr',str(pdf),'-o',str(output)]
    assert main(args)==1
    previous=output.read_bytes()
    monkeypatch.setattr(PdfNormalizerClient,'normalize',lambda *args:(_ for _ in ()).throw(ValueError('service unavailable')))
    assert main(args+['--force'])==2
    assert output.read_bytes()==previous


def test_ingestion_exports_same_result_and_replay_without_model(configured,tmp_path,capsys):
    config,pdf,state,_=configured
    directory=tmp_path/'exports'
    args=['--config',str(config),'ingest','papers',str(pdf),'--ocr-output-dir',str(directory)]
    assert main(args)==0
    result=json.loads(capsys.readouterr().out)
    assert result['data']['index_ready'] and not result['data']['ocr_export_errors']
    exported=result['data']['ocr_exports'][0]
    assert state['calls']==1
    assert Path(exported['document']).read_text()==json.loads(Path(exported['evidence']).read_text())['text']
    assert main(args+['--overwrite-ocr'])==0
    capsys.readouterr()
    assert state['calls']==1
    # A new recognition identity cannot replay the earlier preparation receipt.
    state['profile']='2'*64
    assert main(args+['--overwrite-ocr'])==0
    revised=json.loads(capsys.readouterr().out)
    assert state['calls']==2
    assert revised['data']['sources'][0]['source_id']!=result['data']['sources'][0]['source_id']


def test_ingestion_export_failure_retains_index(configured,tmp_path,monkeypatch,capsys):
    config,pdf,state,_=configured
    import nima_semantica.ocr as ocr
    def broken(*args,**kwargs):raise OSError('disk unavailable')
    monkeypatch.setattr(ocr,'write_exports',broken)
    assert main(['--config',str(config),'ingest','papers',str(pdf),'--ocr-output-dir',str(tmp_path/'exports')])==1
    result=json.loads(capsys.readouterr().out)
    assert result['data']['index_ready'] and result['data']['ocr_export_errors']
    assert state['calls']==1


def test_fast_export_rejected_without_call(configured,tmp_path):
    config,pdf,state,_=configured
    assert main(['--config',str(config),'ingest','papers',str(pdf),'--mode','fast','--ocr-output-dir',str(tmp_path)])==2
    assert state['calls']==0


def test_index_failure_still_exports_successful_preparation(configured,tmp_path,monkeypatch,capsys):
    config,pdf,state,_=configured
    from test_source_pipeline import MANIFEST
    import nima_semantica.setup_services as services
    class BrokenEmbedding:
        def embed(self,*args):raise ValueError('embedding unavailable')
    monkeypatch.setattr(services,'embedding_provider',lambda config:(BrokenEmbedding(),MANIFEST))
    assert main(['--config',str(config),'ingest','papers',str(pdf),'--index-mode','vector',
                 '--ocr-output-dir',str(tmp_path/'exports')])==1
    result=json.loads(capsys.readouterr().out)
    assert not result['data']['index_ready'] and result['data']['failed_stage']=='source_embeddings'
    assert Path(result['data']['ocr_exports'][0]['document']).is_file()
    assert state['calls']==1


def test_active_owner_export_reuses_published_evidence(configured,tmp_path,monkeypatch,capsys):
    config,pdf,state,installation=configured
    args=['--config',str(config),'ingest','papers',str(pdf)]
    assert main(args)==0
    from nima_semantica.tool_contracts import ToolResult
    result=ToolResult.model_validate(json.loads(capsys.readouterr().out))
    binding=Path(installation.data_root)/'projects/papers/owner/project.json'
    write_json(binding,{'corpus_id':'papers','project_id':'owner','mcp_url':'http://owner.test'})
    import nima_semantica.owner_operations as operations
    import nima_semantica.cli as cli
    monkeypatch.setattr(operations,'call_owner',lambda *args,**kwargs:result)
    monkeypatch.setattr(cli,'refresh_project_projection',lambda *args,**kwargs:None)
    from nima_semantica.storage import GraphStore
    from contextlib import closing
    with closing(GraphStore(Path(installation.data_root)/'corpus')):
        assert main(args+['--ocr-output-dir',str(tmp_path/'owner exports')])==0
    saved=json.loads(capsys.readouterr().out)
    assert Path(saved['data']['ocr_exports'][0]['document']).is_file()
    assert state['calls']==1


def test_serialization_failure_preserves_existing_files(tmp_path):
    output=tmp_path/'document.md';sidecar=output.with_suffix('.ocr.json')
    output.write_text('existing');sidecar.write_text('{}')
    with pytest.raises(TypeError):
        write_exports({'text':'new','diagnostics':[{'invalid':object()}]},output,force=True)
    assert output.read_text()=='existing' and sidecar.read_text()=='{}'
