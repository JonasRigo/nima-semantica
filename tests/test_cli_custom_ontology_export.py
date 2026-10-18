"""Real CLI round-trip for a project-local, non-packaged ontology."""
from pathlib import Path
from nima_semantica.installation import Installation,ModelProfile,store_path
from nima_semantica.storage import GraphStore
from nima_semantica.corpus_registry import CorpusRegistry
from nima_semantica.registry_contracts import CorpusDescriptor
from nima_semantica.ontology_profiles import OntologyProfile,NodeType
from nima_semantica.ontology_tools import save_ontology,SaveOntologyRequest,OntologyContext
from nima_semantica.ontology_services import OntologyService
from nima_semantica.graph_commit import commit_okf_delta,OKFCommitApproval
from nima_semantica.okf_contracts import OKFDelta,OKFNode
from nima_semantica.okf_io import import_bundle
from nima_semantica.okf_mapping import bundle_to_snapshot
from nima_semantica.cli import main

def test_cli_exports_project_local_ontology_losslessly(tmp_path):
    config=Installation(data_root=str(tmp_path/'data'),llm=ModelProfile(provider='compatible',model='fixture',base_url='http://127.0.0.1:1/v1'))
    path=tmp_path/'installation.json';path.write_text(config.model_dump_json())
    scope=dict(corpus_id='papers',project_id='research')
    store=GraphStore(store_path(config))
    try:
        CorpusRegistry(store).register_corpus(CorpusDescriptor(corpus_id='papers',name='papers',corpus_revision='1'))
        profile=OntologyProfile(name='custom_physics',version='1.0.0',node_types=(NodeType(name='fixed_point',description='Project physics type'),))
        result=save_ontology(store,SaveOntologyRequest(mode='save',operation_id='profile',profile=profile.model_dump(mode='json')),OntologyContext(**scope,allow_writes=True))
        assert result.status=='complete'
        node=OKFNode(node_id='critical',node_type='fixed_point',ontology_profile=profile.digest,properties={'text':'A conditional critical point'},**scope)
        delta=OKFDelta(delta_id='seed',base_revision=store.graph_revision(**scope),ontology_profile=profile.digest,upsert_nodes=(node,),reason='Fixture custom project graph',**scope)
        commit_okf_delta(store,delta,OKFCommitApproval.for_delta(delta,approval_id='fixture-approval',approved_by='test',rationale='Fixture exact delta'),ontology=OntologyService.from_store(store,**scope))
        expected=store.read_okf_snapshot(**scope)
    finally:store.close()
    out=tmp_path/'okf'
    assert main(['--config',str(path),'export','okf','--corpus','papers','--project','research','--output',str(out)])==0
    assert bundle_to_snapshot(import_bundle(out))==expected
